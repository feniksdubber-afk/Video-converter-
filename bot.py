import logging
import os
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from handlers.hls_handler import (
    show_hls_menu, handle_hls_quality,
    handle_hls_audio_toggle, handle_hls_audio_all, handle_hls_audio_done,
)

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
    batch_command, handle_batch_abort, handle_batch_remove_file,
)
from handlers.r2_browser import r2_command, r2_callback, r2_rename_text, r2_mkdir_text, _show_r2_list_cb
from handlers.save_restricted import (
    save_link_handler, save_topic_handler, save_confirm_callback, handle_save_new_topic_name,
    audio_link_handler, save_audio_topic_handler,
)
from handlers.kino_sender import kino_sender_handler, kino_callback_handler
from handlers.netfilm_handler import netfilm_handler, netfilm_callback_handler
from handlers.url_downloader import dl_handler, dl_callback_handler
from handlers.torrent_handler import torrent_handler, torrent_callback_handler
from utils.auth_handlers import (
    auth_gate, allow_handler, deny_handler, users_handler,
    studios_list_handler, studio_unbind_handler, studio_token_handler, handle_studio_pick,
    studio_switch_handler, handle_studio_switch, handle_studio_switch_menu,
)
from handlers.studio_upload import show_studio_upload_entry, handle_kind_choice, handle_studio_text, handle_tg_video_attach
from handlers.studio_content import (
    show_episodes_entry, show_season_episodes, show_episode_detail,
    handle_episode_upload, handle_new_episode_entry,
    show_browse_entry, handle_bkind_choice, handle_list_page, handle_item_pick,
    prompt_search, handle_clear_search, handle_manual_entry, handle_search_text,
    handle_edit_entry, handle_edit_field_choice, handle_edit_text,
)
from utils.auth import reload_auth
from utils.task_manager import cancel_task, clear_task
from utils.keyboards import main_menu_keyboard, studio_menu_keyboard

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def _setup_asyncio_exception_handler():
    """Pyrogram ichki handle_updates() taskida chiqadigan 'Peer id invalid'
    ValueError xatolarini yutib yuboradi. Bu xato bot ishiga ta'sir qilmaydi —
    userbot yangi kanal/guruh update'i olganda peer cache'da shu chat bo'lmasa
    yuz beradi va kutilgan holat hisoblanadi. Shu sababli asyncio'ning
    unhandled exception handler'ini override qilib, faqat shu xatoni
    WARNING darajasida loglaymiz (ERROR o'rniga), stack trace yo'q."""
    import asyncio

    def _custom_exception_handler(loop, context):
        exc = context.get("exception")
        if isinstance(exc, ValueError) and "Peer id invalid" in str(exc):
            # Kutilgan holat — yutib yuboramiz (yoki faqat debug darajasida)
            logger.debug("Pyrogram peer cache miss (kutilgan): %s", exc)
            return
        # Boshqa xatolar — standart asyncio handler'ga uzatamiz
        loop.default_exception_handler(context)

    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_custom_exception_handler)
    except RuntimeError:
        pass  # Event loop hali yaratilmagan — main() ichida chaqiriladi


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # Sozlamalarni keshga yuklash (birinchi marta)
    user_id = query.from_user.id
    context.user_data["_user_id"] = user_id
    await ensure_loaded(user_id, context)

    # ── Studiya menejerlari faqat konvertatsiya + studiyaga yuklashdan foydalana oladi ──
    from utils.auth import is_admin, is_allowed
    from utils.studio_auth import is_studio_manager
    if is_studio_manager(user_id) and not is_admin(user_id) and not is_allowed(user_id):
        _STUDIO_ALLOWED_PREFIXES = (
            "cat_video", "studio_", "pa_", "task_cancel", "queue_cancel_", "cancel", "back",
            "convert", "resolution", "fmt_", "res_", "backfill_",
        )
        if not data.startswith(_STUDIO_ALLOWED_PREFIXES):
            await query.answer("⛔ Sizga faqat konvertatsiya va studiyaga yuklash ruxsat etilgan.", show_alert=True)
            return

    # ── Netfilm callbacks ────────────────────────────────────────────────────
    if data.startswith(("nf_dl|", "nf_cancel")):
        await netfilm_callback_handler(update, context)
        return
    if data.startswith(("dl_fmt|", "dl_cancel")):
        await dl_callback_handler(update, context)
        return
    if data.startswith(("tr_dl|", "tr_cancel", "tr_force|")):
        await torrent_callback_handler(update, context)
        return

    # ── Kino mirror callbacks ─────────────────────────────────────────────────
    if data.startswith(("kino|", "kinoi|", "kinodl|")):
        await kino_callback_handler(update, context)
        return

    # ── Save Restricted confirm / cancel / topic tanlash ────────────────────────
    if data.startswith("sr_"):
        await save_confirm_callback(update, context)
        return

    # ── Vazifa bekor qilish (FFmpeg, yuklash) ─────────────────────────────────
    if data == "task_cancel":
        uid = query.from_user.id
        if await cancel_task(uid):
            await query.answer("❌ Bekor qilindi")
            try:
                await query.edit_message_text("❌ Jarayon bekor qilindi.")
            except Exception:
                pass
        else:
            await query.answer("Faol vazifa yo'q", show_alert=True)
        return

    # ── Navbatda kutayotgan vazifadan chiqish ─────────────────────────────────
    if data.startswith("queue_cancel_"):
        await query.answer()
        from utils.task_queue import cancel_ticket
        ticket_id = int(data.rsplit("_", 1)[1])
        cancel_ticket(ticket_id)
        return

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
        await _show_r2_list_cb(query, context, page=0)
        return

    # ── Studiya menejeri: konvertatsiyalangan videoni platformaga yuklash ──
    if data in ("backfill_go", "backfill_no"):
        from handlers.studio_backfill import handle_backfill_choice
        await handle_backfill_choice(update, context, go=(data == "backfill_go"))
        return

    if data == "studio_upload":
        await show_studio_upload_entry(update, context)
        return
    if data == "studio_kind_movie":
        await handle_kind_choice(update, context, "movies")
        return
    if data == "studio_kind_series":
        await handle_kind_choice(update, context, "series")
        return
    if data == "studio_switchmenu":
        await handle_studio_switch_menu(update, context)
        return
    if data.startswith("studio_pick_"):
        await handle_studio_pick(update, context)
        return
    if data.startswith("studio_switch_"):
        await handle_studio_switch(update, context)
        return
    if data == "studio_browse":
        await show_browse_entry(update, context)
        return
    if data.startswith("studio_bkind_"):
        # studio_bkind_{mode}_{kind}
        _, _, mode, kind = data.split("_")
        await handle_bkind_choice(update, context, mode, kind)
        return
    if data.startswith("studio_list_"):
        # studio_list_{mode}_{kind}_{page}
        _, _, mode, kind, page = data.split("_")
        await handle_list_page(update, context, mode, kind, int(page))
        return
    if data.startswith("studio_item_"):
        # studio_item_{mode}_{kind}_{id}
        _, _, mode, kind, item_id = data.split("_", 4)
        await handle_item_pick(update, context, mode, kind, item_id)
        return
    if data.startswith("studio_search_"):
        # studio_search_{mode}_{kind}
        _, _, mode, kind = data.split("_")
        await prompt_search(update, context, mode, kind)
        return
    if data.startswith("studio_clr_"):
        # studio_clr_{mode}_{kind}
        _, _, mode, kind = data.split("_")
        await handle_clear_search(update, context, mode, kind)
        return
    if data.startswith("studio_manual_"):
        kind = data.rsplit("_", 1)[1]
        await handle_manual_entry(update, context, kind)
        return
    if data.startswith("studio_edit_"):
        # studio_edit_{kind}_{id}
        _, _, kind, item_id = data.split("_", 3)
        await handle_edit_entry(update, context, kind, item_id)
        return
    if data.startswith("studio_ef_"):
        # studio_ef_{kind}_{id}_{field_code}
        _, _, kind, item_id, field_code = data.split("_", 4)
        await handle_edit_field_choice(update, context, kind, item_id, field_code)
        return
    if data.startswith("studio_tgv_") or data.startswith("studio_tgva_"):
        await handle_tg_video_attach(update, context, data)
        return
    if data.startswith("studio_epss_"):
        # studio_epss_{seriesId}_{season}
        _, _, series_id, season = data.split("_")
        await show_season_episodes(update, context, series_id, int(season))
        return
    if data.startswith("studio_epnew_"):
        # studio_epnew_{seriesId}_{season}
        _, _, series_id, season = data.split("_")
        await handle_new_episode_entry(update, context, series_id, int(season))
        return
    if data.startswith("studio_epup_"):
        # studio_epup_{seriesId}_{season}_{episode}
        _, _, series_id, season, episode = data.split("_")
        await handle_episode_upload(update, context, series_id, int(season), int(episode))
        return
    if data.startswith("studio_epi_"):
        # studio_epi_{seriesId}_{season}_{episode}
        _, _, series_id, season, episode = data.split("_")
        await show_episode_detail(update, context, series_id, int(season), int(episode))
        return
    if data.startswith("studio_eps_"):
        # studio_eps_{seriesId}
        series_id = data.rsplit("_", 1)[1]
        await show_episodes_entry(update, context, series_id)
        return

    # ── Umumiy ──────────────────────────────────────────────
    if data == "cancel":
        from utils.studio_auth import get_bound_studio
        context.user_data["state"] = None
        await query.answer()
        studio = get_bound_studio(query.from_user.id)
        if studio:
            await query.edit_message_text(
                "❌ Bekor qilindi.\n\nYangi video yuboring yoki menyu:",
                reply_markup=studio_menu_keyboard(),
            )
            return
        await query.edit_message_text(
            "❌ Bekor qilindi.\n\nYangi video yuboring yoki menyu:",
            reply_markup=main_menu_keyboard() if context.user_data.get("video_path") else None,
        )
        return
    if data == "back":
        from utils.studio_auth import get_bound_studio
        context.user_data["state"] = None
        await query.answer()
        studio = get_bound_studio(query.from_user.id)
        if studio:
            await query.edit_message_text(
                "Kerakli amalni tanlang:",
                reply_markup=studio_menu_keyboard(),
            )
            return
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
    elif data.startswith("hls_audio_toggle_"):
        track_idx = int(data.replace("hls_audio_toggle_", ""))
        await handle_hls_audio_toggle(update, context, track_idx)
    elif data == "hls_audio_all":
        await handle_hls_audio_all(update, context)
    elif data == "hls_audio_done":
        await handle_hls_audio_done(update, context)
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
    elif data == "batch_abort":            await handle_batch_abort(update, context)
    elif data == "batch_run":              await handle_batch_run(update, context)
    elif data.startswith("batch_rm_"):     await handle_batch_remove_file(update, context, int(data[9:]))
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

    is_private = update.effective_chat.type == "private"

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
        "save_new_topic_name": handle_save_new_topic_name,
    }

    # MUHIM: konvertatsiya oqimi bilan bog'liq holatlar (trim, rename va h.k.)
    # faqat shaxsiy chatda ma'noli — bular foydalanuvchi botning shaxsiy
    # menyusi bilan ishlayotganda yuzaga keladi. Guruhdagi matnlar uchun bu
    # state'larga e'tibor berilmaydi.
    if is_private:
        # R2 rename / papka yaratish
        if state == "r2_rename_input":
            await r2_rename_text(update, context)
            return
        if state == "r2_mkdir_input":
            await r2_mkdir_text(update, context)
            return

        # Batch shablon nomi kiritish
        if state == "batch_save_name":
            from handlers.batch import handle_batch_save_name
            await handle_batch_save_name(update, context)
            return

        # Studiyaga yuklash oqimi (film/serial ID, fasl, qism)
        if state in ("studio_movie_id", "studio_series_id", "studio_episode"):
            if await handle_studio_text(update, context):
                return

        # Studiya kontenti bo'yicha qidiruv matni
        if state == "studio_search_text":
            if await handle_search_text(update, context):
                return

        # Studiya kontenti maydonini tahrirlash matni
        if state == "studio_edit_text":
            if await handle_edit_text(update, context):
                return

        handler = dispatch.get(state)
        if handler:
            await handler(update, context)
            return

    # t.me havola orqali saqlash — guruh va shaxsiy chatlarning ikkalasida
    # ham ishlaydi (guruhga restricted-link tashlash foydali bo'lishi mumkin).
    if await save_link_handler(update, context):
        return

    # Guruhda hech narsaga mos kelmagan matnga umuman e'tibor berilmaydi —
    # bot guruhdagi suhbatlarga aralashmasligi kerak. Faqat shaxsiy chatda
    # konvertatsiyaga taklif qiluvchi yordamchi xabar ko'rsatiladi.
    if is_private:
        await update.message.reply_text("📤 Video yuboring yoki /start bosing.")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await ensure_loaded(user_id, context)

    state = context.user_data.get("state")

    if state == "save_new_topic_name":
        await update.message.reply_text("❗ Iltimos, topic uchun matn ko'rinishida nom yuboring (fayl emas).")
        return

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
    state = context.user_data.get("state")
    if state == "save_new_topic_name":
        await update.message.reply_text("❗ Iltimos, topic uchun matn ko'rinishida nom yuboring (rasm emas).")
        return
    if state == "settings_thumb":
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
    import asyncio
    # Asyncio event loop ishga tushgandan keyin exception handler'ni
    # qaytadan o'rnatamiz — shu paytda loop allaqachon mavjud bo'ladi.
    _setup_asyncio_exception_handler()
    try:
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: (
                logger.debug("Pyrogram peer cache miss (kutilgan): %s", ctx.get("exception"))
                if isinstance(ctx.get("exception"), ValueError)
                   and "Peer id invalid" in str(ctx.get("exception", ""))
                else loop.default_exception_handler(ctx)
            )
        )
    except Exception:
        pass
    await init_db()
    reload_auth()
    logger.info("✅ SQLite DB tayyor.")
    logger.info("✅ Auth whitelist yuklandi.")
    _cleanup_temp_dir()


