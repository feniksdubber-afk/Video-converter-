"""
hls_handler.py — HLS Streaming va R2 Yuklash Moduli

Foydalanuvchi videoni HLS formatiga o'tkazib R2 ga yuklaydi.
Natijada master.m3u8 URL olinadi — AfsonaTv yoki boshqa playerga joylash mumkin.

Oqim:
  1. Foydalanuvchi "📡 HLS → R2" tugmasini bosadi
  2. Bot sifat variantlarini ko'rsatadi (360p, 360p+720p, 360p+720p+1080p)
  3. FFmpeg yordamida segmentlarga bo'ladi
  4. Barcha fayllar R2 ga yuklanadi
  5. master.m3u8 URL qaytariladi
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


def _progress_bar(pct: int, length: int = 14) -> str:
    filled = int(length * pct / 100)
    return "█" * filled + "░" * (length - filled)


def hls_quality_keyboard(available: list[str] | None = None) -> InlineKeyboardMarkup:
    if available is None:
        available = ["360", "720", "1080"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(HLS_PRESET_LABELS["360"],  callback_data="hls_q_360")],
        [InlineKeyboardButton(HLS_PRESET_LABELS["720"],  callback_data="hls_q_720")],
        [InlineKeyboardButton(HLS_PRESET_LABELS["1080"], callback_data="hls_q_1080")],
        [InlineKeyboardButton("❌ Bekor", callback_data="back")],
    ])


async def show_hls_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asosiy HLS menyu — sifat tanlash."""
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

    # Video original sifatini aniqlash
    from utils.ffmpeg_utils import get_video_info
    info = get_video_info(video_path)
    try:
        src_height = int(info.get("height", 1080))
    except (ValueError, TypeError):
        src_height = 1080

    # Faqat original sifatdan past yoki teng presetlarni ko'rsatish
    available_presets = []
    if src_height >= 360:
        available_presets.append("360")
    if src_height >= 720:
        available_presets.append("720")
    if src_height >= 1080:
        available_presets.append("1080")
    if not available_presets:
        available_presets = ["360"]

    context.user_data["available_presets"] = available_presets

    src_label = f"{src_height}p" if src_height else "noma'lum"

    await query.edit_message_text(
        f"📡 *HLS Streaming → R2*\n\n"
        f"📁 Fayl: `{video_name}`\n"
        f"📦 Hajmi: {file_size_mb:.1f} MB\n"
        f"🎬 Original sifat: *{src_label}*\n\n"
        f"*Sifatni tanlang:*\n"
        f"_(Ko'proq sifat = ko'proq vaqt va disk joy)_",
        parse_mode="Markdown",
        reply_markup=hls_quality_keyboard(available_presets),
    )


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

    hls_dir = os.path.join(TEMP_DIR, f"hls_{base_name}_{preset_key}")

    status_msg = await query.message.reply_text(
        f"⚙️ *HLS konvertatsiya boshlandi...*\n"
        f"📊 Sifat: {quality_labels}\n\n"
        f"`[░░░░░░░░░░░░░░]` Hisoblanyapti...",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ HLS konvertatsiya boshlandi → {quality_labels}...")

    # FFmpeg konvertatsiya
    from utils.ffmpeg_utils import convert_to_hls_async
    ok, master_path, err = await convert_to_hls_async(
        video_path, hls_dir, qualities, status_msg
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

    # Fayllarni R2 ga yuklash
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

    # master.m3u8 URL
    master_url = get_public_url(f"{r2_prefix}/master.m3u8")

    fail_text = f"\n⚠️ {failed} fayl xato" if failed else ""
    await status_msg.edit_text(
        f"✅ *HLS Streaming tayyor!*\n\n"
        f"📊 Sifat: {quality_labels}\n"
        f"📁 Yuklandi: {uploaded}/{total_files} fayl{fail_text}\n\n"
        f"🔗 *Master URL (m3u8):*\n"
        f"`{master_url}`\n\n"
        f"📋 Bu URL ni AfsonaTv admin panelida kontentga joylashtiring\n\n"
        f"*Qanday ko'rish:*\n"
        f"• iOS Safari — to'g'ridan-to'g'ri ochiladi\n"
        f"• VLC → Tarmoq oqimi → URL joylashtiring\n"
        f"• hls-js.netlify.app/demo → URL joylashtiring",
        parse_mode="Markdown",
    )

    # Temp fayllarni tozalash
    shutil.rmtree(hls_dir, ignore_errors=True)

    await query.message.reply_text(
        "Boshqa amal?", reply_markup=main_menu_keyboard()
    )
