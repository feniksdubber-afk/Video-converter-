"""
Video Merger, Video Splitter, Video+Audio Merger,
Media Information, Video Renamer, Generate Sample
"""
import os
import re
import subprocess
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from utils.ffmpeg_utils import make_temp_path, get_video_duration, run_ffmpeg_async
from utils.sender import send_file
from utils.user_settings import get


# ═══════════════════════════════════════════════════════════
# 1. VIDEO RENAMER
# ═══════════════════════════════════════════════════════════

async def show_rename_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    old_name = context.user_data.get("video_name", "video")
    context.user_data["state"] = "rename_file"
    await query.edit_message_text(
        f"✏️ *Video Renamer*\n\n"
        f"📁 Hozirgi nom: `{old_name}`\n\n"
        f"Yangi nomni kiriting (kengaytmasiz):",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    # Xavfli belgilarni tozalash
    new_name = re.sub(r'[\\/*?:"<>|]', "_", new_name)

    video_path = context.user_data.get("video_path")
    old_name = context.user_data.get("video_name", "video")
    ext = os.path.splitext(old_name)[1] or ".mp4"
    out_name = new_name + ext

    context.user_data["state"] = None
    status = await update.message.reply_text("📤 Yuborilmoqda...")

    await send_file(update.message, video_path, out_name, f"✅ Yangi nom: `{out_name}`", context=context)
    await status.delete()
    await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


# ═══════════════════════════════════════════════════════════
# 2. MEDIA INFORMATION
# ═══════════════════════════════════════════════════════════

async def show_media_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams",
             "-of", "json", video_path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        streams = data.get("streams", [])

        file_size = int(fmt.get("size", 0))
        duration = float(fmt.get("duration", 0))
        bitrate = int(fmt.get("bit_rate", 0))
        fmt_name = fmt.get("format_long_name", "?")

        def fmt_size(b):
            for u in ["B", "KB", "MB", "GB"]:
                if b < 1024: return f"{b:.1f} {u}"
                b /= 1024
            return f"{b:.1f} GB"

        def fmt_dur(s):
            h, m = int(s // 3600), int((s % 3600) // 60)
            return f"{h:02d}:{m:02d}:{int(s % 60):02d}"

        text = (
            f"📋 *Media Ma'lumotlari*\n\n"
            f"📁 Fayl: `{context.user_data.get('video_name', '?')}`\n"
            f"📦 Hajm: `{fmt_size(file_size)}`\n"
            f"⏱ Davomiylik: `{fmt_dur(duration)}`\n"
            f"📡 Bitrate: `{bitrate // 1000} kbps`\n"
            f"🎞 Format: `{fmt_name}`\n\n"
        )

        for s in streams:
            idx = s.get("index", "?")
            stype = s.get("codec_type", "?").upper()
            codec = s.get("codec_name", "?")
            tags = s.get("tags", {})
            lang = tags.get("language", "")

            if stype == "VIDEO":
                w, h = s.get("width", "?"), s.get("height", "?")
                fps_raw = s.get("r_frame_rate", "0/1")
                try:
                    a, b = fps_raw.split("/")
                    fps = f"{int(a)//int(b)} fps"
                except Exception:
                    fps = fps_raw
                text += f"🎬 Video #{idx}: `{codec}` `{w}x{h}` `{fps}`"
            elif stype == "AUDIO":
                ch = s.get("channels", "?")
                sr = s.get("sample_rate", "?")
                text += f"🎵 Audio #{idx}: `{codec}` `{ch}ch` `{sr}Hz`"
                if lang: text += f" `{lang}`"
            elif stype == "SUBTITLE":
                text += f"📝 Subtitle #{idx}: `{codec}`"
                if lang: text += f" `{lang}`"
            else:
                text += f"❓ Stream #{idx}: `{stype}` `{codec}`"
            text += "\n"

        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Orqaga", callback_data="back")
            ]])
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Ma'lumot olishda xato:\n`{e}`", parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════
# 3. GENERATE SAMPLE
# ═══════════════════════════════════════════════════════════

