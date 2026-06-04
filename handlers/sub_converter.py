import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from config import TEMP_DIR

SUPPORTED_FORMATS = {".srt", ".ass", ".ssa", ".vtt"}

FORMAT_LABELS = {
    "srt": "SRT",
    "ass": "ASS",
    "vtt": "VTT",
}

CONVERSIONS = {
    ".srt":  ["ass", "vtt"],
    ".ass":  ["srt", "vtt"],
    ".ssa":  ["srt", "vtt"],
    ".vtt":  ["srt", "ass"],
}


def _target_keyboard(ext: str) -> InlineKeyboardMarkup:
    targets = CONVERSIONS.get(ext, [])
    rows = [
        [InlineKeyboardButton(FORMAT_LABELS[t], callback_data=f"subconv_{t}")]
        for t in targets
    ]
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(rows)


async def show_sub_converter_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "sub_converter_wait"
    await query.edit_message_text(
        "🔄 *Subtitr Konvertor*\n\n"
        "Formatini o'zgartirish uchun subtitr faylini yuboring:\n"
        "• `.srt` → ASS, VTT\n"
        "• `.ass` / `.ssa` → SRT, VTT\n"
        "• `.vtt` → SRT, ASS",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_sub_converter_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    sub_path = os.path.join(TEMP_DIR, f"subconv_{doc.file_unique_id}{ext}")
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(sub_path)

    context.user_data["sub_conv_path"] = sub_path
    context.user_data["sub_conv_ext"] = ext
    context.user_data["sub_conv_name"] = file_name
    context.user_data["state"] = "sub_converter_fmt"

    src_fmt = ext.lstrip(".")
    targets = CONVERSIONS.get(ext, [])
    targets_str = ", ".join(FORMAT_LABELS[t] for t in targets)

    await update.message.reply_text(
        f"✅ *{src_fmt.upper()}* fayl qabul qilindi!\n\n"
        f"Qaysi formatga o'giraylik?\nMumkin: {targets_str}",
        reply_markup=_target_keyboard(ext),
        parse_mode="Markdown",
    )


async def handle_sub_converter_format(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target_fmt: str
):
    query = update.callback_query
    await query.answer()

    sub_path = context.user_data.get("sub_conv_path")
    if not sub_path or not os.path.exists(sub_path):
        await query.edit_message_text("❌ Subtitr fayl topilmadi. Qaytadan boshlang.")
        return

    ext = context.user_data.get("sub_conv_ext", ".srt")
    file_name = context.user_data.get("sub_conv_name", "subtitle.srt")
    context.user_data["state"] = None

    await query.edit_message_text(
        f"⏳ *{ext.lstrip('.').upper()}* → *{target_fmt.upper()}* formatiga o'girilmoqda...",
        parse_mode="Markdown",
    )

    try:
        with open(sub_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if target_fmt == "srt":
            if ext in (".ass", ".ssa"):
                converted = _ass_to_srt(content)
            elif ext == ".vtt":
                converted = _vtt_to_srt(content)
            else:
                converted = content
        elif target_fmt == "ass":
            if ext == ".srt":
                converted = _srt_to_ass(content)
            elif ext == ".vtt":
                converted = _srt_to_ass(_vtt_to_srt(content))
            else:
                converted = content
        elif target_fmt == "vtt":
            if ext == ".srt":
                converted = _srt_to_vtt(content)
            elif ext in (".ass", ".ssa"):
                converted = _srt_to_vtt(_ass_to_srt(content))
            else:
                converted = content
        else:
            converted = content

        base = os.path.splitext(file_name)[0]
        out_name = f"{base}.{target_fmt}"
        out_path = os.path.join(TEMP_DIR, out_name)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(converted)

        if os.path.exists(sub_path):
            os.remove(sub_path)
        context.user_data.pop("sub_conv_path", None)

        await query.message.reply_document(
            document=open(out_path, "rb"),
            filename=out_name,
            caption=f"✅ *{ext.lstrip('.').upper()}* → *{target_fmt.upper()}* muvaffaqiyatli o'girildi!",
            parse_mode="Markdown",
        )
        if os.path.exists(out_path):
            os.remove(out_path)

        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())

    except Exception as e:
        if os.path.exists(sub_path):
            os.remove(sub_path)
        context.user_data.pop("sub_conv_path", None)
        await query.edit_message_text(
            f"❌ Konvertatsiyada xato:\n`{str(e)[:300]}`",
            parse_mode="Markdown",
        )
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


# ── Conversion logic ─────────────────────────────────────────────────────────

def _srt_ts_to_vtt(ts: str) -> str:
    """00:00:01,500 → 00:00:01.500"""
    return ts.replace(",", ".")


def _vtt_ts_to_srt(ts: str) -> str:
    """00:00:01.500 → 00:00:01,500"""
    return ts.replace(".", ",", 1) if "." in ts else ts + ",000"


def _srt_ts_to_ass(ts: str) -> str:
    """00:00:01,500 → 0:00:01.50"""
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    cs = ms[:2]
    return f"{int(h)}:{m}:{s}.{cs}"


def _ass_ts_to_srt(ts: str) -> str:
    """0:00:01.50 → 00:00:01,500"""
    h, m, rest = ts.split(":")
    s, cs = rest.split(".")
    ms = cs.ljust(3, "0")[:3]
    return f"{int(h):02d}:{m}:{s},{ms}"


def _srt_to_vtt(content: str) -> str:
    blocks = re.split(r"\n\s*\n", content.strip())
    result = ["WEBVTT", ""]
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        ts_line = lines[1]
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", ts_line
        )
        if ts_match:
            start = _srt_ts_to_vtt(ts_match.group(1))
            end = _srt_ts_to_vtt(ts_match.group(2))
            result.append(f"{start} --> {end}")
            result.extend(lines[2:])
            result.append("")
    return "\n".join(result)


