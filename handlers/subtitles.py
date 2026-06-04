import os
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from utils.sender import send_file
from utils.ffmpeg_utils import softsub_video
from config import TEMP_DIR

SUPPORTED_FORMATS = {".srt", ".ass", ".ssa", ".vtt"}


async def show_subtitle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "subtitle_wait"
    await query.edit_message_text(
        "📝 *Soft Sub — Subtitr Birlashtirish*\n\n"
        "Subtitr faylini yuboring:\n"
        "• `.srt` — oddiy subtitr\n"
        "• `.ass` / `.ssa` — stilizatsiyalangan\n"
        "• `.vtt` — WebVTT\n\n"
        "✅ Video qayta kodlanmaydi — tez ishlaydi\n"
        "📦 Natija: *MKV* format (multi-stream uchun eng mos)",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_subtitle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text(
            "❌ Fayl topilmadi. Iltimos subtitr faylini yuboring.",
            reply_markup=cancel_keyboard(),
        )
        return

    file_name = doc.file_name or ""
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        await update.message.reply_text(
            "❌ Noto'g'ri format. `.srt`, `.ass`, `.ssa`, `.vtt` qabul qilinadi.",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await update.message.reply_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        context.user_data["state"] = None
        return

    status = await update.message.reply_text("⏳ Subtitr yuklanmoqda...")

    sub_path = os.path.join(TEMP_DIR, f"softsub_{doc.file_unique_id}{ext}")
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(sub_path)

    context.user_data["state"] = None
    await status.edit_text("⏳ *Subtitr stream sifatida birlashtirilmoqda...*", parse_mode="Markdown")

    ok, output_path, err = softsub_video(video_path, sub_path)

    if os.path.exists(sub_path):
        os.remove(sub_path)

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_softsub.mkv"
        await status.edit_text("✅ Tayyor! Yuborilmoqda...")
        await send_file(update.message, output_path, out_name,
                        "✅ Soft sub muvaffaqiyatli birlashtirildi!", context=context)
        os.remove(output_path)
        await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status.edit_text(
            f"❌ Subtitr birlashtirishda xato:\n`{err}`\n\n"
            "Fayl to'g'ri formatda ekanligini tekshiring.",
            parse_mode="Markdown",
        )
        await update.message.reply_text("Qaytadan?", reply_markup=main_menu_keyboard())
