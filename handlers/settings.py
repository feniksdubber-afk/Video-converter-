import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.user_settings import get, set_, reset, summary
from utils.keyboards import main_menu_keyboard


def _settings_keyboard(context):
    upload = get(context, "upload_mode")
    rename = get(context, "rename_file")
    upload_label = {"document": "📄 Dokument", "video": "🎬 Video", "audio": "🎵 Audio"}.get(upload, upload)

    keyboard = [
        [InlineKeyboardButton(f"📤 Upload rejimi: {upload_label}", callback_data="cfg_upload_cycle")],
        [InlineKeyboardButton(
            f"✏️ Fayl nomini o'zgartirish: {'✅ Yoqiq' if rename else '❌ Óchiq'}",
            callback_data="cfg_rename_toggle",
        )],
        [InlineKeyboardButton("🖼 Custom Thumbnail o'rnatish", callback_data="cfg_thumb_set")],
        [InlineKeyboardButton("🖼 Custom Thumbnail o'chirish", callback_data="cfg_thumb_clear")],
        [InlineKeyboardButton("🎬 Sample davomiyligi", callback_data="cfg_sample_dur")],
        [InlineKeyboardButton("✂️ Split davomiyligi", callback_data="cfg_split_dur")],
        [InlineKeyboardButton("🔄 Sozlamalarni tiklash", callback_data="cfg_reset")],
        [InlineKeyboardButton("❌ Yopish", callback_data="cfg_close")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = summary(context)
    if update.message:
        await update.message.reply_text(text, reply_markup=_settings_keyboard(context), parse_mode="Markdown")
    else:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text, reply_markup=_settings_keyboard(context), parse_mode="Markdown")


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "cfg_upload_cycle":
        modes = ["document", "video", "audio"]
        current = get(context, "upload_mode")
        next_mode = modes[(modes.index(current) + 1) % len(modes)]
        set_(context, "upload_mode", next_mode)
        await query.answer(f"Upload rejimi: {next_mode}")
        await query.edit_message_text(summary(context), reply_markup=_settings_keyboard(context), parse_mode="Markdown")

    elif data == "cfg_rename_toggle":
        current = get(context, "rename_file")
        set_(context, "rename_file", not current)
        await query.answer("O'zgartirildi")
        await query.edit_message_text(summary(context), reply_markup=_settings_keyboard(context), parse_mode="Markdown")

    elif data == "cfg_thumb_set":
        await query.answer()
        context.user_data["state"] = "settings_thumb"
        await query.edit_message_text(
            "🖼 *Custom Thumbnail o'rnatish*\n\n"
            "Rasm yuboring (JPEG/PNG). Keyingi barcha fayllar uchun shu thumbnail ishlatiladi.",
            parse_mode="Markdown",
        )

    elif data == "cfg_thumb_clear":
        set_(context, "custom_thumbnail", None)
        await query.answer("Thumbnail o'chirildi")
        await query.edit_message_text(summary(context), reply_markup=_settings_keyboard(context), parse_mode="Markdown")

    elif data == "cfg_sample_dur":
        await query.answer()
        context.user_data["state"] = "settings_sample_dur"
        await query.edit_message_text(
            "🎬 *Sample davomiyligi*\n\nSoniyalarda kiriting (5-300):\nHozirgi: "
            f"`{get(context, 'sample_duration')}` soniya",
            parse_mode="Markdown",
        )

    elif data == "cfg_split_dur":
        await query.answer()
        context.user_data["state"] = "settings_split_dur"
        await query.edit_message_text(
            "✂️ *Split davomiyligi*\n\nHar bo'lak necha soniya bo'lsin (10-3600):\nHozirgi: "
            f"`{get(context, 'split_duration')}` soniya",
            parse_mode="Markdown",
        )

    elif data == "cfg_reset":
        reset(context)
        await query.answer("Sozlamalar tiklandi")
        await query.edit_message_text(summary(context), reply_markup=_settings_keyboard(context), parse_mode="Markdown")

    elif data == "cfg_close":
        await query.answer()
        await query.edit_message_text(
            "⚙️ Sozlamalar yopildi.",
            reply_markup=main_menu_keyboard() if context.user_data.get("video_path") else None,
        )


async def handle_settings_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = update.message.text.strip()

    if state == "settings_sample_dur":
        if text.isdigit() and 5 <= int(text) <= 300:
            set_(context, "sample_duration", int(text))
            context.user_data["state"] = None
            await update.message.reply_text(
                f"✅ Sample davomiyligi `{text}` soniyaga o'rnatildi.",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await update.message.reply_text("❌ 5 dan 300 gacha raqam kiriting.")

    elif state == "settings_split_dur":
        if text.isdigit() and 10 <= int(text) <= 3600:
            set_(context, "split_duration", int(text))
            context.user_data["state"] = None
            await update.message.reply_text(
                f"✅ Split davomiyligi `{text}` soniyaga o'rnatildi.",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await update.message.reply_text("❌ 10 dan 3600 gacha raqam kiriting.")


async def handle_settings_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Custom thumbnail uchun rasm qabul qilish."""
    if context.user_data.get("state") != "settings_thumb":
        return False  # bu handler uchun emas

    photo = update.message.photo[-1] if update.message.photo else None
    doc = update.message.document

    file_id = None
    if photo:
        file_id = photo.file_id
    elif doc and doc.mime_type and doc.mime_type.startswith("image/"):
        file_id = doc.file_id

    if file_id:
        set_(context, "custom_thumbnail", file_id)
        context.user_data["state"] = None
        await update.message.reply_text(
            "✅ Custom thumbnail o'rnatildi! Keyingi fayllar uchun ishlatiladi.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await update.message.reply_text("❌ Rasm yuboring (JPEG yoki PNG).")
    return True
