"""
hls_handler.py — HLS Streaming va R2 Yuklash Moduli

Oqim:
  1. Foydalanuvchi "📡 HLS → R2" tugmasini bosadi
  2. Bot video info + audio track larni aniqlaydi
  3. Agar 2+ audio track → admin nom beradi va qaysilari chiqishini belgilaydi
  4. Sifat tanlash (original sifatga qarab filtrlangan)
  5. FFmpeg HLS konvertatsiya
  6. R2 ga yuklash → master.m3u8 URL
"""

import asyncio
import os
import shutil

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import TEMP_DIR
from utils.r2_manager import is_configured, upload_file, get_public_url
from utils.keyboards import main_menu_keyboard


# ── Sifat presetlari ──────────────────────────────────────────────────
HLS_PRESETS = {
    "360":  [{"height": 360,  "bitrate": "800k",  "audio_bitrate": "96k"}],
    "720":  [{"height": 360,  "bitrate": "800k",  "audio_bitrate": "96k"},
             {"height": 720,  "bitrate": "2800k", "audio_bitrate": "128k"}],
    "1080": [{"height": 360,  "bitrate": "800k",  "audio_bitrate": "96k"},
             {"height": 720,  "bitrate": "2800k", "audio_bitrate": "128k"},
             {"height": 1080, "bitrate": "5000k", "audio_bitrate": "192k"}],
}

HLS_PRESET_LABELS = {
    "360":  "🟢 360p — Yengil (tez yuklanadi)",
    "720":  "🟡 360p + 720p — O'rtacha (tavsiya etiladi)",
    "1080": "🔴 360p + 720p + 1080p — To'liq sifat",
}

# Audio til nomlari (language kodi → o'zbek nomi)
LANG_NAMES = {
    "uzb": "O'zbekcha", "rus": "Ruscha", "eng": "Inglizcha",
    "tur": "Turkcha", "ara": "Arabcha", "fra": "Fransuzcha",
    "deu": "Nemischa", "jpn": "Yaponcha", "kor": "Koreycha",
    "zho": "Xitoycha", "und": "Noma'lum",
}


def _progress_bar(pct: int, length: int = 14) -> str:
    filled = int(length * pct / 100)
    return "█" * filled + "░" * (length - filled)


def hls_quality_keyboard(available: list[str] | None = None) -> InlineKeyboardMarkup:
    if available is None:
        available = ["360", "720", "1080"]
    buttons = [
        [InlineKeyboardButton(HLS_PRESET_LABELS[p], callback_data=f"hls_q_{p}")]
        for p in ["360", "720", "1080"] if p in available
    ]
    buttons.append([InlineKeyboardButton("❌ Bekor", callback_data="back")])
    return InlineKeyboardMarkup(buttons)


def _track_label(track: dict) -> str:
    """Audio track uchun inson o'qiy oladigan nom."""
    lang = track.get("language", "")
    title = track.get("title", "")
    codec = track.get("codec", "")
    lang_name = LANG_NAMES.get(lang, lang.upper() if lang else "")
    if title:
        return f"{title} ({lang_name or codec})"
    if lang_name:
        return lang_name
    return f"Track {track['index'] + 1} ({codec})"


