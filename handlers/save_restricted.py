"""
save_restricted.py — Restricted kanallardan media yuklab olish.

Yangi imkoniyatlar:
  - ARCHIVE_GROUP_ID → forum topic ga avtomatik saqlash
  - force_document → format saqlanadi
  - Album (media_group) qo'llab-quvvatlash
  - Jarayon davomida bekor qilish
  - To'g'ri user_id (guruh emas)
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time

from pyrogram import Client
from pyrogram.errors import FloodWait
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import API_ID, API_HASH, SESSION_STRING, TEMP_DIR, ARCHIVE_GROUP_ID, AUTO_CREATE_TOPIC, DATA_DIR
from utils.task_manager import (
    register_task, is_cancelled, clear_task, progress_keyboard, cancel_task,
)

logger = logging.getLogger(__name__)

# ── Oddiy link saqlash (save_link_handler) uchun BITTA umumiy topic ─────────
# Har bir foydalanuvchi/fayl uchun emas — hamma uchun bir xil joy.
SHARED_TOPIC_NAME = "📥 Saqlangan medialar"
_SHARED_TOPIC_FILE = os.path.join(DATA_DIR, "shared_topic.json")
_shared_topic_cache: dict | None = None


def _load_shared_topic() -> dict:
    global _shared_topic_cache
    if _shared_topic_cache is not None:
        return _shared_topic_cache
    data = {"chat_id": None, "thread_id": None}
    if os.path.isfile(_SHARED_TOPIC_FILE):
        try:
            with open(_SHARED_TOPIC_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                data.update(saved)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("shared_topic o'qish xato: %s", e)
    _shared_topic_cache = data
    return data


def _save_shared_topic(chat_id: int, thread_id: int | None) -> None:
    global _shared_topic_cache
    _shared_topic_cache = {"chat_id": chat_id, "thread_id": thread_id}
    try:
        with open(_SHARED_TOPIC_FILE, "w", encoding="utf-8") as f:
            json.dump(_shared_topic_cache, f)
    except OSError as e:
        logger.warning("shared_topic saqlash xato: %s", e)


async def _ensure_shared_topic(bot) -> tuple[int | None, int | None]:
    """save_link_handler uchun bitta doimiy topic qaytaradi (kerak bo'lsa yaratadi)."""
    if not ARCHIVE_GROUP_ID:
        return None, None

    cached = _load_shared_topic()
    if cached.get("chat_id") == ARCHIVE_GROUP_ID and (cached.get("thread_id") or not AUTO_CREATE_TOPIC):
        return cached["chat_id"], cached.get("thread_id")

    chat_id = ARCHIVE_GROUP_ID
    thread_id = None
    if AUTO_CREATE_TOPIC:
        try:
            topic = await bot.create_forum_topic(chat_id=chat_id, name=SHARED_TOPIC_NAME)
            thread_id = topic.message_thread_id
        except Exception as e:
            logger.warning("Umumiy topic yaratish xato: %s", e)

    _save_shared_topic(chat_id, thread_id)
    return chat_id, thread_id

_user_client: Client | None = None
_user_lock = asyncio.Lock()

_progress_state: dict = {}


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


def _refresh_kb(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Yangilash", callback_data=f"sr_progress|{msg_id}"),
            InlineKeyboardButton("❌ Bekor", callback_data="sr_cancel_run"),
        ],
    ])


def _progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


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


def parse_topic_link(text: str):
    m = re.search(r"https?://t\.me/c/(\d+)/(\d+)(?:/(\d+))?", text)
    if m:
        chat_id = int("-100" + m.group(1))
        if m.group(3):
            return chat_id, int(m.group(2))
        return chat_id, int(m.group(2))
    return None, None


async def _resolve_peer_safe(client: Client, chat_id):
    try:
        await client.resolve_peer(chat_id)
        return True
    except Exception:
        pass
    try:
        async for dialog in client.get_dialogs(limit=200):
            if dialog.chat.id == chat_id:
                return True
    except Exception:
        pass
    return False