def _vtt_to_srt(content: str) -> str:
    lines = content.split("\n")
    blocks = []
    i = 0
    counter = 1

    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            ts_match = re.match(
                r"(\d{2}:\d{2}:\d{2}[\.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[\.,]\d{3})", line
            )
            if ts_match:
                start = _vtt_ts_to_srt(ts_match.group(1))
                end = _vtt_ts_to_srt(ts_match.group(2))
                text_lines = []
                j = i + 1
                while j < len(lines) and lines[j].strip() != "":
                    text_lines.append(lines[j])
                    j += 1
                if text_lines:
                    blocks.append(f"{counter}\n{start} --> {end}\n" + "\n".join(text_lines))
                    counter += 1
                i = j
                continue
        i += 1

    return "\n\n".join(blocks) + "\n"


def _srt_to_ass(content: str) -> str:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1280\n"
        "PlayResY: 720\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,10,10,20,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    blocks = re.split(r"\n\s*\n", content.strip())
    dialogues = []

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", lines[1]
        )
        if ts_match:
            start = _srt_ts_to_ass(ts_match.group(1))
            end = _srt_ts_to_ass(ts_match.group(2))
            text = r"\N".join(lines[2:])
            dialogues.append(
                f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"
            )

    return header + "\n".join(dialogues) + "\n"


def _ass_to_srt(content: str) -> str:
    lines = content.split("\n")
    blocks = []
    counter = 1

    for line in lines:
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        try:
            start = _ass_ts_to_srt(parts[1].strip())
            end = _ass_ts_to_srt(parts[2].strip())
        except Exception:
            continue
        raw_text = parts[9]
        text = re.sub(r"\{[^}]*\}", "", raw_text)
        text = text.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
        text = text.strip()
        if text:
            blocks.append(f"{counter}\n{start} --> {end}\n{text}")
            counter += 1

    return "\n\n".join(blocks) + "\n"
