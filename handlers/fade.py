import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import run_ffmpeg_async, make_temp_path, get_video_duration, _thread_count


def fade_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⬛→🎬 Fade In (1s)",  callback_data="fade_in_1"),
         InlineKeyboardButton("⬛→🎬 Fade In (2s)",  callback_data="fade_in_2")],
        [InlineKeyboardButton("⬛→🎬 Fade In (3s)",  callback_data="fade_in_3"),
         InlineKeyboardButton("⬛→🎬 Fade In (5s)",  callback_data="fade_in_5")],
        [InlineKeyboardButton("🎬→⬛ Fade Out (1s)", callback_data="fade_out_1"),
         InlineKeyboardButton("🎬→⬛ Fade Out (2s)", callback_data="fade_out_2")],
        [InlineKeyboardButton("🎬→⬛ Fade Out (3s)", callback_data="fade_out_3"),
         InlineKeyboardButton("🎬→⬛ Fade Out (5s)", callback_data="fade_out_5")],
        [InlineKeyboardButton("✨ Fade In + Out (1s ikkisi)", callback_data="fade_both_1"),
         InlineKeyboardButton("✨ Fade In + Out (2s ikkisi)", callback_data="fade_both_2")],
        [InlineKeyboardButton("✨ Fade In + Out (3s ikkisi)", callback_data="fade_both_3")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(rows)


async def show_fade_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return
    context.user_data["state"] = "fade"
    await query.edit_message_text(
        "✨ *Fade Effekti*\n\n"
        "Qaysi effektni qo'shmoqchisiz?\n"
        "_(Video va audio bir vaqtda fade bo'ladi)_",
        reply_markup=fade_keyboard(),
        parse_mode="Markdown",
    )


async def handle_fade_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    fade_type: str, dur: int,
):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    total = get_video_duration(video_path)
    if total <= 0:
        await query.edit_message_text("❌ Video davomiyligini aniqlab bo'lmadi.")
        return

    if dur >= total:
        await query.answer(f"❌ Video {total:.0f}s, fade {dur}s — juda qisqa video!", show_alert=True)
        return

    # Build video/audio filter expressions
    if fade_type == "in":
        vf = f"fade=t=in:st=0:d={dur}"
        af = f"afade=t=in:st=0:d={dur}"
        label = f"Fade In {dur}s"
    elif fade_type == "out":
        st = max(0, total - dur)
        vf = f"fade=t=out:st={st:.3f}:d={dur}"
        af = f"afade=t=out:st={st:.3f}:d={dur}"
        label = f"Fade Out {dur}s"
    else:  # both
        st_out = max(0, total - dur)
        vf = f"fade=t=in:st=0:d={dur},fade=t=out:st={st_out:.3f}:d={dur}"
        af = f"afade=t=in:st=0:d={dur},afade=t=out:st={st_out:.3f}:d={dur}"
        label = f"Fade In+Out {dur}s"

    output_path = make_temp_path("mp4")
    threads = _thread_count()

    args = [
        "-i", video_path,
        "-threads", threads,
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    status_msg = await query.message.reply_text(
        f"⚙️ *{label} qo'shilmoqda...*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {label} effekti qo'shilmoqda...")

    ok, err = await run_ffmpeg_async(
        args, status_msg, label=label, input_path=video_path,
    )

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_{fade_type}_fade{dur}s.mp4"
        from utils.sender import send_file
        await send_file(query.message, output_path, out_name,
                        f"✅ {label}", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