def _resolve_filename(msg) -> str:
    """Asl fayl nomini saqlash."""
    if msg.document and msg.document.file_name:
        return msg.document.file_name
    if msg.video and msg.video.file_name:
        return msg.video.file_name
    if msg.audio and msg.audio.file_name:
        return msg.audio.file_name
    cap = (msg.caption or "").strip()
    if cap and len(cap) < 120 and not cap.startswith("http"):
        ext = _guess_ext(msg)
        safe = re.sub(r'[<>:"/\\|?*]', "_", cap)[:80]
        return f"{safe}{ext}"
    ext = _guess_ext(msg)
    return f"media_{msg.id}{ext}"


def _guess_ext(msg) -> str:
    if msg.document and msg.document.file_name:
        e = os.path.splitext(msg.document.file_name)[1]
        if e:
            return e
    if msg.video:
        return ".mp4"
    if msg.audio:
        return ".mp3"
    if msg.photo:
        return ".jpg"
    if msg.voice:
        return ".ogg"
    if msg.video_note:
        return ".mp4"
    return ".bin"


def _media_obj(msg):
    if msg.video:
        return msg.video
    if msg.document:
        return msg.document
    if msg.audio:
        return msg.audio
    if msg.voice:
        return msg.voice
    if msg.video_note:
        return msg.video_note
    if msg.photo:
        return msg.photo
    return msg


async def _ensure_archive_topic(bot, topic_name: str) -> tuple[int, int | None]:
    """ARCHIVE_GROUP_ID ga topic yaratadi yoki mavjud chat qaytaradi."""
    if not ARCHIVE_GROUP_ID:
        return None, None
    chat_id = ARCHIVE_GROUP_ID
    thread_id = None
    if AUTO_CREATE_TOPIC:
        name = topic_name[:128] or f"Save {time.strftime('%d.%m %H:%M')}"
        try:
            topic = await bot.create_forum_topic(chat_id=chat_id, name=name)
            thread_id = topic.message_thread_id
        except Exception as e:
            logger.warning("Topic yaratish xato: %s", e)
    return chat_id, thread_id


async def _download_and_send(
    pyro_client: Client,
    msg,
    status_msg,
    user_id: int,
    dest_chat_id: int,
    dest_thread_id: int | None,
    bot,
) -> bool:
    from utils.sender import send_file, _r2_pending

    if is_cancelled(user_id):
        return False

    media_obj = _media_obj(msg)
    if not media_obj:
        return False

    filename = _resolve_filename(msg)
    ext = os.path.splitext(filename)[1].lstrip(".") or "bin"
    file_size = getattr(media_obj, "file_size", 0) or 0
    total_mb = file_size / 1024 / 1024 if file_size else 0
    last_pct = [-1]

    async def _dl_progress(current, total):
        if is_cancelled(user_id):
            return
        if not total:
            return
        pct = min(int(current / total * 100), 99)
        cur_mb = current / 1024 / 1024
        bar = _progress_bar(pct)
        txt = f"⬇️ *Yuklanmoqda...*\n\n{bar} `{pct}%`\n`{cur_mb:.1f}` / `{total_mb:.1f}` MB"
        _progress_state[status_msg.message_id] = txt
        if pct - last_pct[0] < 10:
            return
        last_pct[0] = pct
        try:
            await status_msg.edit_text(
                txt, parse_mode="Markdown",
                reply_markup=_refresh_kb(status_msg.message_id),
            )
        except Exception:
            pass

    tmp_path = os.path.join(TEMP_DIR, f"sr_{msg.id}_{user_id}.{ext}")
    try:
        await pyro_client.download_media(media_obj, file_name=tmp_path, progress=_dl_progress)

        if is_cancelled(user_id):
            return False

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            return False

        try:
            await status_msg.edit_text(
                f"📤 *Yuborilmoqda:* `{filename}`",
                parse_mode="Markdown",
                reply_markup=_refresh_kb(status_msg.message_id),
            )
        except Exception:
            pass

        caption = msg.caption or ""
        from utils.db import db_load
        settings = await db_load(user_id)

        class _FakeCtx:
            user_data = {"settings": settings, "_settings_loaded": True, "_user_id": user_id}

        target_chat = dest_chat_id or status_msg.chat_id
        await send_file(
            message=status_msg,
            file_path=tmp_path,
            filename=filename,
            caption=caption,
            context=_FakeCtx(),
            force_document=True,
            target_chat_id=target_chat if target_chat != status_msg.chat_id else None,
            message_thread_id=dest_thread_id,
        )

        short_key = hashlib.md5(f"{user_id}:{filename}".encode()).hexdigest()[:8]
        if short_key in _r2_pending:
            tmp_path = None
        return True

    except Exception as e:
        logger.error("_download_and_send xato: %s", e, exc_info=True)
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


