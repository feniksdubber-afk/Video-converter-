import os
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from config import TEMP_DIR

SUPPORTED_FORMATS = {".srt", ".ass", ".ssa", ".vtt"}

LANGUAGES = {
    "uz":    "🇺🇿 O'zbek",
    "ru":    "🇷🇺 Rus",
    "en":    "🇬🇧 Ingliz",
    "tr":    "🇹🇷 Turk",
    "ko":    "🇰🇷 Koreys",
    "ja":    "🇯🇵 Yapon",
    "ar":    "🇸🇦 Arab",
    "de":    "🇩🇪 Nemis",
    "fr":    "🇫🇷 Fransuz",
    "zh-CN": "🇨🇳 Xitoy",
}


def _lang_keyboard() -> InlineKeyboardMarkup:
    rows = []
    items = list(LANGUAGES.items())
    for i in range(0, len(items), 2):
        row = [
            InlineKeyboardButton(label, callback_data=f"subtrans_{code}")
            for code, label in items[i:i + 2]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(rows)


async def show_sub_translate_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "sub_translate_wait"
    await query.edit_message_text(
        "🌐 *Subtitr Tarjimon*\n\n"
        "Tarjima qilish uchun subtitr faylini yuboring:\n"
        "• `.srt` — oddiy subtitr\n"
        "• `.ass` / `.ssa` — stilizatsiyalangan\n"
        "• `.vtt` — WebVTT\n\n"
        "📌 Tarjima *Google Translate* orqali amalga oshiriladi.",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_sub_translate_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Fayl topilmadi.", reply_markup=cancel_keyboard())
        return

    file_name = doc.file_name or "subtitle.srt"
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        await update.message.reply_text(
            "❌ Noto'g'ri format. `.srt`, `.ass`, `.ssa`, `.vtt` qabul qilinadi.",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    sub_path = os.path.join(TEMP_DIR, f"subtrans_{doc.file_unique_id}{ext}")
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(sub_path)

    context.user_data["sub_translate_path"] = sub_path
    context.user_data["sub_translate_ext"] = ext
    context.user_data["sub_translate_name"] = file_name
    context.user_data["state"] = "sub_translate_lang"

    await update.message.reply_text(
        "✅ Subtitr qabul qilindi!\n\nQaysi tilga tarjima qilaylik?",
        reply_markup=_lang_keyboard(),
    )


async def handle_sub_translate_lang(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    query = update.callback_query
    await query.answer()

    sub_path = context.user_data.get("sub_translate_path")
    if not sub_path or not os.path.exists(sub_path):
        await query.edit_message_text("❌ Subtitr fayl topilmadi. Qaytadan boshlang.")
        return

    ext = context.user_data.get("sub_translate_ext", ".srt")
    file_name = context.user_data.get("sub_translate_name", "subtitle.srt")
    lang_name = LANGUAGES.get(lang_code, lang_code)
    context.user_data["state"] = None

    await query.edit_message_text(
        f"⏳ *{lang_name}* tiliga tarjima qilinmoqda...\n\n"
        "Bu bir necha daqiqa olishi mumkin.",
        parse_mode="Markdown",
    )

    try:
        with open(sub_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if ext == ".srt":
            translated = await _translate_srt(content, lang_code)
        elif ext in (".ass", ".ssa"):
            translated = await _translate_ass(content, lang_code)
        elif ext == ".vtt":
            translated = await _translate_vtt(content, lang_code)
        else:
            translated = content

        base = os.path.splitext(file_name)[0]
        out_name = f"{base}_{lang_code}{ext}"
        out_path = os.path.join(TEMP_DIR, out_name)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(translated)

        if os.path.exists(sub_path):
            os.remove(sub_path)
        context.user_data.pop("sub_translate_path", None)

        await query.message.reply_document(
            document=open(out_path, "rb"),
            filename=out_name,
            caption=f"✅ *{lang_name}* tiliga tarjima qilindi!",
            parse_mode="Markdown",
        )
        if os.path.exists(out_path):
            os.remove(out_path)

        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())

    except Exception as e:
        if os.path.exists(sub_path):
            os.remove(sub_path)
        context.user_data.pop("sub_translate_path", None)
        await query.edit_message_text(
            f"❌ Tarjimada xato:\n`{str(e)[:300]}`\n\n"
            "Internet yoki tarjima xizmati bilan muammo bo'lishi mumkin.",
            parse_mode="Markdown",
        )
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


# ── Internal translation helpers ─────────────────────────────────────────────

async def _translate_batch(texts: list[str], lang_code: str) -> list[str]:
    """Translate list of strings using batch API calls (much faster)."""
    from deep_translator import GoogleTranslator
    results = []
    batch_size = 50  # GoogleTranslator batch limit

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]

        # Separate empty strings (no need to translate)
        non_empty_indices = [j for j, t in enumerate(batch) if t.strip()]
        non_empty_texts = [batch[j].strip() for j in non_empty_indices]

        translated_map = {}
        if non_empty_texts:
            try:
                translator = GoogleTranslator(source="auto", target=lang_code)
                # translate_batch sends all texts in ONE request
                translated_list = translator.translate_batch(non_empty_texts)
                for j, tr in zip(non_empty_indices, translated_list):
                    translated_map[j] = tr or batch[j]
            except Exception:
                # Fallback: keep originals if batch fails
                for j in non_empty_indices:
                    translated_map[j] = batch[j]

        for j, text in enumerate(batch):
            results.append(translated_map.get(j, text))

        if i + batch_size < len(texts):
            await asyncio.sleep(0.3)

    return results


async def _translate_srt(content: str, lang_code: str) -> str:
    blocks = re.split(r"\n\s*\n", content.strip())
    texts = []
    has_text = []

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            texts.append("\n".join(lines[2:]))
            has_text.append(True)
        else:
            texts.append("")
            has_text.append(False)

    to_translate = [t for t, h in zip(texts, has_text) if h]
    translated = await _translate_batch(to_translate, lang_code)

    t_iter = iter(translated)
    result_blocks = []
    for block, h in zip(blocks, has_text):
        lines = block.strip().split("\n")
        if h and len(lines) >= 3:
            new_text = next(t_iter)
            result_blocks.append("\n".join(lines[:2]) + "\n" + new_text)
        else:
            result_blocks.append(block.strip())

    return "\n\n".join(result_blocks) + "\n"


async def _translate_ass(content: str, lang_code: str) -> str:
    lines = content.split("\n")
    texts = []
    line_indices = []

    for i, line in enumerate(lines):
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) == 10:
                raw_text = parts[9]
                clean = re.sub(r"\{[^}]*\}", "", raw_text)
                texts.append(clean)
                line_indices.append((i, parts, raw_text))
            else:
                line_indices.append(None)
        else:
            line_indices.append(None)

    translated = await _translate_batch(texts, lang_code)
    t_iter = iter(translated)

    result_lines = list(lines)
    for i, line in enumerate(lines):
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) == 10:
                raw_text = parts[9]
                overrides = re.findall(r"\{[^}]*\}", raw_text)
                prefix = overrides[0] if overrides else ""
                new_text = next(t_iter)
                parts[9] = prefix + new_text
                result_lines[i] = ",".join(parts)

    return "\n".join(result_lines)


async def _translate_vtt(content: str, lang_code: str) -> str:
    lines = content.split("\n")
    texts = []
    text_groups = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if "-->" in line:
            j = i + 1
            group_lines = []
            while j < len(lines) and lines[j].strip() != "":
                group_lines.append(j)
                j += 1
            if group_lines:
                texts.append("\n".join(lines[k] for k in group_lines))
                text_groups.append(group_lines)
            i = j
        else:
            i += 1

    translated = await _translate_batch(texts, lang_code)
    t_iter = iter(translated)

    result_lines = list(lines)
    for group in text_groups:
        trans = next(t_iter).split("\n")
        for idx, ln_idx in enumerate(group):
            result_lines[ln_idx] = trans[idx] if idx < len(trans) else ""

    return "\n".join(result_lines)
