import os
import subprocess
import json
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import make_temp_path
from utils.sender import send_file


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_streams(video_path: str) -> list[dict]:
    """ffprobe orqali barcha streamlarni qaytaradi."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=index,codec_type,codec_name,channels,r_frame_rate,width,height",
                "-show_entries", "stream_tags=language,title",
                "-of", "json", video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception:
        return []


def _stream_label(s: dict) -> str:
    idx = s.get("index", "?")
    stype = s.get("codec_type", "?").upper()
    codec = s.get("codec_name", "?")
    tags = s.get("tags", {})
    lang = tags.get("language", "")
    title = tags.get("title", "")

    extra = ""
    if stype == "VIDEO":
        w, h = s.get("width", ""), s.get("height", "")
        extra = f" {w}x{h}" if w and h else ""
    elif stype == "AUDIO":
        ch = s.get("channels", "")
        extra = f" {ch}ch" if ch else ""

    label = f"#{idx} {stype} [{codec}{extra}]"
    if lang:
        label += f" {lang}"
    if title:
        label += f" — {title}"
    return label


def _stream_inline_keyboard(streams: list[dict], action: str):
    """action: 'remove' yoki 'extract'"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = []
    for s in streams:
        idx = s.get("index")
        label = _stream_label(s)
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{action}_stream_{idx}")])
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)


# ── Stream Remover ────────────────────────────────────────────────────────────

async def show_stream_remover_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    streams = _get_streams(video_path)
    if not streams:
        await query.edit_message_text("❌ Streamlar aniqlanmadi.", reply_markup=main_menu_keyboard())
        return

    context.user_data["streams"] = streams
    context.user_data["state"] = "stream_remover"

    await query.edit_message_text(
        "🗑 *Stream Remover*\n\nO'chirmoqchi bo'lgan streamni tanlang:",
        reply_markup=_stream_inline_keyboard(streams, "remove"),
        parse_mode="Markdown",
    )


async def handle_remove_stream(update: Update, context: ContextTypes.DEFAULT_TYPE, stream_idx: int):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    streams = context.user_data.get("streams", [])
    total = len(streams)
    if total <= 1:
        await query.edit_message_text(
            "❌ Faqat 1 ta stream bor, o'chirib bo'lmaydi.",
            reply_markup=main_menu_keyboard(),
        )
        return

    output_path = make_temp_path("mkv")  # MKV barcha stream turlari uchun universal
    # -map 0  →  hammasini ol, keyin tanlangan streamni olib tashla
    map_args = []
    for s in streams:
        if s.get("index") != stream_idx:
            map_args += ["-map", f"0:{s['index']}"]

    cmd = ["ffmpeg", "-y", "-i", video_path] + map_args + ["-c", "copy", output_path]

    status_msg = await query.message.reply_text("⏳ Stream o'chirilmoqda...")
    await query.edit_message_text(f"⏳ #{stream_idx} stream o'chirilmoqda...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-1500:])

        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_stream{stream_idx}_removed.mkv"

        await status_msg.edit_text(f"✅ *#{stream_idx} stream o'chirildi!*\n\n📤 Yuborilmoqda...", parse_mode="Markdown")
        await send_file(query.message, output_path, out_name, f"✅ #{stream_idx} stream o'chirildi!")
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    except Exception as e:
        await status_msg.edit_text(f"❌ Xato:\n`{e}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


# ── Stream Extractor ──────────────────────────────────────────────────────────

async def show_stream_extractor_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    streams = _get_streams(video_path)
    if not streams:
        await query.edit_message_text("❌ Streamlar aniqlanmadi.", reply_markup=main_menu_keyboard())
        return

    context.user_data["streams"] = streams
    context.user_data["state"] = "stream_extractor"

    await query.edit_message_text(
        "📦 *Stream Extractor*\n\nAjratib olmoqchi bo'lgan streamni tanlang:",
        reply_markup=_stream_inline_keyboard(streams, "extract"),
        parse_mode="Markdown",
    )


async def handle_extract_stream(update: Update, context: ContextTypes.DEFAULT_TYPE, stream_idx: int):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    streams = context.user_data.get("streams", [])
    target = next((s for s in streams if s.get("index") == stream_idx), None)
    if not target:
        await query.edit_message_text("❌ Stream topilmadi.")
        return

    stype = target.get("codec_type", "").lower()
    codec = target.get("codec_name", "")

    # Chiqish formati stream turiga qarab
    ext_map = {
        "video": "mkv",
        "audio": _audio_ext(codec),
        "subtitle": _subtitle_ext(codec),
        "data": "bin",
    }
    ext = ext_map.get(stype, "mkv")
    output_path = make_temp_path(ext)

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-map", f"0:{stream_idx}",
        "-c", "copy",
        output_path,
    ]

    status_msg = await query.message.reply_text("⏳ Stream ajratilmoqda...")
    await query.edit_message_text(f"⏳ #{stream_idx} stream ajratilmoqda...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-1500:])

        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_stream{stream_idx}.{ext}"

        await status_msg.edit_text(
            f"✅ *#{stream_idx} stream ajratildi!*\n"
            f"📎 Tur: `{stype.upper()}` | Codec: `{codec}`\n\n"
            f"📤 Yuborilmoqda...",
            parse_mode="Markdown",
        )
        await send_file(query.message, output_path, out_name, f"✅ #{stream_idx} {stype} stream!")
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    except Exception as e:
        await status_msg.edit_text(f"❌ Xato:\n`{e}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


def _audio_ext(codec: str) -> str:
    return {"aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "ogg",
            "flac": "flac", "pcm_s16le": "wav"}.get(codec, "mka")


def _subtitle_ext(codec: str) -> str:
    return {"subrip": "srt", "ass": "ass", "webvtt": "vtt",
            "hdmv_pgs_subtitle": "sup"}.get(codec, "srt")
