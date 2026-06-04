import os
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import compress_quality_keyboard, main_menu_keyboard
from utils.ffmpeg_utils import compress_video
from utils.sender import send_file


async def show_compress_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "compress"
    await query.edit_message_text(
        "📐 *Siqish / Optimallashtirish*\n\n"
        "🟢 *Yuqori* — katta fayl, yaxshi sifat\n"
        "🟡 *O'rtacha* — muvozanatli tanlov\n"
        "🔴 *Past* — kichik fayl, past sifat",
        reply_markup=compress_quality_keyboard(), parse_mode="Markdown",
    )


async def handle_compress_quality(update: Update, context: ContextTypes.DEFAULT_TYPE, quality: str):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    labels = {"high": "Yuqori sifat", "medium": "O'rtacha sifat", "low": "Past sifat"}
    label = labels.get(quality, quality)

    await query.edit_message_text(
        f"⏳ *Video siqilmoqda ({label})...*\n\nKuting...", parse_mode="Markdown"
    )

    ok, output_path, err = compress_video(video_path, quality)

    if ok and os.path.exists(output_path):
        orig_size = os.path.getsize(video_path)
        new_size = os.path.getsize(output_path)
        saved = orig_size - new_size
        percent = (saved / orig_size * 100) if orig_size > 0 else 0

        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_compressed.mp4"

        await query.message.reply_text(
            f"✅ Siqildi!\n"
            f"📦 Asl: {_fmt(orig_size)} → Yangi: {_fmt(new_size)}\n"
            f"💾 Tejaldi: {_fmt(saved)} ({percent:.1f}%)\n\n"
            f"📤 Yuborilmoqda..."
        )
        await send_file(query.message, output_path, out_name, f"✅ Siqildi! ({percent:.1f}% kam)")
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await query.message.reply_text(
            f"❌ Xato:\n`{err}`", reply_markup=main_menu_keyboard(), parse_mode="Markdown"
        )


def _fmt(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"
