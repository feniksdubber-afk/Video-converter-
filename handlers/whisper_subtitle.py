"""
whisper_subtitle.py — video/audio ovozidan avtomatik subtitr (.srt) yaratish.

faster-whisper (CTranslate2 asosida, CPU-da ham yetarlicha tez ishlaydi)
kutubxonasidan foydalanadi. Model "small" — tezlik va aniqlik o'rtasida
muvozanat, kichik-o'rtacha VPS'larda ham ishlatilishi mumkin.

Foydalanish:
  1. Video yuboriladi → asosiy menyudan "📝 Subtitrlar" → "🗣 Auto-Subtitr (AI)"
  2. Til tanlanadi (avtomatik aniqlash / o'zbek / rus / ingliz)
  3. Ovoz ffmpeg bilan ajratib olinadi (16kHz mono WAV — Whisper talabi)
  4. faster-whisper orqali transkripsiya qilinadi (executor'da, event loop
     bloklanmasligi uchun)
  5. Natija .srt formatida yig'ilib, foydalanuvchiga yuboriladi

Model birinchi ishlatilganda internetdan yuklab olinadi (~500MB, "small")
va diskda keshlanadi (~/.cache/huggingface yoki WHISPER_MODEL_DIR).
"""

import asyncio
import logging
import os
import subprocess

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import make_temp_path
from utils.sender import send_file
from utils.task_queue import new_ticket, acquire_slot, release_slot

logger = logging.getLogger(__name__)

# ── Sozlamalar ────────────────────────────────────────────────────────────────

WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small")
WHISPER_MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR") or None  # None => standart kesh joyi
# CPU uchun eng samarali kvantlash; agar server kuchli bo'lsa "int8_float16"
# yoki "float32" ham ishlaydi, lekin int8 eng tez va eng kam RAM ishlatadi.
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

LANGUAGES = {
    "auto": "🌐 Avtomatik aniqlash",
    "uz": "🇺🇿 O'zbek",
    "ru": "🇷🇺 Rus",
    "en": "🇬🇧 Ingliz",
}

_model = None
_model_lock = asyncio.Lock()


def _md_escape(text) -> str:
    """Telegram Markdown (legacy) uchun maxsus belgilarni escape qiladi."""
    s = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


async def _get_model():
    """Whisper modelini lazy-load qiladi (faqat birinchi so'rovda, keyin keshda qoladi)."""
    global _model
    async with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            loop = asyncio.get_running_loop()

            def _load():
                return WhisperModel(
                    WHISPER_MODEL_SIZE,
                    device="cpu",
                    compute_type=WHISPER_COMPUTE_TYPE,
                    download_root=WHISPER_MODEL_DIR,
                )

            logger.info("Whisper modeli yuklanmoqda: %s (%s)", WHISPER_MODEL_SIZE, WHISPER_COMPUTE_TYPE)
            _model = await loop.run_in_executor(None, _load)
            logger.info("Whisper modeli tayyor.")
    return _model


def _lang_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"wsub_lang_{code}")]
            for code, label in LANGUAGES.items()]
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="cat_subtitle")])
    return InlineKeyboardMarkup(rows)


def _fmt_ts(seconds: float) -> str:
    """SRT formatidagi vaqt belgisi: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        text = seg.text.strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _extract_audio_sync(video_path: str, wav_path: str) -> None:
    """Whisper uchun 16kHz mono WAV ajratib oladi."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        wav_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])


# ── entry point (menyudan) ───────────────────────────────────────────────────

async def show_whisper_subtitle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    await query.edit_message_text(
        "🗣 *Auto-Subtitr (AI)*\n\n"
        "Video ovozidan avtomatik subtitr (.srt) yaratiladi "
        "(AI orqali, taxminan bir necha daqiqa vaqt olishi mumkin).\n\n"
        "Video tilini tanlang:",
        reply_markup=_lang_keyboard(),
        parse_mode="Markdown",
    )


# ── til tanlangach — transkripsiya ───────────────────────────────────────────

async def handle_whisper_lang(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    user_id = update.effective_user.id
    ticket = new_ticket()

    status_msg = await query.message.reply_text(
        "⏳ *Navbatga qo'yildi...*", parse_mode="Markdown",
    )
    await query.edit_message_text("⏳ Navbatda kutilmoqda...")

    ok = await acquire_slot(ticket, user_id, status_msg, label="Auto-Subtitr (AI)")
    if not ok:
        return  # foydalanuvchi navbatda bekor qildi

    wav_path = make_temp_path("wav")
    try:
        await status_msg.edit_text(
            "🎧 *Ovoz ajratib olinmoqda...*", parse_mode="Markdown",
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _extract_audio_sync, video_path, wav_path)

        await status_msg.edit_text(
            "🧠 *AI orqali tanib olinmoqda...*\n"
            "_(model birinchi marta ishlatilsa, yuklab olinishi biroz vaqt olishi mumkin)_",
            parse_mode="Markdown",
        )

        model = await _get_model()

        def _transcribe():
            kwargs = {}
            if lang != "auto":
                kwargs["language"] = lang
            segments, info = model.transcribe(wav_path, beam_size=5, vad_filter=True, **kwargs)
            # generator — executor ichida to'liq ro'yxatga aylantiramiz
            return list(segments), info

        segments, info = await loop.run_in_executor(None, _transcribe)

        if not segments:
            await status_msg.edit_text(
                "❌ Ovozdan matn aniqlanmadi (jim video yoki nutq yo'q bo'lishi mumkin).",
            )
            await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
            return

        srt_content = _segments_to_srt(segments)

        out_path = make_temp_path("srt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        detected_lang = getattr(info, "language", lang) or lang
        out_name = f"{base}_auto_{detected_lang}.srt"

        await status_msg.edit_text(
            f"✅ *Tayyor!* `{_md_escape(out_name)}`\n"
            f"🌐 Aniqlangan til: `{_md_escape(detected_lang)}`\n"
            f"📤 Yuborilmoqda...",
            parse_mode="Markdown",
        )
        await send_file(
            query.message, out_path, out_name,
            f"✅ Avtomatik subtitr ({_md_escape(detected_lang)})",
            context=context,
        )
        os.remove(out_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())

    except Exception as e:
        logger.exception("Whisper subtitr xato: %s", e)
        try:
            await status_msg.edit_text(f"❌ Xato:\n{e}")
        except Exception:
            pass
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass
        release_slot()
