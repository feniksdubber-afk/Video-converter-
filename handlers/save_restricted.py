import asyncio
import logging
import os
import re
from io import BytesIO

from pyrogram import Client
from pyrogram.errors import FloodWait
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import API_ID, API_HASH, SESSION_STRING, TEMP_DIR

logger = logging.getLogger(__name__)

# ── Userbot client ─────────────────────────────────────────────────────────
_user_client: Client | None = None
_user_lock = asyncio.Lock()


async def get_user_client() -> Client | None:
    global _user_client
    if not SESSION_STRING:
        return None
    async with _user_lock:
        if _user_client is None or not _user_client.is_connected:
            _user_client = Client(
                "user_session",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=SESSION_STRING,
            )
            await _user_client.start()
    return _user_client


# ── Progress holati ────────────────────────────────────────────────────────
_progress_state: dict = {}


def _refresh_kb(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yangilash", callback_data=f"sr_progress|{msg_id}")
    ]])


def _progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


# ── Havola parse ───────────────────────────────────────────────────────────

def parse_tme_link(text: str):
    m = re.search(r"https?://t\.me/c/(\d+)/(\d+)(?:/(\d+))?", text)
    if m:
        chat_id = int("-100" + m.group(1))
        msg_id = int(m.group(3)) if m.group(3) else int(m.group(2))
        return chat_id, msg_id

    m2 = re.search(r"https?://t\.me/(?!c/)([A-Za-z][A-Za-z0-9_]{3,})/(\d+)", text)
    if m2:
        return m2.group(1), int(m2.group(2))

    return None, None


# ── Peer resolve ───────────────────────────────────────────────────────────

async def _resolve_peer_safe(client: Client, chat_id):
    try:
        await client.resolve_peer(chat_id)
        return True
    except Exception:
        pass
    try:
        async for dialog in client.get_dialogs(limit=100):
            if dialog.chat.id == chat_id:
                return True
    except Exception:
        pass
    return False


# ── Asosiy yuklash + yuborish ──────────────────────────────────────────────