def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN topilmadi!")

    _setup_asyncio_exception_handler()

    builder = Application.builder().token(BOT_TOKEN).post_init(_post_init).concurrent_updates(True)

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

    # Ruxsat tekshiruvi — barcha handlerlardan oldin (group -1)
    from telegram.ext import TypeHandler
    app.add_handler(TypeHandler(Update, auth_gate), group=-1)

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("settings", show_settings))
    app.add_handler(CommandHandler("r2", r2_command))
    app.add_handler(CommandHandler("batch", batch_command))
    app.add_handler(CommandHandler("save", save_topic_handler))
    app.add_handler(CommandHandler("savea", save_audio_topic_handler))
    app.add_handler(CommandHandler("a", audio_link_handler))
    app.add_handler(CommandHandler("kino", kino_sender_handler))
    app.add_handler(CommandHandler("netfilm", netfilm_handler))
    app.add_handler(CommandHandler("dl", dl_handler))
    app.add_handler(CommandHandler("torrent", torrent_handler))
    app.add_handler(CommandHandler("allow", allow_handler))
    app.add_handler(CommandHandler("deny", deny_handler))
    app.add_handler(CommandHandler("users", users_handler))
    app.add_handler(CommandHandler("studiyalar", studios_list_handler))
    app.add_handler(CommandHandler("studiya_chiqar", studio_unbind_handler))
    app.add_handler(CommandHandler("studiya_token", studio_token_handler))
    app.add_handler(CommandHandler("studiya_almashtirish", studio_switch_handler))

    from handlers.studio_group import bind_group_command
    app.add_handler(CommandHandler("guruh_biriktirish", bind_group_command))

    from handlers.studio_backfill import backfill_command
    app.add_handler(CommandHandler("kontent_toldirish", backfill_command))

    # MUHIM: video/fayl/audio/rasm qabul qilish va "✅ Video qabul qilindi"
    # kabi konvertatsiya menyusi FAQAT shaxsiy chatda (bot bilan 1:1) ishlashi
    # kerak. Guruhlarga tashlangan videolarga bot umuman e'tibor bermasligi,
    # hech qanday javob yozmasligi kerak — shu sababli filters.ChatType.PRIVATE
    # qo'shildi. (save_restricted/t.me-havola orqali saqlash bunga
    # ta'sirlanmaydi — u alohida text_handler orqali ishlaydi.)
    app.add_handler(MessageHandler(filters.VIDEO & filters.ChatType.PRIVATE, document_handler))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, document_handler))
    app.add_handler(MessageHandler(filters.AUDIO & filters.ChatType.PRIVATE, document_handler))
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
