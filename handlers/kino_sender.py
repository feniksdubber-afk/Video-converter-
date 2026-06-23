"""
kino_sender.py — Userbot orqali @Kinofilmnewbot dan kino yuborish.

Jarayon:
  1. Userbot ?start=<payload> bilan botga /start yuboradi
  2. Bot inline tugmalar bilan javob qaytaradi
  3. 6-tugma (index 5) bosiladi
  4. Bot yuborgan video/fayl ARCHIVE_GROUP_ID guruhiga forward qilinadi

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

# Kino boti username
KINO_BOT = "Kinofilmnewbot"

# Tugmani kutish va javobni kutish uchun timeout (sekund)
WAIT_TIMEOUT = 30


async def kino_sender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /kino <payload> [tugma_raqami]
    Misol: /kino 923       → 6-tugmani bosadi
           /kino 923 3     → 3-tugmani bosadi
    """
    msg = update.effective_message
    args = context.args  # list of words after /kino

    if not args:
        await msg.reply_text(
            "❌ Foydalanish:\n`/kino 923` — 6-tugmani bosadi\n`/kino 923 3` — 3-tugmani bosadi",
            parse_mode="Markdown",
        )
        return

    payload = args[0].strip()
    button_index = 5  # default: 6-tugma (0-indexed = 5)
    if len(args) >= 2 and args[1].isdigit():
        button_index = int(args[1]) - 1  # foydalanuvchi 1-dan hisoblaydi

    # Guruh ID tekshiruv
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
    """Asosiy oqim: /start yuborish → tugma bosish → guruhga yuborish."""
    from pyrogram.raw import functions, types as raw_types

    # ── 1. Kino botini resolve qilish ─────────────────────────────────────────
    kino_peer = await client.resolve_peer(KINO_BOT)

    # ── 2. /start <payload> yuborish ──────────────────────────────────────────
    await client.invoke(
        functions.messages.StartBot(
            bot=kino_peer,
            peer=kino_peer,
            random_id=client.rnd_id(),
            start_param=payload,
        )
    )

    await status_msg.edit_text(
        f"⏳ Bot javobini kutilmoqda (max {WAIT_TIMEOUT}s)...",
    )

    # ── 3. Bot javobini kutish — inline tugmali xabar kelgunicha ──────────────
    bot_msg = await _wait_for_inline_buttons(client, KINO_BOT, timeout=WAIT_TIMEOUT)

    if bot_msg is None:
        await status_msg.edit_text("❌ Bot inline tugmali javob bermadi!")
        return

    # ── 4. Tugmalar ro'yxatini tekshirish ─────────────────────────────────────
    buttons = _flatten_buttons(bot_msg)
    if not buttons:
        await status_msg.edit_text("❌ Xabarda inline tugmalar topilmadi!")
        return

    if button_index >= len(buttons):
        btn_list = "\n".join(
            f"  {i+1}. {b.text}" for i, b in enumerate(buttons)
        )
        await status_msg.edit_text(
            f"❌ {button_index+1}-tugma yo'q. Mavjud tugmalar ({len(buttons)} ta):\n{btn_list}"
        )
        return

    chosen_btn = buttons[button_index]
    await status_msg.edit_text(
        f"🔘 \"{chosen_btn.text}\" tugmasi bosilmoqda...",
    )

    # ── 5. Callback_data tugmani bosish ───────────────────────────────────────
    await client.invoke(
        functions.messages.GetBotCallbackAnswer(
            peer=kino_peer,
            msg_id=bot_msg.id,
            data=chosen_btn.data.encode() if isinstance(chosen_btn.data, str) else chosen_btn.data,
        )
    )

    await status_msg.edit_text(
        f"⏳ Video/fayl javobini kutilmoqda (max {WAIT_TIMEOUT}s)...",
    )

    # ── 6. Bot yangi xabar (video/fayl) yuborishini kutish ────────────────────
    media_msg = await _wait_for_media(client, KINO_BOT, after_msg_id=bot_msg.id, timeout=WAIT_TIMEOUT)

    if media_msg is None:
        await status_msg.edit_text("❌ Bot media (video/fayl) yubormaadi!")
        return

    # ── 7. Guruhga forward qilish ──────────────────────────────────────────────
    await status_msg.edit_text("📤 Guruhga yuborilmoqda...")

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


# ── Yordamchi funksiyalar ──────────────────────────────────────────────────────

async def _wait_for_inline_buttons(client, from_username: str, timeout: int = 30):
    """
    Berilgan foydalanuvchi/botdan inline tugmali xabarni kutadi.
    Topilsa — pyrogram Message qaytaradi, topilmasa None.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    last_seen_id = None

    # Avval mavjud oxirgi xabarni saqlab olamiz (yangi xabarni farqlash uchun)
    try:
        history = await client.get_chat_history(from_username, limit=1)
        async for h in history:
            last_seen_id = h.id
    except Exception:
        pass

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
    """
    Berilgan after_msg_id dan keyin media (video/document) xabarini kutadi.
    """
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
    """InlineKeyboardMarkup ichidagi barcha tugmalarni tekis ro'yxatga soladi."""
    buttons = []
    if not msg.reply_markup:
        return buttons
    # Pyrogram InlineKeyboardMarkup
    markup = msg.reply_markup
    if hasattr(markup, "inline_keyboard"):
        for row in markup.inline_keyboard:
            for btn in row:
                if hasattr(btn, "callback_data") and btn.callback_data:
                    buttons.append(btn)
    return buttons
