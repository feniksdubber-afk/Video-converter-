"""
r2_browser.py — R2 dagi fayllarni botdan boshqarish.

r2_send_tg__ callback:
  - Fayl 2GB dan katta bo'lsa → sifat tanlash menu chiqaradi
  - Kichik bo'lsa → to'g'ridan yuboradi

r2_compress_ callback:
  - Foydalanuvchi tanlagan sifatlarni compress qilib yuboradi
  - R2 da qoldirish yoki o'chirish opsiyasi bilan
"""

import asyncio
import os
import html
import uuid
import json
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.r2_manager import (
    list_files, delete_file, rename_file,
    generate_presigned_url, get_public_url, is_configured, fmt_size
)

PAGE_SIZE = 8

# ── Sifat tanlash state: {short_key: {selections, file_path, filename, url}} ─
_compress_state: dict = {}


def _file_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Havola",    callback_data=f"r2_link_{index}"),
            InlineKeyboardButton("✏️ Rename",    callback_data=f"r2_rename_{index}"),
        ],
        [
            InlineKeyboardButton("🗑 O'chirish", callback_data=f"r2_del_confirm_{index}"),
        ],
        [InlineKeyboardButton("🔙 Ro'yxatga",   callback_data="r2_list_0")],
    ])


async def _get_file_list_ui(query, context, page: int):
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
        rows.append([InlineKeyboardButton(
            f"📄 {name} ({item['size_str']})", callback_data=f"r2_info_{idx}"
        )])

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
        f"Jami: <b>{total}</b> fayl\n\nFayl ustiga bosing:"
    )
    return text, InlineKeyboardMarkup(rows)


# ── Sifat tanlash menu ─────────────────────────────────────────────────────

def _compress_kb(short_key: str, sel: dict) -> InlineKeyboardMarkup:
    """
    sel = {"r2": bool, "720": bool, "480": bool, "360": bool}
    """
    def _btn(label, key):
        icon = "✅" if sel.get(key) else "☑️"
        return InlineKeyboardButton(f"{icon} {label}", callback_data=f"r2_compress_tog_{short_key}_{key}")

    return InlineKeyboardMarkup([
        [_btn("R2 da qoldirish", "r2")],
        [_btn("720p da yuklash", "720"), _btn("480p da yuklash", "480")],
        [_btn("360p da yuklash", "360")],
        [
            InlineKeyboardButton("🚀 Ishni boshlash", callback_data=f"r2_compress_run_{short_key}"),
            InlineKeyboardButton("❌ Bekor", callback_data=f"r2_compress_cancel_{short_key}"),
        ],
    ])


async def _show_compress_menu(query, short_key: str, state: dict):
    sel = state["sel"]
    filename = state["filename"]
    file_size = state.get("file_size", 0)
    size_str = fmt_size(file_size) if file_size else "?"
    resolutions = state.get("resolutions", {})  # {720: "1.2 GB", 480: "600 MB", 360: "300 MB"}

    lines = [
        f"🎬 <b>{html.escape(filename)}</b>",
        f"📦 Asl hajm: <b>{size_str}</b>",
        "",
        "Quyidagilardan birini yoki bir nechtasini tanlang:",
    ]
    if resolutions:
        lines.append("")
        lines.append("📊 <b>Taxminiy hajmlar:</b>")
        for h, sz in sorted(resolutions.items(), reverse=True):
            lines.append(f"  • {h}p → ~{sz}")

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=_compress_kb(short_key, sel),
        parse_mode="HTML"
    )


async def _estimate_sizes(file_path: str, orig_height: int) -> dict:
    """
    ffprobe bilan video davomiyligini olib, har bir sifat uchun
    taxminiy hajmni hisoblaydi (bitrate asosida).
    Qaytaradi: {720: "1.2 GB", 480: "650 MB", 360: "320 MB"}
    """
    import subprocess

    # Bitrate taxmini (libx264 crf=23, fast preset)
    # Empirik: 720p≈2Mbps, 480p≈1Mbps, 360p≈500kbps + audio 128kbps
    bitrates = {720: 2200, 480: 1100, 360: 550}  # kbps (video+audio)

    # Davomiylik
    duration = 0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=15
        )
        duration = float(r.stdout.strip())
    except Exception:
        pass

    result = {}
    for h, kbps in bitrates.items():
        if h >= orig_height:
            continue  # asl sifatdan katta bo'lsa skip
        size_bytes = int(duration * kbps * 1000 / 8) if duration else 0
        result[h] = fmt_size(size_bytes) if size_bytes else "?"

    return result


# ── r2_send_tg__ handler ───────────────────────────────────────────────────

