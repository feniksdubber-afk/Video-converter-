import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from utils.sender import send_file
from utils.ffmpeg_utils import hardsub_video_async
from config import TEMP_DIR

SUPPORTED_FORMATS = {".srt", ".ass", ".ssa"}

FONT_SIZES = {
    "small":  ("🔡 Kichik (18px)",     18),
    "medium": ("🔠 O'rtacha (24px)",   24),
    "large":  ("🔆 Katta (32px)",      32),
    "xlarge": ("💥 Juda katta (42px)", 42),
}


def hardsub_size_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, (label, _) in FONT_SIZES.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"hs_size_{key}")])
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(rows)


async def show_hardsub_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return
    context.user_data["state"] = "hardsub_wait"
    await query.edit_message_text(
        "🔥 *Hardsub (Yoqilgan Subtitr)*\n\n"
        "Subtitr faylini yuboring:\n"
        "• `.srt` — oddiy subtitr (shrift o'lchami tanlanadi)\n"
        "• `.ass` / `.ssa` — o'z stiliga ega subtitr\n\n"
        "⚠️ Hardsub qayta kodlash talab qiladi — biroz vaqt oladi.",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_hardsub_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Fayl topilmadi.", reply_markup=cancel_keyboard())
        return

    file_name = doc.file_name or ""
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        await update.message.reply_text(
            "❌ Noto'g'ri format. Faqat `.srt`, `.ass`, `.ssa` qabul qilinadi.",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await update.message.reply_text("❌ Video topilmadi. Qaytadan video yuboring.")
        context.user_data["state"] = None
        return

    sub_path = os.path.join(TEMP_DIR, f"hs_{doc.file_unique_id}{ext}")
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(sub_path)
    context.user_data["hardsub_path"] = sub_path

    if ext in (".ass", ".ssa"):
        context.user_data["state"] = None
        await update.message.reply_text("⏳ ASS/SSA stili bilan hardsub qo'shilmoqda...")
        await _do_hardsub(update.message, context, sub_path, font_size=24, is_ass=True)
    else:
        context.user_data["state"] = "hardsub_size"
        await update.message.reply_text(
            "✅ SRT fayl qabul qilindi!\n\nShrift o'lchamini tanlang:",
            reply_markup=hardsub_size_keyboard(),
        )


async def handle_hardsub_size(update: Update, context: ContextTypes.DEFAULT_TYPE, size_key: str):
    query = update.callback_query
    await query.answer()

    if size_key not in FONT_SIZES:
        await query.answer("❌ Noto'g'ri tanlov", show_alert=True)
        return

    sub_path = context.user_data.get("hardsub_path")
    if not sub_path or not os.path.exists(sub_path):
        await query.edit_message_text("❌ Subtitr fayl topilmadi. Qaytadan boshlang.")
        return

    label, font_size = FONT_SIZES[size_key]
    context.user_data["state"] = None
    await query.edit_message_text(f"⏳ Hardsub qo'shilmoqda — {label}...")
    await _do_hardsub(query.message, context, sub_path, font_size=font_size, is_ass=False)


async def _do_hardsub(message, context, sub_path: str, font_size: int, is_ass: bool):
    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await message.reply_text("❌ Video topilmadi.")
        return

    status_msg = await message.reply_text(
        "🔥 *Hardsub qo'shilmoqda...*\n\n`[░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )

    ok, output_path, err = await hardsub_video_async(
        video_path, sub_path, font_size, status_msg, is_ass=is_ass,
    )

    if os.path.exists(sub_path):
        os.remove(sub_path)
    context.user_data.pop("hardsub_path", None)

    if ok and os.path.exists(output_path):
        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]
        out_name = f"{base}_hardsub.mp4"
        await send_file(message, output_path, out_name,
                        "✅ Hardsub muvaffaqiyatli qo'shildi!", context=context)
        os.remove(output_path)
        await message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status_msg.edit_text(f"❌ Xato:\n`{err}`", parse_mode="Markdown")
        await message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
