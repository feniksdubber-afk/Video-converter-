import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import TEMP_DIR
from utils.user_settings import ensure_loaded, get, set_, reset, summary
from utils.keyboards import main_menu_keyboard


def _settings_keyboard(context):
    upload = get(context, "upload_mode")
    rename = get(context, "rename_file")
    upload_label = {"document": "📄 Dokument", "video": "🎬 Video", "audio": "🎵 Audio"}.get(upload, upload)

    keyboard = [
        [InlineKeyboardButton(f"📤 Upload rejimi: {upload_label}", callback_data="cfg_upload_cycle")],
        [InlineKeyboardButton(
            "✏️ Fayl nomini o'zgartirish: " + ("✅ Yoqiq" if rename else "❌ O'chiq"),
            callback_data="cfg_rename_toggle",
        )],
        [InlineKeyboardButton("🖼 Custom Thumbnail o'rnatish",  callback_data="cfg_thumb_set")],
        [InlineKeyboardButton("🖼 Custom Thumbnail o'chirish",  callback_data="cfg_thumb_clear")],
        [InlineKeyboardButton("🎬 Sample davomiyligi",          callback_data="cfg_sample_dur")],
        [InlineKeyboardButton("✂️ Split davomiyligi",           callback_data="cfg_split_dur")],
        [InlineKeyboardButton("🔄 Sozlamalarni tiklash",        callback_data="cfg_reset")],
        [InlineKeyboardButton("❌ Yopish",                      callback_data="cfg_close")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = (update.message or update.callback_query).from_user.id
    await ensure_loaded(user_id, context)
    text = summary(context)
    if update.message:
        await update.message.reply_text(text, reply_markup=_settings_keyboard(context), parse_mode="Markdown")
    else:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text, reply_markup=_settings_keyboard(context), parse_mode="Markdown")


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await ensure_loaded(user_id, context)
    data = query.data

    if data == "cfg_upload_cycle":
        modes = ["document", "video", "audio"]
        current = get(context, "upload_mode")
        next_mode = modes[(modes.index(current) + 1) % len(modes)]
        await set_(user_id, context, "upload_mode", next_mode)
        await query.answer(f"Upload rejimi: {next_mode}")
        await query.edit_message_text(summary(context), reply_markup=_settings_keyboard(context), parse_mode="Markdown")

    elif data == "cfg_rename_toggle":
        current = get(context, "rename_file")
        await set_(user_id, context, "rename_file", not current)
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
        await set_(user_id, context, "custom_thumbnail", None)
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
        await reset(user_id, context)
        await query.answer("Sozlamalar tiklandi ✅")
        await query.edit_message_text(summary(context), reply_markup=_settings_keyboard(context), parse_mode="Markdown")

    elif data == "cfg_close":
        await query.answer()
        await query.edit_message_text(
            "⚙️ Sozlamalar yopildi.",
            reply_markup=main_menu_keyboard() if context.user_data.get("video_path") else None,
        )


async def handle_settings_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await ensure_loaded(user_id, context)
    state = context.user_data.get("state")
    text = update.message.text.strip()

    if state == "settings_sample_dur":
        if text.isdigit() and 5 <= int(text) <= 300:
            await set_(user_id, context, "sample_duration", int(text))
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
            await set_(user_id, context, "split_duration", int(text))
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
        return False

    user_id = update.message.from_user.id
    await ensure_loaded(user_id, context)

    photo = update.message.photo[-1] if update.message.photo else None
    doc   = update.message.document

    tg_file = None
    if photo:
        tg_file = await photo.get_file()
    elif doc and doc.mime_type and doc.mime_type.startswith("image/"):
        tg_file = await doc.get_file()

    if tg_file:
        import uuid
        # Rasmni darhol diskka saqlaymiz (file_id eskirib qolishi mumkin)
        thumb_dir = os.path.join(TEMP_DIR, "thumbnails")
        os.makedirs(thumb_dir, exist_ok=True)

        # Oldingi thumbnailni o'chirish
        old_path = get(context, "custom_thumbnail")
        if old_path and isinstance(old_path, str) and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

        thumb_path = os.path.join(thumb_dir, f"thumb_{user_id}_{uuid.uuid4().hex}.jpg")
        await tg_file.download_to_drive(thumb_path)

        await set_(user_id, context, "custom_thumbnail", thumb_path)
        context.user_data["state"] = None
        await update.message.reply_text(
            "✅ Custom thumbnail o'rnatildi! Keyingi fayllar uchun ishlatiladi.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await update.message.reply_text("❌ Rasm yuboring (JPEG yoki PNG).")
    return True