async def _handle_r2_send_tg(query, context, short_key: str):
    from utils.sender import _r2_pending, PYROGRAM_LIMIT, send_file
    from utils.ffmpeg_utils import get_video_resolution

    entry = _r2_pending.get(short_key)
    if not entry:
        await query.answer(
            "❌ Fayl topilmadi yoki muddati o'tgan. Qaytadan yuklang.",
            show_alert=True
        )
        return

    await query.answer()
    filename = entry["filename"]
    file_path = entry.get("file_path", "")
    url = entry["url"]

    # Fayl yo'q
    if not file_path or not os.path.exists(file_path):
        await query.message.reply_text(
            f"⚠️ Fayl serverda saqlanmagan (ehtimol o'chirilgan).\n\n"
            f"🔗 R2 havolasi:\n`{url}`",
            parse_mode="Markdown",
        )
        return

    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(filename)[1].lower()
    VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts"}

    # 2GB dan kichik → to'g'ridan yuborish
    if file_size <= PYROGRAM_LIMIT:
        status = await query.message.reply_text("📤 *Yuborilmoqda...*", parse_mode="Markdown")
        await send_file(status, file_path, filename, f"📥 {filename}", context=context)
        try:
            await status.delete()
        except Exception:
            pass
        return

    # 2GB dan katta va video — sifat tanlash menu
    if ext in VIDEO_EXT:
        status = await query.message.reply_text("🔍 Video tahlil qilinmoqda...")
        w, h = await asyncio.get_running_loop().run_in_executor(
            None, get_video_resolution, file_path
        )
        resolutions = await _estimate_sizes(file_path, h or 9999)

        sel = {"r2": True, "720": False, "480": False, "360": False}
        _compress_state[short_key] = {
            "filename": filename,
            "file_path": file_path,
            "file_size": file_size,
            "url": url,
            "sel": sel,
            "resolutions": resolutions,
            "chat_id": query.message.chat_id,
        }

        # Status xabarni menu ga aylantirish
        lines = [
            f"🎬 <b>{html.escape(filename)}</b>",
            f"📦 Asl hajm: <b>{fmt_size(file_size)}</b>",
            f"📐 Asl sifat: <b>{w}x{h}</b>",
            "",
            "Fayl 2 GB dan katta. Quyidagilardan tanlang:",
        ]
        if resolutions:
            lines.append("")
            lines.append("📊 <b>Taxminiy hajmlar:</b>")
            for rh, sz in sorted(resolutions.items(), reverse=True):
                lines.append(f"  • {rh}p → ~{sz}")

        await status.edit_text(
            "\n".join(lines),
            reply_markup=_compress_kb(short_key, sel),
            parse_mode="HTML"
        )
    else:
        # Video emas, katta fayl — faqat havola
        await query.message.reply_text(
            f"⚠️ Fayl 2 GB dan katta va video emas.\n\n"
            f"🔗 R2 havolasi:\n`{url}`",
            parse_mode="Markdown",
        )


# ── r2_compress_ handler ───────────────────────────────────────────────────

async def _handle_r2_compress(query, context, data: str):
    from utils.ffmpeg_utils import downscale_for_telegram_async
    from utils.sender import send_file
    from utils.r2_manager import delete_file as r2_delete

    # Toggle tugmasi: r2_compress_tog_{key}_{field}
    if data.startswith("r2_compress_tog_"):
        rest = data[len("r2_compress_tog_"):]
        # short_key 8 belgi, keyin _ keyin field
        short_key = rest[:8]
        field = rest[9:]  # r2 / 720 / 480 / 360
        state = _compress_state.get(short_key)
        if not state:
            await query.answer("❌ Sessiya o'tgan, qaytadan bosing.", show_alert=True)
            return
        state["sel"][field] = not state["sel"][field]
        await query.answer()
        await _show_compress_menu(query, short_key, state)
        return

    # Bekor qilish
    if data.startswith("r2_compress_cancel_"):
        short_key = data[len("r2_compress_cancel_"):]
        _compress_state.pop(short_key, None)
        await query.edit_message_text("❌ Bekor qilindi.")
        await query.answer()
        return

    # Ishni boshlash: r2_compress_run_{key}
    if data.startswith("r2_compress_run_"):
        short_key = data[len("r2_compress_run_"):]
        state = _compress_state.pop(short_key, None)
        if not state:
            await query.answer("❌ Sessiya o'tgan.", show_alert=True)
            return

        sel = state["sel"]
        file_path = state["file_path"]
        filename = state["filename"]
        url = state["url"]
        keep_r2 = sel.get("r2", True)
        targets = [h for h in [720, 480, 360] if sel.get(str(h))]

        await query.answer()

        if not targets:
            await query.edit_message_text(
                "⚠️ Hech qanday sifat tanlanmadi. Iltimos kamida bittasini belgilang.",
                reply_markup=_compress_kb(short_key, sel),
                parse_mode="HTML"
            )
            _compress_state[short_key] = state  # qaytarib qo'yamiz
            return

        if not os.path.exists(file_path):
            await query.edit_message_text(
                f"❌ Fayl topilmadi.\n\n🔗 R2 havolasi:\n`{url}`",
                parse_mode="Markdown"
            )
            return

        await query.edit_message_text(
            f"⚙️ <b>Ishlov berilmoqda...</b>\n"
            f"Sifatlar: {', '.join(str(t)+'p' for t in targets)}\n\n"
            f"Bu bir necha daqiqa olishi mumkin ⏳",
            parse_mode="HTML"
        )

        success_count = 0
        for height in targets:
            status = await query.message.reply_text(
                f"🔄 <b>{height}p</b> ga o'zgartirilmoqda...",
                parse_mode="HTML"
            )
            ok, out_path, err = await downscale_for_telegram_async(file_path, height, status)
            if ok and os.path.exists(out_path):
                out_filename = f"{os.path.splitext(filename)[0]}_{height}p.mp4"
                try:
                    await status.edit_text(
                        f"📤 <b>{height}p</b> yuborilmoqda...",
                        parse_mode="HTML"
                    )
                    await send_file(
                        status, out_path, out_filename,
                        f"📥 {out_filename}", context=context
                    )
                    success_count += 1
                    try:
                        await status.delete()
                    except Exception:
                        pass
                finally:
                    if os.path.exists(out_path):
                        os.remove(out_path)
            else:
                await status.edit_text(
                    f"❌ <b>{height}p</b> xato: {html.escape(err or "noma'lum")}",
                    parse_mode="HTML"
                )

        # R2 dan o'chirish
        if not keep_r2:
            from utils.r2_manager import is_configured
            if is_configured():
                # URL dan key ni ajratib olamiz
                try:
                    from utils.r2_manager import get_public_url
                    # URL dan object key ni ajratib olamiz
                    # get_public_url("test") → "https://pub-xxx.r2.dev/test"
                    base = get_public_url("").rstrip("/")
                    key = url[len(base):].lstrip("/") if url.startswith(base) else ""
                    if key:
                        deleted = await r2_delete(key)
                        r2_note = "✅ R2 dan o'chirildi." if deleted else "⚠️ R2 dan o'chirib bo'lmadi."
                    else:
                        r2_note = "⚠️ R2 kaliti aniqlanmadi, qo'lda o'chiring."
                except Exception as e:
                    r2_note = f"⚠️ R2 o'chirishda xato: {e}"
            else:
                r2_note = ""
        else:
            r2_note = "☁️ R2 da saqlanib qoldi."

        # Yakuniy xabar
        result_text = (
            f"✅ <b>Tayyor!</b>\n"
            f"{success_count}/{len(targets)} ta sifat yuborildi.\n"
        )
        if r2_note:
            result_text += f"\n{r2_note}"

        await query.message.reply_text(result_text, parse_mode="HTML")
        return


