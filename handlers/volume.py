import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import run_ffmpeg_async, make_temp_path, _thread_count


# (label, filter_args)
VOLUME_OPTS = {
    "n20":    ("🔇 -20 dB (juda past)",         ["-af", "volume=-20dB"]),
    "n10":    ("🔈 -10 dB",                      ["-af", "volume=-10dB"]),
    "n6":     ("🔉 -6 dB (yarim)",               ["-af", "volume=-6dB"]),
    "n3":     ("🔉 -3 dB",                       ["-af", "volume=-3dB"]),
    "p3":     ("🔊 +3 dB",                       ["-af", "volume=+3dB"]),
    "p6":     ("🔊 +6 dB (ikki barobar)",        ["-af", "volume=+6dB"]),
    "p10":    ("📢 +10 dB",                      ["-af", "volume=+10dB"]),
    "p20":    ("📣 +20 dB (juda baland)",        ["-af", "volume=+20dB"]),
    "norm":   ("⚡ Auto Normalize (EBU R128)",    ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]),
    "mute_v": ("🔕 Ovoz qoldirish (audiosi bo'lmagan)", None),
}


def volume_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📣 +20 dB", callback_data="vol_p20"),
         InlineKeyboardButton("📢 +10 dB", callback_data="vol_p10")],
        [InlineKeyboardButton("🔊 +6 dB",  callback_data="vol_p6"),
         InlineKeyboardButton("🔊 +3 dB",  callback_data="vol_p3")],
        [InlineKeyboardButton("🔉 -3 dB",  callback_data="vol_n3"),
         InlineKeyboardButton("🔉 -6 dB",  callback_data="vol_n6")],
        [InlineKeyboardButton("🔈 -10 dB", callback_data="vol_n10"),
         InlineKeyboardButton("🔇 -20 dB", callback_data="vol_n20")],
        [InlineKeyboardButton("⚡ Auto Normalize (EBU R128)", callback_data="vol_norm")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(rows)


async def show_volume_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return
    context.user_data["state"] = "volume"
    await query.edit_message_text(
        "🔊 *Ovoz Balandligi*\n\n"
        "Ovozni qancha o'zgartirish kerak?\n"
        "_(Auto Normalize — ovozni optimal darajaga moslashtiradi)_",
        reply_markup=volume_keyboard(),
        parse_mode="Markdown",
    )


async def handle_volume_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, vol_key: str):
    query = update.callback_query
    await query.answer()

    if vol_key not in VOLUME_OPTS:
        await query.answer("❌ Noto'g'ri tanlov", show_alert=True)
        return

    label, af_args = VOLUME_OPTS[vol_key]
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    output_path = make_temp_path("mp4")
    threads = _thread_count()

    args = ["-i", video_path, "-threads", threads]
    if af_args:
        args += af_args
    args += ["-c:v", "copy", "-movflags", "+faststart", output_path]

    status_msg = await query.message.reply_text(
        f"⚙️ *Ovoz o'zgartirilmoqda → {label}*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {label}...")

    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label=f"Ovoz: {label}",
        input_path=video_path,
    )

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_vol_{vol_key}.mp4"
        from utils.sender import send_file
        await send_file(query.message, output_path, out_name,
                        f"✅ {label}", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