async def show_sample_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    duration = get_video_duration(video_path)
    sample_dur = get(context, "sample_duration")
    mid = max(0, int(duration / 2) - sample_dur // 2)

    context.user_data["state"] = None
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"▶️ Boshidan ({sample_dur}s)", callback_data="sample_from_0")],
        [InlineKeyboardButton(f"⏱ O'rtasidan ({sample_dur}s)", callback_data=f"sample_from_{mid}")],
        [InlineKeyboardButton("✏️ Vaqtni o'zim kiritaman", callback_data="sample_manual")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ])
    await query.edit_message_text(
        f"🎬 *Generate Sample*\n\n"
        f"⏱ Video davomiyligi: `{_fmt_dur(duration)}`\n"
        f"📏 Namuna uzunligi: `{sample_dur}` soniya\n"
        f"_(⚙️ /settings da o'zgartirish mumkin)_\n\n"
        f"Qayerdan boshlash?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def handle_sample_from(update: Update, context: ContextTypes.DEFAULT_TYPE, start_sec: int):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    sample_dur = get(context, "sample_duration")
    output_path = make_temp_path("mp4")

    status_msg = await query.message.reply_text(
        f"⚙️ *Namuna yaratilmoqda...*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {start_sec}s dan {sample_dur}s namuna...")

    args = [
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(sample_dur),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        args, status_msg, label="Namuna yaratilmoqda",
        input_path=None,  # -t bilan progress boshqacha, manual
    )
    # run_ffmpeg_async duration aniqlay olmasa — oddiy subprocess
    if not ok:
        import subprocess as sp
        r = sp.run(["ffmpeg", "-y"] + args, capture_output=True, text=True, timeout=600)
        ok = r.returncode == 0
        err = r.stderr[-1000:] if not ok else ""

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_sample_{start_sec}s.mp4"
        await status_msg.edit_text("✅ Tayyor! Yuborilmoqda...")
        await send_file(query.message, output_path, out_name, f"🎬 Namuna ({sample_dur}s)", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


async def handle_sample_manual_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "sample_manual"
    await query.edit_message_text(
        "⏱ *Boshlanish vaqtini kiriting:*\n\nFormat: `MM:SS` yoki soniyalar\nMasalan: `01:30` yoki `90`",
        parse_mode="Markdown",
    )


async def handle_sample_manual_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    sec = _parse_to_seconds(text)
    if sec is None:
        await update.message.reply_text("❌ Noto'g'ri format. Masalan: `01:30` yoki `90`", parse_mode="Markdown")
        return
    context.user_data["state"] = None

    video_path = context.user_data.get("video_path")
    sample_dur = get(context, "sample_duration")
    output_path = make_temp_path("mp4")

    status = await update.message.reply_text("⏳ Namuna yaratilmoqda...")

    args = [
        "-ss", str(sec), "-i", video_path,
        "-t", str(sample_dur),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-movflags", "+faststart",
        output_path,
    ]
    import subprocess as sp
    r = sp.run(["ffmpeg", "-y"] + args, capture_output=True, text=True, timeout=600)

    if r.returncode == 0 and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_sample.mp4"
        await status.edit_text("✅ Tayyor! Yuborilmoqda...")
        await send_file(update.message, output_path, out_name, f"🎬 Namuna ({sample_dur}s)", context=context)
        os.remove(output_path)
        await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status.edit_text(f"❌ Xato:\n`{r.stderr[-800:]}`", parse_mode="Markdown")
        await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


# ═══════════════════════════════════════════════════════════
# 4. VIDEO SPLITTER
# ═══════════════════════════════════════════════════════════

async def show_splitter_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    duration = get_video_duration(video_path)
    split_dur = get(context, "split_duration")
    parts = max(1, int(duration / split_dur) + (1 if duration % split_dur > 0 else 0))

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✂️ {split_dur}s bo'laklarga bo'lish (~{parts} ta)", callback_data=f"split_go_{split_dur}")],
        [InlineKeyboardButton("✏️ Bo'lak uzunligini o'rnatish", callback_data="split_set_dur")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ])
    await query.edit_message_text(
        f"✂️ *Video Splitter*\n\n"
        f"⏱ Davomiylik: `{_fmt_dur(duration)}`\n"
        f"📏 Bo'lak uzunligi: `{split_dur}` soniya\n"
        f"🔢 Taxminiy bo'laklar soni: `{parts}`\n\n"
        f"_(⚙️ /settings da o'zgartirish mumkin)_",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def handle_split_go(update: Update, context: ContextTypes.DEFAULT_TYPE, chunk_sec: int):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    duration = get_video_duration(video_path)
    video_name = context.user_data.get("video_name", "video")
    base = os.path.splitext(video_name)[0]

    total_parts = max(1, int(duration / chunk_sec) + (1 if duration % chunk_sec > 0 else 0))
    status_msg = await query.message.reply_text(
        f"✂️ *Bo'linmoqda...*\n0 / {total_parts} tayyor", parse_mode="Markdown"
    )
    await query.edit_message_text(f"⏳ {total_parts} bo'lakka bo'linmoqda...")

    sent = 0
    start = 0
    part = 1
    import subprocess as sp

    while start < duration:
        output_path = make_temp_path("mp4")
        args = [
            "ffmpeg", "-y",
            "-ss", str(start), "-i", video_path,
            "-t", str(chunk_sec),
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        r = sp.run(args, capture_output=True, timeout=600)
        if r.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            out_name = f"{base}_part{part:02d}.mp4"
            await send_file(query.message, output_path, out_name, f"✂️ Bo'lak {part}/{total_parts}", context=context)
            os.remove(output_path)
            sent += 1
            await status_msg.edit_text(
                f"✂️ *Bo'linmoqda...*\n{sent} / {total_parts} yuborildi",
                parse_mode="Markdown",
            )
        start += chunk_sec
        part += 1

    await status_msg.edit_text(f"✅ Hammasi yuborildi! Jami: {sent} ta bo'lak.")
    await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


async def handle_split_set_dur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "split_dur_input"
    await query.edit_message_text(
        "✏️ Bo'lak uzunligini soniyalarda kiriting (10-3600):",
        reply_markup=cancel_keyboard(),
    )


async def handle_split_dur_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.isdigit() and 10 <= int(text) <= 3600:
        from utils.user_settings import set_
        set_(context, "split_duration", int(text))
        context.user_data["state"] = None
        await update.message.reply_text(
            f"✅ Bo'lak uzunligi `{text}` soniyaga o'rnatildi.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await update.message.reply_text("❌ 10 dan 3600 gacha raqam kiriting.")


# ═══════════════════════════════════════════════════════════
# 5. VIDEO MERGER
# ═══════════════════════════════════════════════════════════

async def show_merger_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    queue = context.user_data.get("merge_queue", [])
    main_video = context.user_data.get("video_name", "video")

    if not queue:
        context.user_data["merge_queue"] = []

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Video qo'shish (keyingisini yuboring)", callback_data="merge_add_next")],
        [InlineKeyboardButton(f"▶️ Birlashtirish ({len(queue)+1} ta video)", callback_data="merge_go")] if queue else [],
        [InlineKeyboardButton("🗑 Ro'yxatni tozalash", callback_data="merge_clear")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ])
    items = "\n".join([f"  {i+1}. `{os.path.basename(p)}`" for i, p in enumerate(queue)])
    text = (
        f"🎬 *Video Merger*\n\n"
        f"1. `{main_video}` _(asosiy video)_\n"
        f"{items}\n\n"
        f"Video qo'shish uchun '➕ Video qo'shish' tugmasini bosib keyingi videoni yuboring."
    )
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def handle_merge_add_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "merge_waiting"
    await query.edit_message_text(
        "📤 *Birlashtiriladigan videoni yuboring:*\n\n"
        "_(Yuborilgandan so'ng avtomatik qo'shiladi)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="merge_cancel_add")]]),
    )


async def handle_merge_video_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Video handler dan chaqiriladi. True qaytarsa — merge rejimida."""
    if context.user_data.get("state") != "merge_waiting":
        return False

    msg = update.message
    file = msg.video or msg.document
    if not file:
        return False

    file_name = getattr(file, "file_name", None) or "video.mp4"
    ext = os.path.splitext(file_name)[1].lstrip(".").lower() or "mp4"
    local_path = os.path.join("/tmp/videobot", f"merge_{file.file_unique_id}.{ext}")

    status = await msg.reply_text("⬇️ Yuklanmoqda...")
    if file.file_size and file.file_size <= 20 * 1024 * 1024:
        tg_file = await file.get_file()
        await tg_file.download_to_drive(local_path)
    else:
        from handlers.video_handler import _download_via_pyrogram
        await _download_via_pyrogram(file.file_id, file.file_size, local_path, status)

    queue = context.user_data.setdefault("merge_queue", [])
    queue.append(local_path)
    context.user_data["state"] = None

    await status.edit_text(
        f"✅ Video qo'shildi! Jami: {len(queue)+1} ta\n\n"
        f"Yana video qo'shish yoki birlashtirish uchun /merge yoki menyuga qayting.",
    )
    await msg.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    return True


async def handle_merge_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    main_path = context.user_data.get("video_path")
    queue = context.user_data.get("merge_queue", [])

    if not queue:
        await query.answer("❌ Birlashtiradigan video yo'q!", show_alert=True)
        return

    all_videos = [main_path] + queue
    # ffmpeg concat demuxer uchun list fayl
    list_path = make_temp_path("txt")
    with open(list_path, "w") as f:
        for p in all_videos:
            f.write(f"file '{p}'\n")

    output_path = make_temp_path("mp4")
    status_msg = await query.message.reply_text(
        f"⚙️ *{len(all_videos)} ta video birlashtirilmoqda...*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {len(all_videos)} ta video birlashtirilmoqda...")

    ffmpeg_args = [
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        ffmpeg_args, status_msg,
        label=f"{len(all_videos)} ta video birlashtirilmoqda",
        input_path=all_videos[0],
    )
    try:
        os.remove(list_path)
    except Exception:
        pass

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_merged.mp4"
        await status_msg.edit_text("✅ Tayyor! Yuborilmoqda...")
        await send_file(query.message, output_path, out_name, f"✅ {len(all_videos)} ta video birlashtirildi!", context=context)
        try:
            os.remove(output_path)
        except Exception:
            pass
        for p in queue:
            try:
                os.remove(p)
            except Exception:
                pass
        context.user_data["merge_queue"] = []
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err[-800:]}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


async def handle_merge_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    queue = context.user_data.get("merge_queue", [])
    for p in queue:
        try: os.remove(p)
        except: pass
    context.user_data["merge_queue"] = []
    await show_merger_menu(update, context)


# ═══════════════════════════════════════════════════════════
# 6. VIDEO + AUDIO MERGER
# ═══════════════════════════════════════════════════════════

async def show_vid_aud_merger_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "vid_aud_merge_waiting"
    await query.edit_message_text(
        "🎵 *Video + Audio Merger*\n\n"
        "Videoga qo'shiladigan audio faylni yuboring.\n"
        "_(Eski audio almashtiriladi)_\n\n"
        "Qo'llab-quvvatlanadigan formatlar: MP3, AAC, OGG, WAV, FLAC va boshqalar.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor", callback_data="back")]]),
    )


async def handle_vid_aud_merge_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get("state") != "vid_aud_merge_waiting":
        return False

    msg = update.message
    doc = msg.document or msg.audio
    if not doc:
        return False

    mime = getattr(doc, "mime_type", "") or ""
    if not (mime.startswith("audio/") or (getattr(doc, "file_name", "") or "").lower().split(".")[-1]
            in ["mp3", "aac", "ogg", "wav", "flac", "m4a", "opus", "wma"]):
        await msg.reply_text("❌ Audio fayl yuboring.")
        return True

    file_name = getattr(doc, "file_name", "audio.mp3") or "audio.mp3"
    ext = os.path.splitext(file_name)[1].lstrip(".").lower() or "mp3"
    audio_path = make_temp_path(ext)

    status = await msg.reply_text("⬇️ Audio yuklanmoqda...")
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        from handlers.video_handler import get_pyrogram_client
        client = await get_pyrogram_client()
        await client.download_media(doc.file_id, file_name=audio_path)
    else:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(audio_path)

    video_path = context.user_data.get("video_path")
    output_path = make_temp_path("mp4")

    await status.edit_text(
        "⚙️ *Birlashtirilmoqda...*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )

    ffmpeg_args = [
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        ffmpeg_args, status,
        label="Video + Audio birlashtirilmoqda",
        input_path=video_path,
    )
    try:
        os.remove(audio_path)
    except Exception:
        pass
    context.user_data["state"] = None

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_with_audio.mp4"
        await status.edit_text("✅ Tayyor! Yuborilmoqda...")
        await send_file(msg, output_path, out_name, "✅ Video + Audio birlashtirildi!", context=context)
        try:
            os.remove(output_path)
        except Exception:
            pass
        await msg.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status.edit_text(f"❌ Xato:\n`{err[-800:]}`", parse_mode="Markdown")
        await msg.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    return True


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def _fmt_dur(s: float) -> str:
    h, m = int(s // 3600), int((s % 3600) // 60)
    return f"{h:02d}:{m:02d}:{int(s % 60):02d}"


def _parse_to_seconds(text: str):
    text = text.strip()
    if re.match(r"^\d+$", text):
        return int(text)
    m = re.match(r"^(\d+):(\d{2})$", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"^(\d+):(\d{2}):(\d{2})$", text)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None
