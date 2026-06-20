"""
r2_browser.py — R2 papka brauzeri va fayl menejeri.

Papka navigatsiyasi, key-hash callback, rename tuzatilgan.
"""

import asyncio
import hashlib
import html
import os
import re
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.r2_manager import (
    list_prefix, list_all_files, delete_file, rename_file, move_file,
    generate_presigned_url, get_public_url, is_configured, fmt_size,
    join_key, create_folder, delete_prefix,
)

PAGE_SIZE = 8

_compress_state: dict = {}


def _key_hash(key: str) -> str:
    return hashlib.md5(key.encode()).hexdigest()[:10]


def _store_key_map(context, items: list[dict]) -> None:
    km = context.user_data.setdefault("r2_key_map", {})
    for item in items:
        km[_key_hash(item["key"])] = item["key"]


def _get_key(context, kh: str) -> str | None:
    return context.user_data.get("r2_key_map", {}).get(kh)


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "").replace("\\", "/").strip("/")
    return p + "/" if p else ""


async def _get_folder_ui(context, prefix: str, page: int):
    if not is_configured():
        return "❌ R2 sozlanmagan.", None

    prefix = _norm_prefix(prefix)
    data = await list_prefix(prefix)
    folders = data["folders"]
    files = data["files"]
    context.user_data["r2_prefix"] = prefix

    all_items = [{"type": "folder", **f} for f in folders]
    all_items += [{"type": "file", **f} for f in files]
    total = len(all_items)

    if total == 0:
        text = f"📭 *Bo'sh papka*\n`{prefix or '/'}`\n\nFayl yoki papka yo'q."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Papka yarat", callback_data="r2_mkdir")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="r2_up")],
            [InlineKeyboardButton("🔄 Yangilash", callback_data="r2_refresh")],
        ])
        return text, kb

    start = page * PAGE_SIZE
    page_items = all_items[start:start + PAGE_SIZE]
    rows = []

    for item in page_items:
        if item["type"] == "folder":
            rows.append([InlineKeyboardButton(
                f"📁 {item['name']}/",
                callback_data=f"r2_open_{item['prefix']}",
            )])
        else:
            kh = _key_hash(item["key"])
            name = os.path.basename(item["key"])
            if len(name) > 26:
                name = name[:23] + "..."
            rows.append([InlineKeyboardButton(
                f"📄 {name} ({item['size_str']})",
                callback_data=f"r2_info_{kh}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"r2_page_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"r2_page_{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton("➕ Papka", callback_data="r2_mkdir"),
        InlineKeyboardButton("🔄 Yangilash", callback_data="r2_refresh"),
    ])
    if prefix:
        rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="r2_up")])

    _store_key_map(context, files)
    pages = max(1, (total - 1) // PAGE_SIZE + 1)
    text = (
        f"☁️ *R2 Menejer*\n"
        f"📂 `{prefix or 'root/'}`\n"
        f"📁 {len(folders)} papka | 📄 {len(files)} fayl\n"
        f"Sahifa {page + 1}/{pages}"
    )
    return text, InlineKeyboardMarkup(rows)


def _file_keyboard(kh: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Havola", callback_data=f"r2_link_{kh}"),
            InlineKeyboardButton("✏️ Rename", callback_data=f"r2_rename_{kh}"),
        ],
        [
            InlineKeyboardButton("🗑 O'chirish", callback_data=f"r2_del_confirm_{kh}"),
        ],
        [InlineKeyboardButton("🔙 Ro'yxatga", callback_data="r2_refresh")],
    ])


def _compress_kb(short_key: str, sel: dict) -> InlineKeyboardMarkup:
    def _btn(label, key):
        icon = "✅" if sel.get(key) else "☑️"
        return InlineKeyboardButton(f"{icon} {label}", callback_data=f"r2_compress_tog_{short_key}_{key}")

    return InlineKeyboardMarkup([
        [_btn("R2 da qoldirish", "r2")],
        [_btn("720p", "720"), _btn("480p", "480"), _btn("360p", "360")],
        [
            InlineKeyboardButton("🚀 Boshlash", callback_data=f"r2_compress_run_{short_key}"),
            InlineKeyboardButton("❌ Bekor", callback_data=f"r2_compress_cancel_{short_key}"),
        ],
    ])