async def _download_and_send(
    pyro_client: Client,
    msg,
    to_chat: int,
    status_msg,
) -> bool:
    """
    1. Pyrogram (userbot) orqali diskka yuklab oladi — progress bar bilan.
    2. Bot (HTTP API) orqali foydalanuvchiga yuboradi.
       50MB dan katta bo'lsa sender.py → Pyrogram MTProto bot client ishlatiladi.
    """
    from utils.sender import send_file

    # Fayl hajmi va kengaytma
    ext = "mp4"
    file_size = 0
    if msg.video:
        file_size = msg.video.file_size or 0
        ext = "mp4"
    elif msg.document:
        file_size = msg.document.file_size or 0
        if msg.document.file_name:
            ext = os.path.splitext(msg.document.file_name)[1].lstrip(".") or "bin"
    elif msg.audio:
        file_size = msg.audio.file_size or 0
        ext = "mp3"
    elif msg.photo:
        file_size = getattr(msg.photo, "file_size", 0) or 0
        ext = "jpg"
    elif msg.voice:
        file_size = msg.voice.file_size or 0
        ext = "ogg"
    elif msg.video_note:
        file_size = msg.video_note.file_size or 0
        ext = "mp4"

    # Media ob'ekti (thumbnail yuklanib qolmasin)
    if msg.video:
        media_obj = msg.video
    elif msg.document:
        media_obj = msg.document
    elif msg.audio:
        media_obj = msg.audio
    elif msg.voice:
        media_obj = msg.voice
    elif msg.video_note:
        media_obj = msg.video_note
    elif msg.photo:
        media_obj = msg.photo
    else:
        media_obj = msg

    total_mb = file_size / 1024 / 1024 if file_size else 0
    last_pct = [-1]

    async def _dl_progress(current, total):
        if not total:
            return
        pct = min(int(current / total * 100), 99)
        cur_mb = current / 1024 / 1024
        bar = _progress_bar(pct)
        txt = (
            "⬇️ *Yuklanmoqda...*\n\n"
            + bar + f" `{pct}%`\n"
            + f"`{cur_mb:.1f}` / `{total_mb:.1f}` MB"
        )
        _progress_state[status_msg.message_id] = txt
        if pct - last_pct[0] < 10:
            return
        last_pct[0] = pct
        try:
            await status_msg.edit_text(
                txt, parse_mode="Markdown",
                reply_markup=_refresh_kb(status_msg.message_id)
            )
        except Exception:
            pass

    tmp_path = os.path.join(TEMP_DIR, f"sr_{msg.id}.{ext}")
    try:
        # 1. Diskka yuklab olish (userbot orqali — restricted kanaldan)
        await pyro_client.download_media(media_obj, file_name=tmp_path, progress=_dl_progress)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            logger.error(f"Download muvaffaqiyatsiz: {tmp_path} mavjud emas yoki bo'sh")
            return False

        # 2. "Yuborilmoqda" holati
        try:
            await status_msg.edit_text("📤 *Yuborilmoqda...*", parse_mode="Markdown")
        except Exception:
            pass

        # 3. Fayl nomini aniqlash
        if msg.document and msg.document.file_name:
            filename = msg.document.file_name
        elif msg.video:
            filename = f"video_{msg.id}.mp4"
        elif msg.audio:
            filename = f"audio_{msg.id}.mp3"
        elif msg.photo:
            filename = f"photo_{msg.id}.jpg"
        elif msg.voice:
            filename = f"voice_{msg.id}.ogg"
        elif msg.video_note:
            filename = f"videonote_{msg.id}.mp4"
        else:
            filename = f"file_{msg.id}.{ext}"

        caption = msg.caption or ""

        # 4. Bot orqali yuborish (send_file: <50MB PTB, >50MB Pyrogram bot client)
        fake_message = status_msg  # send_file message.reply_* ishlatadi
        await send_file(
            message=fake_message,
            file_path=tmp_path,
            filename=filename,
            caption=caption,
        )
        return True

    except Exception as e:
        logger.error(f"_download_and_send xato: {e}", exc_info=True)
        return False
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def _download_and_send_one(
    client: Client,
    to_chat: int,
    from_chat,
    msg_id: int,
    status_msg,
    _retry: int = 0,
) -> bool:
    try:
        await _resolve_peer_safe(client, from_chat)
        msg = await client.get_messages(from_chat, msg_id)
        if not msg or msg.empty or not msg.media:
            return False
        return await _download_and_send(client, msg, to_chat, status_msg)

    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s")
        await asyncio.sleep(e.value)
        return await _download_and_send_one(client, to_chat, from_chat, msg_id, status_msg, _retry)
    except OSError as e:
        if _retry < 3:
            logger.warning(f"OSError msg {msg_id}, retry {_retry+1}/3: {e}")
            await asyncio.sleep(2 * (_retry + 1))
            return await _download_and_send_one(client, to_chat, from_chat, msg_id, status_msg, _retry + 1)
        logger.error(f"msg {msg_id} OSError (3 retry): {e}")
        return False
    except Exception as e:
        logger.error(f"msg {msg_id} xato: {e}", exc_info=True)
        return False


async def _send_batch(client: Client, to_chat: int, from_chat, ids: list, status_msg):
    sent = 0
    total = len(ids)
    for i, mid in enumerate(ids):
        if i % 5 == 0:
            try:
                await status_msg.edit_text(
                    f"📥 Yuklanmoqda... {i}/{total}\n"
                    f"{_progress_bar(int(i / total * 100))}"
                )
            except Exception:
                pass
        ok = await _download_and_send_one(client, to_chat, from_chat, mid, status_msg)
        if ok:
            sent += 1
        await asyncio.sleep(1.2)
    await status_msg.edit_text(f"✅ {sent}/{total} ta media yuborildi.")


# ── Telegram handler'lari ──────────────────────────────────────────────────

