"""
batch.py — Ko'p fayllarni ketma-ket ishlash (Batch Processor)

Imkoniyatlar:
  - Shablon saqlash / ishlatish
  - 50 tagacha fayl (BATCH_MAX_FILES)
  - Jarayon davomida ❌ Bekor qilish
  - Har faylni alohida o'chirish
  - R2: batch/{user_id}/{uuid}_{nom}
  - Faqat R2 qadam bo'lsa Telegramga yubormaslik
"""

import os
import asyncio
import json
import time
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.ffmpeg_utils import make_temp_path, get_video_duration, sanitize_filename, run_ffmpeg_async
from utils.sender import send_file
from utils.keyboards import main_menu_keyboard
from utils.db import db_load_batch_templates, db_save_batch_template, db_delete_batch_template
from utils.task_manager import (
    register_task, set_task_proc, is_cancelled, clear_task, progress_keyboard,
)
from utils.r2_manager import join_key

BATCH_MAX_FILES = int(os.environ.get("BATCH_MAX_FILES", "50"))

STEP_DEFS = {
    "stream_remove_extra_audio": {
        "label": "🎵 Birinchi audio qolsin",
        "desc": "Faqat 1-audio, subtitrlar o'chadi",
    },
    "remove_all_subs": {
        "label": "📝 Subtitrlarni o'chirish",
        "desc": "Barcha subtitle stream olib tashlanadi",
    },
    "convert_mp4": {
        "label": "🎬 MP4 ga o'tkazish",
        "desc": "MP4 formatiga konvertatsiya",
    },
    "convert_mkv": {
        "label": "📦 MKV ga o'tkazish",
        "desc": "MKV formatiga konvertatsiya",
    },
    "compress_high": {
        "label": "📉 Siqish (Yuqori)",
        "desc": "CRF 18",
    },
    "compress_medium": {
        "label": "📉 Siqish (O'rta)",
        "desc": "CRF 23",
    },
    "compress_low": {
        "label": "📉 Siqish (Past)",
        "desc": "CRF 28",
    },
    "remove_audio": {
        "label": "🔇 Ovozni o'chirish",
        "desc": "Barcha audio olib tashlanadi",
    },
    "res_1080": {"label": "📐 1080p", "desc": "1080p ga o'zgartirish"},
    "res_720":  {"label": "📐 720p",  "desc": "720p ga o'zgartirish"},
    "res_480":  {"label": "📐 480p",  "desc": "480p ga o'zgartirish"},
    "faststart": {
        "label": "⚡️ Faststart (tez ochilish)",
        "desc": "MOOV atomni boshiga ko'chiradi — video darhol striming boshlanadi",
    },
    "upload_r2": {
        "label": "☁️ R2 ga yuklash",
        "desc": "Cloudflare R2 ga yuklab havola yuboradi",
    },
}


def _fmt_size(b: int) -> str:
    if not b:
        return "?"
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


def _progress_bar(pct: int, length: int = 12) -> str:
    filled = int(length * pct / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


# ── Keyboardlar ─────────────────────────────────────────────────────────────

def batch_main_keyboard(templates: list[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("➕ Yangi vazifa", callback_data="batch_new")]]
    if templates:
        rows.append([InlineKeyboardButton("── Shablonlar ──", callback_data="batch_noop")])
        for t in templates:
            rows.append([
                InlineKeyboardButton(f"▶️ {t['name'][:28]}", callback_data=f"batch_use_{t['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"batch_del_{t['id']}"),
            ])
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def step_select_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for key, info in STEP_DEFS.items():
        mark = "✅ " if key in selected else "⬜ "
        rows.append([InlineKeyboardButton(f"{mark}{info['label']}", callback_data=f"batch_step_{key}")])
    if selected:
        rows.append([
            InlineKeyboardButton("💾 Saqlash", callback_data="batch_save_ask"),
            InlineKeyboardButton("▶️ Davom etish", callback_data="batch_start_nosave"),
        ])
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="batch_menu")])
    return InlineKeyboardMarkup(rows)


