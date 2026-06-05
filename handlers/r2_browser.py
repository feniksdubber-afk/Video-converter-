"""
r2_browser.py — R2 dagi fayllarni botdan boshqarish.

Buyruqlar:
  /r2          — fayllar ro'yxati (oxirgi 30 ta)
  /r2_delete   — fayl o'chirish
  /r2_link     — public havola olish
  /r2_rename   — fayl nomini o'zgartirish

Callback datalar:
  r2_list_<page>
  r2_info_<key_b64>
  r2_del_confirm_<key_b64>
  r2_del_do_<key_b64>
  r2_link_<key_b64>
  r2_rename_<key_b64>
"""

import base64
import os
import uuid
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.r2_manager import list_files, delete_file, rename_file, generate_presigned_url, get_public_url, is_configured, fmt_size

PAGE_SIZE = 8


def _enc(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode()).decode()


def _dec(b64: str) -> str:
    return base64.urlsafe_b64decode(b64.encode()).decode()


def _file_keyboard(key: str) -> InlineKeyboardMarkup:
    kb = _enc(key)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Havola",     callback_data=f"r2_link_{kb}"),
            InlineKeyboardButton("✏️ Rename",     callback_data=f"r2_rename_{kb}"),
        ],
        [
            InlineKeyboardButton("🗑 O'chirish",  callback_data=f"r2_del_confirm_{kb}"),
        ],
        [InlineKeyboardButton("🔙 Ro'yxatga",    callback_data="r2_list_0")],
    ])


def _list_keyboard(items: list[dict], page: int, total: int) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        name = os.path.basename(item["key"])
        if len(name) > 28:
            name = name[:25] + "..."
        rows.append([InlineKeyboardButton(
            f"📄 {name} ({item['size_str']})",
            callback_data=f"r2_info_{_enc(item['key'])}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Oldingi", callback_data=f"r2_list_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Keyingi ▶️", callback_data=f"r2_list_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="r2_list_0")])
    return InlineKeyboardMarkup(rows)


async def r2_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_configured():
        await update.message.reply_text("❌ R2 sozlanmagan. .env da R2_* o'zgaruvchilarini tekshiring.")
        return
    await _show_list(update.message, context, page=0)


async def _show_list(message, context, page: int = 0):
    all_items = await list_files(max_keys=200)
    total = len(all_items)

    if total == 0:
        await message.reply_text("📭 R2 da hech qanday fayl yo'q.")
        return

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = all_items[start:end]

    text = (
        f"☁️ *R2 Fayl Menejer* — sahifa {page + 1}/{(total - 1) // PAGE_SIZE + 1}\n"
        f"Jami: *{total}* fayl\n\n"
        "Fayl ustiga bosing:"
    )
    kb = _list_keyboard(page_items, page, total)
    await message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


async def r2_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("r2_list_"):
        page = int(data.split("_")[-1])
        all_items = await list_files(max_keys=200)
        total = len(all_items)
        if total == 0:
            await query.edit_message_text("📭 R2 da hech qanday fayl yo'q.")
            return
        start = page * PAGE_SIZE
        page_items = all_items[start:start + PAGE_SIZE]
        text = (
            f"☁️ *R2 Fayl Menejer* — sahifa {page + 1}/{(total - 1) // PAGE_SIZE + 1}\n"
            f"Jami: *{total}* fayl\n\n"
            "Fayl ustiga bosing:"
        )
        kb = _list_keyboard(page_items, page, total)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        await query.answer()

    elif data.startswith("r2_info_"):
        key = _dec(data[8:])
        name = os.path.basename(key)
        url = get_public_url(key)
        text = (
            f"📄 *{name}*\n\n"
            f"🗂 Kalit: `{key}`\n"
            f"🔗 URL: {url}\n\n"
            "Nima qilmoqchisiz?"
        )
        await query.edit_message_text(text, reply_markup=_file_keyboard(key), parse_mode="Markdown")
        await query.answer()

    elif data.startswith("r2_link_"):
        key = _dec(data[8:])
        url = get_public_url(key)
        presigned = await generate_presigned_url(key, expires=86400)
        text = (
            f"🔗 *{os.path.basename(key)}* havolasi:\n\n"
            f"*Public URL:*\n`{url}`\n\n"
            f"*Vaqtinchalik havola (24 soat):*\n`{presigned or 'Olishda xato'}`"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=f"r2_info_{_enc(key)}")]]),
            parse_mode="Markdown"
        )
        await query.answer()

    elif data.startswith("r2_del_confirm_"):
        key = _dec(data[15:])
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Ha, o'chir",  callback_data=f"r2_del_do_{_enc(key)}"),
                InlineKeyboardButton("❌ Bekor",        callback_data=f"r2_info_{_enc(key)}"),
            ]
        ])
        await query.edit_message_text(
            f"⚠️ *{os.path.basename(key)}* faylini o'chirishga ishonchingiz komilmi?\n\n"
            f"Bu amalni qaytarib bo'lmaydi!",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await query.answer()

    elif data.startswith("r2_del_do_"):
        key = _dec(data[10:])
        ok = await delete_file(key)
        if ok:
            await query.edit_message_text(
                f"✅ *{os.path.basename(key)}* o'chirildi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ro'yxatga", callback_data="r2_list_0")]]),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                f"❌ O'chirishda xato yuz berdi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=f"r2_info_{_enc(key)}")]]),
                parse_mode="Markdown"
            )
        await query.answer()

    elif data.startswith("r2_rename_"):
        key = _dec(data[10:])
        context.user_data["r2_rename_key"] = key
        context.user_data["state"] = "r2_rename_input"
        await query.edit_message_text(
            f"✏️ *{os.path.basename(key)}* uchun yangi nom kiriting:\n\n"
            f"_(Faqat fayl nomi, yo'l emas. Masalan: `yangi_nom.mp4`)_",
            parse_mode="Markdown"
        )
        await query.answer()

    elif data.startswith("r2_send_tg__"):
        filename = data[len("r2_send_tg__"):]
        await query.answer("📥 Yuklanmoqda...")
        await _send_r2_file_to_telegram(query.message, filename)


