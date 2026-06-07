import logging
import os
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from handlers.hls_handler import show_hls_menu, handle_hls_quality

from config import BOT_TOKEN, LOCAL_BOT_API_URL
from utils.db import init_db
from utils.user_settings import ensure_loaded
from utils.post_action import handle_pa_send, handle_pa_continue, handle_pa_switch
from handlers.start import (
    start_handler, help_handler,
    show_cat_video, show_cat_audio, show_cat_subtitle,
    show_cat_stream, show_cat_tools, show_help_cb,
)
from handlers.video_handler import video_received
from handlers.converter import (
    show_convert_menu, show_resolution_menu, handle_format_choice, handle_resolution_choice,
    handle_convert_as_video, handle_convert_as_file,
)
from handlers.compressor import show_compress_menu, handle_compress_quality
from handlers.trimmer import show_trim_menu, handle_trim_text
from handlers.audio import show_remove_audio_menu, show_video_to_audio_menu, handle_audio_format
from handlers.screenshots import show_screenshots_menu, handle_screenshots_count, show_manual_shot_menu, handle_manual_shot_text
from handlers.subtitles import show_subtitle_menu, handle_subtitle_file
from handlers.hardsub import show_hardsub_menu, handle_hardsub_file, handle_hardsub_size
from handlers.sub_translate import (
    show_sub_translate_menu, handle_sub_translate_file, handle_sub_translate_lang,
)
from handlers.sub_converter import (
    show_sub_converter_menu, handle_sub_converter_file, handle_sub_converter_format,
)
from handlers.subtitle_extractor import (
    show_subtitle_extractor_menu,
    handle_subext_pick, handle_subext_format, handle_subext_all,
)
from handlers.streams import (
    show_stream_remover_menu, show_stream_extractor_menu, handle_extract_stream,
    handle_toggle_remove_stream, handle_select_all_audio_remove,
    handle_select_all_subs_remove, handle_select_all_streams_remove,
    handle_remove_confirm,
    handle_extract_all_audio, handle_extract_all_subs, handle_extract_all_streams,
)
from handlers.thumbnail import (
    show_thumbnail_menu, handle_thumbnail_embedded, handle_thumbnail_time,
    handle_thumbnail_manual_prompt, handle_thumbnail_manual_text,
)
from handlers.settings import show_settings, handle_settings_callback, handle_settings_text, handle_settings_photo
from handlers.video_tools import (
    show_rename_menu, handle_rename_text,
    show_media_info,
    show_sample_menu, handle_sample_from, handle_sample_manual_prompt, handle_sample_manual_text,
    show_splitter_menu, handle_split_go, handle_split_set_dur, handle_split_dur_text,
    show_merger_menu, handle_merge_add_next, handle_merge_video_received, handle_merge_go, handle_merge_clear,
    show_vid_aud_merger_menu, handle_vid_aud_merge_received,
)
from handlers.speed import show_speed_menu, handle_speed_choice
from handlers.rotate import show_rotate_menu, handle_rotate_choice
from handlers.gif_maker import show_gif_menu, handle_gif_quality, handle_gif_duration
from handlers.volume import show_volume_menu, handle_volume_choice
from handlers.fade import show_fade_menu, handle_fade_choice
from handlers.watermark import (
    show_watermark_menu, handle_watermark_text, handle_watermark_pos,
    handle_watermark_style, handle_watermark_size,
)
from handlers.crop import (
    show_crop_menu, handle_crop_preset, handle_crop_custom_prompt, handle_crop_custom_text,
)
from handlers.batch import (
    show_batch_menu, show_batch_new, handle_batch_step_toggle,
    handle_batch_save_ask, handle_batch_start_nosave,
    handle_batch_use_template, handle_batch_delete_template,
    handle_batch_clear_files, handle_batch_run,
)
from handlers.r2_browser import r2_command, r2_callback, r2_rename_text, _show_r2_list_cb
from utils.keyboards import main_menu_keyboard

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # Sozlamalarni keshga yuklash (birinchi marta)
    user_id = query.from_user.id
    context.user_data["_user_id"] = user_id
    await ensure_loaded(user_id, context)

    # ── Post-action (yuborish / davom etish / versiya tanlash) ──
    if data == "pa_send":              await handle_pa_send(update, context);    return
    if data == "pa_continue":          await handle_pa_continue(update, context); return
    if data.startswith("pa_switch_"):  await handle_pa_switch(update, context, int(data[10:])); return

    # ── Kategoriya menyulari ─────────────────────────────────
    if data == "cat_video":            await show_cat_video(update, context);    return
    if data == "cat_audio":            await show_cat_audio(update, context);    return
    if data == "cat_subtitle":         await show_cat_subtitle(update, context); return
    if data == "cat_stream":           await show_cat_stream(update, context);   return
    if data == "cat_tools":            await show_cat_tools(update, context);    return
    if data == "help_cb":              await show_help_cb(update, context);      return
    if data == "cat_batch":
        await query.answer()
        await show_batch_menu(update, context)
        return
    if data == "cat_r2":
        await query.answer()
        await _show_r2_list_cb(query, page=0)
        return

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
        has_video = bool(context.user_data.get("video_path"))
        if has_video:
            await query.edit_message_text(
                "Kategoriyani tanlang:",
                reply_markup=main_menu_keyboard(),
            )
        else:
            from utils.keyboards import start_keyboard
            await query.edit_message_text(
                "📤 Video yuboring yoki bo'limni tanlang:",
                reply_markup=start_keyboard(),
            )
        return

    # ── Asosiy funksiyalar ───────────────────────────────────
    if data == "convert":              await show_convert_menu(update, context)
    elif data == "resolution":         await show_resolution_menu(update, context)
    elif data == "compress":           await show_compress_menu(update, context)
    elif data == "trim":               await show_trim_menu(update, context)
    elif data == "remove_audio":       await show_remove_audio_menu(update, context)
    elif data == "video_to_audio":     await show_video_to_audio_menu(update, context)
    elif data == "screenshots":        await show_screenshots_menu(update, context)
    elif data == "manual_shot":        await show_manual_shot_menu(update, context)
    elif data == "media_info":         await show_media_info(update, context)
    elif data == "rename":             await show_rename_menu(update, context)
    elif data == "generate_sample":    await show_sample_menu(update, context)
    elif data == "splitter":           await show_splitter_menu(update, context)
    elif data == "merger":             await show_merger_menu(update, context)
    elif data == "vid_aud_merge":      await show_vid_aud_merger_menu(update, context)
    elif data == "settings":           await show_settings(update, context)

    # ── Subtitle ─────────────────────────────────────────────
    elif data == "subtitle":           await show_subtitle_menu(update, context)
    elif data == "hardsub":            await show_hardsub_menu(update, context)
    elif data.startswith("hs_size_"):  await handle_hardsub_size(update, context, data[8:])
    elif data == "subtitle_extractor": await show_subtitle_extractor_menu(update, context)
    elif data == "sub_translate":      await show_sub_translate_menu(update, context)
    elif data.startswith("subtrans_"): await handle_sub_translate_lang(update, context, data[9:])
    elif data == "sub_converter":      await show_sub_converter_menu(update, context)
    elif data.startswith("subconv_"):  await handle_sub_converter_format(update, context, data[8:])
    elif data == "subext_all":         await handle_subext_all(update, context)
    elif data.startswith("subext_pick_"):
        await handle_subext_pick(update, context, int(data.split("_")[-1]))
    elif data.startswith("subext_fmt_"):
        parts = data.split("_")
        await handle_subext_format(update, context, int(parts[-2]), parts[-1])

    # ── Stream ───────────────────────────────────────────────
    elif data == "stream_remover":     await show_stream_remover_menu(update, context)
    elif data == "stream_extractor":   await show_stream_extractor_menu(update, context)
    elif data.startswith("rmtoggle_"):
        await handle_toggle_remove_stream(update, context, int(data.split("_")[-1]))
    elif data == "rmall_audio":        await handle_select_all_audio_remove(update, context)
    elif data == "rmall_subs":         await handle_select_all_subs_remove(update, context)
    elif data == "rmall_streams":      await handle_select_all_streams_remove(update, context)
    elif data == "rm_confirm":         await handle_remove_confirm(update, context)
    elif data == "extract_all_audio":  await handle_extract_all_audio(update, context)
    elif data == "extract_all_subs":   await handle_extract_all_subs(update, context)
    elif data == "extract_all_streams": await handle_extract_all_streams(update, context)
    elif data.startswith("extract_stream_"):
        await handle_extract_stream(update, context, int(data.split("_")[-1]))

    # ── Thumbnail ────────────────────────────────────────────
    elif data == "thumbnail":          await show_thumbnail_menu(update, context)
    elif data == "thumb_embedded":     await handle_thumbnail_embedded(update, context)
    elif data.startswith("thumb_time_"):
        await handle_thumbnail_time(update, context, int(data.split("_")[-1]))
    elif data == "thumb_manual":       await handle_thumbnail_manual_prompt(update, context)

    # ── Settings ─────────────────────────────────────────────
    elif data.startswith("cfg_"):      await handle_settings_callback(update, context)

    # ── Sample ───────────────────────────────────────────────
    elif data.startswith("sample_from_"):
        await handle_sample_from(update, context, int(data.split("_")[-1]))
    elif data == "sample_manual":      await handle_sample_manual_prompt(update, context)

    # ── Splitter ─────────────────────────────────────────────
    elif data.startswith("split_go_"):
        await handle_split_go(update, context, int(data.split("_")[-1]))
    elif data == "split_set_dur":      await handle_split_set_dur(update, context)

    # ── Merger ───────────────────────────────────────────────
    elif data == "merge_add_next":     await handle_merge_add_next(update, context)
    elif data == "merge_go":           await handle_merge_go(update, context)
    elif data == "merge_clear":        await handle_merge_clear(update, context)
    elif data == "merge_cancel_add":
        context.user_data["state"] = None
        await query.answer()
        await query.edit_message_text("Quyidagi amallardan birini tanlang:", reply_markup=main_menu_keyboard())

    # ── Format / Res / Quality / Audio / Screenshots ──────────
    elif data == "convert_header":     await query.answer()
    elif data == "fmt_as_video":       await handle_convert_as_video(update, context)
    elif data == "fmt_as_file":        await handle_convert_as_file(update, context)
    elif data.startswith("fmt_"):      await handle_format_choice(update, context, data[4:])
    elif data.startswith("res_"):      await handle_resolution_choice(update, context, int(data[4:]))
    elif data.startswith("cq_"):       await handle_compress_quality(update, context, data[3:])
    elif data.startswith("aud_"):      await handle_audio_format(update, context, data[4:])
    elif data.startswith("ss_"):       await handle_screenshots_count(update, context, int(data[3:]))

    # ── Speed ────────────────────────────────────────────────
    elif data == "speed":              await show_speed_menu(update, context)
    elif data.startswith("spd_"):      await handle_speed_choice(update, context, data[4:])

    # ── Rotate / Flip ────────────────────────────────────────
    elif data == "rotate":             await show_rotate_menu(update, context)
    elif data.startswith("rot_"):      await handle_rotate_choice(update, context, data[4:])

    # ── GIF Maker ────────────────────────────────────────────
    elif data == "gif_maker":          await show_gif_menu(update, context)
    elif data.startswith("gif_q_"):    await handle_gif_quality(update, context, data[6:])
    elif data.startswith("gif_d_"):
        parts = data[6:].rsplit("_", 1)
        await handle_gif_duration(update, context, parts[0], parts[1])

    # ── Volume ───────────────────────────────────────────────
    elif data == "volume":             await show_volume_menu(update, context)
    # ── HLS Streaming ─────────────────────────────────────────────────
    elif data == "hls":
        await show_hls_menu(update, context)
    elif data.startswith("hls_q_"):
        # format: hls_q_360, hls_q_720, hls_q_1080
        await handle_hls_quality(update, context, data[6:])

    elif data.startswith("vol_"):      await handle_volume_choice(update, context, data[4:])

    # ── Fade ─────────────────────────────────────────────────
    elif data == "fade":               await show_fade_menu(update, context)
    elif data.startswith("fade_"):
        parts = data.split("_")
        fade_type = parts[1]
        dur = int(parts[2])
        await handle_fade_choice(update, context, fade_type, dur)

    # ── Watermark ────────────────────────────────────────────
    elif data == "watermark":          await show_watermark_menu(update, context)
    elif data.startswith("wm_pos_"):   await handle_watermark_pos(update, context, data[7:])
    elif data.startswith("wm_style_"):
        rest = data[9:]
        for style_key in ("white_shadow", "white_box", "yellow_bold", "red_bold", "black_box"):
            if rest.endswith("_" + style_key):
                pos_key = rest[: -(len(style_key) + 1)]
                await handle_watermark_style(update, context, pos_key, style_key)
                break
        else:
            await query.answer("Noma'lum uslub", show_alert=True)
    elif data.startswith("wm_size_"):
        rest = data[8:]
        size = int(rest.rsplit("_", 1)[1])
        pos_style = rest.rsplit("_", 1)[0]
        for style_key in ("white_shadow", "white_box", "yellow_bold", "red_bold", "black_box"):
            if pos_style.endswith("_" + style_key):
                pos_key = pos_style[: -(len(style_key) + 1)]
                await handle_watermark_size(update, context, pos_key, style_key, size)
                break
        else:
            await query.answer("Noma'lum uslub", show_alert=True)

    # ── Crop ─────────────────────────────────────────────────
    elif data == "crop":               await show_crop_menu(update, context)
    elif data == "crop_custom":        await handle_crop_custom_prompt(update, context)
    elif data.startswith("crop_"):     await handle_crop_preset(update, context, data[5:])

    # ── Batch ────────────────────────────────────────────────────────────────
    elif data == "batch":                  await show_batch_menu(update, context)
    elif data == "batch_menu":             await show_batch_menu(update, context)
    elif data == "batch_new":              await show_batch_new(update, context)
    elif data == "batch_noop":             await query.answer()
    elif data == "batch_save_ask":         await handle_batch_save_ask(update, context)
    elif data == "batch_start_nosave":     await handle_batch_start_nosave(update, context)
    elif data == "batch_clear_files":      await handle_batch_clear_files(update, context)
    elif data == "batch_run":              await handle_batch_run(update, context)
    elif data.startswith("batch_step_"):   await handle_batch_step_toggle(update, context, data[11:])
    elif data.startswith("batch_use_"):    await handle_batch_use_template(update, context, int(data[10:]))
    elif data.startswith("batch_del_"):    await handle_batch_delete_template(update, context, int(data[10:]))

    # ── R2 Fayl Menejer ──────────────────────────────────────────────────────
    elif data.startswith("r2_"):           await r2_callback(update, context)

    else:
        await query.answer("Noma'lum buyruq", show_alert=True)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await ensure_loaded(user_id, context)

    state = context.user_data.get("state")
    dispatch = {
        "trim_start":          handle_trim_text,
        "trim_end":            handle_trim_text,
        "manual_shot":         handle_manual_shot_text,
        "thumbnail_manual":    handle_thumbnail_manual_text,
        "rename_file":         handle_rename_text,
        "sample_manual":       handle_sample_manual_text,
        "split_dur_input":     handle_split_dur_text,
        "settings_sample_dur": handle_settings_text,
        "settings_split_dur":  handle_settings_text,
        "watermark_text":      handle_watermark_text,
        "crop_custom":         handle_crop_custom_text,
    }

    # R2 rename matn
    if state == "r2_rename_input":
        await r2_rename_text(update, context)
        return

    # Batch shablon nomi kiritish
    if state == "batch_save_name":
        from handlers.batch import handle_batch_save_name
        await handle_batch_save_name(update, context)
        return
    handler = dispatch.get(state)
    if handler:
        await handler(update, context)
    else:
        await update.message.reply_text("📤 Video yuboring yoki /start bosing.")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await ensure_loaded(user_id, context)

    state = context.user_data.get("state")

    if state == "settings_thumb":
        await handle_settings_photo(update, context)
        return

    if await handle_vid_aud_merge_received(update, context):
        return

    if await handle_merge_video_received(update, context):
        return

    if state == "subtitle_wait":
        await handle_subtitle_file(update, context)
        return
    elif state == "hardsub_wait":
        await handle_hardsub_file(update, context)
        return
    elif state == "sub_translate_wait":
        await handle_sub_translate_file(update, context)
        return
    elif state == "sub_converter_wait":
        await handle_sub_converter_file(update, context)
        return

    # Subtitle fayl to'g'ridan-to'g'ri yuborilgan bo'lsa
    doc = update.message.document
    if doc and doc.file_name:
        ext = os.path.splitext(doc.file_name)[1].lower()
        if ext in (".srt", ".ass", ".ssa", ".vtt"):
            await _handle_subtitle_direct(update, context)
            return

    await video_received(update, context)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await ensure_loaded(user_id, context)
    if context.user_data.get("state") == "settings_thumb":
        await handle_settings_photo(update, context)


