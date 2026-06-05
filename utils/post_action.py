"""
post_action.py — Amal tugagandan keyin chiqadigan menyu.

Foydalanuvchiga ikkita tanlov beradi:
  1. 📤 Videoni yuborish  → faylni yuboradi, keyin tarix tanlash (agar >1 versiya)
  2. ✏️ Boshqa amal      → main_menu_keyboard() ko'rsatadi (yubormasdan)
"""

import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from utils.video_history import get_history, get_current_index, cleanup_except_current


def post_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Videoni yuborish", callback_data="pa_send"),
            InlineKeyboardButton("✏️ Boshqa amal",     callback_data="pa_continue"),
        ]
    ])


def version_select_keyboard(context) -> InlineKeyboardMarkup:
    """Tarixdagi versiyalar ro'yxatini tugmalar sifatida ko'rsatadi."""
    history = get_history(context)
    current = get_current_index(context)
    rows = []
    for i, entry in enumerate(history):
        mark = "✅ " if i == current else ""
        rows.append([InlineKeyboardButton(
            f"{mark}{entry['label']} — {entry['name']}",
            callback_data=f"pa_switch_{i}"
        )])
    rows.append([InlineKeyboardButton("📤 Shu versiyani yuborish", callback_data="pa_send")])
    rows.append([InlineKeyboardButton("✏️ Boshqa amal",           callback_data="pa_continue")])
    return InlineKeyboardMarkup(rows)


async def ask_post_action(message: Message, context, result_label: str = ""):
    """
    Amal tugagandan keyin chaqiriladi.
    Agar >1 versiya bo'lsa — versiyalar menyusini ham ko'rsatadi.
    """
    history = get_history(context)

    if len(history) > 1:
        lines = [f"✅ *{result_label}* bajarildi!\n"] if result_label else ["✅ *Amal bajarildi!*\n"]
        lines.append("📂 *Versiyalarni tanlang:*")
        current = get_current_index(context)
        for i, entry in enumerate(history):
            mark = "▶️" if i == current else "   "
            lines.append(f"{mark} `{i+1}.` {entry['label']} — _{entry['name']}_")
        lines.append("\nQaysi versiyada ishlaysiz yoki yuborasiz?")

        await message.reply_text(
            "\n".join(lines),
            reply_markup=version_select_keyboard(context),
            parse_mode="Markdown",
        )
    else:
        await message.reply_text(
            f"✅ *{result_label}* bajarildi!\n\nNima qilamiz?" if result_label
            else "✅ Amal bajarildi!\n\nNima qilamiz?",
            reply_markup=post_action_keyboard(),
            parse_mode="Markdown",
        )


async def handle_pa_send(update, context):
    """📤 Videoni yuborish bosilganda."""
    from utils.sender import send_file
    from utils.keyboards import main_menu_keyboard

    query = update.callback_query
    await query.answer()

    path = context.user_data.get("video_path")
    name = context.user_data.get("video_name", "video.mp4")

    if not path or not os.path.exists(path):
        await query.edit_message_text("❌ Fayl topilmadi. Qaytadan video yuboring.")
        return

    await query.edit_message_text("📤 *Yuborilmoqda...*", parse_mode="Markdown")

    await send_file(query.message, path, name, f"✅ {name}", context=context)

    # Yuborilgandan keyin hamma narsani tozala
    cleanup_except_current(context)

    await query.message.reply_text(
        "Yangi video yuboring yoki /start bosing.",
        reply_markup=None,
    )

    # Holatni tozalash
    context.user_data.pop("video_path", None)
    context.user_data.pop("video_name", None)
    context.user_data.pop("video_history", None)
    context.user_data.pop("video_history_index", None)


async def handle_pa_continue(update, context):
    """✏️ Boshqa amal bosilganda."""
    from utils.keyboards import main_menu_keyboard

    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Quyidagi amallardan birini tanlang:",
        reply_markup=main_menu_keyboard(),
    )


async def handle_pa_switch(update, context, index: int):
    """Versiya tanlanganda."""
    from utils.video_history import switch_to

    query = update.callback_query
    await query.answer()

    if switch_to(context, index):
        history = get_history(context)
        entry = history[index]
        await query.edit_message_text(
            f"✅ *{entry['label']}* tanlandi.\n_{entry['name']}_\n\nNima qilamiz?",
            reply_markup=version_select_keyboard(context),
            parse_mode="Markdown",
        )
    else:
        await query.answer("❌ Bu versiya fayli topilmadi.", show_alert=True)