async def _get_album_messages(client: Client, chat_id, msg) -> list:
    """Media group (album) barcha qismlarini oladi."""
    mgid = getattr(msg, "media_group_id", None)
    if not mgid:
        return [msg]
    parts = []
    async for m in client.get_chat_history(chat_id, limit=50):
        if getattr(m, "media_group_id", None) == mgid and m.media:
            parts.append(m)
    if not parts:
        return [msg]
    parts.sort(key=lambda x: x.id)
    return parts


async def _download_and_send_one(
    client: Client,
    from_chat,
    msg_id: int,
    status_msg,
    user_id: int,
    dest_chat_id: int,
    dest_thread_id: int | None,
    bot,
    _retry: int = 0,
) -> bool:
    try:
        await _resolve_peer_safe(client, from_chat)
        msg = await client.get_messages(from_chat, msg_id)
        if not msg or msg.empty or not msg.media:
            return False

        album = await _get_album_messages(client, from_chat, msg)
        ok_any = False
        for part in album:
            if is_cancelled(user_id):
                break
            if await _download_and_send(
                client, part, status_msg, user_id,
                dest_chat_id, dest_thread_id, bot,
            ):
                ok_any = True
            await asyncio.sleep(0.8)
        return ok_any

    except FloodWait as e:
        wait_txt = f"⏳ *Telegram cheklovi:* {e.value} soniya kutilmoqda..."
        _progress_state[status_msg.message_id] = wait_txt
        try:
            await status_msg.edit_text(
                wait_txt, parse_mode="Markdown",
                reply_markup=_refresh_kb(status_msg.message_id),
            )
        except Exception:
            pass
        await asyncio.sleep(e.value)
        return await _download_and_send_one(
            client, from_chat, msg_id, status_msg, user_id,
            dest_chat_id, dest_thread_id, bot, _retry,
        )
    except OSError as e:
        if _retry < 3:
            await asyncio.sleep(2 * (_retry + 1))
            return await _download_and_send_one(
                client, from_chat, msg_id, status_msg, user_id,
                dest_chat_id, dest_thread_id, bot, _retry + 1,
            )
        logger.error("msg %s OSError: %s", msg_id, e)
        return False
    except Exception as e:
        logger.error("msg %s xato: %s", msg_id, e, exc_info=True)
        return False


async def _send_batch(
    client: Client, from_chat, ids: list, status_msg,
    user_id: int, dest_chat_id: int, dest_thread_id: int | None, bot,
):
    sent = 0
    total = len(ids)
    for i, mid in enumerate(ids):
        if is_cancelled(user_id):
            await status_msg.edit_text(f"❌ Bekor qilindi. {sent}/{total} yuborildi.")
            return
        txt = f"📥 *{i + 1}/{total}* yuklanmoqda...\n{_progress_bar(int(i / total * 100))}"
        _progress_state[status_msg.message_id] = txt
        try:
            await status_msg.edit_text(
                txt,
                parse_mode="Markdown",
                reply_markup=_refresh_kb(status_msg.message_id),
            )
        except Exception:
            pass
        ok = await _download_and_send_one(
            client, from_chat, mid, status_msg, user_id,
            dest_chat_id, dest_thread_id, bot,
        )
        if ok:
            sent += 1
        await asyncio.sleep(1.2)
    archive_note = f"\n☁️ Arxiv guruhi: `{dest_chat_id}`" if ARCHIVE_GROUP_ID else ""
    await status_msg.edit_text(f"✅ {sent}/{total} ta media yuborildi.{archive_note}")


