"""
batch.py — Ko'p fayllarni ketma-ket ishlash (Batch Processing)

Ishlash tartibi:
  1. Foydalanuvchi /batch yoki "📦 Batch" tugmasini bosadi
  2. Vazifa qadamlarini tugmalar orqali tanlaydi (stream_remove, convert, va h.k.)
  3. Nomlab saqlaydi (masalan "MKV tozalash")
  4. Fayllarni yuboradi (10 tagacha)
  5. "▶️ Boshlash" bosganda birma-bir qayta ishlaydi
  6. Xato bo'lsa — o'tkazib yuborib davom etadi
"""

import os
import asyncio
import subprocess
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import ContextTypes
from utils.ffmpeg_utils import make_temp_path, get_video_duration
from utils.sender import send_file
from utils.keyboards import main_menu_keyboard
from utils.db import db_load_batch_templates, db_save_batch_template, db_delete_batch_template

# ── Mavjud qadamlar ro'yxati ──────────────────────────────────────────────────

STEP_DEFS = {
    "stream_remove_extra_audio": {
        "label": "🎵 Birinchi audio qolsin (qolganlari o'chsin)",
        "desc": "Faqat birinchi audio treki saqlanadi, qolgan audio va barcha subtitrlar o'chiriladi",
    },
    "remove_all_subs": {
        "label": "📝 Barcha subtitrlarni o'chirish",
        "desc": "Barcha subtitle stream olib tashlanadi",
    },
    "convert_mp4": {
        "label": "🎬 MP4 ga o'tkazish",
        "desc": "Natijani MP4 formatiga konvertatsiya qiladi",
    },
    "convert_mkv": {
        "label": "📦 MKV ga o'tkazish",
        "desc": "Natijani MKV formatiga konvertatsiya qiladi",
    },
    "compress_high": {
        "label": "📉 Siqish (Yuqori sifat)",
        "desc": "CRF 18 bilan yuqori sifatda siqadi",
    },
    "compress_medium": {
        "label": "📉 Siqish (O'rta sifat)",
        "desc": "CRF 23 bilan o'rta sifatda siqadi",
    },
    "compress_low": {
        "label": "📉 Siqish (Kichik hajm)",
        "desc": "CRF 28 bilan kichik hajmda siqadi",
    },
    "remove_audio": {
        "label": "🔇 Ovozni to'liq o'chirish",
        "desc": "Barcha audio treklar olib tashlanadi",
    },
    "res_1080": {
        "label": "📐 1080p ga o'zgartirish",
        "desc": "Video o'lchamini 1080p ga o'zgartiradi",
    },
    "res_720": {
        "label": "📐 720p ga o'zgartirish",
        "desc": "Video o'lchamini 720p ga o'zgartiradi",
    },
    "res_480": {
        "label": "📐 480p ga o'zgartirish",
        "desc": "Video o'lchamini 480p ga o'zgartiradi",
    },
}


# ── Keyboard lar ──────────────────────────────────────────────────────────────

def batch_main_keyboard(templates: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ Yangi vazifa yaratish", callback_data="batch_new")],
    ]
    if templates:
        rows.append([InlineKeyboardButton("── Saqlangan shablonlar ──", callback_data="batch_noop")])
        for t in templates:
            rows.append([
                InlineKeyboardButton(f"▶️ {t['name']}", callback_data=f"batch_use_{t['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"batch_del_{t['id']}"),
            ])
    rows.append([InlineKeyboardButton("🔙 Asosiy menyu", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def step_select_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for key, info in STEP_DEFS.items():
        mark = "✅ " if key in selected else "⬜ "
        rows.append([InlineKeyboardButton(f"{mark}{info['label']}", callback_data=f"batch_step_{key}")])

    rows.append([InlineKeyboardButton("── ──────────────── ──", callback_data="batch_noop")])

    if selected:
        rows.append([
            InlineKeyboardButton("💾 Nomlab saqlash", callback_data="batch_save_ask"),
            InlineKeyboardButton("▶️ Saqlamas ishlatish", callback_data="batch_start_nosave"),
        ])
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="batch_menu")])
    return InlineKeyboardMarkup(rows)


def batch_files_keyboard(files: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, f in enumerate(files):
        rows.append([InlineKeyboardButton(f"📄 {i+1}. {f['name'][:35]}", callback_data="batch_noop")])

    action_row = [InlineKeyboardButton("▶️ Boshlash", callback_data="batch_run")]
    if files:
        action_row.append(InlineKeyboardButton("🗑 Tozalash", callback_data="batch_clear_files"))
    rows.append(action_row)
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="batch_menu")])
    return InlineKeyboardMarkup(rows)


