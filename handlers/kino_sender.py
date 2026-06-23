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

from telegram import Update
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID, TEMP_DIR
from handlers.save_restricted import get_user_client

logger = logging.getLogger(__name__)

KINO_BOT = "Kinofilmnewbot"
WAIT_TIMEOUT = 30

_kino_lock = asyncio.Lock()


async def kino_sender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    args = context.args

    if not args:
        await msg.reply_text(
            "❌ Foydalanish:\n`/kino 923` — 6-tugmani bosadi\n`/kino 923 3` — 3-tugmani bosadi",
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
        f"⏳ @{KINO_BOT} ga `/start {payload}` yuborilmoqda...",
        parse_mode="Markdown",
    )

    client = await get_user_client()
    if not client:
        await status_msg.edit_text("❌ Userbot (SESSION_STRING) ulangmagan!")
        return

    async with _kino_lock:
        try:
            await _run_kino_flow(client, payload, button_index, dest_chat, status_msg)
        except Exception as e:
            logger.exception("kino_sender xato: %s", e)
            await status_msg.edit_text(f"❌ Xato: {e}")


async def _run_kino_flow(client, payload: str, button_index: int, dest_chat, status_msg):
    from pyrogram.raw import functions

    # ── 1. Kino botini resolve ────────────────────────────────────────────────
    kino_peer = await client.resolve_peer(KINO_BOT)

    # ── 2. StartBot DAN OLDIN oxirgi xabar ID ni eslab qolamiz ───────────────
    last_msg_id = 0
    try:
        async for h in client.get_chat_history(KINO_BOT, limit=1):
            last_msg_id = h.id
            break
    except Exception:
        pass

    # ── 3. /start <payload> FAQAT BIR MARTA ─────────────────────────────────
    await client.invoke(
        functions.messages.StartBot(
            bot=kino_peer,
            peer=kino_peer,
            random_id=client.rnd_id(),
            start_param=payload,
        )
    )

    await status_msg.edit_text(f"⏳ Bot javobini kutilmoqda (max {WAIT_TIMEOUT}s)...")

    # ── 4. Inline tugmali xabarni kutish ─────────────────────────────────────
    bot_msg = await _wait_for_new_message_with_buttons(
        client, KINO_BOT, after_id=last_msg_id, timeout=WAIT_TIMEOUT
    )

    if bot_msg is None:
        await status_msg.edit_text("❌ Bot inline tugmali javob bermadi!")
        return

    # ── 5. Tugmalar ───────────────────────────────────────────────────────────
    buttons = _flatten_buttons(bot_msg)
    if not buttons:
        await status_msg.edit_text("❌ Xabarda inline tugmalar topilmadi!")
        return

    if button_index >= len(buttons):
        btn_list = "\n".join(f"  {i+1}. {b.text}" for i, b in enumerate(buttons))
        await status_msg.edit_text(
            f"❌ {button_index+1}-tugma yo'q. Mavjud ({len(buttons)} ta):\n{btn_list}"
        )
        return

    chosen_btn = buttons[button_index]
    await status_msg.edit_text(f"🔘 \"{chosen_btn.text}\" tugmasi bosilmoqda...")

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

    await status_msg.edit_text(f"⏳ Video/fayl javobini kutilmoqda (max {WAIT_TIMEOUT}s)...")

    # ── 7. Bot media yuborishini kutish ───────────────────────────────────────
    media_msg = await _wait_for_new_media(
        client, KINO_BOT, after_id=bot_msg.id, timeout=WAIT_TIMEOUT
    )

    if media_msg is None:
        await status_msg.edit_text("❌ Bot video/fayl yuboramadi (timeout)!")
        return

    # ── 8. Yuklab olib, guruhga YANGI XABAR sifatida yuborish ────────────────
    # forward/copy ishlamaydi (CHAT_FORWARDS_RESTRICTED), shuning uchun
    # faylni TEMP_DIR ga yuklaymiz va userbot orqali send_video/send_document
    # sifatida yuboramiz — bu forward emas, yangi xabar, cheklov chetlanadi.
    await status_msg.edit_text("⬇️ Fayl yuklanmoqda...")

    tmp_path = None
    try:
        ext = _guess_ext(media_msg)
        tmp_path = os.path.join(TEMP_DIR, f"kino_{media_msg.id}_{int(time.time())}.{ext}")

        await client.download_media(media_msg, file_name=tmp_path)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            await status_msg.edit_text("❌ Fayl yuklanmadi (bo'sh yoki yo'q)!")
            return

        size_mb = os.path.getsize(tmp_path) / 1024 / 1024
        await status_msg.edit_text(f"📤 Guruhga yuborilmoqda ({size_mb:.1f} MB)...")

        caption = media_msg.caption or f"🎬 {chosen_btn.text}"

        # Guruhga yangi xabar sifatida yuborish
        if media_msg.video:
            await client.send_video(
                chat_id=dest_chat,
                video=tmp_path,
                caption=caption,
                supports_streaming=True,
            )
        elif media_msg.document:
            await client.send_document(
                chat_id=dest_chat,
                document=tmp_path,
                caption=caption,
            )
        else:
            await client.send_document(
                chat_id=dest_chat,
                document=tmp_path,
                caption=caption,
            )

        await status_msg.edit_text(
            f"✅ \"{chosen_btn.text}\" guruhga yuborildi!",
        )

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ── Yordamchi funksiyalar ─────────────────────────────────────────────────────

def _guess_ext(msg) -> str:
    """Media turidan fayl kengaytmasini taxmin qiladi."""
    if msg.video:
        mime = getattr(msg.video, "mime_type", "") or ""
        if "mp4" in mime:
            return "mp4"
        if "mkv" in mime or "matroska" in mime:
            return "mkv"
        return "mp4"
    if msg.document:
        mime = getattr(msg.document, "mime_type", "") or ""
        fname = getattr(msg.document, "file_name", "") or ""
        if "." in fname:
            return fname.rsplit(".", 1)[-1]
        if "mp4" in mime:
            return "mp4"
        if "mkv" in mime:
            return "mkv"
        return "bin"
    return "mp4"


async def _wait_for_new_message_with_buttons(client, from_username: str, after_id: int, timeout: int = 30):
    """after_id dan katta ID li, reply_markup li xabarni kutadi."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        try:
            async for msg in client.get_chat_history(from_username, limit=5):
                if msg.id <= after_id:
                    break
                if msg.reply_markup:
                    return msg
        except Exception as e:
            logger.warning("_wait_for_buttons xato: %s", e)
    return None


async def _wait_for_new_media(client, from_username: str, after_id: int, timeout: int = 30):
    """after_id dan katta ID li, media li xabarni kutadi."""
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
    """callback_data li tugmalarni tekis ro'yxatga soladi."""
    buttons = []
    if not msg.reply_markup:
        return buttons
    if hasattr(msg.reply_markup, "inline_keyboard"):
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                if hasattr(btn, "callback_data") and btn.callback_data is not None:
                    buttons.append(btn)
    return buttons