async def _show_compress_menu(query, short_key: str, state: dict):
    sel = state["sel"]
    filename = state["filename"]
    file_size = state.get("file_size", 0)
    size_str = fmt_size(file_size) if file_size else "?"
    resolutions = state.get("resolutions", {})
    lines = [
        f"🎬 <b>{html.escape(filename)}</b>",
        f"📦 Asl hajm: <b>{size_str}</b>",
        "", "Tanlang:",
    ]
    if resolutions:
        lines.append("\n📊 <b>Taxminiy:</b>")
        for h, sz in sorted(resolutions.items(), reverse=True):
            lines.append(f"  • {h}p → ~{sz}")
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=_compress_kb(short_key, sel),
        parse_mode="HTML",
    )


async def _estimate_sizes(file_path: str, orig_height: int) -> dict:
    import subprocess
    bitrates = {720: 2200, 480: 1100, 360: 550}
    duration = 0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(r.stdout.strip())
    except Exception:
        pass
    result = {}
    for h, kbps in bitrates.items():
        if h >= orig_height:
            continue
        size_bytes = int(duration * kbps * 1000 / 8) if duration else 0
        result[h] = fmt_size(size_bytes) if size_bytes else "?"
    return result


async def _handle_r2_send_tg(query, context, short_key: str):
    from utils.sender import _r2_pending, PYROGRAM_LIMIT, send_file, _persist_r2_pending
    from utils.ffmpeg_utils import get_video_resolution

    entry = _r2_pending.get(short_key)
    if not entry:
        await query.answer("❌ Fayl topilmadi yoki muddati o'tgan.", show_alert=True)
        return

    await query.answer()
    filename = entry["filename"]
    file_path = entry.get("file_path", "")
    url = entry["url"]

    if not file_path or not os.path.exists(file_path):
        await query.message.reply_text(f"⚠️ Fayl yo'q.\n🔗 `{url}`", parse_mode="Markdown")
        return

    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(filename)[1].lower()
    VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts"}

    if file_size <= PYROGRAM_LIMIT:
        status = await query.message.reply_text("📤 *Yuborilmoqda...*", parse_mode="Markdown")
        await send_file(status, file_path, filename, f"📥 {filename}", context=context)
        try:
            await status.delete()
        except Exception:
            pass
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            _r2_pending.pop(short_key, None)
            _persist_r2_pending()
        except Exception:
            pass
        return

    if ext in VIDEO_EXT:
        status = await query.message.reply_text("🔍 Video tahlil...")
        w, h = await asyncio.get_running_loop().run_in_executor(
            None, get_video_resolution, file_path,
        )
        resolutions = await _estimate_sizes(file_path, h or 9999)
        sel = {"r2": True, "720": False, "480": False, "360": False}
        _compress_state[short_key] = {
            "filename": filename, "file_path": file_path,
            "file_size": file_size, "url": url, "sel": sel,
            "resolutions": resolutions,
        }
        lines = [
            f"🎬 <b>{html.escape(filename)}</b>",
            f"📦 {fmt_size(file_size)} | {w}x{h}",
            "", "2 GB dan katta — tanlang:",
        ]
        await status.edit_text(
            "\n".join(lines),
            reply_markup=_compress_kb(short_key, sel),
            parse_mode="HTML",
        )
    else:
        await query.message.reply_text(f"⚠️ Katta fayl (video emas).\n🔗 `{url}`", parse_mode="Markdown")


