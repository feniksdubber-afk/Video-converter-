import os
import glob
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from utils.ffmpeg_utils import run_ffmpeg_async, make_temp_path, _thread_count


POSITIONS = {
    "center":       ("⬛ Markazga",           "(w-text_w)/2:(h-text_h)/2"),
    "bottom_center":("⬇️ Pastga markazga",    "(w-text_w)/2:h-th-20"),
    "bottom_right": ("↘️ Past o'ng burchak",  "w-tw-20:h-th-20"),
    "bottom_left":  ("↙️ Past chap burchak",  "20:h-th-20"),
    "top_center":   ("⬆️ Tepaga markazga",    "(w-text_w)/2:20"),
    "top_right":    ("↗️ Yuqori o'ng burchak","w-tw-20:20"),
    "top_left":     ("↖️ Yuqori chap burchak","20:20"),
}

STYLES = {
    "white_shadow": ("⬜ Oq + soya",     "fontcolor=white", "shadowcolor=black@0.8:shadowx=2:shadowy=2"),
    "white_box":    ("⬜📦 Oq + quti",   "fontcolor=white", "box=1:boxcolor=black@0.5:boxborderw=8"),
    "yellow_bold":  ("🟡 Sariq qalin",   "fontcolor=yellow","shadowcolor=black@0.9:shadowx=3:shadowy=3"),
    "red_bold":     ("🔴 Qizil qalin",   "fontcolor=red",   "shadowcolor=black@0.9:shadowx=3:shadowy=3"),
    "black_box":    ("⬛📦 Qora + quti", "fontcolor=black", "box=1:boxcolor=white@0.7:boxborderw=8"),
}


