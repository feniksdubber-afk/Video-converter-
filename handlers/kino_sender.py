"""
kino_sender.py — Userbot orqali @Kinofilmnewbot dan kino yuborish.

Foydalanish:
  /kino 923          — payload 923 uchun 6-tugmani bosadi
  /kino 923 3        — payload 923 uchun 3-tugmani bosadi (ixtiyoriy)
"""

import asyncio
import logging
import os
import time

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID, TEMP_DIR
from handlers.save_restricted import get_user_client

logger = logging.getLogger(__name__)

KINO_BOT = "Kinofilmnewbot"
WAIT_TIMEOUT = 45

_kino_lock = asyncio.Lock()


def _progress_bar(percent: int, length: int = 14) -> str:
    filled = int(length * percent / 100)
    return "▰" * filled + "▱" * (length - filled)


def _fmt_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


async def kino_sender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    args = context.args

    if not args:
        await msg.reply_text(
            "❗ *Foydalanish:*\n"
            "`/kino 923` — 6-tugmani bosadi\n"
            "`/kino 923 3` — 3-tugmani bosadi",
            parse_mode="Markdown",
        )
        return

    payload = args[0].strip()
    button_index = 5  # default: 6-tugma (0-indexed = 5)
    if len(args) >= 2 and args[1].isdigit():
        button_index = int(args[1]) - 1

    dest_chat = ARCHIVE_GROUP_ID
    if not dest_chat:
        await msg.reply_text("❌ `ARCHIVE_GROUP_ID` `.env`da sozlanmagan!", parse_mode="Markdown")
        return

    if _kino_lock.locked():
        await msg.reply_text("⏳ Oldingi so'rov bajarilmoqda, kuting...")
        return

    status_msg = await msg.reply_text(
        f"🎬 *Kino qidirilmoqda...*\n\n`{payload}` so'rovi yuborilmoqda",
        parse_mode="Markdown",
    )

    client = await get_user_client()
    if not client:
        await status_msg.edit_text("❌ Userbot (`SESSION_STRING`) ulangmagan!", parse_mode="Markdown")
        return

    async with _kino_lock:
        try:
            await _run_kino_flow(client, payload, button_index, dest_chat, status_msg, context)
        except Exception as e:
            logger.exception("kino_sender xato: %s", e)
            await status_msg.edit_text(f"❌ *Xato yuz berdi:*\n`{e}`", parse_mode="Markdown")


