"""
r2_browser.py — R2 dagi fayllarni botdan boshqarish.
Fayl kalitlari uzunligi muammosini hal qilish uchun indekslash usuli qo'llanildi.
"""

import os
import html
import uuid
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.r2_manager import list_files, delete_file, rename_file, generate_presigned_url, get_public_url, is_configured, fmt_size

PAGE_SIZE = 8

def _file_keyboard(index: int) -> InlineKeyboardMarkup:
    """Fayl uchun tugmalar (indeks asosida)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Havola",     callback_data=f"r2_link_{index}"),
            InlineKeyboardButton("✏️ Rename",     callback_data=f"r2_rename_{index}"),
        ],
        [
            InlineKeyboardButton("🗑 O'chirish",  callback_data=f"r2_del_confirm_{index}"),
        ],
        [InlineKeyboardButton("🔙 Ro'yxatga",    callback_data="r2_list_0")],
    ])

def _list_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    """Fayllar ro'yxati uchun tugmalar."""
    rows = []
    return InlineKeyboardMarkup(rows)

async def _get_file_list_ui(query, context, page: int):
    """Ro'yxatni yangilash va ko'rsatish funksiyasi."""
    all_items = await list_files(max_keys=200)
    if context is not None:
        context.user_data["r2_files"] = all_items
    total = len(all_items)

    if total == 0:
        return "📭 R2 da hech qanday fayl yo'q.", None

    start = page * PAGE_SIZE
    page_items = all_items[start:start + PAGE_SIZE]

    rows = []
    for i, item in enumerate(page_items):
        idx = start + i
        name = os.path.basename(item["key"])
        if len(name) > 28:
            name = name[:25] + "..."
        rows.append([InlineKeyboardButton(f"📄 {name} ({item['size_str']})", callback_data=f"r2_info_{idx}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Oldingi", callback_data=f"r2_list_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Keyingi ▶️", callback_data=f"r2_list_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="r2_list_0")])

    text = (
        f"☁️ <b>R2 Fayl Menejer</b> — sahifa {page + 1}/{(total - 1) // PAGE_SIZE + 1}\n"
        f"Jami: <b>{total}</b> fayl\n\n"
        f"Fayl ustiga bosing:"
    )
    return text, InlineKeyboardMarkup(rows)

async def r2_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_configured():
        await update.message.reply_text("❌ R2 sozlanmagan.")
        return
    text, kb = await _get_file_list_ui(update.message, context, 0)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def r2_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    files = context.user_data.get("r2_files", [])

    if data.startswith("r2_list_"):
        page = int(data.split("_")[-1])
        text, kb = await _get_file_list_ui(query, context, page)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await query.answer()

    elif data.startswith("r2_info_"):
        idx = int(data.split("_")[-1])
        if not files:
            files = await list_files(max_keys=200)
            context.user_data["r2_files"] = files
        if idx >= len(files):
            await query.answer("❌ Fayl topilmadi, ro'yxatni yangilang")
            return
        key = files[idx]["key"]
        name = os.path.basename(key)
        url = get_public_url(key)
        text = (
            f"📄 <b>{html.escape(name)}</b>\n\n"
            f"🗂 Kalit: <code>{html.escape(key)}</code>\n"
            f"🔗 URL: {html.escape(url)}\n\n"
            f"Nima qilmoqchisiz?"
        )
        await query.edit_message_text(text, reply_markup=_file_keyboard(idx), parse_mode="HTML")
        await query.answer()

    elif data.startswith("r2_link_"):
        idx = int(data.split("_")[-1])
        if not files:
            files = await list_files(max_keys=200)
            context.user_data["r2_files"] = files
        if idx >= len(files):
            await query.answer("❌ Fayl topilmadi, ro'yxatni yangilang")
            return
        key = files[idx]["key"]
        url = get_public_url(key)
        presigned = await generate_presigned_url(key, expires=86400)
        text = (
            f"🔗 <b>{html.escape(os.path.basename(key))}</b> havolasi:\n\n"
            f"<b>Public URL:</b>\n<code>{html.escape(url)}</code>\n\n"
            f"<b>Vaqtinchalik havola (24 soat):</b>\n<code>{html.escape(presigned or 'Xato')}</code>"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=f"r2_info_{idx}")]]),
            parse_mode="HTML"
        )
        await query.answer()

    elif data.startswith("r2_del_confirm_"):
        idx = int(data.split("_")[-1])
        if not files:
            files = await list_files(max_keys=200)
            context.user_data["r2_files"] = files
        if idx >= len(files):
            await query.answer("❌ Fayl topilmadi, ro'yxatni yangilang")
            return
        key = files[idx]["key"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Ha, o'chir", callback_data=f"r2_del_do_{idx}"),
             InlineKeyboardButton("❌ Bekor", callback_data=f"r2_info_{idx}")]
        ])
        await query.edit_message_text(
            f"⚠️ <b>{html.escape(os.path.basename(key))}</b> ni o'chirishga ishonchingiz komilmi?",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await query.answer()

    elif data.startswith("r2_del_do_"):
        idx = int(data.split("_")[-1])
        if not files:
            files = await list_files(max_keys=200)
            context.user_data["r2_files"] = files
        if idx >= len(files):
            await query.answer("❌ Fayl topilmadi, ro'yxatni yangilang")
            return
        key = files[idx]["key"]
        if await delete_file(key):
            await query.edit_message_text(
                "✅ O'chirildi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ro'yxatga", callback_data="r2_list_0")]]),
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                "❌ Xato yuz berdi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=f"r2_info_{idx}")]]),
                parse_mode="HTML"
            )
        await query.answer()

    elif data.startswith("r2_rename_"):
        idx = int(data.split("_")[-1])
        if not files:
            files = await list_files(max_keys=200)
            context.user_data["r2_files"] = files
        if idx >= len(files):
            await query.answer("❌ Fayl topilmadi, ro'yxatni yangilang")
            return
        context.user_data["r2_rename_key"] = files[idx]["key"]
        await query.edit_message_text("✏️ Yangi nom kiriting:", parse_mode="HTML")
        await query.answer()

    elif data.startswith("r2_send_tg__"):
        # sender.py da saqlangan short_key orqali faylni Telegram ga yuborish
        from utils.sender import _r2_pending, PYROGRAM_LIMIT
        short_key = data[len("r2_send_tg__"):]
        entry = _r2_pending.get(short_key)
        if not entry:
            await query.answer("❌ Fayl topilmadi yoki muddati o'tgan. Qaytadan yuklang.", show_alert=True)
            return
        await query.answer("📤 Yuborilmoqda...")
        filename = entry["filename"]
        file_path = entry.get("file_path", "")
        url = entry["url"]
        # Agar local fayl mavjud bo'lsa — Pyrogram orqali yubor
        if file_path and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            from utils.sender import send_file
            await send_file(query.message, file_path, filename, f"📥 {filename}", context=context)
        else:
            # Local fayl yo'q — faqat havola yuboramiz
            await query.message.reply_text(
                f"⚠️ Fayl serverda saqlanmagan (ehtimol o'chirilgan).\n\n"
                f"🔗 R2 havolasi:\n`{url}`",
                parse_mode="Markdown",
            )

async def _show_r2_list_cb(query, context=None, page: int = 0):
    """Callback query orqali R2 fayl ro'yxatini ko'rsatish."""
    text, kb = await _get_file_list_ui(query, context, page)
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def r2_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_key = context.user_data.get("r2_rename_key")
    if not old_key: return False
    new_name = update.message.text.strip()
    dir_part = os.path.dirname(old_key)
    new_key = os.path.join(dir_part, new_name).lstrip("/")
    if await rename_file(old_key, new_key):
        await update.message.reply_text("✅ Muvaffaqiyatli nomlandi.")
    else:
        await update.message.reply_text("❌ Xato.")
    context.user_data.pop("r2_rename_key", None)
    return True