async def show_hls_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asosiy HLS menyu — audio track lar aniqlanadi, keyin sifat tanlash."""
    query = update.callback_query
    await query.answer()

    if not is_configured():
        await query.edit_message_text(
            "❌ *R2 sozlanmagan!*\n\n"
            "Botni ishlatish uchun R2 environment o'zgaruvchilarini sozlang:\n"
            "`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, "
            "`R2_BUCKET_NAME`, `R2_PUBLIC_URL`",
            parse_mode="Markdown",
        )
        return

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text(
            "❌ Video topilmadi. Iltimos avval video yuboring.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Orqaga", callback_data="back"),
            ]]),
        )
        return

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    video_name = context.user_data.get("video_name", "video")

    from utils.ffmpeg_utils import get_video_info, get_audio_tracks
    info = get_video_info(video_path)
    try:
        src_height = int(info.get("height", 1080))
    except (ValueError, TypeError):
        src_height = 1080

    available_presets = []
    if src_height >= 360:  available_presets.append("360")
    if src_height >= 720:  available_presets.append("720")
    if src_height >= 1080: available_presets.append("1080")
    if not available_presets:
        available_presets = ["360"]

    context.user_data["available_presets"] = available_presets
    context.user_data["src_height"] = src_height  # keyingi handlerlarda ishlatiladi

    # Audio track larni aniqlash
    tracks = get_audio_tracks(video_path)
    context.user_data["raw_audio_tracks"] = tracks
    context.user_data["selected_audio_tracks"] = []  # admin tanlaydi

    src_label = f"{src_height}p"

    if len(tracks) > 1:
        # Audio tanlash bosqichiga o'tamiz
        await _show_audio_select(query, context, tracks, video_name, file_size_mb, src_label, available_presets)
    else:
        # Bitta audio — to'g'ridan sifat tanlashga
        if tracks:
            context.user_data["selected_audio_tracks"] = [
                {"index": tracks[0]["index"], "language": tracks[0]["language"],
                 "name": _track_label(tracks[0]), "default": True}
            ]
        await _show_quality_select(query, context, video_name, file_size_mb, src_label, available_presets)


async def _show_audio_select(query, context, tracks, video_name, file_size_mb, src_label, available_presets):
    """Admin audio track larni tanlaydi."""
    selected = context.user_data.get("selected_audio_tracks", [])
    selected_indices = {t["index"] for t in selected}

    buttons = []
    for track in tracks:
        idx = track["index"]
        label = _track_label(track)
        check = "✅" if idx in selected_indices else "⬜"
        buttons.append([InlineKeyboardButton(
            f"{check} {label}",
            callback_data=f"hls_audio_toggle_{idx}"
        )])

    # Davom etish tugmasi (kamida 1 tanlangan bo'lsa)
    if selected:
        buttons.append([InlineKeyboardButton("➡️ Davom etish →", callback_data="hls_audio_done")])
    buttons.append([InlineKeyboardButton("⏭ Barchasini qo'shish", callback_data="hls_audio_all")])
    buttons.append([InlineKeyboardButton("❌ Bekor", callback_data="back")])

    track_list = "\n".join(f"  {i+1}. {_track_label(t)}" for i, t in enumerate(tracks))

    await query.edit_message_text(
        f"📡 *HLS Streaming → R2*\n\n"
        f"📁 `{video_name}` | {file_size_mb:.1f} MB | *{src_label}*\n\n"
        f"🎧 *{len(tracks)} ta audio track topildi:*\n{track_list}\n\n"
        f"*Qaysilarini qo'shish kerak?*\n"
        f"_(Tanlangan audio lar player da til o'zgartirish sifatida ko'rinadi)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _show_quality_select(query, context, video_name, file_size_mb, src_label, available_presets):
    """Sifat tanlash."""
    selected = context.user_data.get("selected_audio_tracks", [])
    audio_info = ""
    if len(selected) > 1:
        names = ", ".join(t["name"] for t in selected)
        audio_info = f"🎧 Audio: {names}\n"
    elif len(selected) == 1:
        audio_info = f"🎧 Audio: {selected[0]['name']}\n"

    await query.edit_message_text(
        f"📡 *HLS Streaming → R2*\n\n"
        f"📁 Fayl: `{video_name}`\n"
        f"📦 Hajmi: {file_size_mb:.1f} MB\n"
        f"🎬 Original sifat: *{src_label}*\n"
        f"{audio_info}\n"
        f"*Video sifatini tanlang:*\n"
        f"_(Ko'proq sifat = ko'proq vaqt va disk joy)_",
        parse_mode="Markdown",
        reply_markup=hls_quality_keyboard(available_presets),
    )


async def handle_hls_audio_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, track_idx: int):
    """Audio track tanlash/olib tashlash."""
    query = update.callback_query
    await query.answer()

    tracks = context.user_data.get("raw_audio_tracks", [])
    selected = context.user_data.get("selected_audio_tracks", [])
    selected_indices = {t["index"] for t in selected}

    track = next((t for t in tracks if t["index"] == track_idx), None)
    if not track:
        return

    if track_idx in selected_indices:
        selected = [t for t in selected if t["index"] != track_idx]
    else:
        is_default = len(selected) == 0
        selected.append({
            "index": track["index"],
            "language": track.get("language", "und"),
            "name": _track_label(track),
            "default": is_default,
        })

    context.user_data["selected_audio_tracks"] = selected

    video_name = context.user_data.get("video_name", "video")
    video_path = context.user_data.get("video_path", "")
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024) if os.path.exists(video_path) else 0
    available_presets = context.user_data.get("available_presets", ["360", "720", "1080"])
    src_height = context.user_data.get("src_height", 720)

    await _show_audio_select(query, context, tracks, video_name, file_size_mb, f"{src_height}p", available_presets)


async def handle_hls_audio_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Barcha audio track larni qo'shish."""
    query = update.callback_query
    await query.answer()

    tracks = context.user_data.get("raw_audio_tracks", [])
    selected = []
    for i, track in enumerate(tracks):
        selected.append({
            "index": track["index"],
            "language": track.get("language", "und"),
            "name": _track_label(track),
            "default": i == 0,
        })
    context.user_data["selected_audio_tracks"] = selected

    video_name = context.user_data.get("video_name", "video")
    video_path = context.user_data.get("video_path", "")
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024) if os.path.exists(video_path) else 0
    available_presets = context.user_data.get("available_presets", ["360", "720", "1080"])
    src_height = context.user_data.get("src_height", 720)

    await _show_quality_select(query, context, video_name, file_size_mb, f"{src_height}p", available_presets)


