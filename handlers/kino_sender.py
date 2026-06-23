"""
kino_sender.py — Userbot orqali @Kinofilmnewbot dan kino yuborish.

Foydalanish:
  /kino 923          — payload 923 uchun 6-tugmani bosadi
  /kino 923 3        — payload 923 uchun 3-tugmani bosadi (ixtiyoriy)
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID
from handlers.save_restricted import get_user_client

logger = logging.getLogger(__name__)

KINO_BOT = "Kinofilmnewbot"
WAIT_TIMEOUT = 30


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

    status_msg = await msg.reply_text(
        f"⏳ @{KINO_BOT} ga `/start {payload}` yuborilmoqda...",
        parse_mode="Markdown",
    )

    client = await get_user_client()
    if not client:
        await status_msg.edit_text("❌ Userbot (SESSION_STRING) ulangmagan!")
        return

    try:
        await _run_kino_flow(client, payload, button_index, dest_chat, status_msg)
    except Exception as e:
        logger.exception("kino_sender xato: %s", e)
        await status_msg.edit_text(f"❌ Xato: {e}")


async def _run_kino_flow(client, payload: str, button_index: int, dest_chat, status_msg):
    from pyrogram.raw import functions

    # ── 1. Kino botini resolve ────────────────────────────────────────────────
    kino_peer = await client.resolve_peer(KINO_BOT)

    # ── 2. Guruhni cache'ga olish (forward uchun kerak) ───────────────────────
    # Pyrogram userbot guruhni bilmasa forward xato beradi.
    # get_chat() chaqirish peer cache'ni to'ldiradi.
    try:
        await client.get_chat(dest_chat)
    except Exception as e:
        logger.warning("get_chat(%s) xato (davom etilmoqda): %s", dest_chat, e)
        # get_dialogs() bilan kuchliroq urinish
        try:
            async for _ in client.get_dialogs():
                pass
        except Exception:
            pass

    # ── 3. Oxirgi xabar ID ni eslab qolamiz ──────────────────────────────────
    last_seen_id = None
    try:
        async for h in client.get_chat_history(KINO_BOT, limit=1):
            last_seen_id = h.id
    except Exception:
        pass

    # ── 4. /start <payload> yuborish ─────────────────────────────────────────
    await client.invoke(
        functions.messages.StartBot(
            bot=kino_peer,
            peer=kino_peer,
            random_id=client.rnd_id(),
            start_param=payload,
        )
    )

    await status_msg.edit_text(f"⏳ Bot javobini kutilmoqda (max {WAIT_TIMEOUT}s)...")

    # ── 5. Inline tugmali xabarni kutish ─────────────────────────────────────
    bot_msg = await _wait_for_inline_buttons(client, KINO_BOT, last_seen_id, timeout=WAIT_TIMEOUT)

    if bot_msg is None:
        await status_msg.edit_text("❌ Bot inline tugmali javob bermadi!")
        return

    # ── 6. Tugmalar ro'yxati ──────────────────────────────────────────────────
    buttons = _flatten_buttons(bot_msg)
    if not buttons:
        await status_msg.edit_text("❌ Xabarda inline tugmalar topilmadi!")
        return

    if button_index >= len(buttons):
        btn_list = "\n".join(f"  {i+1}. {b.text}" for i, b in enumerate(buttons))
        await status_msg.edit_text(
            f"❌ {button_index+1}-tugma yo'q. Mavjud tugmalar ({len(buttons)} ta):\n{btn_list}"
        )
        return

    chosen_btn = buttons[button_index]
    await status_msg.edit_text(f"🔘 \"{chosen_btn.text}\" tugmasi bosilmoqda...")

    # ── 7. Tugmani bosish — timeout bo'lsa ham davom etamiz ───────────────────
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
        # Kino botlari ko'pincha callback answer bermaydi — bu normal
        logger.info("GetBotCallbackAnswer xato (normal): %s", e)

    await status_msg.edit_text(f"⏳ Video/fayl javobini kutilmoqda (max {WAIT_TIMEOUT}s)...")

    # ── 8. Bot media yuborishini kutish ───────────────────────────────────────
    media_msg = await _wait_for_media(client, KINO_BOT, after_msg_id=bot_msg.id, timeout=WAIT_TIMEOUT)

    if media_msg is None:
        await status_msg.edit_text("❌ Bot video/fayl yuboramadi!")
        return

    # ── 9. Guruhga forward qilish ─────────────────────────────────────────────
    await status_msg.edit_text("📤 Guruhga yuborilmoqda...")

    try:
        await client.forward_messages(
            chat_id=dest_chat,
            from_chat_id=KINO_BOT,
            message_ids=media_msg.id,
        )
        await status_msg.edit_text(
            f"✅ \"{chosen_btn.text}\" kino guruhga yuborildi!\n"
            f"📩 Xabar ID: `{media_msg.id}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        # Forward xato bo'lsa, to'g'ridan-to'g'ri copy_message bilan urinamiz
        logger.warning("forward_messages xato, copy urinilmoqda: %s", e)
        try:
            await client.copy_message(
                chat_id=dest_chat,
                from_chat_id=KINO_BOT,
                message_id=media_msg.id,
            )
            await status_msg.edit_text(
                f"✅ \"{chosen_btn.text}\" kino guruhga yuborildi! (copy)\n"
                f"📩 Xabar ID: `{media_msg.id}`",
                parse_mode="Markdown",
            )
        except Exception as e2:
            await status_msg.edit_text(f"❌ Guruhga yuborishda xato: {e2}")


# ── Yordamchi funksiyalar ─────────────────────────────────────────────────────

async def _wait_for_inline_buttons(client, from_username: str, last_seen_id, timeout: int = 30):
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        try:
            async for msg in client.get_chat_history(from_username, limit=5):
                if last_seen_id and msg.id <= last_seen_id:
                    break
                if msg.reply_markup:
                    return msg
        except Exception as e:
            logger.warning("_wait_for_inline_buttons xato: %s", e)

    return None


async def _wait_for_media(client, from_username: str, after_msg_id: int, timeout: int = 30):
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        try:
            async for msg in client.get_chat_history(from_username, limit=5):
                if msg.id <= after_msg_id:
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
    markup = msg.reply_markup
    if hasattr(markup, "inline_keyboard"):
        for row in markup.inline_keyboard:
            for btn in row:
                if hasattr(btn, "callback_data") and btn.callback_data is not None:
                    buttons.append(btn)
    return buttons
