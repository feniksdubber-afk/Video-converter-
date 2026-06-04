import logging
import os
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

from config import BOT_TOKEN
from handlers.start import start_handler, help_handler
from handlers.video_handler import video_received
from handlers.converter import show_convert_menu, show_resolution_menu, handle_format_choice, handle_resolution_choice
from handlers.compressor import show_compress_menu, handle_compress_quality
from handlers.trimmer import show_trim_menu, handle_trim_text
from handlers.audio import show_remove_audio_menu, show_video_to_audio_menu, handle_audio_format
from handlers.screenshots import show_screenshots_menu, handle_screenshots_count, show_manual_shot_menu, handle_manual_shot_text
from handlers.subtitles import show_subtitle_menu, handle_subtitle_file
from handlers.streams import show_stream_remover_menu, handle_remove_stream, show_stream_extractor_menu, handle_extract_stream
from handlers.thumbnail import show_thumbnail_menu, handle_thumbnail_embedded, handle_thumbnail_time, handle_thumbnail_manual_prompt, handle_thumbnail_manual_text
from handlers.settings import show_settings, handle_settings_callback, handle_settings_text, handle_settings_photo
from handlers.video_tools import (
    show_rename_menu, handle_rename_text,
    show_media_info,
    show_sample_menu, handle_sample_from, handle_sample_manual_prompt, handle_sample_manual_text,
    show_splitter_menu, handle_split_go, handle_split_set_dur, handle_split_dur_text,
    show_merger_menu, handle_merge_add_next, handle_merge_video_received, handle_merge_go, handle_merge_clear,
    show_vid_aud_merger_menu, handle_vid_aud_merge_received,
)
from utils.keyboards import main_menu_keyboard

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # ── Umumiy ──────────────────────────────────────────────
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
        await query.edit_message_text("Quyidagi amallardan birini tanlang:", reply_markup=main_menu_keyboard())
        return

    # ── Asosiy funksiyalar ───────────────────────────────────
    if data == "convert":             await show_convert_menu(update, context)
    elif data == "resolution":        await show_resolution_menu(update, context)
    elif data == "compress":          await show_compress_menu(update, context)
    elif data == "trim":              await show_trim_menu(update, context)
    elif data == "remove_audio":      await show_remove_audio_menu(update, context)
    elif data == "video_to_audio":    await show_video_to_audio_menu(update, context)
    elif data == "screenshots":       await show_screenshots_menu(update, context)
    elif data == "manual_shot":       await show_manual_shot_menu(update, context)
    elif data == "subtitle":          await show_subtitle_menu(update, context)
    elif data == "media_info":        await show_media_info(update, context)
    elif data == "rename":            await show_rename_menu(update, context)
    elif data == "generate_sample":   await show_sample_menu(update, context)
    elif data == "splitter":          await show_splitter_menu(update, context)
    elif data == "merger":            await show_merger_menu(update, context)
    elif data == "vid_aud_merge":     await show_vid_aud_merger_menu(update, context)
    elif data == "settings":          await show_settings(update, context)

    # ── Stream ──────────────────────────────────────────────
    elif data == "stream_remover":    await show_stream_remover_menu(update, context)
    elif data == "stream_extractor":  await show_stream_extractor_menu(update, context)
    elif data.startswith("remove_stream_"):
        await handle_remove_stream(update, context, int(data.split("_")[-1]))
    elif data.startswith("extract_stream_"):
        await handle_extract_stream(update, context, int(data.split("_")[-1]))

    # ── Thumbnail ────────────────────────────────────────────
    elif data == "thumbnail":         await show_thumbnail_menu(update, context)
    elif data == "thumb_embedded":    await handle_thumbnail_embedded(update, context)
    elif data.startswith("thumb_time_"):
        await handle_thumbnail_time(update, context, int(data.split("_")[-1]))
    elif data == "thumb_manual":      await handle_thumbnail_manual_prompt(update, context)

    # ── Settings ─────────────────────────────────────────────
    elif data.startswith("cfg_"):     await handle_settings_callback(update, context)

    # ── Sample ───────────────────────────────────────────────
    elif data.startswith("sample_from_"):
        await handle_sample_from(update, context, int(data.split("_")[-1]))
    elif data == "sample_manual":     await handle_sample_manual_prompt(update, context)

    # ── Splitter ─────────────────────────────────────────────
    elif data.startswith("split_go_"):
        await handle_split_go(update, context, int(data.split("_")[-1]))
    elif data == "split_set_dur":     await handle_split_set_dur(update, context)

    # ── Merger ───────────────────────────────────────────────
    elif data == "merge_add_next":    await handle_merge_add_next(update, context)
    elif data == "merge_go":          await handle_merge_go(update, context)
    elif data == "merge_clear":       await handle_merge_clear(update, context)
    elif data == "merge_cancel_add":
        context.user_data["state"] = None
        await query.answer()
        await query.edit_message_text("Quyidagi amallardan birini tanlang:", reply_markup=main_menu_keyboard())

    # ── Format / Res / Quality / Audio / Screenshots ─────────
    elif data.startswith("fmt_"):     await handle_format_choice(update, context, data[4:])
    elif data.startswith("res_"):     await handle_resolution_choice(update, context, int(data[4:]))
    elif data.startswith("cq_"):      await handle_compress_quality(update, context, data[3:])
    elif data.startswith("aud_"):     await handle_audio_format(update, context, data[4:])
    elif data.startswith("ss_"):      await handle_screenshots_count(update, context, int(data[3:]))

    else:
        await query.answer("Noma'lum buyruq", show_alert=True)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    dispatch = {
        "trim_start":         handle_trim_text,
        "trim_end":           handle_trim_text,
        "manual_shot":        handle_manual_shot_text,
        "thumbnail_manual":   handle_thumbnail_manual_text,
        "rename_file":        handle_rename_text,
        "sample_manual":      handle_sample_manual_text,
        "split_dur_input":    handle_split_dur_text,
        "settings_sample_dur": handle_settings_text,
        "settings_split_dur":  handle_settings_text,
    }
    handler = dispatch.get(state)
    if handler:
        await handler(update, context)
    else:
        await update.message.reply_text("📤 Video yuboring yoki /start bosing.")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    # Settings thumbnail
    if state == "settings_thumb":
        await handle_settings_photo(update, context)
        return

    # Video+Audio merge — audio kutilmoqda
    if await handle_vid_aud_merge_received(update, context):
        return

    # Merger — qo'shimcha video kutilmoqda
    if await handle_merge_video_received(update, context):
        return

    if state == "subtitle_wait":
        await handle_subtitle_file(update, context)
    else:
        await video_received(update, context)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") == "settings_thumb":
        await handle_settings_photo(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Xato yuz berdi:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Kutilmagan xato yuz berdi. /start bosing."
        )


def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN topilmadi!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("settings", show_settings))

    app.add_handler(MessageHandler(filters.VIDEO, video_received))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.AUDIO, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    logger.info("Bot ishga tushmoqda...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