async def save_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id, msg_id = parse_tme_link(text)
    if not chat_id:
        return False

    client = await get_user_client()
    if client is None:
        await update.message.reply_text(
            "⚠️ Save Restricted funksiyasi hozircha sozlanmagan.\n"
            "Admin bilan bog'laning."
        )
        return True

    status = await update.message.reply_text("⏳ Tekshirilmoqda...")
    try:
        await _resolve_peer_safe(client, chat_id)
        msg = await client.get_messages(chat_id, msg_id)
        if not msg or msg.empty:
            await status.edit_text("❌ Xabar topilmadi. Havola to'g'riligini tekshiring.")
            return True
        if not msg.media:
            await status.edit_text(
                f"📝 *Xabar matni:*\n\n{msg.text or '(bosh)'}",
                parse_mode="Markdown"
            )
            return True

        _progress_state[status.message_id] = "⬇️ *Yuklanmoqda...*"
        await status.edit_text(
            "⬇️ *Yuklanmoqda...*",
            parse_mode="Markdown",
            reply_markup=_refresh_kb(status.message_id)
        )

        ok = await _download_and_send_one(
            client, update.effective_chat.id, chat_id, msg_id, status
        )
        _progress_state.pop(status.message_id, None)

        if ok:
            try:
                await status.delete()
            except Exception:
                pass
        else:
            await status.edit_text("❌ Yuklab bo'lmadi. Log'da xatoni tekshiring.")

    except Exception as e:
        logger.error(f"save_link_handler xato: {e}", exc_info=True)
        try:
            await status.edit_text(f"❌ Xato: {e}")
        except Exception:
            pass

    return True


async def save_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = update.message.text.split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text(
            "❗ Foydalanish:\n`/save https://t.me/c/1234567890/456`",
            parse_mode="Markdown"
        )
        return

    client = await get_user_client()
    if client is None:
        await update.message.reply_text("⚠️ Save Restricted funksiyasi sozlanmagan.")
        return

    chat_id, thread_id = parse_tme_link(args[1])
    if not chat_id:
        await update.message.reply_text("❌ Havola xato.")
        return

    status = await update.message.reply_text("🔍 Topik skanlanmoqda...")
    try:
        await _resolve_peer_safe(client, chat_id)

        media_ids = []
        async for m in client.get_chat_history(chat_id, limit=1000):
            if (m.reply_to_message_id == thread_id or m.id == thread_id) and m.media:
                media_ids.append(m.id)
        media_ids.sort()

        if not media_ids:
            await status.edit_text("❌ Topikda media topilmadi.")
            return

        count = len(media_ids)
        if count <= 30:
            await status.edit_text(f"📦 {count} ta media yuklanmoqda...")
            await _send_batch(client, update.effective_chat.id, chat_id, media_ids, status)
        else:
            key = f"sr_ids_{update.effective_chat.id}"
            context.bot_data[key] = {"chat_id": chat_id, "ids": media_ids}
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Ha, yuborsin", callback_data=f"sr_confirm|{key}"),
                InlineKeyboardButton("❌ Bekor", callback_data="sr_cancel"),
            ]])
            await status.edit_text(
                f"⚠️ Topikda *{count}* ta media bor.\nHammasi yuklab yuborilsinmi?",
                reply_markup=kb,
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"save_topic_handler xato: {e}", exc_info=True)
        await status.edit_text(f"❌ Xato: {e}")


async def save_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data.startswith("sr_progress|"):
        msg_id = int(query.data.split("|")[1])
        txt = _progress_state.get(msg_id, "⏳ Ma'lumot yo'q.")
        await query.answer(txt[:200], show_alert=True)
        return

    await query.answer()

    if query.data == "sr_cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        return

    _, key = query.data.split("|", 1)
    data = context.bot_data.get(key)
    if not data:
        await query.edit_message_text("❌ Ma'lumot topilmadi. /save qayta yuboring.")
        return

    client = await get_user_client()
    if client is None:
        await query.edit_message_text("⚠️ Userbot ulanmagan.")
        return

    _progress_state[query.message.message_id] = "📦 *Yuklanmoqda...*"
    await query.edit_message_text(
        "📦 *Yuklanmoqda...*",
        parse_mode="Markdown",
        reply_markup=_refresh_kb(query.message.message_id)
    )
    context.bot_data.pop(key, None)
    await _send_batch(
        client, query.message.chat.id, data["chat_id"], data["ids"], query.message
    )
