import os
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import audio_format_keyboard, main_menu_keyboard
from utils.ffmpeg_utils import remove_audio, video_to_audio


async def show_remove_audio_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    await query.edit_message_text("⏳ *Ovoz o'chirilmoqda...*\n\nKuting...", parse_mode="Markdown")

    ok, output_path, err = remove_audio(video_path)

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_no_audio.mp4"

        await query.message.reply_text("✅ Tayyor! Yuborilmoqda...")
        with open(output_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=out_name,
                caption="✅ Ovoz muvaffaqiyatli o'chirildi!",
            )
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await query.message.reply_text(
            f"❌ Xato:\n`{err}`",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )


async def show_video_to_audio_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "video_to_audio"
    await query.edit_message_text(
        "🎵 *Videoni Audioga Aylantirish*\n\n"
        "Audio formatini tanlang:",
        reply_markup=audio_format_keyboard(),
        parse_mode="Markdown",
    )


async def handle_audio_format(update: Update, context: ContextTypes.DEFAULT_TYPE, fmt: str):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    await query.edit_message_text(
        f"⏳ *{fmt.upper()} formatiga o'tkazilmoqda...*\n\nKuting...",
        parse_mode="Markdown",
    )

    ok, output_path, err = video_to_audio(video_path, fmt)

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}.{fmt}"

        await query.message.reply_text("✅ Tayyor! Yuborilmoqda...")
        with open(output_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=out_name,
                caption=f"✅ Audio muvaffaqiyatli ajratildi! ({fmt.upper()})",
            )
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await query.message.reply_text(
            f"❌ Xato:\n`{err}`",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