def batch_files_keyboard(files: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, f in enumerate(files):
        name = f["name"][:32] + ("..." if len(f["name"]) > 32 else "")
        size = _fmt_size(f.get("file_size") or 0)
        rows.append([
            InlineKeyboardButton(f"📄 {i + 1}. {name} ({size})", callback_data="batch_noop"),
            InlineKeyboardButton("❌", callback_data=f"batch_rm_{i}"),
        ])
    action = []
    if files:
        action.append(InlineKeyboardButton(f"▶️ Boshlash ({len(files)})", callback_data="batch_run"))
        action.append(InlineKeyboardButton("🗑 Hammasi", callback_data="batch_clear_files"))
    if action:
        rows.append(action)
    rows.append([InlineKeyboardButton("🚫 Bekor qilish", callback_data="batch_abort")])
    rows.append([InlineKeyboardButton("🔙 Batch menyu", callback_data="batch_menu")])
    return InlineKeyboardMarkup(rows)


async def _batch_menu_text(templates: list[dict]) -> str:
    return (
        "📦 *Batch Processor*\n\n"
        "Ko'p faylni bir xil vazifa bilan ketma-ket qayta ishlash.\n\n"
        f"📋 Shablonlar: *{len(templates)} ta* | "
        f"📁 Maks fayl: *{BATCH_MAX_FILES}*\n\n"
        "Yangi vazifa yoki shablon tanlang:"
    )


# ── Menyu ─────────────────────────────────────────────────────────────────────

async def batch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    templates = await db_load_batch_templates(user_id)
    context.user_data["batch_templates"] = templates
    await update.message.reply_text(
        await _batch_menu_text(templates),
        reply_markup=batch_main_keyboard(templates),
        parse_mode="Markdown",
    )


async def show_batch_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    templates = await db_load_batch_templates(user_id)
    context.user_data["batch_templates"] = templates
    await query.edit_message_text(
        await _batch_menu_text(templates),
        reply_markup=batch_main_keyboard(templates),
        parse_mode="Markdown",
    )


async def show_batch_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["batch_selected_steps"] = []
    await query.edit_message_text(
        "🛠 *Yangi Batch Vazifa*\n\nQadamlarni tanlang _(tartib muhim)_:",
        reply_markup=step_select_keyboard([]),
        parse_mode="Markdown",
    )


async def handle_batch_step_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, step_key: str):
    query = update.callback_query
    await query.answer()
    selected: list = context.user_data.setdefault("batch_selected_steps", [])
    if step_key in selected:
        selected.remove(step_key)
    else:
        selected.append(step_key)
    steps_text = ""
    if selected:
        steps_text = "\n\n📋 *Tanlangan:*\n" + "\n".join(
            f"  {i + 1}. {STEP_DEFS[s]['label']}" for i, s in enumerate(selected)
        )
    try:
        await query.edit_message_text(
            f"🛠 *Yangi Batch Vazifa*{steps_text}",
            reply_markup=step_select_keyboard(selected),
            parse_mode="Markdown",
        )
    except Exception:
        pass


async def handle_batch_save_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data.get("batch_selected_steps", [])
    if not selected:
        await query.answer("⚠️ Qadam tanlanmadi!", show_alert=True)
        return
    context.user_data["state"] = "batch_save_name"
    steps_text = "\n".join(f"  {i + 1}. {STEP_DEFS[s]['label']}" for i, s in enumerate(selected))
    await query.edit_message_text(
        f"💾 *Shablon nomi*\n\nQadamlar:\n{steps_text}\n\nNom yozing:",
        parse_mode="Markdown",
    )


async def handle_batch_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Nom bo'sh bo'lmasligi kerak.")
        return
    selected = context.user_data.get("batch_selected_steps", [])
    if not selected:
        await update.message.reply_text("❌ Qadam topilmadi.")
        return
    user_id = update.message.from_user.id
    template_id = await db_save_batch_template(user_id, name, selected)
    context.user_data["state"] = None
    context.user_data["batch_current_template"] = {"id": template_id, "name": name, "steps": selected}
    context.user_data["batch_files"] = []
    await update.message.reply_text(
        f"✅ *'{name}'* saqlandi!\n\n📤 Fayllarni yuboring ({BATCH_MAX_FILES} tagacha):",
        reply_markup=batch_files_keyboard([]),
        parse_mode="Markdown",
    )


