import os
import subprocess
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import make_temp_path, get_video_duration


async def show_thumbnail_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    # Avval embedded thumbnail bor-yo'qligini tekshir
    has_thumb = _has_embedded_thumbnail(video_path)
    duration = get_video_duration(video_path)

    text = "🖼 *Thumbnail / Cover Extractor*\n\n"
    if has_thumb:
        text += "✅ Faylda *embedded thumbnail* mavjud.\n\n"
    else:
        text += "ℹ️ Faylda embedded thumbnail yo'q.\n\n"
    text += "Qaysi usulni tanlaysiz?"

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = []
    if has_thumb:
        keyboard.append([InlineKeyboardButton("📎 Embedded thumbnail olish", callback_data="thumb_embedded")])

    if duration > 0:
        keyboard.append([InlineKeyboardButton("🕐 Boshidan (0:00)", callback_data="thumb_time_0")])
        mid = int(duration / 2)
        keyboard.append([InlineKeyboardButton(f"⏱ O'rtasidan ({_fmt_time(mid)})", callback_data=f"thumb_time_{mid}")])
        keyboard.append([InlineKeyboardButton("🕐 Vaqtni o'zim kiritaman", callback_data="thumb_manual")])

    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])

    context.user_data["state"] = "thumbnail"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def handle_thumbnail_embedded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    output_path = make_temp_path("jpg")

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-map", "0:v:1",         # odatda 2-video stream = thumbnail
        "-frames:v", "1",
        "-c:v", "mjpeg",
        output_path,
    ]
    # Agar 0:v:1 bo'lmasa 0:v:0 dan olish
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not os.path.exists(output_path):
        cmd2 = [
            "ffmpeg", "-y", "-i", video_path,
            "-map", "0:v:0",
            "-frames:v", "1",
            "-q:v", "2",
            output_path,
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=60)
        if result2.returncode != 0:
            await query.edit_message_text("❌ Thumbnail olib bo'lmadi.", reply_markup=main_menu_keyboard())
            return

    await _send_thumbnail(query, context, output_path, "embedded thumbnail")


async def handle_thumbnail_time(update: Update, context: ContextTypes.DEFAULT_TYPE, seconds: int):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    output_path = make_temp_path("jpg")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(seconds),
        "-i", video_path,
        "-frames:v", "1", "-q:v", "2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not os.path.exists(output_path):
        await query.edit_message_text(
            f"❌ {_fmt_time(seconds)} dan thumbnail olib bo'lmadi.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await _send_thumbnail(query, context, output_path, f"{_fmt_time(seconds)} dagi kadr")


async def handle_thumbnail_manual_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "thumbnail_manual"
    await query.edit_message_text(
        "⏱ *Vaqtni kiriting:*\n\nFormat: `MM:SS` yoki `HH:MM:SS`\nMasalan: `01:30` yoki `00:01:30`",
        parse_mode="Markdown",
    )


async def handle_thumbnail_manual_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await update.message.reply_text("❌ Video topilmadi.")
        return

    output_path = make_temp_path("jpg")
    cmd = [
        "ffmpeg", "-y",
        "-ss", text,
        "-i", video_path,
        "-frames:v", "1", "-q:v", "2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not os.path.exists(output_path):
        await update.message.reply_text(
            f"❌ `{text}` vaqtidan thumbnail olib bo'lmadi.\nTo'g'ri format: `01:30`",
            parse_mode="Markdown",
        )
        return

    with open(output_path, "rb") as f:
        await update.message.reply_photo(photo=f, caption=f"🖼 `{text}` dagi kadr", parse_mode="Markdown")
    os.remove(output_path)
    context.user_data["state"] = None
    await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


# ── helpers ───────────────────────────────────────────────────────────────────

async def _send_thumbnail(query, context, output_path: str, label: str):
    try:
        with open(output_path, "rb") as f:
            await query.message.reply_photo(photo=f, caption=f"🖼 {label}", parse_mode="Markdown")
        os.remove(output_path)
        context.user_data["state"] = None
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    except Exception as e:
        await query.message.reply_text(f"❌ Xato: {e}", reply_markup=main_menu_keyboard())


def _has_embedded_thumbnail(video_path: str) -> bool:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=codec_name,codec_type",
             "-of", "json", video_path],
            capture_output=True, text=True, timeout=15,
        )
        import json
        streams = json.loads(result.stdout).get("streams", [])
        # 2 ta video stream bo'lsa — biri asosiy, biri thumbnail
        return len(streams) >= 2
    except Exception:
        return False


def _fmt_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
