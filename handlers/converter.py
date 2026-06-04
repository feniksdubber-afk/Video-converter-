import os
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import format_keyboard, resolution_keyboard, main_menu_keyboard
from utils.ffmpeg_utils import convert_video, change_resolution, get_video_info
from utils.sender import send_file


async def show_convert_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "convert_format"
    await query.edit_message_text(
        "🎬 *Video Konvertor*\n\nQaysi formatga o'tkazmoqchisiz?",
        reply_markup=format_keyboard(), parse_mode="Markdown",
    )


async def show_resolution_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "change_resolution"
    video_path = context.user_data.get("video_path")
    info_text = ""
    if video_path:
        info = get_video_info(video_path)
        w, h = info.get("width", "?"), info.get("height", "?")
        info_text = f"\n📊 Hozirgi o'lcham: {w}x{h}"
    await query.edit_message_text(
        f"📐 *O'lcham O'zgartirish*{info_text}\n\nYangi o'lchamni tanlang:",
        reply_markup=resolution_keyboard(), parse_mode="Markdown",
    )


async def handle_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, fmt: str):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    await query.edit_message_text(
        f"⏳ *{fmt.upper()} formatiga o'tkazilmoqda...*\n\nKuting...", parse_mode="Markdown"
    )

    ok, output_path, err = convert_video(video_path, fmt)

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_converted.{fmt}"

        await query.message.reply_text("✅ Tayyor! Yuborilmoqda...")
        await send_file(query.message, output_path, out_name, f"✅ {fmt.upper()} formatiga o'tkazildi!")
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await query.message.reply_text(
            f"❌ Konvertatsiyada xato:\n`{err}`", reply_markup=main_menu_keyboard(), parse_mode="Markdown"
        )


async def handle_resolution_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, height: int):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    res_labels = {2160: "4K (2160p)", 1080: "1080p", 720: "720p", 480: "480p", 360: "360p", 240: "240p"}
    label = res_labels.get(height, f"{height}p")

    await query.edit_message_text(
        f"⏳ *{label} o'lchamiga o'zgartirilmoqda...*\n\nKuting...", parse_mode="Markdown"
    )

    ok, output_path, err = change_resolution(video_path, height)

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_{height}p.mp4"

        await query.message.reply_text("✅ Tayyor! Yuborilmoqda...")
        await send_file(query.message, output_path, out_name, f"✅ {label} o'lchamiga o'zgartirildi!")
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await query.message.reply_text(
            f"❌ Xato:\n`{err}`", reply_markup=main_menu_keyboard(), parse_mode="Markdown"
        )
