import logging
import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from config import BOT_TOKEN
from handlers.start import start_handler, help_handler
from handlers.video_handler import video_received
from handlers.converter import (
    show_convert_menu,
    show_resolution_menu,
    handle_format_choice,
    handle_resolution_choice,
)
from handlers.compressor import show_compress_menu, handle_compress_quality
from handlers.trimmer import show_trim_menu, handle_trim_text
from handlers.audio import show_remove_audio_menu, show_video_to_audio_menu, handle_audio_format
from handlers.screenshots import (
    show_screenshots_menu,
    handle_screenshots_count,
    show_manual_shot_menu,
    handle_manual_shot_text,
)
from handlers.subtitles import show_subtitle_menu, handle_subtitle_file
from utils.keyboards import main_menu_keyboard

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "cancel":
        context.user_data["state"] = None
        await query.answer()
        await query.edit_message_text(
            "❌ Bekor qilindi.\n\nYangi video yuboring yoki menyu:",
            reply_markup=main_menu_keyboard() if context.user_data.get("video_path") else None,
        )
        return

    if data == "back":
        context.user_data["state"] = None
        await query.answer()
        await query.edit_message_text(
            "Quyidagi amallardan birini tanlang:",
            reply_markup=main_menu_keyboard(),
        )
        return

    if data == "convert":
        await show_convert_menu(update, context)
    elif data == "resolution":
        await show_resolution_menu(update, context)
    elif data == "compress":
        await show_compress_menu(update, context)
    elif data == "trim":
        await show_trim_menu(update, context)
    elif data == "remove_audio":
        await show_remove_audio_menu(update, context)
    elif data == "video_to_audio":
        await show_video_to_audio_menu(update, context)
    elif data == "screenshots":
        await show_screenshots_menu(update, context)
    elif data == "manual_shot":
        await show_manual_shot_menu(update, context)
    elif data == "subtitle":
        await show_subtitle_menu(update, context)

    elif data.startswith("fmt_"):
        fmt = data[4:]
        await handle_format_choice(update, context, fmt)

    elif data.startswith("res_"):
        height = int(data[4:])
        await handle_resolution_choice(update, context, height)

    elif data.startswith("cq_"):
        quality = data[3:]
        await handle_compress_quality(update, context, quality)

    elif data.startswith("aud_"):
        fmt = data[4:]
        await handle_audio_format(update, context, fmt)

    elif data.startswith("ss_"):
        count = int(data[3:])
        await handle_screenshots_count(update, context, count)

    else:
        await query.answer("Noma'lum buyruq", show_alert=True)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state in ("trim_start", "trim_end"):
        await handle_trim_text(update, context)
    elif state == "manual_shot":
        await handle_manual_shot_text(update, context)
    else:
        await update.message.reply_text(
            "📤 Video yuboring yoki /start bosing."
        )


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == "subtitle_wait":
        await handle_subtitle_file(update, context)
    else:
        await video_received(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Xato yuz berdi:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Kutilmagan xato yuz berdi. Iltimos qaytadan urinib ko'ring.\n"
            "/start buyrug'ini bosing."
        )


def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable topilmadi!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))

    app.add_handler(MessageHandler(filters.VIDEO, video_received))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_error_handler(error_handler)

    logger.info("Bot ishga tushmoqda...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