async def handle_hls_audio_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Audio tanlash tugadi — sifat tanlashga o'tish."""
    query = update.callback_query
    await query.answer()

    video_name = context.user_data.get("video_name", "video")
    video_path = context.user_data.get("video_path", "")
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024) if os.path.exists(video_path) else 0
    available_presets = context.user_data.get("available_presets", ["360", "720", "1080"])
    src_height = context.user_data.get("src_height", 720)

    await _show_quality_select(query, context, video_name, file_size_mb, f"{src_height}p", available_presets)


async def handle_hls_quality(update: Update, context: ContextTypes.DEFAULT_TYPE, preset_key: str):
    """Tanlangan sifat bilan HLS konvertatsiyani boshlash."""
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    video_name = context.user_data.get("video_name", "video")
    base_name = os.path.splitext(video_name)[0]
    qualities = HLS_PRESETS[preset_key]
    quality_labels = " + ".join(f"{q['height']}p" for q in qualities)
    audio_tracks = context.user_data.get("selected_audio_tracks") or None
    multi_audio = audio_tracks and len(audio_tracks) > 1

    hls_dir = os.path.join(TEMP_DIR, f"hls_{base_name}_{preset_key}")

    audio_label = ""
    if multi_audio:
        names = " + ".join(t["name"] for t in audio_tracks)
        audio_label = f"\n🎧 Audio: {names}"

    status_msg = await query.message.reply_text(
        f"⚙️ *HLS konvertatsiya boshlandi...*\n"
        f"📊 Sifat: {quality_labels}{audio_label}\n\n"
        f"`[░░░░░░░░░░░░░░]` Hisoblanyapti...",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ HLS konvertatsiya boshlandi → {quality_labels}...")

    from utils.ffmpeg_utils import convert_to_hls_async
    ok, master_path, err = await convert_to_hls_async(
        video_path, hls_dir, qualities, status_msg,
        audio_tracks=audio_tracks if multi_audio else None,
    )

    if not ok:
        await status_msg.edit_text(
            f"❌ *Konvertatsiyada xato:*\n`{err[:500]}`",
            parse_mode="Markdown",
        )
        shutil.rmtree(hls_dir, ignore_errors=True)
        return

    # Barcha fayllarni hisoblash
    all_files = []
    for root, dirs, files in os.walk(hls_dir):
        for f in files:
            all_files.append(os.path.join(root, f))

    total_files = len(all_files)
    r2_prefix = f"hls/{base_name}"

    await status_msg.edit_text(
        f"✅ Konvertatsiya tugadi!\n\n"
        f"☁️ *R2 ga yuklanmoqda...*\n"
        f"`{total_files}` fayl topildi",
        parse_mode="Markdown",
    )

    uploaded = 0
    failed = 0
    for local_file in all_files:
        rel = os.path.relpath(local_file, hls_dir)
        object_key = f"{r2_prefix}/{rel}"
        try:
            await upload_file(local_file, object_key)
            uploaded += 1
        except Exception:
            failed += 1

        done = uploaded + failed
        if done % 10 == 0 or done == total_files:
            pct = int(done / total_files * 100)
            bar = _progress_bar(pct)
            try:
                await status_msg.edit_text(
                    f"☁️ *R2 ga yuklanmoqda...*\n\n"
                    f"`[{bar}]` `{pct}%`\n"
                    f"`{done}/{total_files}` fayl yuklandi",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    master_url = get_public_url(f"{r2_prefix}/master.m3u8")

    fail_text = f"\n⚠️ {failed} fayl xato" if failed else ""
    await status_msg.edit_text(
        f"✅ *HLS Streaming tayyor!*\n\n"
        f"📊 Sifat: {quality_labels}\n"
        f"📁 Yuklandi: {uploaded}/{total_files} fayl{fail_text}\n\n"
        f"🔗 *Master URL (m3u8):*\n"
        f"`{master_url}`\n\n"
        f"📋 Bu URL ni AfsonaTv admin panelida kontentga joylashtiring",
        parse_mode="Markdown",
    )

    shutil.rmtree(hls_dir, ignore_errors=True)
    await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