# ── Show batch menu ───────────────────────────────────────────────────────────

async def show_batch_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    templates = await db_load_batch_templates(user_id)
    context.user_data["batch_templates"] = templates

    count = len(templates)
    text = (
        "📦 *Batch Processor*\n\n"
        "Ko'p fayllarni bir xil vazifa bilan ketma-ket qayta ishlash.\n\n"
        f"📋 Saqlangan shablonlar: *{count} ta*\n\n"
        "Yangi vazifa yarating yoki saqlangan shablonni tanlang:"
    )
    await query.edit_message_text(text, reply_markup=batch_main_keyboard(templates), parse_mode="Markdown")


# ── New task builder ──────────────────────────────────────────────────────────

async def show_batch_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["batch_selected_steps"] = []

    text = (
        "🛠 *Yangi Batch Vazifa*\n\n"
        "Qo'llaniladigan qadamlarni tanlang:\n"
        "_(bir nechta tanlab bo'ladi, tartib muhim)_"
    )
    await query.edit_message_text(
        text,
        reply_markup=step_select_keyboard([]),
        parse_mode="Markdown"
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
        steps_text = "\n\n📋 *Tanlangan qadamlar:*\n" + "\n".join(
            f"  {i+1}. {STEP_DEFS[s]['label']}" for i, s in enumerate(selected)
        )

    text = f"🛠 *Yangi Batch Vazifa*\n\nQo'llaniladigan qadamlarni tanlang:{steps_text}"
    try:
        await query.edit_message_text(
            text,
            reply_markup=step_select_keyboard(selected),
            parse_mode="Markdown"
        )
    except Exception:
        pass


async def handle_batch_save_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected = context.user_data.get("batch_selected_steps", [])
    if not selected:
        await query.answer("⚠️ Hech qanday qadam tanlanmadi!", show_alert=True)
        return

    context.user_data["state"] = "batch_save_name"

    steps_text = "\n".join(f"  {i+1}. {STEP_DEFS[s]['label']}" for i, s in enumerate(selected))
    await query.edit_message_text(
        f"💾 *Shablonni saqlash*\n\n"
        f"Qadamlar:\n{steps_text}\n\n"
        f"Ushbu shablon uchun nom yozing:\n"
        f"_(masalan: MKV tozalash, Seriallar compress)_",
        parse_mode="Markdown"
    )


async def handle_batch_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi shablon nomini kiritganda."""
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Nom bo'sh bo'lmasligi kerak.")
        return

    selected = context.user_data.get("batch_selected_steps", [])
    if not selected:
        await update.message.reply_text("❌ Qadam topilmadi. Qaytadan boshlang.")
        return

    user_id = update.message.from_user.id
    template_id = await db_save_batch_template(user_id, name, selected)

    context.user_data["state"] = None
    context.user_data["batch_current_template"] = {"id": template_id, "name": name, "steps": selected}
    context.user_data["batch_files"] = []

    await update.message.reply_text(
        f"✅ *'{name}'* saqlandi!\n\n"
        f"📤 Endi fayllarni yuboring (10 tagacha MKV/MP4/AVI).\n"
        f"Hamma faylni yuborganingizdan keyin *▶️ Boshlash* bosing:",
        reply_markup=batch_files_keyboard([]),
        parse_mode="Markdown"
    )


async def handle_batch_start_nosave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shablonsiz ishlatish."""
    query = update.callback_query
    await query.answer()

    selected = context.user_data.get("batch_selected_steps", [])
    if not selected:
        await query.answer("⚠️ Hech qanday qadam tanlanmadi!", show_alert=True)
        return

    context.user_data["batch_current_template"] = {"id": None, "name": "Vaqtinchalik vazifa", "steps": selected}
    context.user_data["batch_files"] = []

    steps_text = "\n".join(f"  {i+1}. {STEP_DEFS[s]['label']}" for i, s in enumerate(selected))

    await query.edit_message_text(
        f"📦 *Batch Vazifa tayyor*\n\n"
        f"Qadamlar:\n{steps_text}\n\n"
        f"📤 Endi fayllarni yuboring (10 tagacha).\n"
        f"Hamma faylni yuborganingizdan keyin *▶️ Boshlash* bosing:",
        reply_markup=batch_files_keyboard([]),
        parse_mode="Markdown"
    )


# ── Use saved template ────────────────────────────────────────────────────────

async def handle_batch_use_template(update: Update, context: ContextTypes.DEFAULT_TYPE, template_id: int):
    query = update.callback_query
    await query.answer()

    templates = context.user_data.get("batch_templates", [])
    template = next((t for t in templates if t["id"] == template_id), None)
    if not template:
        await query.answer("❌ Shablon topilmadi!", show_alert=True)
        return

    context.user_data["batch_current_template"] = template
    context.user_data["batch_files"] = []

    steps_text = "\n".join(
        f"  {i+1}. {STEP_DEFS.get(s, {}).get('label', s)}"
        for i, s in enumerate(template["steps"])
    )

    await query.edit_message_text(
        f"📦 *'{template['name']}'* shablon tanlandi!\n\n"
        f"Qadamlar:\n{steps_text}\n\n"
        f"📤 Endi fayllarni yuboring (10 tagacha).\n"
        f"Hamma faylni yuborganingizdan keyin *▶️ Boshlash* bosing:",
        reply_markup=batch_files_keyboard([]),
        parse_mode="Markdown"
    )


async def handle_batch_delete_template(update: Update, context: ContextTypes.DEFAULT_TYPE, template_id: int):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    await db_delete_batch_template(user_id, template_id)

    templates = await db_load_batch_templates(user_id)
    context.user_data["batch_templates"] = templates

    await query.edit_message_text(
        "🗑 Shablon o'chirildi!\n\nBatch menyu:",
        reply_markup=batch_main_keyboard(templates),
        parse_mode="Markdown"
    )


# ── File receiving during batch mode ─────────────────────────────────────────

async def batch_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Video handler tomonidan chaqiriladi.
    Agar batch rejimida bo'lsa, faylni listga qo'shadi va True qaytaradi.
    """
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
        VIDEO_EXTS = [".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts"]
        if mime.startswith("video/") or (doc.file_name and
                any(doc.file_name.lower().endswith(ext) for ext in VIDEO_EXTS)):
            file = doc
            file_name = doc.file_name or "video.mkv"
        else:
            return False
    else:
        return False

    files: list = context.user_data.setdefault("batch_files", [])
    if len(files) >= 10:
        await message.reply_text("⚠️ Maksimal 10 ta fayl. Avval mavjud fayllarni ishlating.")
        return True

    files.append({
        "file_id": file.file_id,
        "file_unique_id": file.file_unique_id,
        "file_size": getattr(file, "file_size", 0),
        "name": file_name,
    })

    template = context.user_data["batch_current_template"]
    steps_text = "\n".join(
        f"  {i+1}. {STEP_DEFS.get(s, {}).get('label', s)}"
        for i, s in enumerate(template["steps"])
    )

    await message.reply_text(
        f"✅ *{len(files)} ta fayl qabul qilindi*\n\n"
        f"📄 Oxirgi: `{file_name}`\n\n"
        f"Vazifa: *{template['name']}*\n"
        f"Qadamlar:\n{steps_text}\n\n"
        f"Yana fayl yuboring yoki *▶️ Boshlash* bosing:",
        reply_markup=batch_files_keyboard(files),
        parse_mode="Markdown"
    )
    return True


async def handle_batch_clear_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["batch_files"] = []

    await query.edit_message_text(
        "🗑 Fayllar tozalandi. Yangi fayllar yuboring:",
        reply_markup=batch_files_keyboard([]),
    )


# ── BATCH RUN ─────────────────────────────────────────────────────────────────

async def handle_batch_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    template = context.user_data.get("batch_current_template")
    files: list = context.user_data.get("batch_files", [])

    if not template or not template.get("steps"):
        await query.answer("❌ Vazifa topilmadi!", show_alert=True)
        return

    if not files:
        await query.answer("❌ Hech qanday fayl yo'q!", show_alert=True)
        return

    steps = template["steps"]
    total = len(files)

    status_msg = await query.message.reply_text(
        f"🚀 *Batch ishlov boshlandi!*\n\n"
        f"📋 Vazifa: *{template['name']}*\n"
        f"📁 Fayllar: *{total} ta*\n"
        f"⚙️ Qadamlar: *{len(steps)} ta*\n\n"
        f"⏳ 1/{total} tayyorlanmoqda...",
        parse_mode="Markdown"
    )
    await query.edit_message_text(f"⏳ Batch ishlov boshlandi... ({total} fayl)")

    results = []

    for idx, file_info in enumerate(files):
        file_num = idx + 1
        file_name = file_info["name"]

        try:
            await status_msg.edit_text(
                f"⏳ *{file_num}/{total}* — `{file_name}`\n\n"
                f"⬇️ Yuklanmoqda...",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        try:
            # Faylni yuklab olish
            from telegram import Bot
            from config import TEMP_DIR
            ext = os.path.splitext(file_name)[1].lstrip(".").lower() or "mkv"
            local_path = os.path.join(TEMP_DIR, f"batch_{file_info['file_unique_id']}.{ext}")

            tg_file = await context.bot.get_file(file_info["file_id"])
            await tg_file.download_to_drive(local_path)

            # Qadamlarni ketma-ket bajarish
            current_path = local_path
            current_name = file_name

            for step_idx, step_key in enumerate(steps):
                try:
                    await status_msg.edit_text(
                        f"⚙️ *{file_num}/{total}* — `{file_name}`\n"
                        f"🔧 Qadam {step_idx+1}/{len(steps)}: {STEP_DEFS.get(step_key, {}).get('label', step_key)}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                new_path, new_name = await _run_step(step_key, current_path, current_name)

                if current_path != local_path and os.path.exists(current_path) and current_path != new_path:
                    os.remove(current_path)

                current_path = new_path
                current_name = new_name

            # Natijani yuborish
            await status_msg.edit_text(
                f"📤 *{file_num}/{total}* — `{current_name}`\n"
                f"✅ Qayta ishlandi, yuborilmoqda...",
                parse_mode="Markdown"
            )
            await send_file(query.message, current_path, current_name,
                            f"✅ {file_num}/{total} | {current_name}", context=context)

            # Vaqtinchalik fayllarni tozalash
            if os.path.exists(current_path):
                os.remove(current_path)
            if os.path.exists(local_path) and local_path != current_path:
                try:
                    os.remove(local_path)
                except Exception:
                    pass

            results.append({"name": file_name, "ok": True})

        except Exception as e:
            results.append({"name": file_name, "ok": False, "error": str(e)[:100]})
            # Xatolik bo'lsa o'tkazib yuborish
            try:
                await status_msg.edit_text(
                    f"⚠️ *{file_num}/{total}* — `{file_name}`\n"
                    f"❌ Xato: {str(e)[:80]}\n"
                    f"➡️ Keyingisiga o'tilmoqda...",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            await asyncio.sleep(1)

            # Vaqtinchalik fayllarni tozalash
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
            except Exception:
                pass

    # Yakuniy hisobot
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = total - ok_count

    summary_lines = [
        f"🏁 *Batch yakunlandi!*\n",
        f"✅ Muvaffaqiyatli: *{ok_count}/{total}*",
    ]
    if fail_count:
        summary_lines.append(f"❌ Xato: *{fail_count} ta*")
        for r in results:
            if not r["ok"]:
                summary_lines.append(f"  • `{r['name'][:30]}` — {r.get('error', 'Xato')[:60]}")

    summary_lines.append("\n📤 Barcha natijalar yuborildi!")

    try:
        await status_msg.edit_text("\n".join(summary_lines), parse_mode="Markdown")
    except Exception:
        pass

    # Batch holatini tozalash
    context.user_data.pop("batch_current_template", None)
    context.user_data.pop("batch_files", None)

    await query.message.reply_text(
        "Yangi video yuboring yoki /start bosing.",
        reply_markup=main_menu_keyboard()
    )


# ── Step executors ────────────────────────────────────────────────────────────

async def _run_step(step_key: str, input_path: str, input_name: str) -> tuple[str, str]:
    """Bitta qadamni bajaradi. (new_path, new_name) qaytaradi."""
    base = os.path.splitext(input_name)[0]

    if step_key == "stream_remove_extra_audio":
        return await _step_keep_first_audio_no_subs(input_path, input_name)

    elif step_key == "remove_all_subs":
        return await _step_remove_subs(input_path, input_name)

    elif step_key == "convert_mp4":
        return await _step_convert(input_path, base, "mp4")

    elif step_key == "convert_mkv":
        return await _step_convert(input_path, base, "mkv")

    elif step_key == "compress_high":
        return await _step_compress(input_path, base, 18)

    elif step_key == "compress_medium":
        return await _step_compress(input_path, base, 23)

    elif step_key == "compress_low":
        return await _step_compress(input_path, base, 28)

    elif step_key == "remove_audio":
        return await _step_remove_audio(input_path, base)

    elif step_key in ("res_1080", "res_720", "res_480"):
        h = {"res_1080": 1080, "res_720": 720, "res_480": 480}[step_key]
        return await _step_change_res(input_path, base, h)

    else:
        raise ValueError(f"Noma'lum qadam: {step_key}")


async def _get_streams(video_path: str) -> list[dict]:
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "stream=index,codec_type,codec_name",
         "-of", "json", video_path],
        capture_output=True, text=True, timeout=30
    )
    data = json.loads(result.stdout)
    return data.get("streams", [])


async def _step_keep_first_audio_no_subs(input_path: str, input_name: str) -> tuple[str, str]:
    """Faqat birinchi audio qolsin, subtitrlar o'chsin."""
    streams = await _get_streams(input_path)

    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    # subtitle va data olib tashlanadi

    map_args = []
    for s in video_streams:
        map_args += ["-map", f"0:{s['index']}"]

    if audio_streams:
        # Faqat birinchi audio
        map_args += ["-map", f"0:{audio_streams[0]['index']}"]

    # attachment (shrift, thumbnail) lar ham qoladi
    attachment_streams = [s for s in streams if s.get("codec_type") not in ("audio", "video", "subtitle")]
    for s in attachment_streams:
        map_args += ["-map", f"0:{s['index']}"]

    base = os.path.splitext(input_name)[0]
    ext = os.path.splitext(input_path)[1] or ".mkv"
    out_path = make_temp_path(ext.lstrip("."))
    out_name = f"{base}_cleaned{ext}"

    cmd = ["ffmpeg", "-y", "-i", input_path] + map_args + ["-c", "copy", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:])
    return out_path, out_name