async def _prepare_destination(update: Update, context: ContextTypes.DEFAULT_TYPE, label: str):
    """Manzil chat va topic ni aniqlaydi."""
    user_id = update.effective_user.id
    register_task(user_id, label=f"Save: {label}")

    dest_chat = update.effective_chat.id
    dest_thread = getattr(update.message, "message_thread_id", None)

    if ARCHIVE_GROUP_ID:
        dest_chat, dest_thread = await _ensure_archive_topic(context.bot, label)
        if dest_chat:
            try:
                await update.message.reply_text(
                    f"📁 Arxiv: topic *{label[:60]}* ga saqlanmoqda...",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    return user_id, dest_chat, dest_thread


async def save_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id, msg_id = parse_tme_link(text)
    if not chat_id:
        return False

    client = await get_user_client()
    if client is None:
        await update.message.reply_text(
            "⚠️ Save Restricted sozlanmagan.\n`SESSION_STRING` kerak.",
            parse_mode="Markdown",
        )
        return True

    status = await update.message.reply_text("⏳ Tekshirilmoqda...")
    user_id = update.effective_user.id

    try:
        await _resolve_peer_safe(client, chat_id)
        msg = await client.get_messages(chat_id, msg_id)
        if not msg or msg.empty:
            await status.edit_text("❌ Xabar topilmadi.")
            return True
        if not msg.media:
            await status.edit_text(f"📝 *Matn:*\n\n{msg.text or '(bosh)'}", parse_mode="Markdown")
            return True

        uid = user_id
        register_task(uid, label=f"Save: {_resolve_filename(msg)}")
        dest_chat, dest_thread = await _ensure_shared_topic(context.bot)
        if not dest_chat:
            dest_chat = update.effective_chat.id
            dest_thread = getattr(update.message, "message_thread_id", None)

        _progress_state[status.message_id] = "⬇️ *Yuklanmoqda...*"
        await status.edit_text(
            "⬇️ *Yuklanmoqda...*",
            parse_mode="Markdown",
            reply_markup=_refresh_kb(status.message_id),
        )

        ok = await _download_and_send_one(
            client, chat_id, msg_id, status, uid, dest_chat, dest_thread, context.bot,
        )
        _progress_state.pop(status.message_id, None)
        clear_task(uid)

        if ok:
            try:
                await status.delete()
            except Exception:
                pass
        else:
            err = "Bekor qilindi." if is_cancelled(uid) else "Yuklab bo'lmadi."
            await status.edit_text(f"❌ {err}")

    except Exception as e:
        logger.error("save_link_handler: %s", e, exc_info=True)
        clear_task(user_id)
        try:
            await status.edit_text(f"❌ Xato: {e}")
        except Exception:
            pass

    return True


async def save_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = update.message.text.split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text(
            "❗ Foydalanish:\n`/save https://t.me/c/1234567890/456`\n"
            "yoki havolani to'g'ridan yuboring.",
            parse_mode="Markdown",
        )
        return

    client = await get_user_client()
    if client is None:
        await update.message.reply_text("⚠️ Save Restricted sozlanmagan.")
        return

    chat_id, thread_id = parse_topic_link(args[1])
    if not chat_id or not thread_id:
        await update.message.reply_text("❌ Havola xato.", parse_mode="Markdown")
        return

    status = await update.message.reply_text("🔍 Topik skanlanmoqda...")
    user_id = update.effective_user.id

    try:
        await _resolve_peer_safe(client, chat_id)
        media_ids = []

        # Topikning ildiz (root) xabari ham media bo'lishi mumkin
        try:
            root = await client.get_messages(chat_id, thread_id)
            if root and not root.empty and root.media:
                media_ids.append(root.id)
        except Exception:
            pass

        # Eski usul (get_chat_history, limit=2000) butun chatning eng so'nggi
        # 2000 xabarini skanlardi — agar chatda boshqa topiklarda ko'p
        # yozishma bo'lsa, kerakli topik shu oynadan chiqib qolib,
        # "media topilmadi" deb noto'g'ri javob berardi.
        # get_discussion_replies thread bo'yicha to'g'ridan-to'g'ri so'raydi,
        # shuning uchun chat qancha katta bo'lishidan qat'i nazar ishlaydi.
        async for m in client.get_discussion_replies(chat_id, thread_id):
            if m.media:
                media_ids.append(m.id)
        media_ids = sorted(set(media_ids))

        if not media_ids:
            await status.edit_text("❌ Topikda media topilmadi.")
            return

        count = len(media_ids)
        label = f"Topic_{thread_id}_{count}files"
        uid, dest_chat, dest_thread = await _prepare_destination(update, context, label)

        if count <= 30:
            await status.edit_text(
                f"📦 {count} ta media yuklanmoqda...",
                reply_markup=_refresh_kb(status.message_id),
            )
            await _send_batch(client, chat_id, media_ids, status, uid, dest_chat, dest_thread, context.bot)
        else:
            key = f"sr_ids_{update.effective_chat.id}_{user_id}"
            context.bot_data[key] = {
                "chat_id": chat_id, "ids": media_ids,
                "user_id": uid, "dest_chat": dest_chat, "dest_thread": dest_thread,
            }
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Ha, yuborsin", callback_data=f"sr_confirm|{key}"),
                InlineKeyboardButton("❌ Bekor", callback_data="sr_cancel"),
            ]])
            await status.edit_text(
                f"⚠️ Topikda *{count}* ta media bor.\nHammasi yuklab yuborilsinmi?",
                reply_markup=kb, parse_mode="Markdown",
            )
        clear_task(uid)
    except Exception as e:
        logger.error("save_topic_handler: %s", e, exc_info=True)
        clear_task(user_id)
        await status.edit_text(f"❌ Xato: {e}")


