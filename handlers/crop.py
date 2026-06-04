import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from utils.ffmpeg_utils import run_ffmpeg_async, make_temp_path, get_video_info, _thread_count


# (label, crop_filter_template) — uses {w} and {h} for input dimensions
CROP_PRESETS = {
    "16_9":   ("📺 16:9 (Widescreen)",       "crop=iw:iw*9/16:(iw-ow)/2:(ih-ow)/2"),
    "4_3":    ("📺 4:3 (Klassik)",            "crop=ih*4/3:ih:(iw-ow)/2:0"),
    "1_1":    ("⬛ 1:1 (Kvadrat / Instagram)", "crop=min(iw\\,ih):min(iw\\,ih):(iw-ow)/2:(ih-oh)/2"),
    "9_16":   ("📱 9:16 (Vertikal / Reels)",  "crop=ih*9/16:ih:(iw-ow)/2:0"),
    "21_9":   ("🎬 21:9 (Cinema)",            "crop=iw:iw*9/21:(iw-ow)/2:(ih-oh)/2"),
    "3_2":    ("📷 3:2 (Foto)",               "crop=ih*3/2:ih:(iw-ow)/2:0"),
    "2_1":    ("🖥 2:1",                       "crop=iw:iw/2:(iw-ow)/2:(ih-oh)/2"),
}


def crop_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📺 16:9 Widescreen", callback_data="crop_16_9"),
         InlineKeyboardButton("📱 9:16 Vertikal",   callback_data="crop_9_16")],
        [InlineKeyboardButton("⬛ 1:1 Kvadrat",      callback_data="crop_1_1"),
         InlineKeyboardButton("📺 4:3 Klassik",     callback_data="crop_4_3")],
        [InlineKeyboardButton("🎬 21:9 Cinema",     callback_data="crop_21_9"),
         InlineKeyboardButton("📷 3:2 Foto",        callback_data="crop_3_2")],
        [InlineKeyboardButton("🖥 2:1",             callback_data="crop_2_1")],
        [InlineKeyboardButton("✏️ Qo'lda kiritish (w:h:x:y)", callback_data="crop_custom")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(rows)


async def show_crop_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    info = get_video_info(video_path)
    w = info.get("width", "?")
    h = info.get("height", "?")

    context.user_data["state"] = "crop"
    await query.edit_message_text(
        f"📐 *Crop (Qirqish)*\n\n"
        f"🎬 Hozirgi o'lcham: `{w} × {h}`\n\n"
        f"Nisbatni tanlang (markaz ushlab turiladi):",
        reply_markup=crop_keyboard(),
        parse_mode="Markdown",
    )


async def handle_crop_preset(update: Update, context: ContextTypes.DEFAULT_TYPE, preset_key: str):
    query = update.callback_query
    await query.answer()

    if preset_key not in CROP_PRESETS:
        await query.answer("❌ Noto'g'ri preset", show_alert=True)
        return

    label, vf = CROP_PRESETS[preset_key]
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    await _do_crop(query, context, video_path, vf, label, f"crop_{preset_key}")


async def handle_crop_custom_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "crop_custom"

    info = get_video_info(context.user_data.get("video_path", ""))
    w = info.get("width", "?")
    h = info.get("height", "?")

    await query.edit_message_text(
        f"✏️ *Qo'lda Crop*\n\n"
        f"Joriy o'lcham: `{w} × {h}`\n\n"
        f"Formatda kiriting: `kenglik:balandlik:x:y`\n\n"
        f"_Masalan: `1280:720:0:0` — chap yuqori burchakdan 1280×720_\n"
        f"_Yoki: `640:480:320:120` — markazdan_",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_crop_custom_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split(":")
    if len(parts) != 4:
        await update.message.reply_text(
            "❌ Noto'g'ri format. `kenglik:balandlik:x:y` shaklida kiriting.\n"
            "_Masalan: `1280:720:0:0`_",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    try:
        cw, ch, cx, cy = [int(p) for p in parts]
    except ValueError:
        await update.message.reply_text(
            "❌ Faqat son kiriting. _Masalan: `1280:720:0:0`_",
            reply_markup=cancel_keyboard(), parse_mode="Markdown",
        )
        return

    context.user_data["state"] = None
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await update.message.reply_text("❌ Video topilmadi.")
        return

    vf = f"crop={cw}:{ch}:{cx}:{cy}"
    label = f"Crop {cw}×{ch} (+{cx},{cy})"

    status_msg = await update.message.reply_text(
        f"⚙️ *{label}*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )

    output_path = make_temp_path("mp4")
    threads = _thread_count()

    args = [
        "-i", video_path, "-threads", threads,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "copy", "-movflags", "+faststart",
        output_path,
    ]

    ok, err = await run_ffmpeg_async(
        args, status_msg, label=label, input_path=video_path,
    )

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_crop_custom.mp4"
        from utils.sender import send_file
        await send_file(update.message, output_path, out_name,
                        f"✅ {label}", context=context)
        os.remove(output_path)
        await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


async def _do_crop(query, context, video_path: str, vf: str, label: str, suffix: str):
    output_path = make_temp_path("mp4")
    threads = _thread_count()

    args = [
        "-i", video_path, "-threads", threads,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "copy", "-movflags", "+faststart",
        output_path,
    ]

    status_msg = await query.message.reply_text(
        f"⚙️ *{label} qirilmoqda...*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {label}...")

    ok, err = await run_ffmpeg_async(
        args, status_msg, label=label, input_path=video_path,
    )

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_{suffix}.mp4"
        from utils.sender import send_file
        await send_file(query.message, output_path, out_name,
                        f"✅ {label}", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
