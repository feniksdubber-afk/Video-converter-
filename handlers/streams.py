import asyncio
import os
import subprocess
import json
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, stream_remover_keyboard, stream_extractor_keyboard
from utils.ffmpeg_utils import make_temp_path
from utils.sender import send_file


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_streams(video_path: str) -> list[dict]:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=index,codec_type,codec_name,channels,r_frame_rate,width,height",
                "-show_entries", "stream_tags=language,title",
                "-of", "json", video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception:
        return []


async def _get_streams_async(video_path: str) -> list[dict]:
    """_get_streams ni async (non-blocking) versiyasi."""
    return await asyncio.get_running_loop().run_in_executor(None, _get_streams, video_path)


def _audio_ext(codec: str) -> str:
    return {"aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "ogg",
            "flac": "flac", "pcm_s16le": "wav"}.get(codec, "mka")


def _subtitle_ext(codec: str) -> str:
    return {"subrip": "srt", "ass": "ass", "webvtt": "vtt",
            "hdmv_pgs_subtitle": "sup"}.get(codec, "srt")


# ── Stream Remover ─────────────────────────────────────────────────────────

async def show_stream_remover_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    streams = await _get_streams_async(video_path)
    if not streams:
        await query.edit_message_text("❌ Streamlar aniqlanmadi.", reply_markup=main_menu_keyboard())
        return

    context.user_data["streams"] = streams
    context.user_data["remove_selected"] = set()
    context.user_data["state"] = "stream_remover"

    await query.edit_message_text(
        "🗑 *Stream Remover*\n\nO'chirmoqchi bo'lgan streamlarni tanlang.\n"
        "Tugmani bosib belgilang, oxirida *Tayyor* ni bosing:",
        reply_markup=stream_remover_keyboard(streams, set()),
        parse_mode="Markdown",
    )


async def handle_toggle_remove_stream(update: Update, context: ContextTypes.DEFAULT_TYPE, stream_idx: int):
    query = update.callback_query
    await query.answer()

    streams = context.user_data.get("streams", [])
    selected: set = context.user_data.get("remove_selected", set())

    if stream_idx in selected:
        selected.discard(stream_idx)
    else:
        selected.add(stream_idx)

    context.user_data["remove_selected"] = selected

    await query.edit_message_text(
        "🗑 *Stream Remover*\n\nO'chirmoqchi bo'lgan streamlarni tanlang.\n"
        "Tugmani bosib belgilang, oxirida *Tayyor* ni bosing:",
        reply_markup=stream_remover_keyboard(streams, selected),
        parse_mode="Markdown",
    )


async def handle_select_all_audio_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    streams = context.user_data.get("streams", [])
    selected: set = context.user_data.get("remove_selected", set())

    audio_indices = {s["index"] for s in streams if s.get("codec_type") == "audio"}
    if audio_indices.issubset(selected):
        selected -= audio_indices
    else:
        selected |= audio_indices

    context.user_data["remove_selected"] = selected

    await query.edit_message_text(
        "🗑 *Stream Remover*\n\nO'chirmoqchi bo'lgan streamlarni tanlang.\n"
        "Tugmani bosib belgilang, oxirida *Tayyor* ni bosing:",
        reply_markup=stream_remover_keyboard(streams, selected),
        parse_mode="Markdown",
    )


async def handle_select_all_subs_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    streams = context.user_data.get("streams", [])
    selected: set = context.user_data.get("remove_selected", set())

    sub_indices = {s["index"] for s in streams if s.get("codec_type") == "subtitle"}
    if sub_indices.issubset(selected):
        selected -= sub_indices
    else:
        selected |= sub_indices

    context.user_data["remove_selected"] = selected

    await query.edit_message_text(
        "🗑 *Stream Remover*\n\nO'chirmoqchi bo'lgan streamlarni tanlang.\n"
        "Tugmani bosib belgilang, oxirida *Tayyor* ni bosing:",
        reply_markup=stream_remover_keyboard(streams, selected),
        parse_mode="Markdown",
    )


async def handle_select_all_streams_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    streams = context.user_data.get("streams", [])
    selected: set = context.user_data.get("remove_selected", set())

    all_indices = {s["index"] for s in streams}
    if all_indices.issubset(selected):
        selected -= all_indices
    else:
        selected |= all_indices

    context.user_data["remove_selected"] = selected

    await query.edit_message_text(
        "🗑 *Stream Remover*\n\nO'chirmoqchi bo'lgan streamlarni tanlang.\n"
        "Tugmani bosib belgilang, oxirida *Tayyor* ni bosing:",
        reply_markup=stream_remover_keyboard(streams, selected),
        parse_mode="Markdown",
    )


async def handle_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    streams = context.user_data.get("streams", [])
    selected: set = context.user_data.get("remove_selected", set())

    if not selected:
        await query.answer("⚠️ Hech qaysi stream tanlanmadi!", show_alert=True)
        return

    remaining = [s for s in streams if s.get("index") not in selected]
    if not remaining:
        await query.answer("❌ Hamma streamlarni o'chirib bo'lmaydi!", show_alert=True)
        return

    map_args = []
    for s in remaining:
        map_args += ["-map", f"0:{s['index']}"]

    output_path = make_temp_path("mkv")
    cmd = ["ffmpeg", "-y", "-i", video_path] + map_args + ["-c", "copy", output_path]

    status_msg = await query.message.reply_text(
        f"⏳ *{len(selected)} ta stream o'chirilmoqda...*",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {len(selected)} ta stream o'chirilmoqda...")

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-1500:])

        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_streams_removed.mkv"

        removed_labels = ", ".join(f"#{i}" for i in sorted(selected))
        await status_msg.edit_text(
            f"✅ *Streamlar o'chirildi:* `{removed_labels}`",
            parse_mode="Markdown",
        )

        # Tarixga qo'sh, eski faylni o'chir
        from utils.video_history import push_version
        old_path = context.user_data.get("video_path")
        push_version(context, output_path, out_name, f"🎞 Stream removed ({len(selected)} ta)")
        if old_path and os.path.exists(old_path) and old_path != output_path:
            os.remove(old_path)

        context.user_data["remove_selected"] = set()

        from utils.post_action import ask_post_action
        await ask_post_action(status_msg, context, f"{len(selected)} ta stream o'chirildi")
    except Exception as e:
        await status_msg.edit_text(f"❌ Xato:\n`{e}`", parse_mode="Markdown")
        from utils.keyboards import main_menu_keyboard
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


