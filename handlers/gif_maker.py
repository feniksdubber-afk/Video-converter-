import os
import asyncio
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import make_temp_path, get_video_duration, _thread_count


# GIF presets: (label, fps, width, description)
GIF_PRESETS = {
    "low":    ("🔴 Engil (240p, 8fps)",   8,  320, "Kichik hajm"),
    "medium": ("🟡 O'rtacha (480p, 12fps)", 12, 480, "Yaxshi sifat"),
    "high":   ("🟢 Yuqori (720p, 15fps)", 15, 720, "Eng yaxshi"),
}

# Duration presets in seconds
DURATION_PRESETS = {
    "5":  "5 soniya",
    "10": "10 soniya",
    "15": "15 soniya",
    "30": "30 soniya",
    "full": "To'liq video",
}


def gif_quality_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, (label, fps, w, desc) in GIF_PRESETS.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"gif_q_{key}")])
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def gif_duration_keyboard(quality_key: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("5 soniya",   callback_data=f"gif_d_{quality_key}_5"),
         InlineKeyboardButton("10 soniya",  callback_data=f"gif_d_{quality_key}_10")],
        [InlineKeyboardButton("15 soniya",  callback_data=f"gif_d_{quality_key}_15"),
         InlineKeyboardButton("30 soniya",  callback_data=f"gif_d_{quality_key}_30")],
        [InlineKeyboardButton("⏱ To'liq video (max 60s)", callback_data=f"gif_d_{quality_key}_full")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="gif_maker")],
    ]
    return InlineKeyboardMarkup(rows)


async def show_gif_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return
    context.user_data["state"] = "gif_maker"

    dur = get_video_duration(video_path)
    dur_str = f"{int(dur)}s" if dur > 0 else "?"

    await query.edit_message_text(
        f"🎨 *GIF Yaratish*\n\n"
        f"📹 Video davomiyligi: `{dur_str}`\n\n"
        f"GIF sifatini tanlang:",
        reply_markup=gif_quality_keyboard(),
        parse_mode="Markdown",
    )


async def handle_gif_quality(update: Update, context: ContextTypes.DEFAULT_TYPE, quality_key: str):
    query = update.callback_query
    await query.answer()

    if quality_key not in GIF_PRESETS:
        await query.answer("❌ Noto'g'ri sifat", show_alert=True)
        return

    label, fps, width, desc = GIF_PRESETS[quality_key]
    context.user_data["gif_quality"] = quality_key

    await query.edit_message_text(
        f"🎨 *GIF Yaratish* — {label}\n\n"
        f"📐 Kenglik: `{width}px` | 🎞 FPS: `{fps}`\n\n"
        f"Qancha davomiylikda GIF kerak?",
        reply_markup=gif_duration_keyboard(quality_key),
        parse_mode="Markdown",
    )


async def handle_gif_duration(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    quality_key: str, duration_key: str,
):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    if quality_key not in GIF_PRESETS:
        await query.edit_message_text("❌ Noto'g'ri sifat.")
        return

    label, fps, width, desc = GIF_PRESETS[quality_key]

    # Duration
    total_dur = get_video_duration(video_path)
    if duration_key == "full":
        clip_dur = min(total_dur, 60)
    else:
        clip_dur = min(float(duration_key), total_dur)

    if clip_dur <= 0:
        await query.edit_message_text("❌ Video davomiyligi nol.")
        return

    palette_path = make_temp_path("png")
    output_path = make_temp_path("gif")

    status_msg = await query.message.reply_text(
        f"🎨 *GIF tayyorlanmoqda...*\n\n"
        f"📐 {width}px | 🎞 {fps}fps | ⏱ {clip_dur:.0f}s\n\n"
        "`[░░░░░░░░░░░░]` Palitra yaratilmoqda...",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ GIF yaratilmoqda ({clip_dur:.0f}s)...")

    try:
        # Pass 1: palette generation
        pass1 = [
            "ffmpeg", "-y",
            "-ss", "0", "-t", str(clip_dur),
            "-i", video_path,
            "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen=stats_mode=diff",
            palette_path,
        ]
        r1 = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(pass1, capture_output=True, text=True, timeout=120),
        )
        if r1.returncode != 0:
            raise RuntimeError(r1.stderr[-1000:])

        try:
            await status_msg.edit_text(
                f"🎨 *GIF tayyorlanmoqda...*\n\n"
                f"📐 {width}px | 🎞 {fps}fps | ⏱ {clip_dur:.0f}s\n\n"
                "`[██████░░░░░░]` GIF render qilinmoqda...",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        # Pass 2: GIF render
        pass2 = [
            "ffmpeg", "-y",
            "-ss", "0", "-t", str(clip_dur),
            "-i", video_path,
            "-i", palette_path,
            "-filter_complex",
            f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5",
            output_path,
        ]
        r2 = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(pass2, capture_output=True, text=True, timeout=300),
        )
        if r2.returncode != 0:
            raise RuntimeError(r2.stderr[-1000:])

        if os.path.exists(palette_path):
            os.remove(palette_path)

        gif_size = os.path.getsize(output_path)
        size_str = f"{gif_size/1024/1024:.1f} MB" if gif_size > 1024*1024 else f"{gif_size/1024:.0f} KB"

        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_{width}p_{fps}fps.gif"

        await status_msg.edit_text(
            f"✅ *GIF tayyor!*\n\n"
            f"📐 {width}px | 🎞 {fps}fps | ⏱ {clip_dur:.0f}s\n"
            f"📦 Hajmi: `{size_str}`\n\n📤 Yuborilmoqda...",
            parse_mode="Markdown",
        )
        from utils.sender import send_file
        await send_file(query.message, output_path, out_name,
                        f"✅ GIF | {width}p {fps}fps {clip_dur:.0f}s", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())

    except Exception as e:
        for p in [palette_path, output_path]:
            if os.path.exists(p):
                os.remove(p)
        await status_msg.edit_text(f"❌ GIF yaratishda xato:\n`{e}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