async def save_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data.startswith("sr_progress|"):
        msg_id = int(query.data.split("|")[1])
        txt = _progress_state.get(msg_id, "⏳ Ma'lumot yo'q.")
        await query.answer(txt[:200], show_alert=True)
        return

    if query.data == "sr_cancel_run":
        uid = query.from_user.id
        await cancel_task(uid)
        await query.answer("❌ Bekor qilindi")
        try:
            await query.edit_message_text("❌ Yuklash bekor qilindi.")
        except Exception:
            pass
        return

    await query.answer()

    if query.data == "sr_cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        return

    _, key = query.data.split("|", 1)
    data = context.bot_data.get(key)
    if not data:
        await query.edit_message_text("❌ Ma'lumot topilmadi.")
        return

    client = await get_user_client()
    if client is None:
        await query.edit_message_text("⚠️ Userbot ulanmagan.")
        return

    uid = data.get("user_id", query.from_user.id)
    register_task(uid, label="Save batch")
    _progress_state[query.message.message_id] = "📦 *Yuklanmoqda...*"
    await query.edit_message_text(
        "📦 *Yuklanmoqda...*",
        parse_mode="Markdown",
        reply_markup=_refresh_kb(query.message.message_id),
    )
    context.bot_data.pop(key, None)
    await _send_batch(
        client, data["chat_id"], data["ids"], query.message,
        uid, data["dest_chat"], data["dest_thread"], context.bot,
    )
    clear_task(uid)