async def r2_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rename uchun matn kiritilganda."""
    old_key = context.user_data.get("r2_rename_key")
    if not old_key:
        return False

    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text("❌ Nom bo'sh bo'lmasin.")
        return True

    # Eski yo'lni saqlash, faqat fayl nomini almashtirish
    dir_part = os.path.dirname(old_key)
    new_key = os.path.join(dir_part, new_name).lstrip("/")

    status = await update.message.reply_text("⏳ Nomlanmoqda...")
    new_url = await rename_file(old_key, new_key)
    context.user_data.pop("r2_rename_key", None)
    context.user_data["state"] = None

    if new_url:
        await status.edit_text(
            f"✅ *Muvaffaqiyatli!*\n\n"
            f"Yangi nom: `{new_name}`\n"
            f"🔗 URL: {new_url}",
            parse_mode="Markdown"
        )
    else:
        await status.edit_text("❌ Nomni o'zgartirishda xato yuz berdi.")
    return True


async def _send_r2_file_to_telegram(message, filename: str):
    """R2 dagi faylni vaqtinchalik yuklab, Telegramga yuboradi."""
    from config import TEMP_DIR

    url = get_public_url(filename)
    tmp_path = os.path.join(TEMP_DIR, f"r2tmp_{uuid.uuid4().hex}_{os.path.basename(filename)}")
    status_msg = await message.reply_text(
        f"📥 *Telegramga yuborilmoqda...*\n\n`[░░░░░░░░░░░░]` `0%`\n`{os.path.basename(filename)}`",
        parse_mode="Markdown",
    )

    try:
        # R2 dan yuklab olish
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ R2 dan yuklashda xato: HTTP {resp.status}")
                    return
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_pct = -1
                with open(tmp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 256):  # 256 KB
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(int(downloaded / total * 100), 99)
                            if pct - last_pct >= 10:
                                last_pct = pct
                                filled = int(12 * pct / 100)
                                bar = "[" + "█" * filled + "░" * (12 - filled) + "]"
                                try:
                                    await status_msg.edit_text(
                                        f"📥 *Telegramga yuborilmoqda...*\n\n`{bar}` `{pct}%`\n`{os.path.basename(filename)}`",
                                        parse_mode="Markdown",
                                    )
                                except Exception:
                                    pass

        # Telegramga yuborish
        await status_msg.edit_text("📤 *Telegram serveriga yuborilmoqda...*", parse_mode="Markdown")
        file_size = os.path.getsize(tmp_path)
        with open(tmp_path, "rb") as f:
            await message.reply_document(
                document=f,
                filename=os.path.basename(filename),
                caption=f"📁 `{os.path.basename(filename)}`\n📦 `{fmt_size(file_size)}`",
                parse_mode="Markdown",
            )
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ Yuborishda xato:\n`{e}`", parse_mode="Markdown")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