# ── Asosiy r2_callback ─────────────────────────────────────────────────────

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

    # ── Compress sub-handlers ────────────────────────────────────────────
    if data.startswith("r2_compress_"):
        await _handle_r2_compress(query, context, data)
        return

    # ── r2_send_tg__ ────────────────────────────────────────────────────
    if data.startswith("r2_send_tg__"):
        short_key = data[len("r2_send_tg__"):]
        await _handle_r2_send_tg(query, context, short_key)
        return

    # ── Ro'yxat ─────────────────────────────────────────────────────────
    if data.startswith("r2_list_"):
        page = int(data.split("_")[-1])
        text, kb = await _get_file_list_ui(query, context, page)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await query.answer()
        return

    if data.startswith("r2_info_"):
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
            f"🔗 URL: {html.escape(url)}\n\nNima qilmoqchisiz?"
        )
        await query.edit_message_text(text, reply_markup=_file_keyboard(idx), parse_mode="HTML")
        await query.answer()
        return

    if data.startswith("r2_link_"):
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
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Orqaga", callback_data=f"r2_info_{idx}")
            ]]),
            parse_mode="HTML"
        )
        await query.answer()
        return

    if data.startswith("r2_del_confirm_"):
        idx = int(data.split("_")[-1])
        if not files:
            files = await list_files(max_keys=200)
            context.user_data["r2_files"] = files
        if idx >= len(files):
            await query.answer("❌ Fayl topilmadi, ro'yxatni yangilang")
            return
        key = files[idx]["key"]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Ha, o'chir", callback_data=f"r2_del_do_{idx}"),
            InlineKeyboardButton("❌ Bekor",      callback_data=f"r2_info_{idx}"),
        ]])
        await query.edit_message_text(
            f"⚠️ <b>{html.escape(os.path.basename(key))}</b> ni o'chirishga ishonchingiz komilmi?",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await query.answer()
        return

    if data.startswith("r2_del_do_"):
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
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Ro'yxatga", callback_data="r2_list_0")
                ]]),
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                "❌ Xato yuz berdi.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Orqaga", callback_data=f"r2_info_{idx}")
                ]]),
                parse_mode="HTML"
            )
        await query.answer()
        return

    if data.startswith("r2_rename_"):
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
        return

    await query.answer("Noma'lum buyruq", show_alert=True)


async def _show_r2_list_cb(query, context=None, page: int = 0):
    text, kb = await _get_file_list_ui(query, context, page)
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


async def r2_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_key = context.user_data.get("r2_rename_key")
    if not old_key:
        return False
    new_name = update.message.text.strip()
    dir_part = os.path.dirname(old_key)
    new_key = os.path.join(dir_part, new_name).lstrip("/")
    if await rename_file(old_key, new_key):
        await update.message.reply_text("✅ Muvaffaqiyatli nomlandi.")
    else:
        await update.message.reply_text("❌ Xato.")
    context.user_data.pop("r2_rename_key", None)
    return True
