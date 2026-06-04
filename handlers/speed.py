import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import run_ffmpeg_async, make_temp_path, get_video_info, _thread_count


SPEEDS = {
    "0.25": ("🐢 0.25×  (4× yavaş)", 0.25),
    "0.5":  ("🐌 0.5×   (2× yavaş)", 0.50),
    "0.75": ("🚶 0.75×  (yavaşroq)",  0.75),
    "1.25": ("🏃 1.25×  (tezroq)",    1.25),
    "1.5":  ("🚴 1.5×   (1.5× tez)",  1.50),
    "2.0":  ("🚗 2×     (ikki barobar tez)", 2.0),
    "4.0":  ("🚀 4×     (to'rt barobar tez)", 4.0),
}


def speed_keyboard() -> InlineKeyboardMarkup:
    rows = []
    items = list(SPEEDS.items())
    for i in range(0, len(items), 2):
        row = []
        for key, (label, _) in items[i:i+2]:
            row.append(InlineKeyboardButton(label, callback_data=f"spd_{key}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def _atempo_chain(speed: float) -> list[str]:
    """atempo filtrlari zanjirini qaytaradi (0.5–2.0 chegarasi uchun)."""
    filters = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return filters


async def show_speed_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return
    context.user_data["state"] = "speed"
    await query.edit_message_text(
        "🚀 *Tezlik O'zgartirish*\n\n"
        "Video tezligini tanlang:\n"
        "_(Audio ham avtomatik moslanadi)_",
        reply_markup=speed_keyboard(),
        parse_mode="Markdown",
    )


async def handle_speed_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, speed_key: str):
    query = update.callback_query
    await query.answer()

    if speed_key not in SPEEDS:
        await query.answer("❌ Noto'g'ri tezlik", show_alert=True)
        return

    label, speed = SPEEDS[speed_key]
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    info = get_video_info(video_path)
    has_video = bool(info)

    output_path = make_temp_path("mp4")
    threads = _thread_count()

    # video filter: setpts
    vf = f"setpts={1/speed:.6f}*PTS"
    # audio filter chain
    af = ",".join(_atempo_chain(speed))

    args = [
        "-i", video_path,
        "-threads", threads,
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    status_msg = await query.message.reply_text(
        f"⚙️ *Tezlik o'zgartirilmoqda → {label}*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {label} tezlikka o'tkazilmoqda...")

    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label=f"Tezlik: {label}",
        input_path=video_path,
    )

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_{speed_key}x.mp4"
        from utils.sender import send_file
        await send_file(query.message, output_path, out_name,
                        f"✅ Tezlik: {label}", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