async def _start_file_collection(query, context, template: dict):
    context.user_data["batch_current_template"] = template
    context.user_data["batch_files"] = []
    steps_text = "\n".join(
        f"  {i + 1}. {STEP_DEFS.get(s, {}).get('label', s)}" for i, s in enumerate(template["steps"])
    )
    await query.edit_message_text(
        f"📦 *{template['name']}*\n\nQadamlar:\n{steps_text}\n\n"
        f"📤 Video fayllarni yuboring ({BATCH_MAX_FILES} tagacha):",
        reply_markup=batch_files_keyboard([]),
        parse_mode="Markdown",
    )


async def handle_batch_start_nosave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data.get("batch_selected_steps", [])
    if not selected:
        await query.answer("⚠️ Qadam tanlanmadi!", show_alert=True)
        return
    await _start_file_collection(query, context, {"id": None, "name": "Vaqtinchalik", "steps": selected})


async def handle_batch_use_template(update: Update, context: ContextTypes.DEFAULT_TYPE, template_id: int):
    query = update.callback_query
    await query.answer()
    templates = context.user_data.get("batch_templates", [])
    template = next((t for t in templates if t["id"] == template_id), None)
    if not template:
        await query.answer("❌ Shablon topilmadi!", show_alert=True)
        return
    await _start_file_collection(query, context, template)


async def handle_batch_delete_template(update: Update, context: ContextTypes.DEFAULT_TYPE, template_id: int):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await db_delete_batch_template(user_id, template_id)
    templates = await db_load_batch_templates(user_id)
    context.user_data["batch_templates"] = templates
    await query.edit_message_text(
        "🗑 O'chirildi.",
        reply_markup=batch_main_keyboard(templates),
        parse_mode="Markdown",
    )


async def handle_batch_abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("batch_current_template", None)
    context.user_data.pop("batch_files", None)
    context.user_data["state"] = None
    user_id = query.from_user.id
    templates = await db_load_batch_templates(user_id)
    await query.edit_message_text(
        "❌ Batch bekor qilindi.",
        reply_markup=batch_main_keyboard(templates),
        parse_mode="Markdown",
    )


async def handle_batch_remove_file(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int):
    query = update.callback_query
    files: list = context.user_data.get("batch_files", [])
    if 0 <= index < len(files):
        removed = files.pop(index)
        await query.answer(f"O'chirildi: {removed['name'][:20]}")
    else:
        await query.answer("❌ Topilmadi")
        return
    template = context.user_data.get("batch_current_template", {})
    await query.edit_message_text(
        f"📦 *{template.get('name', 'Batch')}* — {len(files)} ta fayl",
        reply_markup=batch_files_keyboard(files),
        parse_mode="Markdown",
    )


async def handle_batch_clear_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["batch_files"] = []
    await query.edit_message_text(
        "🗑 Fayllar tozalandi. Yangi fayllar yuboring:",
        reply_markup=batch_files_keyboard([]),
    )


# ── Fayl qabul qilish ─────────────────────────────────────────────────────────