def _find_font() -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    candidates += glob.glob("/nix/store/*/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    candidates += glob.glob("/nix/store/*/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    candidates += glob.glob("/nix/store/*/share/fonts/*/DejaVuSans*.ttf")
    for c in candidates:
        if os.path.exists(c):
            return c
    return ""


def position_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("↖️ Yuqori chap", callback_data="wm_pos_top_left"),
         InlineKeyboardButton("⬆️ Yuqori markaz", callback_data="wm_pos_top_center"),
         InlineKeyboardButton("↗️ Yuqori o'ng", callback_data="wm_pos_top_right")],
        [InlineKeyboardButton("⬛ Markaz", callback_data="wm_pos_center")],
        [InlineKeyboardButton("↙️ Past chap", callback_data="wm_pos_bottom_left"),
         InlineKeyboardButton("⬇️ Past markaz", callback_data="wm_pos_bottom_center"),
         InlineKeyboardButton("↘️ Past o'ng", callback_data="wm_pos_bottom_right")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(rows)


def style_keyboard(pos_key: str) -> InlineKeyboardMarkup:
    rows = []
    for skey, (slabel, *_) in STYLES.items():
        rows.append([InlineKeyboardButton(slabel, callback_data=f"wm_style_{pos_key}_{skey}")])
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="watermark")])
    return InlineKeyboardMarkup(rows)


def size_keyboard(pos_key: str, style_key: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔡 Kichik (24px)",   callback_data=f"wm_size_{pos_key}_{style_key}_24"),
         InlineKeyboardButton("🔠 O'rtacha (36px)", callback_data=f"wm_size_{pos_key}_{style_key}_36")],
        [InlineKeyboardButton("🔆 Katta (48px)",    callback_data=f"wm_size_{pos_key}_{style_key}_48"),
         InlineKeyboardButton("💥 Juda katta (64px)", callback_data=f"wm_size_{pos_key}_{style_key}_64")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data=f"wm_pos_{pos_key}")],
    ]
    return InlineKeyboardMarkup(rows)


async def show_watermark_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return
    context.user_data["state"] = "watermark_text"
    await query.edit_message_text(
        "💧 *Watermark (Suv belgisi)*\n\n"
        "Watermark matnini kiriting:\n\n"
        "_Masalan: `© MyChannel`, `CONFIDENTIAL`, `@username`_",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_watermark_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Matn bo'sh bo'lmasligi kerak.", reply_markup=cancel_keyboard())
        return

    context.user_data["watermark_text"] = text
    context.user_data["state"] = "watermark_pos"

    await update.message.reply_text(
        f"💧 *Watermark: `{text}`*\n\n"
        f"Qayerga joylashtiramiz?",
        reply_markup=position_keyboard(),
        parse_mode="Markdown",
    )


async def handle_watermark_pos(update: Update, context: ContextTypes.DEFAULT_TYPE, pos_key: str):
    query = update.callback_query
    await query.answer()

    if pos_key not in POSITIONS:
        await query.answer("❌ Noto'g'ri pozitsiya", show_alert=True)
        return

    context.user_data["watermark_pos"] = pos_key
    pos_label, _ = POSITIONS[pos_key]
    wm_text = context.user_data.get("watermark_text", "")

    await query.edit_message_text(
        f"💧 *Watermark: `{wm_text}`*\n"
        f"📍 Pozitsiya: {pos_label}\n\n"
        f"Uslubni tanlang:",
        reply_markup=style_keyboard(pos_key),
        parse_mode="Markdown",
    )


async def handle_watermark_style(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    pos_key: str, style_key: str,
):
    query = update.callback_query
    await query.answer()

    if pos_key not in POSITIONS or style_key not in STYLES:
        await query.answer("❌ Noto'g'ri tanlov", show_alert=True)
        return

    context.user_data["watermark_style"] = style_key
    pos_label, _ = POSITIONS[pos_key]
    style_label, *_ = STYLES[style_key]
    wm_text = context.user_data.get("watermark_text", "")

    await query.edit_message_text(
        f"💧 *Watermark: `{wm_text}`*\n"
        f"📍 {pos_label} | 🎨 {style_label}\n\n"
        f"Matn o'lchamini tanlang:",
        reply_markup=size_keyboard(pos_key, style_key),
        parse_mode="Markdown",
    )


async def handle_watermark_size(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    pos_key: str, style_key: str, font_size: int,
):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    wm_text = context.user_data.get("watermark_text", "")
    if not video_path or not os.path.exists(video_path) or not wm_text:
        await query.edit_message_text("❌ Kerakli ma'lumotlar topilmadi.")
        return

    pos_label, xy_expr = POSITIONS.get(pos_key, ("?", "10:10"))
    style_label, color_arg, extra_arg = STYLES.get(style_key, ("?", "fontcolor=white", ""))

    font_path = _find_font()
    font_part = f":fontfile='{font_path}'" if font_path else ""

    # Escape text for ffmpeg drawtext
    safe_text = (wm_text
                 .replace("\\", "\\\\")
                 .replace("'", "\\'")
                 .replace(":", "\\:")
                 .replace("%", "\\%"))

    drawtext = (
        f"drawtext=text='{safe_text}'"
        f"{font_part}"
        f":fontsize={font_size}"
        f":{color_arg}"
        f":{extra_arg}"
        f":x={xy_expr.split(':')[0]}"
        f":y={xy_expr.split(':')[1]}"
        f":alpha=0.9"
    )

    output_path = make_temp_path("mp4")
    threads = _thread_count()

    args = [
        "-i", video_path,
        "-threads", threads,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    status_msg = await query.message.reply_text(
        f"💧 *Watermark qo'shilmoqda...*\n\n"
        f"📝 `{wm_text}` | 📍 {pos_label}\n\n"
        "`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ Watermark qo'shilmoqda...")

    ok, err = await run_ffmpeg_async(
        args, status_msg, label="Watermark qo'shilmoqda", input_path=video_path,
    )

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_watermark.mp4"
        from utils.sender import send_file
        await send_file(query.message, output_path, out_name,
                        f"✅ Watermark: `{wm_text}`", context=context)
        os.remove(output_path)
        context.user_data["watermark_text"] = None
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