async def _handle_r2_compress(query, context, data: str):
    from utils.ffmpeg_utils import downscale_for_telegram_async
    from utils.sender import send_file, _r2_pending, _persist_r2_pending

    if data.startswith("r2_compress_tog_"):
        rest = data[len("r2_compress_tog_"):]
        short_key = rest[:8]
        field = rest[9:]
        state = _compress_state.get(short_key)
        if not state:
            await query.answer("❌ Sessiya o'tgan.", show_alert=True)
            return
        state["sel"][field] = not state["sel"][field]
        await query.answer()
        await _show_compress_menu(query, short_key, state)
        return

    if data.startswith("r2_compress_cancel_"):
        short_key = data[len("r2_compress_cancel_"):]
        _compress_state.pop(short_key, None)
        await query.edit_message_text("❌ Bekor qilindi.")
        await query.answer()
        return

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
            await query.edit_message_text("⚠️ Kamida bitta sifat tanlang.")
            _compress_state[short_key] = state
            return

        await query.edit_message_text("⚙️ Ishlov berilmoqda...", parse_mode="HTML")
        success_count = 0
        for height in targets:
            status = await query.message.reply_text(f"🔄 {height}p...", parse_mode="HTML")
            ok, out_path, err = await downscale_for_telegram_async(
                file_path, height, status,
                user_id=context.user_data.get("_user_id", query.from_user.id),
            )
            if ok and os.path.exists(out_path):
                out_filename = f"{os.path.splitext(filename)[0]}_{height}p.mp4"
                try:
                    await send_file(status, out_path, out_filename, f"📥 {out_filename}", context=context)
                    success_count += 1
                    try:
                        await status.delete()
                    except Exception:
                        pass
                finally:
                    if os.path.exists(out_path):
                        os.remove(out_path)
            else:
                await status.edit_text(f"❌ {height}p: {html.escape(err or '?')}", parse_mode="HTML")

        if not keep_r2 and url:
            try:
                base = get_public_url("").rstrip("/")
                key = url[len(base):].lstrip("/") if url.startswith(base) else ""
                if key:
                    await delete_file(key)
            except Exception:
                pass
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
        _r2_pending.pop(short_key, None)
        _persist_r2_pending()
        await query.message.reply_text(f"✅ {success_count}/{len(targets)} yuborildi.", parse_mode="HTML")
        return