# ── Stream Extractor ──────────────────────────────────────────────────────────

async def show_stream_extractor_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    streams = await _get_streams_async(video_path)
    if not streams:
        await query.edit_message_text("❌ Streamlar aniqlanmadi.", reply_markup=main_menu_keyboard())
        return

    context.user_data["streams"] = streams
    context.user_data["state"] = "stream_extractor"

    await query.edit_message_text(
        "📦 *Stream Extractor*\n\nAjratib olmoqchi bo'lgan streamni tanlang:",
        reply_markup=stream_extractor_keyboard(streams),
        parse_mode="Markdown",
    )


async def handle_extract_stream(update: Update, context: ContextTypes.DEFAULT_TYPE, stream_idx: int):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    streams = context.user_data.get("streams", [])
    target = next((s for s in streams if s.get("index") == stream_idx), None)
    if not target:
        await query.edit_message_text("❌ Stream topilmadi.")
        return

    stype = target.get("codec_type", "").lower()
    codec = target.get("codec_name", "")

    ext_map = {
        "video": "mkv",
        "audio": _audio_ext(codec),
        "subtitle": _subtitle_ext(codec),
        "data": "bin",
    }
    ext = ext_map.get(stype, "mkv")
    output_path = make_temp_path(ext)

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-map", f"0:{stream_idx}",
        "-c", "copy",
        output_path,
    ]

    status_msg = await query.message.reply_text("⏳ Stream ajratilmoqda...")
    await query.edit_message_text(f"⏳ #{stream_idx} stream ajratilmoqda...")

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-1500:])

        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_stream{stream_idx}.{ext}"

        await status_msg.edit_text(
            f"✅ *#{stream_idx} stream ajratildi!*\n"
            f"📎 Tur: `{stype.upper()}` | Codec: `{codec}`\n\n"
            f"📤 Yuborilmoqda...",
            parse_mode="Markdown",
        )
        await send_file(query.message, output_path, out_name, f"✅ #{stream_idx} {stype} stream!", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    except Exception as e:
        await status_msg.edit_text(f"❌ Xato:\n`{e}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


async def handle_extract_all_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _extract_by_type(query, context, "audio")


async def handle_extract_all_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _extract_by_type(query, context, "subtitle")


async def handle_extract_all_streams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _extract_by_type(query, context, "all")


async def _extract_by_type(query, context, stype_filter: str):
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    streams = context.user_data.get("streams", [])

    if stype_filter == "all":
        targets = streams
        label = "barcha streamlar"
    else:
        targets = [s for s in streams if s.get("codec_type") == stype_filter]
        label_map = {"audio": "barcha audiolar", "subtitle": "barcha subtitrlar"}
        label = label_map.get(stype_filter, stype_filter)

    if not targets:
        await query.answer(f"❌ Bu turdagi stream topilmadi!", show_alert=True)
        return

    status_msg = await query.message.reply_text(
        f"⏳ *{label.capitalize()} ajratilmoqda... (0/{len(targets)})*",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {label} ajratilmoqda...")

    video_name = context.user_data.get("video_name", "video")
    base = os.path.splitext(video_name)[0]

    sent = 0
    errors = []

    for i, s in enumerate(targets):
        idx = s.get("index")
        stype = s.get("codec_type", "").lower()
        codec = s.get("codec_name", "")

        ext_map = {
            "video": "mkv",
            "audio": _audio_ext(codec),
            "subtitle": _subtitle_ext(codec),
            "data": "bin",
        }
        ext = ext_map.get(stype, "mkv")
        output_path = make_temp_path(ext)

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-map", f"0:{idx}",
            "-c", "copy",
            output_path,
        ]

        try:
            await status_msg.edit_text(
                f"⏳ *{label.capitalize()} ajratilmoqda... ({i}/{len(targets)})*\n"
                f"Hozir: #{idx} {stype.upper()} [{codec}]",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        try:
            _loop = asyncio.get_running_loop()
            result = await _loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600),
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-500:])

            tags = s.get("tags", {})
            lang = tags.get("language", "")
            out_name = f"{base}_stream{idx}_{stype}"
            if lang:
                out_name += f"_{lang}"
            out_name += f".{ext}"

            await send_file(query.message, output_path, out_name,
                            f"✅ #{idx} {stype.upper()} [{codec}]", context=context)
            os.remove(output_path)
            sent += 1
        except Exception as e:
            errors.append(f"#{idx}: {e}")
            if os.path.exists(output_path):
                os.remove(output_path)

    summary = f"✅ *{sent} ta stream yuborildi!*"
    if errors:
        summary += f"\n⚠️ {len(errors)} ta xato:\n" + "\n".join(f"`{e}`" for e in errors[:3])

    try:
        await status_msg.edit_text(summary, parse_mode="Markdown")
    except Exception:
        pass

    await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