async def _run_kino_flow(client, payload: str, button_index: int, dest_chat, status_msg, context):
    from pyrogram.raw import functions

    # ── 1. Kino botini resolve ────────────────────────────────────────────────
    kino_peer = await client.resolve_peer(KINO_BOT)

    # ── 2. Oxirgi xabar ID ni eslab qolamiz ──────────────────────────────────
    last_msg_id = 0
    try:
        async for h in client.get_chat_history(KINO_BOT, limit=1):
            last_msg_id = h.id
            break
    except Exception:
        pass

    # ── 3. /start <payload> yuborish ─────────────────────────────────────────
    await status_msg.edit_text(
        f"🎬 *Kino botiga so'rov yuborildi*\n\n"
        f"🔍 Bot javobini kutilmoqda...",
        parse_mode="Markdown",
    )
    await client.invoke(
        functions.messages.StartBot(
            bot=kino_peer,
            peer=kino_peer,
            random_id=client.rnd_id(),
            start_param=payload,
        )
    )

    # ── 4. Inline tugmali xabarni kutish ─────────────────────────────────────
    bot_msg = await _wait_for_new_message_with_buttons(
        client, KINO_BOT, after_id=last_msg_id, timeout=WAIT_TIMEOUT, status_msg=status_msg
    )

    if bot_msg is None:
        await status_msg.edit_text("❌ Bot javob bermadi (timeout)!\n\nQaytadan urinib ko'ring.")
        return

    # ── 5. Tugmalar ro'yxati ──────────────────────────────────────────────────
    buttons = _flatten_buttons(bot_msg)
    if not buttons:
        await status_msg.edit_text("❌ Bot tugmali javob yubordi, lekin tugmalar topilmadi!")
        return

    if button_index >= len(buttons):
        btn_list = "\n".join(f"  `{i+1}.` {b.text}" for i, b in enumerate(buttons))
        await status_msg.edit_text(
            f"❌ *{button_index+1}-tugma mavjud emas.*\n\n"
            f"📋 *Tugmalar ({len(buttons)} ta):*\n{btn_list}\n\n"
            f"Masalan: `/kino {payload} 1`",
            parse_mode="Markdown",
        )
        return

    chosen_btn = buttons[button_index]
    await status_msg.edit_text(
        f"🎬 *Kino topildi!*\n\n"
        f"🔘 `{chosen_btn.text}` tanlandi\n"
        f"⏳ Video yuklab olishni boshlash uchun so'rov yuborilmoqda...",
        parse_mode="Markdown",
    )

    # ── 6. Tugmani bosish ─────────────────────────────────────────────────────
    cb_data = chosen_btn.callback_data
    if isinstance(cb_data, str):
        cb_data = cb_data.encode("utf-8")

    try:
        await client.invoke(
            functions.messages.GetBotCallbackAnswer(
                peer=kino_peer,
                msg_id=bot_msg.id,
                data=cb_data,
            )
        )
    except Exception as e:
        logger.info("GetBotCallbackAnswer xato (normal): %s", e)

    # ── 7. Bot media yuborishini kutish ───────────────────────────────────────
    await status_msg.edit_text(
        f"🎬 *{chosen_btn.text}*\n\n"
        f"⏳ Video yuborilishini kutilmoqda...",
        parse_mode="Markdown",
    )

    media_msg = await _wait_for_new_media(
        client, KINO_BOT, after_id=bot_msg.id, timeout=WAIT_TIMEOUT
    )

    if media_msg is None:
        await status_msg.edit_text(
            f"❌ *Bot video yuboramadi* (timeout {WAIT_TIMEOUT}s)\n\n"
            f"Bot sekin ishlayotgan bo'lishi mumkin. Qaytadan urinib ko'ring.",
            parse_mode="Markdown",
        )
        return

    # ── 8. Fayl hajmini aniqlaymiz ────────────────────────────────────────────
    media_obj = media_msg.video or media_msg.document or media_msg.audio
    file_size = getattr(media_obj, "file_size", 0) or 0
    title = chosen_btn.text or "Kino"

    await status_msg.edit_text(
        f"🎬 *{title}*\n\n"
        f"⬇️ Yuklab olinmoqda...\n"
        f"`{'▱' * 14}` `0%`\n"
        f"`0.0 MB` / `{_fmt_size(file_size) if file_size else '? MB'}`",
        parse_mode="Markdown",
    )

    # ── 9. Yuklab olish (progress bilan) ─────────────────────────────────────
    tmp_path = None
    try:
        ext = _guess_ext(media_msg)
        tmp_path = os.path.join(TEMP_DIR, f"kino_{media_msg.id}_{int(time.time())}.{ext}")
        last_edit = [0.0]
        last_pct = [-1]

        async def _dl_progress(current, total):
            now = time.monotonic()
            if now - last_edit[0] < 2.5:
                return
            if not total:
                return
            pct = min(int(current / total * 100), 99)
            if pct == last_pct[0]:
                return
            last_pct[0] = pct
            last_edit[0] = now
            bar = _progress_bar(pct)
            cur_mb = current / 1024 / 1024
            tot_mb = total / 1024 / 1024
            try:
                await status_msg.edit_text(
                    f"🎬 *{title}*\n\n"
                    f"⬇️ *Yuklab olinmoqda...*\n"
                    f"`{bar}` `{pct}%`\n"
                    f"`{cur_mb:.1f} MB` / `{tot_mb:.1f} MB`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        await client.download_media(media_msg, file_name=tmp_path, progress=_dl_progress)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            await status_msg.edit_text("❌ Fayl yuklanmadi (bo'sh yoki yo'q)!")
            return

        actual_size = os.path.getsize(tmp_path)
        size_str = _fmt_size(actual_size)

        # ── 10. Metadata (thumbnail, davomiylik, o'lcham) ────────────────────
        await status_msg.edit_text(
            f"🎬 *{title}*\n\n"
            f"✅ Yuklab olindi: `{size_str}`\n"
            f"🔄 Metadata tayyorlanmoqda...",
            parse_mode="Markdown",
        )

        meta = {}
        thumb_path = None
        is_video_file = ext in {"mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "ts", "wmv"}

        if is_video_file:
            meta = await _get_video_meta(tmp_path)
            dur = meta.get("duration", 0)
            if dur > 0:
                thumb_path = await _make_thumb(tmp_path, dur)

        # ── 11. Guruhga yuborish (progress bilan) ────────────────────────────
        await status_msg.edit_text(
            f"🎬 *{title}*\n\n"
            f"📤 *Guruhga yuborilmoqda...*\n"
            f"`{'▱' * 14}` `0%`\n"
            f"`0.0 MB` / `{size_str}`",
            parse_mode="Markdown",
        )

        last_edit[0] = 0.0
        last_pct[0] = -1

        async def _ul_progress(current, total):
            now = time.monotonic()
            if now - last_edit[0] < 2.5:
                return
            if not total:
                return
            pct = min(int(current / total * 100), 99)
            if pct == last_pct[0]:
                return
            last_pct[0] = pct
            last_edit[0] = now
            bar = _progress_bar(pct)
            cur_mb = current / 1024 / 1024
            tot_mb = total / 1024 / 1024
            try:
                await status_msg.edit_text(
                    f"🎬 *{title}*\n\n"
                    f"📤 *Guruhga yuborilmoqda...*\n"
                    f"`{bar}` `{pct}%`\n"
                    f"`{cur_mb:.1f} MB` / `{tot_mb:.1f} MB`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        caption = (media_msg.caption or "").strip() or f"🎬 {title}"

        try:
            if is_video_file:
                await client.send_video(
                    chat_id=dest_chat,
                    video=tmp_path,
                    caption=caption,
                    supports_streaming=True,
                    duration=meta.get("duration") or None,
                    width=meta.get("width") or None,
                    height=meta.get("height") or None,
                    thumb=thumb_path or None,
                    progress=_ul_progress,
                )
            else:
                await client.send_document(
                    chat_id=dest_chat,
                    document=tmp_path,
                    caption=caption,
                    thumb=thumb_path or None,
                    progress=_ul_progress,
                )
        finally:
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass

        # ── 12. Yakuniy xabar ─────────────────────────────────────────────────
        dur_str = ""
        if meta.get("duration"):
            m, s = divmod(int(meta["duration"]), 60)
            h, m = divmod(m, 60)
            dur_str = f"\n⏱ Davomiyligi: `{h:02d}:{m:02d}:{s:02d}`" if h else f"\n⏱ Davomiyligi: `{m:02d}:{s:02d}`"

        res_str = ""
        if meta.get("width") and meta.get("height"):
            res_str = f"\n📐 O'lchami: `{meta['width']}×{meta['height']}`"

        await status_msg.edit_text(
            f"✅ *{title}*\n\n"
            f"📦 Hajmi: `{size_str}`"
            f"{dur_str}"
            f"{res_str}\n\n"
            f"🗂 Guruhga muvaffaqiyatli yuborildi!",
            parse_mode="Markdown",
        )

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ── Video metadata ────────────────────────────────────────────────────────────

import subprocess
import uuid


def _get_video_meta_sync(file_path: str) -> dict:
    meta = {"duration": 0, "width": 0, "height": 0}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1",
             file_path],
            capture_output=True, text=True, timeout=30,
        )
        for line in r.stdout.splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                val = val.strip()
                if key == "duration":
                    try:
                        meta["duration"] = int(float(val))
                    except ValueError:
                        pass
                elif key == "width":
                    try:
                        meta["width"] = int(val)
                    except ValueError:
                        pass
                elif key == "height":
                    try:
                        meta["height"] = int(val)
                    except ValueError:
                        pass
    except Exception as e:
        logger.warning("ffprobe xato: %s", e)
    return meta


async def _get_video_meta(file_path: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_video_meta_sync, file_path)


def _make_thumb_sync(file_path: str, duration: int) -> str | None:
    try:
        thumb_path = os.path.join(TEMP_DIR, f"kthumb_{uuid.uuid4().hex}.jpg")
        seek = max(1, duration // 4) if duration > 4 else 1
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", file_path,
             "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "5", thumb_path],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        logger.warning("thumbnail yaratish xato: %s", e)
    return None


async def _make_thumb(file_path: str, duration: int) -> str | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _make_thumb_sync, file_path, duration)


# ── Yordamchi funksiyalar ─────────────────────────────────────────────────────

def _guess_ext(msg) -> str:
    if msg.video:
        mime = getattr(msg.video, "mime_type", "") or ""
        fname = getattr(msg.video, "file_name", "") or ""
        if "." in fname:
            return fname.rsplit(".", 1)[-1].lower()
        if "mkv" in mime or "matroska" in mime:
            return "mkv"
        return "mp4"
    if msg.document:
        mime = getattr(msg.document, "mime_type", "") or ""
        fname = getattr(msg.document, "file_name", "") or ""
        if "." in fname:
            return fname.rsplit(".", 1)[-1].lower()
        if "mkv" in mime:
            return "mkv"
        if "mp4" in mime:
            return "mp4"
        return "bin"
    return "mp4"


async def _wait_for_new_message_with_buttons(
    client, from_username: str, after_id: int, timeout: int = 30, status_msg=None
):
    deadline = asyncio.get_event_loop().time() + timeout
    dots = 0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        dots = (dots % 3) + 1
        try:
            async for msg in client.get_chat_history(from_username, limit=5):
                if msg.id <= after_id:
                    break
                if msg.reply_markup:
                    return msg
        except Exception as e:
            logger.warning("_wait_for_buttons xato: %s", e)
        if status_msg:
            try:
                waited = int(timeout - (deadline - asyncio.get_event_loop().time()))
                await status_msg.edit_text(
                    f"🎬 *Kino botiga so'rov yuborildi*\n\n"
                    f"🔍 Bot javobini kutilmoqda{'.' * dots}\n"
                    f"⏱ `{waited}s` / `{timeout}s`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
    return None


async def _wait_for_new_media(client, from_username: str, after_id: int, timeout: int = 30):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        try:
            async for msg in client.get_chat_history(from_username, limit=5):
                if msg.id <= after_id:
                    break
                if msg.video or msg.document or msg.audio:
                    return msg
        except Exception as e:
            logger.warning("_wait_for_media xato: %s", e)
    return None


def _flatten_buttons(msg) -> list:
    buttons = []
    if not msg.reply_markup:
        return buttons
    if hasattr(msg.reply_markup, "inline_keyboard"):
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                if hasattr(btn, "callback_data") and btn.callback_data is not None:
                    buttons.append(btn)
    return buttons