async def r2_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_configured():
        await update.message.reply_text("❌ R2 sozlanmagan.")
        return
    context.user_data["r2_prefix"] = ""
    context.user_data["r2_page"] = 0
    text, kb = await _get_folder_ui(context, "", 0)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def r2_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("r2_compress_"):
        await _handle_r2_compress(query, context, data)
        return

    if data.startswith("r2_send_tg__"):
        await _handle_r2_send_tg(query, context, data[len("r2_send_tg__"):])
        return

    if data == "r2_refresh":
        prefix = context.user_data.get("r2_prefix", "")
        page = context.user_data.get("r2_page", 0)
        text, kb = await _get_folder_ui(context, prefix, page)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await query.answer()
        return

    if data == "r2_up":
        prefix = context.user_data.get("r2_prefix", "")
        parent = prefix.rstrip("/").rsplit("/", 1)[0] if prefix else ""
        context.user_data["r2_page"] = 0
        text, kb = await _get_folder_ui(context, parent, 0)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await query.answer()
        return

    if data.startswith("r2_open_"):
        prefix = data[len("r2_open_"):]
        context.user_data["r2_page"] = 0
        text, kb = await _get_folder_ui(context, prefix, 0)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await query.answer()
        return

    if data.startswith("r2_page_"):
        page = int(data.split("_")[-1])
        context.user_data["r2_page"] = page
        prefix = context.user_data.get("r2_prefix", "")
        text, kb = await _get_folder_ui(context, prefix, page)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await query.answer()
        return

    if data == "r2_mkdir":
        context.user_data["state"] = "r2_mkdir_input"
        await query.edit_message_text("📁 Yangi papka nomini kiriting:", parse_mode="HTML")
        await query.answer()
        return

    if data.startswith("r2_info_"):
        kh = data[len("r2_info_"):]
        key = _get_key(context, kh)
        if not key:
            await query.answer("❌ Yangilang", show_alert=True)
            return
        name = os.path.basename(key)
        url = get_public_url(key)
        text = (
            f"📄 <b>{html.escape(name)}</b>\n\n"
            f"🗂 <code>{html.escape(key)}</code>\n"
            f"🔗 {html.escape(url)}"
        )
        await query.edit_message_text(text, reply_markup=_file_keyboard(kh), parse_mode="HTML")
        await query.answer()
        return

    if data.startswith("r2_link_"):
        kh = data[len("r2_link_"):]
        key = _get_key(context, kh)
        if not key:
            await query.answer("❌ Yangilang", show_alert=True)
            return
        url = get_public_url(key)
        presigned = await generate_presigned_url(key, expires=86400)
        text = (
            f"🔗 <b>{html.escape(os.path.basename(key))}</b>\n\n"
            f"Public:\n<code>{html.escape(url)}</code>\n\n"
            f"24 soat:\n<code>{html.escape(presigned or 'Xato')}</code>"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Orqaga", callback_data=f"r2_info_{kh}")
            ]]),
            parse_mode="HTML",
        )
        await query.answer()
        return

    if data.startswith("r2_del_confirm_"):
        kh = data[len("r2_del_confirm_"):]
        key = _get_key(context, kh)
        if not key:
            await query.answer("❌ Yangilang", show_alert=True)
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Ha", callback_data=f"r2_del_do_{kh}"),
            InlineKeyboardButton("❌ Yo'q", callback_data=f"r2_info_{kh}"),
        ]])
        await query.edit_message_text(
            f"⚠️ <b>{html.escape(os.path.basename(key))}</b> o'chirilsinmi?",
            reply_markup=kb, parse_mode="HTML",
        )
        await query.answer()
        return

    if data.startswith("r2_del_do_"):
        kh = data[len("r2_del_do_"):]
        key = _get_key(context, kh)
        if key and await delete_file(key):
            await query.edit_message_text("✅ O'chirildi.", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Ro'yxatga", callback_data="r2_refresh")
            ]]))
        else:
            await query.edit_message_text("❌ Xato.")
        await query.answer()
        return

    if data.startswith("r2_rename_"):
        kh = data[len("r2_rename_"):]
        key = _get_key(context, kh)
        if not key:
            await query.answer("❌ Yangilang", show_alert=True)
            return
        context.user_data["r2_rename_key"] = key
        context.user_data["state"] = "r2_rename_input"
        await query.edit_message_text("✏️ Yangi nom kiriting:", parse_mode="HTML")
        await query.answer()
        return

    await query.answer("Noma'lum buyruq", show_alert=True)


async def _show_r2_list_cb(query, context=None, page: int = 0):
    prefix = context.user_data.get("r2_prefix", "") if context else ""
    text, kb = await _get_folder_ui(context, prefix, page)
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


async def r2_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_key = context.user_data.get("r2_rename_key")
    if not old_key:
        return False
    new_name = update.message.text.strip()
    dir_part = os.path.dirname(old_key.replace("\\", "/"))
    new_key = join_key(dir_part, new_name) if dir_part else new_name
    if await rename_file(old_key, new_key):
        await update.message.reply_text("✅ Nomlandi.")
    else:
        await update.message.reply_text("❌ Xato.")
    context.user_data.pop("r2_rename_key", None)
    context.user_data["state"] = None
    return True


async def r2_mkdir_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip().replace("/", "_")
    if not name:
        await update.message.reply_text("❌ Nom bo'sh bo'lmasligi kerak.")
        return True
    prefix = _norm_prefix(context.user_data.get("r2_prefix", ""))
    folder_key = join_key(prefix.rstrip("/"), name)
    if await create_folder(folder_key):
        await update.message.reply_text(f"✅ Papka yaratildi: `{folder_key}/`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Xato.")
    context.user_data["state"] = None
    return True
