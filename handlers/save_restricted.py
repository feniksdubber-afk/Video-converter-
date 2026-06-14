import asyncio
import logging
import os
import re
import tempfile
from io import BytesIO

from pyrogram import Client
from pyrogram.errors import FloodWait
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import API_ID, API_HASH, SESSION_STRING, TEMP_DIR

logger = logging.getLogger(__name__)

# ── Userbot client (user session, telefon raqam bilan) ─────────────────────
_user_client: Client | None = None
_user_lock = asyncio.Lock()


async def get_user_client() -> Client | None:
    """Userbot clientini qaytaradi. SESSION_STRING yo'q bo'lsa None."""
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


# ── Havola parse ────────────────────────────────────────────────────────────

def parse_tme_link(text: str):
    """
    Qo'llab-quvvatlanadigan formatlar:
      t.me/c/CHATID/MSGID
      t.me/c/CHATID/TOPICID/MSGID
      t.me/USERNAME/MSGID
    Qaytaradi: (chat_id, msg_id) yoki (None, None)
    """
    # Private kanal/guruh
    m = re.search(r"https?://t\.me/c/(\d+)/(\d+)(?:/(\d+))?", text)
    if m:
        chat_id = int("-100" + m.group(1))
        msg_id = int(m.group(3)) if m.group(3) else int(m.group(2))
        return chat_id, msg_id

    # Public username
    m2 = re.search(r"https?://t\.me/(?!c/)([A-Za-z][A-Za-z0-9_]{3,})/(\d+)", text)
    if m2:
        return m2.group(1), int(m2.group(2))

    return None, None


# ── Yuklab olish va yuborish ────────────────────────────────────────────────

def _progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


async def _stream_to_bytesio(client: Client, msg) -> BytesIO | None:
    """
    Kichik fayllar uchun (<50MB) RAM'ga stream qilish.
    Katta fayllar uchun None qaytaradi → disk ishlatiladi.
    """
    size = 0
    if msg.video:
        size = msg.video.file_size or 0
    elif msg.document:
        size = msg.document.file_size or 0
    elif msg.photo:
        size = msg.photo.file_size or 0
    elif msg.audio:
        size = msg.audio.file_size or 0

    if size > 50 * 1024 * 1024:
        return None  # katta fayl — diskka yukla

    buf = BytesIO()
    async for chunk in client.stream_media(msg):
        buf.write(chunk)
    buf.seek(0)
    return buf


async def _send_media_msg(client: Client, msg, to_chat: int, status_msg, use_disk: bool):
    """Bitta xabarni yuklab olib, foydalanuvchiga yuboradi."""
    from telegram import Bot
    # bot instance'ini status_msg orqali olamiz
    tg_bot: Bot = status_msg.get_bot()

    caption = msg.caption or ""

    if use_disk:
        # Diskka yuklab olish
        ext = "mp4"
        if msg.video:
            ext = "mp4"
        elif msg.document and msg.document.file_name:
            ext = os.path.splitext(msg.document.file_name)[1].lstrip(".") or "bin"
        elif msg.audio:
            ext = "mp3"
        elif msg.photo:
            ext = "jpg"

        tmp_path = os.path.join(TEMP_DIR, f"sr_{msg.id}.{ext}")
        try:
            await client.download_media(msg, file_name=tmp_path)
            await _send_from_path(tg_bot, to_chat, tmp_path, msg, caption)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    else:
        # RAM (BytesIO)
        buf = await _stream_to_bytesio(client, msg)
        if buf is None:
            # Fallback — diskka
            await _send_media_msg(client, msg, to_chat, status_msg, use_disk=True)
            return
        await _send_from_buf(tg_bot, to_chat, buf, msg, caption)


async def _send_from_path(bot, to_chat: int, path: str, msg, caption: str):
    with open(path, "rb") as f:
        if msg.video:
            await bot.send_video(to_chat, video=f, caption=caption,
                                 duration=msg.video.duration,
                                 width=msg.video.width,
                                 height=msg.video.height)
        elif msg.photo:
            await bot.send_photo(to_chat, photo=f, caption=caption)
        elif msg.audio:
            await bot.send_audio(to_chat, audio=f, caption=caption,
                                 duration=msg.audio.duration)
        elif msg.voice:
            await bot.send_voice(to_chat, voice=f, caption=caption)
        elif msg.video_note:
            await bot.send_video_note(to_chat, video_note=f)
        elif msg.sticker:
            await bot.send_sticker(to_chat, sticker=f)
        else:
            name = (msg.document and msg.document.file_name) or "file"
            await bot.send_document(to_chat, document=f, caption=caption, filename=name)


