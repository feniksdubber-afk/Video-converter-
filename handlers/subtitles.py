import os
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from utils.ffmpeg_utils import merge_subtitle
from config import TEMP_DIR


async def show_subtitle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "subtitle_wait"
    await query.edit_message_text(
        "📝 *Subtitr Birlashtirish*\n\n"
        "Iltimos `.srt` formatidagi subtitr faylini yuboring.\n\n"
        "⚠️ Faqat SRT format qo'llab-quvvatlanadi",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_subtitle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text(
            "❌ Fayl topilmadi. Iltimos SRT faylni yuboring.",
            reply_markup=cancel_keyboard(),
        )
        return

    file_name = doc.file_name or ""
    if not file_name.lower().endswith(".srt"):
        await update.message.reply_text(
            "❌ Noto'g'ri format. Faqat `.srt` fayl qabul qilinadi.",
            reply_markup=cancel_keyboard(),
        )
        return

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await update.message.reply_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        context.user_data["state"] = None
        return

    status = await update.message.reply_text("⏳ Subtitr yuklanmoqda...")

    srt_path = os.path.join(TEMP_DIR, f"{doc.file_unique_id}.srt")
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(srt_path)

    context.user_data["state"] = None
    await status.edit_text(
        "⏳ *Subtitr video bilan birlashtirilmoqda...*\n\nKuting...",
        parse_mode="Markdown",
    )

    ok, output_path, err = merge_subtitle(video_path, srt_path)

    if os.path.exists(srt_path):
        os.remove(srt_path)

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_subtitled.mp4"

        await status.edit_text("✅ Tayyor! Yuborilmoqda...")
        with open(output_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=out_name,
                caption="✅ Subtitr muvaffaqiyatli birlashtirildi!",
            )
        os.remove(output_path)
        await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status.edit_text(
            f"❌ Subtitr birlashtirishda xato:\n`{err}`\n\n"
            "SRT fayl to'g'ri formatda ekanligini tekshiring.",
            parse_mode="Markdown",
        )
        await update.message.reply_text("Qaytadan?", reply_markup=main_menu_keyboard())