async def batch_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if "batch_current_template" not in context.user_data:
        return False

    message = update.message
    file = None
    file_name = "video.mkv"

    if message.video:
        file = message.video
        file_name = message.video.file_name or "video.mp4"
    elif message.document:
        doc = message.document
        mime = doc.mime_type or ""
        exts = [".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".wmv"]
        if mime.startswith("video/") or (doc.file_name and any(doc.file_name.lower().endswith(e) for e in exts)):
            file = doc
            file_name = doc.file_name or "video.mkv"
        else:
            return False
    else:
        return False

    files: list = context.user_data.setdefault("batch_files", [])
    if any(f["file_unique_id"] == file.file_unique_id for f in files):
        await message.reply_text("⚠️ Bu fayl allaqachon ro'yxatda.")
        return True

    if len(files) >= BATCH_MAX_FILES:
        await message.reply_text(f"⚠️ Maksimum {BATCH_MAX_FILES} ta fayl.")
        return True

    files.append({
        "file_id": file.file_id,
        "file_unique_id": file.file_unique_id,
        "file_size": getattr(file, "file_size", 0),
        "name": file_name,
    })

    template = context.user_data["batch_current_template"]
    total_size = sum(f.get("file_size") or 0 for f in files)
    await message.reply_text(
        f"✅ *{len(files)}/{BATCH_MAX_FILES}* fayl | Jami: `{_fmt_size(total_size)}`\n"
        f"📄 `{file_name}`\n\n"
        f"Vazifa: *{template['name']}*\n"
        f"Yana yuboring yoki *▶️ Boshlash* bosing:",
        reply_markup=batch_files_keyboard(files),
        parse_mode="Markdown",
    )
    return True


# ── BATCH RUN ─────────────────────────────────────────────────────────────────

async def _update_status(msg, text: str, show_cancel: bool = True):
    kb = progress_keyboard(show_cancel) if show_cancel else None
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass


async def handle_batch_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    template = context.user_data.get("batch_current_template")
    files: list = context.user_data.get("batch_files", [])

    if not template or not template.get("steps"):
        await query.answer("❌ Vazifa topilmadi!", show_alert=True)
        return
    if not files:
        await query.answer("❌ Fayl yo'q!", show_alert=True)
        return

    user_id = query.from_user.id
    steps = template["steps"]
    total_files = len(files)
    do_r2 = "upload_r2" in steps
    process_steps = [s for s in steps if s != "upload_r2"]
    r2_only = steps == ["upload_r2"]
    send_telegram = not r2_only

    register_task(user_id, label=f"Batch: {template['name']}")
    t_start = time.time()

    status_msg = await query.message.reply_text(
        f"🚀 *Batch boshlandi*\n\n"
        f"📋 {template['name']}\n"
        f"📁 {total_files} fayl | ⚙️ {len(process_steps)} qadam\n\n"
        f"⏳ Tayyorlanmoqda...",
        reply_markup=progress_keyboard(True),
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ Batch ishlayapti ({total_files} fayl)...")

    results = []
    cancelled = False

    for idx, file_info in enumerate(files):
        if is_cancelled(user_id):
            cancelled = True
            break

        file_num = idx + 1
        file_name = file_info["name"]
        local_path = None
        current_path = None

        try:
            await _update_status(
                status_msg,
                f"⬇️ *{file_num}/{total_files}* — `{file_name}`\n\nYuklanmoqda...",
            )

            from config import TEMP_DIR
            from handlers.video_handler import get_pyrogram_client

            ext = os.path.splitext(file_name)[1].lstrip(".").lower() or "mkv"
            local_path = os.path.join(TEMP_DIR, f"batch_{file_info['file_unique_id']}.{ext}")
            file_size = file_info.get("file_size") or 0

            if file_size and file_size <= 20 * 1024 * 1024:
                tg_file = await context.bot.get_file(file_info["file_id"])
                await tg_file.download_to_drive(local_path)
            else:
                client = await get_pyrogram_client()
                total_mb = file_size / 1024 / 1024 if file_size else 0
                last_pct = [0]

                async def _dl_progress(current, total):
                    if is_cancelled(user_id) or not total:
                        return
                    pct = min(int(current / total * 100), 99)
                    if pct - last_pct[0] < 8:
                        return
                    last_pct[0] = pct
                    await _update_status(
                        status_msg,
                        f"⬇️ *{file_num}/{total_files}* — `{file_name}`\n"
                        f"{_progress_bar(pct)} `{pct}%`\n"
                        f"`{current / 1024 / 1024:.1f}` / `{total_mb:.1f}` MB",
                    )

                await client.download_media(file_info["file_id"], file_name=local_path, progress=_dl_progress)

            if is_cancelled(user_id):
                cancelled = True
                break

            current_path = local_path
            current_name = file_name

            for step_idx, step_key in enumerate(process_steps):
                if is_cancelled(user_id):
                    cancelled = True
                    break

                step_label = STEP_DEFS.get(step_key, {}).get("label", step_key)
                await _update_status(
                    status_msg,
                    f"⚙️ *{file_num}/{total_files}* — `{current_name}`\n"
                    f"Qadam {step_idx + 1}/{len(process_steps)}: {step_label}",
                )

                new_path, new_name = await _run_step(
                    step_key, current_path, current_name,
                    status_msg=status_msg, user_id=user_id,
                )

                if current_path != local_path and os.path.exists(current_path) and current_path != new_path:
                    os.remove(current_path)

                current_path = new_path
                current_name = new_name

            if cancelled:
                break

            r2_url = None
            if do_r2 and os.path.exists(current_path):
                from utils.r2_manager import upload_file as r2_upload, is_configured as r2_ok

                if r2_ok():
                    r2_size = os.path.getsize(current_path)
                    r2_last = [-1]

                    async def _r2_progress(uploaded, total, pct):
                        if pct - r2_last[0] < 5:
                            return
                        r2_last[0] = pct
                        await _update_status(
                            status_msg,
                            f"☁️ *{file_num}/{total_files}* — `{current_name}`\n"
                            f"{_progress_bar(pct)} `{pct}%`\n"
                            f"`{_fmt_size(uploaded)}` / `{_fmt_size(total)}`",
                        )

                    safe = sanitize_filename(current_name)
                    object_key = join_key("batch", str(user_id), f"{uuid.uuid4().hex[:8]}_{safe}")
                    r2_url = await r2_upload(current_path, object_key, progress_cb=_r2_progress)

                    await query.message.reply_text(
                        f"☁️ *R2:* `{current_name}`\n🔗 `{r2_url}`",
                        parse_mode="Markdown",
                    )
                else:
                    await query.message.reply_text("⚠️ R2 sozlanmagan.", parse_mode="Markdown")

            if send_telegram and os.path.exists(current_path):
                from utils.ffmpeg_utils import downscale_for_telegram_async, get_video_resolution

                current_file_size = os.path.getsize(current_path)
                PYROGRAM_LIMIT = 2 * 1024 * 1024 * 1024

                if current_file_size <= PYROGRAM_LIMIT:
                    await _update_status(
                        status_msg,
                        f"📤 *{file_num}/{total_files}* — `{current_name}`\nYuborilmoqda...",
                    )
                    await send_file(
                        query.message, current_path, current_name,
                        f"✅ {file_num}/{total_files} | {current_name}",
                        context=context, force_document=True,
                    )
                else:
                    _w, _h = get_video_resolution(current_path)
                    _candidates = [h for h in (720, 540, 480) if _h == 0 or h < _h]
                    _tg_sent = False
                    for _target_h in _candidates:
                        if is_cancelled(user_id):
                            break
                        _ok, _ds_path, _ = await downscale_for_telegram_async(
                            current_path, _target_h, status_msg, user_id=user_id,
                        )
                        if not _ok or not os.path.exists(_ds_path):
                            continue
                        if os.path.getsize(_ds_path) <= PYROGRAM_LIMIT:
                            _tg_name = f"{os.path.splitext(current_name)[0]}_{_target_h}p.mp4"
                            await send_file(
                                query.message, _ds_path, _tg_name,
                                f"✅ {file_num}/{total_files} | {_target_h}p",
                                context=context, force_document=True,
                            )
                            os.remove(_ds_path)
                            _tg_sent = True
                            break
                        os.remove(_ds_path)
                    if not _tg_sent and do_r2:
                        await query.message.reply_text(
                            f"⚠️ `{current_name}` — Telegram uchun juda katta, faqat R2 da.",
                            parse_mode="Markdown",
                        )

            results.append({"name": file_name, "ok": True, "r2_url": r2_url})

        except Exception as e:
            results.append({"name": file_name, "ok": False, "error": str(e)[:120]})
            await _update_status(
                status_msg,
                f"⚠️ *{file_num}/{total_files}* — `{file_name}`\n❌ {str(e)[:80]}\nKeyingisi...",
            )
            await asyncio.sleep(0.5)

        finally:
            for p in (current_path, local_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

        if cancelled:
            break

    clear_task(user_id)
    elapsed = int(time.time() - t_start)
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count
    skipped = total_files - len(results)

    lines = [
        "🏁 *Batch yakunlandi!*" if not cancelled else "⛔ *Batch bekor qilindi*",
        f"✅ Muvaffaqiyat: *{ok_count}/{total_files}*",
        f"⏱ Vaqt: *{elapsed // 60}m {elapsed % 60}s*",
    ]
    if fail_count:
        lines.append(f"❌ Xato: *{fail_count}*")
        for r in results:
            if not r["ok"]:
                lines.append(f"  • `{r['name'][:28]}` — {r.get('error', '?')[:50]}")
    if cancelled and skipped:
        lines.append(f"⏭ O'tkazildi: *{skipped}* fayl")
    r2_count = sum(1 for r in results if r.get("r2_url"))
    if r2_count:
        lines.append(f"☁️ R2: *{r2_count}* ta")

    await _update_status(status_msg, "\n".join(lines), show_cancel=False)

    context.user_data.pop("batch_current_template", None)
    context.user_data.pop("batch_files", None)

    await query.message.reply_text(
        "📦 Yangi batch uchun /batch yoki menyu:",
        reply_markup=main_menu_keyboard(),
    )


# ── Qadam bajaruvchilar ───────────────────────────────────────────────────────

async def _run_step(
    step_key: str, input_path: str, input_name: str,
    status_msg=None, user_id: int = 0,
) -> tuple[str, str]:
    base = os.path.splitext(input_name)[0]

    if step_key == "stream_remove_extra_audio":
        return await _step_keep_first_audio_no_subs(input_path, input_name, user_id)
    if step_key == "remove_all_subs":
        return await _step_remove_subs(input_path, input_name, user_id)
    if step_key == "convert_mp4":
        return await _step_convert(input_path, base, "mp4", user_id)
    if step_key == "convert_mkv":
        return await _step_convert(input_path, base, "mkv", user_id)
    if step_key == "compress_high":
        return await _step_compress(input_path, base, 18, status_msg, user_id)
    if step_key == "compress_medium":
        return await _step_compress(input_path, base, 23, status_msg, user_id)
    if step_key == "compress_low":
        return await _step_compress(input_path, base, 28, status_msg, user_id)
    if step_key == "remove_audio":
        return await _step_remove_audio(input_path, base, user_id)
    if step_key in ("res_1080", "res_720", "res_480"):
        h = {"res_1080": 1080, "res_720": 720, "res_480": 480}[step_key]
        return await _step_change_res(input_path, base, h, status_msg, user_id)
    if step_key == "faststart":
        return await _step_faststart(input_path, base, user_id)
    raise ValueError(f"Noma'lum qadam: {step_key}")


async def _get_streams(video_path: str) -> list[dict]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "stream=index,codec_type,codec_name",
            "-of", "json", video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return json.loads(stdout.decode(errors="replace")).get("streams", [])
    except Exception:
        return []


async def _run_cmd(cmd: list[str], timeout: int = 1800, user_id: int = 0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    if user_id:
        set_task_proc(user_id, proc)
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if user_id and is_cancelled(user_id):
            return -1, "Bekor qilindi"
        return proc.returncode or 0, stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Vaqt tugadi ({timeout}s)")


async def _step_keep_first_audio_no_subs(input_path: str, input_name: str, user_id: int = 0):
    streams = await _get_streams(input_path)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    map_args = []
    for s in video_streams:
        map_args += ["-map", f"0:{s['index']}"]
    if audio_streams:
        map_args += ["-map", f"0:{audio_streams[0]['index']}"]
    for s in streams:
        if s.get("codec_type") not in ("audio", "video", "subtitle"):
            map_args += ["-map", f"0:{s['index']}"]
    base = os.path.splitext(input_name)[0]
    ext = os.path.splitext(input_path)[1] or ".mkv"
    out_path = make_temp_path(ext.lstrip("."))
    cmd = ["ffmpeg", "-y", "-i", input_path] + map_args + ["-c", "copy", out_path]
    rc, stderr = await _run_cmd(cmd, 600, user_id)
    if rc != 0:
        raise RuntimeError(stderr[-800:] or "FFmpeg xato")
    return out_path, f"{base}_cleaned{ext}"


async def _step_remove_subs(input_path: str, input_name: str, user_id: int = 0):
    base = os.path.splitext(input_name)[0]
    ext = os.path.splitext(input_path)[1] or ".mkv"
    out_path = make_temp_path(ext.lstrip("."))
    cmd = ["ffmpeg", "-y", "-i", input_path, "-map", "0", "-map", "-0:s", "-c", "copy", out_path]
    rc, stderr = await _run_cmd(cmd, 600, user_id)
    if rc != 0:
        raise RuntimeError(stderr[-800:])
    return out_path, f"{base}_nosubs{ext}"


async def _step_convert(input_path: str, base_name: str, fmt: str, user_id: int = 0):
    out_path = make_temp_path(fmt)
    out_name = f"{base_name}.{fmt}"
    if fmt == "mp4":
        cmd = ["ffmpeg", "-y", "-i", input_path, "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", out_path]
    else:
        cmd = ["ffmpeg", "-y", "-i", input_path, "-c", "copy", out_path]
    rc, stderr = await _run_cmd(cmd, 1200, user_id)
    if rc != 0 and fmt == "mp4":
        cmd2 = ["ffmpeg", "-y", "-i", input_path, "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-movflags", "+faststart", out_path]
        rc, stderr = await _run_cmd(cmd2, 1800, user_id)
    if rc != 0:
        raise RuntimeError(stderr[-800:])
    return out_path, out_name


async def _step_compress(input_path: str, base_name: str, crf: int, status_msg=None, user_id: int = 0):
    out_path = make_temp_path("mp4")
    out_name = f"{base_name}_compressed.mp4"
    args = ["-i", input_path, "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-c:a", "copy", "-movflags", "+faststart", out_path]
    if status_msg and user_id:
        ok, err = await run_ffmpeg_async(
            args, status_msg, label=f"Siqish CRF{crf}", input_path=input_path, user_id=user_id,
        )
        if not ok:
            raise RuntimeError(err or "Siqish xato")
        return out_path, out_name
    cmd = ["ffmpeg", "-y"] + args
    rc, stderr = await _run_cmd(cmd, 1800, user_id)
    if rc != 0:
        raise RuntimeError(stderr[-800:])
    return out_path, out_name


async def _step_remove_audio(input_path: str, base_name: str, user_id: int = 0):
    ext = os.path.splitext(input_path)[1] or ".mp4"
    out_path = make_temp_path(ext.lstrip("."))
    cmd = ["ffmpeg", "-y", "-i", input_path, "-c:v", "copy", "-an", out_path]
    rc, stderr = await _run_cmd(cmd, 600, user_id)
    if rc != 0:
        raise RuntimeError(stderr[-800:])
    return out_path, f"{base_name}_noaudio{ext}"


async def _step_faststart(input_path: str, base_name: str, user_id: int = 0):
    """MOOV atomni faylning boshiga ko'chiradi (stream copy, qayta encode yo'q) —
    natijada video Telegram/R2 dan darhol striming bo'la boshlaydi."""
    ext = os.path.splitext(input_path)[1] or ".mp4"
    out_path = make_temp_path(ext.lstrip("."))
    cmd = ["ffmpeg", "-y", "-i", input_path, "-c", "copy", "-movflags", "+faststart", out_path]
    rc, stderr = await _run_cmd(cmd, 900, user_id)
    if rc != 0:
        raise RuntimeError(stderr[-800:])
    return out_path, f"{base_name}_fs{ext}"


async def _step_change_res(input_path: str, base_name: str, height: int, status_msg=None, user_id: int = 0):
    out_path = make_temp_path("mp4")
    out_name = f"{base_name}_{height}p.mp4"
    args = ["-i", input_path, "-vf", f"scale=-2:{height}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "copy", "-movflags", "+faststart", out_path]
    if status_msg and user_id:
        ok, err = await run_ffmpeg_async(
            args, status_msg, label=f"{height}p ga o'zgartirish", input_path=input_path, user_id=user_id,
        )
        if not ok:
            raise RuntimeError(err or "Resolution xato")
        return out_path, out_name
    cmd = ["ffmpeg", "-y"] + args
    rc, stderr = await _run_cmd(cmd, 1800, user_id)
    if rc != 0:
        raise RuntimeError(stderr[-800:])
    return out_path, out_name