async def _handle_subtitle_direct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """SRT/ASS/VTT fayl to'g'ridan-to'g'ri yuborilganda menyu ko'rsatadi."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    doc = update.message.document
    ext = os.path.splitext(doc.file_name or "")[1].lower()
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Tarjima qilish", callback_data="sub_translate"),
            InlineKeyboardButton("🔄 Format o'zgartirish", callback_data="sub_converter"),
        ],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")],
    ])
    await update.message.reply_text(
        f"📄 *{ext.upper()}* subtitr fayl aniqlandi!\n\n"
        "Nima qilmoqchisiz?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    from telegram.error import Conflict, NetworkError, TimedOut
    err = context.error

    if isinstance(err, Conflict):
        logger.critical(
            "❌ CONFLICT: Boshqa bot instance ishlamoqda! "
            "Faqat bitta instance bo'lishi kerak. Bot to'xtatilmoqda..."
        )
        # Boshqa instance bilan to'qnashuv — qayta urinishning ma'nosi yo'q
        import os, signal
        os.kill(os.getpid(), signal.SIGTERM)
        return

    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning("Tarmoq xatosi (vaqtinchalik): %s", err)
        return

    logger.error("Xato yuz berdi:", exc_info=err)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Kutilmagan xato yuz berdi. /start bosing."
            )
        except Exception:
            pass


def _cleanup_temp_dir():
    """24 soatdan eski vaqtinchalik fayllarni o'chiradi (bot restart da)."""
    from config import TEMP_DIR
    import time
    now = time.time()
    removed = 0
    try:
        for fname in os.listdir(TEMP_DIR):
            fpath = os.path.join(TEMP_DIR, fname)
            try:
                if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > 86400:
                    os.remove(fpath)
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass
    if removed:
        logger.info(f"🧹 TEMP_DIR: {removed} ta eski fayl o'chirildi.")


async def _post_init(app):
    """Bot ishga tushganda SQLite bazasini initsializatsiya qiladi."""
    await init_db()
    logger.info("✅ SQLite DB tayyor.")
    _cleanup_temp_dir()


def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN topilmadi!")

    builder = Application.builder().token(BOT_TOKEN).post_init(_post_init)

    if LOCAL_BOT_API_URL:
        # Local Bot API server mavjud (Replit yoki Railway internal)
        logger.info(f"🔗 Local Bot API: {LOCAL_BOT_API_URL}")
        builder = (
            builder
            .base_url(LOCAL_BOT_API_URL)
            .base_file_url(LOCAL_BOT_API_URL.replace("/bot", "/file/bot"))
            .local_mode(True)
        )
    else:
        # Standart Telegram API (50 MB limit, R2 orqali katta fayllar)
        logger.info("🌐 Standart Telegram API ishlatilmoqda")

    app = builder.build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("settings", show_settings))
    app.add_handler(CommandHandler("r2", r2_command))

    app.add_handler(MessageHandler(filters.VIDEO, document_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.AUDIO, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    logger.info("Bot ishga tushmoqda...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