async def _step_remove_subs(input_path: str, input_name: str) -> tuple[str, str]:
    """Barcha subtitrlarni olib tashlash."""
    base = os.path.splitext(input_name)[0]
    ext = os.path.splitext(input_path)[1] or ".mkv"
    out_path = make_temp_path(ext.lstrip("."))
    out_name = f"{base}_nosubs{ext}"

    cmd = ["ffmpeg", "-y", "-i", input_path, "-map", "0", "-map", "-0:s", "-c", "copy", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:])
    return out_path, out_name


async def _step_convert(input_path: str, base_name: str, fmt: str) -> tuple[str, str]:
    """Formatni o'zgartirish."""
    out_path = make_temp_path(fmt)
    out_name = f"{base_name}.{fmt}"

    if fmt == "mp4":
        cmd = ["ffmpeg", "-y", "-i", input_path,
               "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", out_path]
    else:
        cmd = ["ffmpeg", "-y", "-i", input_path, "-c", "copy", out_path]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if result.returncode != 0:
        # Fallback: stream copy ishlamasa re-encode qilamiz
        if fmt == "mp4":
            cmd2 = ["ffmpeg", "-y", "-i", input_path,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-c:a", "aac", "-movflags", "+faststart", out_path]
            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=1800)
            if result2.returncode != 0:
                raise RuntimeError(result2.stderr[-800:])
        else:
            raise RuntimeError(result.stderr[-800:])
    return out_path, out_name


async def _step_compress(input_path: str, base_name: str, crf: int) -> tuple[str, str]:
    """Video siqish."""
    out_path = make_temp_path("mp4")
    out_name = f"{base_name}_compressed.mp4"

    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
           "-c:a", "copy", "-movflags", "+faststart", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:])
    return out_path, out_name


async def _step_remove_audio(input_path: str, base_name: str) -> tuple[str, str]:
    """Ovozni o'chirish."""
    ext = os.path.splitext(input_path)[1] or ".mp4"
    out_path = make_temp_path(ext.lstrip("."))
    out_name = f"{base_name}_noaudio{ext}"

    cmd = ["ffmpeg", "-y", "-i", input_path, "-c:v", "copy", "-an", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:])
    return out_path, out_name


async def _step_change_res(input_path: str, base_name: str, height: int) -> tuple[str, str]:
    """O'lcham o'zgartirish."""
    out_path = make_temp_path("mp4")
    out_name = f"{base_name}_{height}p.mp4"

    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-vf", f"scale=-2:{height}",
           "-c:v", "libx264", "-preset", "fast", "-crf", "22",
           "-c:a", "copy", "-movflags", "+faststart", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:])
    return out_path, out_name
