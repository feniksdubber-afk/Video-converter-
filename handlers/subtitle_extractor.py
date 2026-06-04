import os
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from utils.ffmpeg_utils import make_temp_path
from utils.sender import send_file


# ── format helpers ────────────────────────────────────────────────────────────

SUBTITLE_FORMATS = {
    "srt":  "SRT (.srt)",
    "ass":  "ASS/SSA (.ass)",
    "vtt":  "WebVTT (.vtt)",
}

# ffmpeg codec name → native extension
CODEC_EXT = {
    "subrip": "srt",
    "ass":    "ass",
    "ssa":    "ass",
    "webvtt": "vtt",
    "hdmv_pgs_subtitle": "sup",
    "dvd_subtitle": "sub",
}


def _sub_streams(streams: list[dict]) -> list[dict]:
    return [s for s in streams if s.get("codec_type") == "subtitle"]


def _stream_label(s: dict) -> str:
    idx   = s.get("index", "?")
    codec = s.get("codec_name", "?")
    tags  = s.get("tags", {})
    lang  = tags.get("language", "")
    title = tags.get("title", "")
    label = f"#{idx} [{codec}]"
    if lang:
        label += f" {lang}"
    if title:
        label += f" — {title}"
    return label


def _sub_list_keyboard(streams: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(
        "📤 Barcha subtitrlarni chiqarib olish", callback_data="subext_all")])
    for s in streams:
        rows.append([InlineKeyboardButton(
            _stream_label(s), callback_data=f"subext_pick_{s['index']}")])
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def _fmt_keyboard(stream_idx: int) -> InlineKeyboardMarkup:
    rows = []
    for fmt, label in SUBTITLE_FORMATS.items():
        rows.append([InlineKeyboardButton(
            label, callback_data=f"subext_fmt_{stream_idx}_{fmt}")])
    rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="subtitle_extractor")])
    return InlineKeyboardMarkup(rows)


# ── entry point ───────────────────────────────────────────────────────────────

async def show_subtitle_extractor_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    from handlers.streams import _get_streams
    streams = _get_streams(video_path)
    subs = _sub_streams(streams)

    if not subs:
        await query.edit_message_text(
            "❌ Bu videoda subtitr stream topilmadi.",
            reply_markup=main_menu_keyboard(),
        )
        return

    context.user_data["streams"] = streams
    context.user_data["state"] = "subtitle_extractor"

    sub_info = "\n".join(f"  • {_stream_label(s)}" for s in subs)
    await query.edit_message_text(
        f"📝 *Subtitr Extractor*\n\n"
        f"Topilgan subtitrlar:\n{sub_info}\n\n"
        f"Qaysi subtitrni chiqarib olmoqchisiz?",
        reply_markup=_sub_list_keyboard(subs),
        parse_mode="Markdown",
    )


# ── pick stream → choose format ───────────────────────────────────────────────

async def handle_subext_pick(update: Update, context: ContextTypes.DEFAULT_TYPE, stream_idx: int):
    query = update.callback_query
    await query.answer()

    streams = context.user_data.get("streams", [])
    target = next((s for s in streams if s.get("index") == stream_idx), None)
    if not target:
        await query.edit_message_text("❌ Stream topilmadi.")
        return

    context.user_data["subext_target_idx"] = stream_idx

    label = _stream_label(target)
    await query.edit_message_text(
        f"📝 *Subtitr Extractor*\n\n"
        f"Tanlangan: `{label}`\n\n"
        f"Qaysi formatda saqlash?",
        reply_markup=_fmt_keyboard(stream_idx),
        parse_mode="Markdown",
    )


# ── extract one stream in chosen format ──────────────────────────────────────

async def handle_subext_format(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    stream_idx: int,
    fmt: str,
):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    output_path = make_temp_path(fmt)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-map", f"0:{stream_idx}",
        output_path,
    ]

    status_msg = await query.message.reply_text(
        f"⏳ *#{stream_idx} subtitr → {fmt.upper()} formatiga o'tkazilmoqda...*",
        parse_mode="Markdown",
    )
    await query.edit_message_text(
        f"⏳ #{stream_idx} subtitr ajratilmoqda → {fmt.upper()}..."
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-1500:])

        video_name = context.user_data.get("video_name", "video")
        base = os.path.splitext(video_name)[0]

        streams = context.user_data.get("streams", [])
        target = next((s for s in streams if s.get("index") == stream_idx), {})
        lang = target.get("tags", {}).get("language", "")
        out_name = f"{base}_sub{stream_idx}"
        if lang:
            out_name += f"_{lang}"
        out_name += f".{fmt}"

        await status_msg.edit_text(
            f"✅ *Tayyor!* `{out_name}`\n📤 Yuborilmoqda...",
            parse_mode="Markdown",
        )
        await send_file(query.message, output_path, out_name,
                        f"✅ #{stream_idx} subtitr ({fmt.upper()})", context=context)
        os.remove(output_path)
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    except Exception as e:
        if os.path.exists(output_path):
            os.remove(output_path)
        await status_msg.edit_text(f"❌ Xato:\n`{e}`", parse_mode="Markdown")
        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())


# ── extract ALL subtitle streams ─────────────────────────────────────────────

async def handle_subext_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi.")
        return

    streams = context.user_data.get("streams", [])
    subs = _sub_streams(streams)
    if not subs:
        await query.answer("❌ Subtitr topilmadi!", show_alert=True)
        return

    status_msg = await query.message.reply_text(
        f"⏳ *{len(subs)} ta subtitr chiqarib olinmoqda...*",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"⏳ {len(subs)} ta subtitr ajratilmoqda...")

    video_name = context.user_data.get("video_name", "video")
    base = os.path.splitext(video_name)[0]
    sent = 0
    errors = []

    for i, s in enumerate(subs):
        idx   = s.get("index")
        codec = s.get("codec_name", "")
        lang  = s.get("tags", {}).get("language", "")

        # native extraction format; SUP can't be re-encoded to text
        fmt = CODEC_EXT.get(codec, "srt")
        # image-based subs (PGS/DVD) stay as-is; text subs → srt by default
        if fmt not in ("sup", "sub"):
            fmt = "srt"

        output_path = make_temp_path(fmt)
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-map", f"0:{idx}",
            output_path,
        ]

        try:
            await status_msg.edit_text(
                f"⏳ *Subtitrlar ajratilmoqda... ({i}/{len(subs)})*\n"
                f"Hozir: #{idx} [{codec}]",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-500:])

            out_name = f"{base}_sub{idx}"
            if lang:
                out_name += f"_{lang}"
            out_name += f".{fmt}"

            await send_file(query.message, output_path, out_name,
                            f"✅ #{idx} [{codec}]{' ' + lang if lang else ''}", context=context)
            os.remove(output_path)
            sent += 1
        except Exception as e:
            errors.append(f"#{idx}: {e}")
            if os.path.exists(output_path):
                os.remove(output_path)

    summary = f"✅ *{sent} ta subtitr yuborildi!*"
    if errors:
        summary += f"\n⚠️ {len(errors)} ta xato:\n" + "\n".join(f"`{e}`" for e in errors[:3])

    try:
        await status_msg.edit_text(summary, parse_mode="Markdown")
    except Exception:
        pass

    await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