async def _send_from_buf(bot, to_chat: int, buf: BytesIO, msg, caption: str):
    if msg.video:
        buf.name = "video.mp4"
        await bot.send_video(to_chat, video=buf, caption=caption,
                             duration=msg.video.duration,
                             width=msg.video.width,
                             height=msg.video.height)
    elif msg.photo:
        buf.name = "photo.jpg"
        await bot.send_photo(to_chat, photo=buf, caption=caption)
    elif msg.audio:
        buf.name = "audio.mp3"
        await bot.send_audio(to_chat, audio=buf, caption=caption)
    elif msg.voice:
        buf.name = "voice.ogg"
        await bot.send_voice(to_chat, voice=buf, caption=caption)
    elif msg.video_note:
        buf.name = "videonote.mp4"
        await bot.send_video_note(to_chat, video_note=buf)
    elif msg.sticker:
        buf.name = "sticker.webp"
        await bot.send_sticker(to_chat, sticker=buf)
    else:
        name = (msg.document and msg.document.file_name) or "file"
        buf.name = name
        await bot.send_document(to_chat, document=buf, caption=caption, filename=name)


async def _download_and_send_one(client: Client, to_chat: int, from_chat, msg_id: int, status_msg) -> bool:
    """Bitta xabarni yuklab yuboradi. True = muvaffaqiyat."""
    try:
        msg = await client.get_messages(from_chat, msg_id)
        if not msg or msg.empty or not msg.media:
            return False

        size = 0
        if msg.video:
            size = msg.video.file_size or 0
        elif msg.document:
            size = msg.document.file_size or 0
        elif msg.photo:
            size = getattr(msg.photo, "file_size", 0) or 0

        use_disk = size > 50 * 1024 * 1024
        await _send_media_msg(client, msg, to_chat, status_msg, use_disk)
        return True

    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s")
        await asyncio.sleep(e.value)
        return await _download_and_send_one(client, to_chat, from_chat, msg_id, status_msg)
    except Exception as e:
        logger.error(f"msg {msg_id} xato: {e}")
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


# ── Telegram handler'lari ───────────────────────────────────────────────────

async def save_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi t.me havola yuborganida chaqiriladi.
    text_handler'dan /start oldidan chaqiriladi.
    """
    text = update.message.text.strip()
    chat_id, msg_id = parse_tme_link(text)
    if not chat_id:
        return False  # bu havola emas, text_handler davom etsin

    client = await get_user_client()
    if client is None:
        await update.message.reply_text(
            "⚠️ Save Restricted funksiyasi hozircha sozlanmagan.\n"
            "Admin bilan bog'laning."
        )
        return True

    status = await update.message.reply_text("⏳ Tekshirilmoqda...")
    try:
        msg = await client.get_messages(chat_id, msg_id)
        if not msg or msg.empty:
            await status.edit_text("❌ Xabar topilmadi. Havola to'g'riligini tekshiring.")
            return True
        if not msg.media:
            # Oddiy matn — shunchaki yozib beramiz
            await status.edit_text(
                f"📝 *Xabar matni:*\n\n{msg.text or '(bosh)'}",
                parse_mode="Markdown"
            )
            return True

        await status.edit_text("⬇️ Yuklanmoqda...")
        ok = await _download_and_send_one(client, update.effective_chat.id, chat_id, msg_id, status)
        if ok:
            await status.delete()
        else:
            await status.edit_text("❌ Yuklab bo'lmadi. Havola yoki ruxsat xatosi.")
    except Exception as e:
        await status.edit_text(f"❌ Xato: {e}")

    return True  # text_handler'ga o'tmasin


async def save_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/save [havola] — topikdagi barcha medialarni yuboradi."""
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
        media_ids = []
        async for msg in client.get_chat_history(chat_id, limit=1000):
            if (msg.reply_to_message_id == thread_id or msg.id == thread_id) and msg.media:
                media_ids.append(msg.id)
        media_ids.sort()

        if not media_ids:
            await status.edit_text("❌ Topikda media topilmadi.")
            return

        count = len(media_ids)
        if count <= 30:
            await status.edit_text(f"📦 {count} ta media yuklanmoqda...")
            await _send_batch(client, update.effective_chat.id, chat_id, media_ids, status)
        else:
            # Katta miqdor — tasdiqlash so'rash
            # ID'larni context'ga saqlaymiz (callback_data limit muammosini oldini olish uchun)
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
        await status.edit_text(f"❌ Xato: {e}")


async def save_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """✅ Ha tugmasi bosilganda."""
    query = update.callback_query
    await query.answer()

    if query.data == "sr_cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        return

    # sr_confirm|key
    _, key = query.data.split("|", 1)
    data = context.bot_data.get(key)
    if not data:
        await query.edit_message_text("❌ Ma'lumot topilmadi. /save qayta yuboring.")
        return

    client = await get_user_client()
    if client is None:
        await query.edit_message_text("⚠️ Userbot ulanmagan.")
        return

    await query.edit_message_text("📦 Yuklanmoqda...")
    context.bot_data.pop(key, None)
    await _send_batch(client, query.message.chat.id, data["chat_id"], data["ids"], query.message)
