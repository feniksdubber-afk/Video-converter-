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


# ── Progress holati (message_id → info) ───────────────────────────────────
_progress_state: dict = {}


def _refresh_kb(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yangilash", callback_data=f"sr_progress|{msg_id}")
    ]])


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

async def _resolve_peer_safe(client: Client, chat_id):
    """
    Pyrogram peer cache'ini to'ldiradi.
    get_chat() ko'pincha PeerIdInvalid beradi — resolve_peer() ishonchli.
    """
    try:
        await client.resolve_peer(chat_id)
        return True
    except Exception:
        pass
    # Fallback: get_dialogs orqali peer'ni topishga urinish
    try:
        async for dialog in client.get_dialogs(limit=100):
            if dialog.chat.id == chat_id:
                return True
    except Exception:
        pass
    return False


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

    # Aniq media ob'ektni beramiz — thumbnail yuklanib qolmasin
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

    buf = BytesIO()
    async for chunk in client.stream_media(media_obj):
        buf.write(chunk)
    buf.seek(0)
    return buf


async def _send_media_msg(client: Client, msg, to_chat: int, status_msg, use_disk: bool):
    """Bitta xabarni yuklab olib, foydalanuvchiga yuboradi."""
    from telegram import Bot
    tg_bot: Bot = status_msg.get_bot()

    caption = msg.caption or ""

    # download_media faqat msg ni olgan client orqali ishlaydi (file_reference sessiyaga bog'liq)
    dl_client = client

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
        downloaded = False
        try:
            total_size = (
                (msg.video and msg.video.file_size) or
                (msg.document and msg.document.file_size) or
                (msg.audio and msg.audio.file_size) or 0
            )
            total_mb = total_size / 1024 / 1024 if total_size else 0
            last_pct = [-1]

            def _pb(p):
                filled = int(12 * p / 100)
                return "[" + "█" * filled + "░" * (12 - filled) + "]"

            async def _dl_progress(current, total):
                if not total:
                    return
                pct = min(int(current / total * 100), 99)
                cur_mb = current / 1024 / 1024
                bar = _pb(pct)
                txt = (
                    "⬇️ *Yuklanmoqda...*\n\n"
                    + bar + f" `{pct}%`\n"
                    + f"`{cur_mb:.1f}` / `{total_mb:.1f}` MB"
                )
                # Global state yangilash (tugma bosilganda ishlatiladi)
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

            # msg emas, aniq media ob'ektni beramiz — thumbnail yuklanib qolmasin
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

            await dl_client.download_media(media_obj, file_name=tmp_path, progress=_dl_progress)
            downloaded = True

            # ✅ FIX: download tugadi — yuborish boshlanganligi ko'rsat
            try:
                await status_msg.edit_text(
                    "📤 *Yuborilmoqda...*",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

            # ✅ FIX: _send_from_path exception raise qiladi, silent fail yo'q
            await _send_from_path(tg_bot, to_chat, tmp_path, msg, caption, pyro_client=client)

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

        # ✅ FIX: yuborish boshlanganligi ko'rsat
        try:
            await status_msg.edit_text(
                "📤 *Yuborilmoqda...*",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        await _send_from_buf(tg_bot, to_chat, buf, msg, caption)


async def _send_from_path(bot, to_chat: int, path: str, msg, caption: str, pyro_client: Client = None):
    """
    50MB dan kichik: bot (HTTP API) orqali yuboradi.
    50MB dan katta: pyrogram (MTProto) orqali yuboradi — 2GB gacha ishlaydi.

    ✅ FIX: endi exception yutib yubormaydi — xato yuqoriga chiqariladi.
    """
    file_size = os.path.getsize(path)
    use_pyro = pyro_client is not None and file_size > 50 * 1024 * 1024

    if use_pyro:
        # MTProto orqali — 2GB gacha
        if msg.video:
            await pyro_client.send_video(
                to_chat, path, caption=caption,
                duration=msg.video.duration,
                width=msg.video.width,
                height=msg.video.height,
                supports_streaming=True,
            )
        elif msg.photo:
            await pyro_client.send_photo(to_chat, path, caption=caption)
        elif msg.audio:
            await pyro_client.send_audio(to_chat, path, caption=caption,
                                         duration=msg.audio.duration)
        elif msg.voice:
            await pyro_client.send_voice(to_chat, path, caption=caption)
        elif msg.video_note:
            await pyro_client.send_video_note(to_chat, path)
        else:
            name = (msg.document and msg.document.file_name) or "file"
            await pyro_client.send_document(to_chat, path, caption=caption, file_name=name)
    else:
        # HTTP Bot API — 50MB gacha
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


async def _download_and_send_one(client: Client, to_chat: int, from_chat, msg_id: int, status_msg, _retry: int = 0) -> bool:
    """Bitta xabarni yuklab yuboradi. True = muvaffaqiyat."""
    try:
        await _resolve_peer_safe(client, from_chat)
        # Har safar yangi msg olamiz — file_reference yangilanadi
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
        return await _download_and_send_one(client, to_chat, from_chat, msg_id, status_msg, _retry)
    except OSError as e:
        # Railway cross-DC ulanish muammosi — qayta urinish
        if _retry < 3:
            logger.warning(f"OSError msg {msg_id}, retry {_retry+1}/3: {e}")
            await asyncio.sleep(2 * (_retry + 1))
            return await _download_and_send_one(client, to_chat, from_chat, msg_id, status_msg, _retry + 1)
        logger.error(f"msg {msg_id} OSError (3 retry ham ishlamadi): {e}")
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
        await _resolve_peer_safe(client, chat_id)
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

        _progress_state[status.message_id] = "⬇️ *Yuklanmoqda...*"
        await status.edit_text(
            "⬇️ *Yuklanmoqda...*",
            parse_mode="Markdown",
            reply_markup=_refresh_kb(status.message_id)
        )
        ok = await _download_and_send_one(client, update.effective_chat.id, chat_id, msg_id, status)
        _progress_state.pop(status.message_id, None)
        if ok:
            # ✅ FIX: "Yuborildi" deb ko'rsat, keyin o'chir
            try:
                await status.edit_text("✅ Yuborildi!")
            except Exception:
                pass
            await asyncio.sleep(1)
            try:
                await status.delete()
            except Exception:
                pass
        else:
            await status.edit_text("❌ Yuklab bo'lmadi. Havola yoki ruxsat xatosi.")
    except Exception as e:
        logger.error(f"save_link_handler xato: {e}", exc_info=True)
        try:
            await status.edit_text(f"❌ Xato: {e}")
        except Exception:
            pass

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
        await _resolve_peer_safe(client, chat_id)

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
        logger.error(f"save_topic_handler xato: {e}", exc_info=True)
        await status.edit_text(f"❌ Xato: {e}")


async def save_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ha/Bekor/Progress tugmalari."""
    query = update.callback_query

    # 🔄 Yangilash tugmasi
    if query.data.startswith("sr_progress|"):
        msg_id = int(query.data.split("|")[1])
        txt = _progress_state.get(msg_id, "⏳ Ma'lumot yo'q.")
        await query.answer(txt[:200], show_alert=True)
        return

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

    _progress_state[query.message.message_id] = "📦 *Yuklanmoqda...*"
    await query.edit_message_text(
        "📦 *Yuklanmoqda...*",
        parse_mode="Markdown",
        reply_markup=_refresh_kb(query.message.message_id)
    )
    context.bot_data.pop(key, None)
    await _send_batch(client, query.message.chat.id, data["chat_id"], data["ids"], query.message)
