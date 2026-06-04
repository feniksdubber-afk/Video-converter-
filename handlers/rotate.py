import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import run_ffmpeg_async, make_temp_path, _thread_count


OPERATIONS = {
    "rot90cw":  ("↩️ 90° Soat yo'nalishida",       ["-vf", "transpose=1"]),
    "rot90ccw": ("↪️ 90° Soat əksi yo'nalishida",  ["-vf", "transpose=2"]),
    "rot180":   ("🔃 180° Aylanish",                ["-vf", "transpose=1,transpose=1"]),
    "fliph":    ("↔️ Gorizontal Aylantirish",        ["-vf", "hflip"]),
    "flipv":    ("↕️ Vertikal Aylantirish",          ["-vf", "vflip"]),
    "rot90cw_fh": ("↩️↔️ 90° CW + Gorizontal",      ["-vf", "transpose=1,hflip"]),
    "rot90ccw_fh": ("↪️↔️ 90° CCW + Gorizontal",   ["-vf", "transpose=2,hflip"]),
}


def rotate_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("↩️ 90° CW",  callback_data="rot_rot90cw"),
         InlineKeyboardButton("↪️ 90° CCW", callback_data="rot_rot90ccw")],
        [InlineKeyboardButton("🔃 180°",    callback_data="rot_rot180")],
        [InlineKeyboardButton("↔️ Gorizontal Flip", callback_data="rot_fliph"),
         InlineKeyboardButton("↕️ Vertikal Flip",   callback_data="rot_flipv")],
        [InlineKeyboardButton("↩️↔️ 90°CW + H-Flip", callback_data="rot_rot90cw_fh"),
         InlineKeyboardButton("↪️↔️ 90°CCW + H-Flip", callback_data="rot_rot90ccw_fh")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(rows)


async def show_rotate_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return
    context.user_data["state"] = "rotate"
    await query.edit_message_text(
        "🔄 *Rotate / Flip*\n\nAmalni tanlang:",
        reply_markup=rotate_keyboard(),
        parse_mode="Markdown",
    )


async def handle_rotate_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, op_key: str):
    query = update.callback_query
    await query.answer()

    if op_key not in OPERATIONS:
        await query.answer("❌ Noto'g'ri amal", show_alert=True)
        return

    label, vf_args = OPERATIONS[op_key]
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    output_path = make_temp_path("mp4")
    threads = _thread_count()

    args = [
        "-i", video_path,
        "-threads", threads,
    ] + vf_args + [
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    status_msg = await query.message.reply_text(
        f"⚙️ *{label} bajarilmoqda...*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {label} bajarilmoqda...")

    ok, err = await run_ffmpeg_async(
        args, status_msg, label=label, input_path=video_path,
    )

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_{op_key}.mp4"
        from utils.sender import send_file
        await send_file(query.message, output_path, out_name,
                        f"✅ {label}", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
