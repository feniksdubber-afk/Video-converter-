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
import secrets
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

_BATCH_CONCURRENCY = 5  # bir vaqtda nechta fayl yuklanadi/yuboriladi (Pyrogram client bilan ham moslashtirilgan)

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
    """Eski (yagona) umumiy topic — endi ishlatilmaydi, faqat orqaga moslik
    (_load_topics migratsiyasi) uchun _load_shared_topic/_save_shared_topic
    bilan birga saqlanib qolmoqda."""
    if not ARCHIVE_GROUP_ID:
        return None, None
    cached = _load_shared_topic()
    return cached.get("chat_id"), cached.get("thread_id")


# ── "Qaysi topicga?" tanlovi uchun yaratilgan topiclar ro'yxati ─────────────
_TOPICS_FILE = os.path.join(DATA_DIR, "archive_topics.json")
_topics_cache: list[dict] | None = None


def _load_topics() -> list[dict]:
    """Tanlov tugmalari uchun saqlangan topiclar ro'yxatini qaytaradi."""
    global _topics_cache
    if _topics_cache is not None:
        return _topics_cache
    topics: list[dict] = []
    if os.path.isfile(_TOPICS_FILE):
        try:
            with open(_TOPICS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, list):
                topics = [t for t in saved if isinstance(t, dict) and t.get("thread_id")]
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("topics ro'yxati o'qish xato: %s", e)
    if not topics:
        # Eski (yagona) umumiy topic bo'lsa — ro'yxatga moslab qo'shamiz
        old = _load_shared_topic()
        if old.get("thread_id"):
            topics = [{"thread_id": old["thread_id"], "name": SHARED_TOPIC_NAME}]
    _topics_cache = topics
    return topics


def _save_topics(topics: list[dict]) -> None:
    global _topics_cache
    _topics_cache = topics
    try:
        with open(_TOPICS_FILE, "w", encoding="utf-8") as f:
            json.dump(topics, f, ensure_ascii=False)
    except OSError as e:
        logger.warning("topics ro'yxati saqlash xato: %s", e)


def _add_topic(thread_id: int, name: str) -> None:
    """Yangi yaratilgan topicni ro'yxat boshiga qo'shadi (eskirgan nusxalarni olib tashlaydi)."""
    topics = [t for t in _load_topics() if t.get("thread_id") != thread_id]
    topics.insert(0, {"thread_id": thread_id, "name": name[:64] or f"Topic {thread_id}"})
    _save_topics(topics[:50])


async def _fetch_live_topics(client: Client, chat_id: int) -> list[dict] | None:
    """ARCHIVE_GROUP_ID guruhidagi haqiqiy (Telegram'dagi) topiclarni userbot
    orqali (raw API — channels.getForumTopics) o'qiydi. Muvaffaqiyatsiz
    bo'lsa None qaytaradi (registrydagi eski ro'yxat saqlanib qoladi)."""
    try:
        from pyrogram.raw.functions.channels import GetForumTopics
        peer = await client.resolve_peer(chat_id)
        result = await client.invoke(
            GetForumTopics(channel=peer, offset_date=0, offset_id=0, offset_topic=0, limit=100)
        )
        topics: list[dict] = []
        for t in getattr(result, "topics", []):
            tid = getattr(t, "id", None)
            title = getattr(t, "title", None)
            if tid and title:
                topics.append({"thread_id": tid, "name": title[:64]})
        return topics
    except Exception as e:
        logger.warning("Live topics olish xato: %s", e)
        return None


def _topics_list_kb(key: str, topics: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(t.get("name", "Topic")[:32], callback_data=f"sr_dest_pick|{key}|{t['thread_id']}"),
            InlineKeyboardButton("🗑", callback_data=f"sr_dest_rm|{key}|{t['thread_id']}"),
        ]
        for t in topics
    ]
    rows.append([
        InlineKeyboardButton("🔄 Yangilash", callback_data=f"sr_dest_refresh|{key}"),
        InlineKeyboardButton("🔙 Orqaga", callback_data=f"sr_dest_back|{key}"),
    ])
    return InlineKeyboardMarkup(rows)


async def _render_topics_list(query, key: str, note: str = "") -> None:
    """'Mavjud topicga' ro'yxatini (yoki bo'sh holatini) qayta chizadi."""
    prefix = f"{note}\n\n" if note else ""
    topics = _load_topics()
    if not topics:
        await query.edit_message_text(
            f"{prefix}ℹ️ Hali birorta topic yaratilmagan.\n🆕 Yangi topic yarating.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🆕 Yangi topicga", callback_data=f"sr_dest_new|{key}")],
                [InlineKeyboardButton("🔄 Yangilash", callback_data=f"sr_dest_refresh|{key}")],
                [InlineKeyboardButton("❌ Bekor", callback_data=f"sr_dest_cancel|{key}")],
            ]),
        )
        return
    await query.edit_message_text(
        f"{prefix}📂 *Mavjud topiclar:*\n_🗑 — ro'yxatdan olib tashlash (Telegram'dagi topic o'chmaydi)_",
        parse_mode="Markdown",
        reply_markup=_topics_list_kb(key, topics),
    )


def _new_pending_key() -> str:
    return secrets.token_hex(4)


def _dest_choice_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Mavjud topicga", callback_data=f"sr_dest_list|{key}")],
        [InlineKeyboardButton("🆕 Yangi topicga", callback_data=f"sr_dest_new|{key}")],
        [InlineKeyboardButton("❌ Bekor", callback_data=f"sr_dest_cancel|{key}")],
    ])


class _MsgRef:
    """Callback orqali emas, matn (state) orqali davom etilganda ham xuddi
    PTB Message obyekti kabi murojaat qilish uchun yengil wrapper.

    utils/sender.py (send_file) status xabarda quyidagilarni chaqiradi:
    .chat_id, .edit_text, .reply_text, .reply_video, .reply_audio,
    .reply_document, .get_bot() — shularning barchasi shu yerda
    haqiqiy bot chaqiruvlariga proksilanadi."""

    def __init__(self, bot, chat_id: int, message_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id

    def get_bot(self):
        return self.bot

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        return await self.bot.edit_message_text(
            chat_id=self.chat_id, message_id=self.message_id,
            text=text, parse_mode=parse_mode, reply_markup=reply_markup,
        )

    async def delete(self):
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except Exception:
            pass

    async def reply_text(self, text, **kwargs):
        return await self.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)

    async def reply_video(self, video, **kwargs):
        return await self.bot.send_video(chat_id=self.chat_id, video=video, **kwargs)

    async def reply_audio(self, audio, **kwargs):
        return await self.bot.send_audio(chat_id=self.chat_id, audio=audio, **kwargs)

    async def reply_document(self, document, **kwargs):
        return await self.bot.send_document(chat_id=self.chat_id, document=document, **kwargs)


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
                # MUHIM: Pyrogram default'da bir vaqtda faqat 1 ta transmissiyaga
                # (yuklash/yuborish) ruxsat beradi — _BATCH_CONCURRENCY qancha
                # bo'lishidan qat'i nazar, fayllar MTProto darajasida navbatda
                # kutib, ketma-ket bajariladi ("⏳ navbatda..." shu sababdan
                # uzoq turib qoladi). Buni _BATCH_CONCURRENCY bilan moslashtiramiz.
                max_concurrent_transmissions=_BATCH_CONCURRENCY,
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
    """chat_id, thread_id, from_msg_id ni qaytaradi.
    from_msg_id — havolada 3-segment (konkret xabar) bo'lsa shu, aks holda None.
    Masalan: t.me/c/123/12616/12617 → thread_id=12616, from_msg_id=12617
             t.me/c/123/12616        → thread_id=12616, from_msg_id=None"""
    m = re.search(r"https?://t\.me/c/(\d+)/(\d+)(?:/(\d+))?", text)
    if m:
        chat_id = int("-100" + m.group(1))
        thread_id = int(m.group(2))
        from_msg_id = int(m.group(3)) if m.group(3) else None
        return chat_id, thread_id, from_msg_id
    return None, None, None


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
    silent: bool = False,
    report=None,
) -> bool:
    from utils.sender import send_file, _r2_pending
    from utils.db import is_already_saved, mark_saved

    if is_cancelled(user_id):
        return False

    media_obj = _media_obj(msg)
    if not media_obj:
        return False

    source_chat_id = getattr(getattr(msg, "chat", None), "id", None)
    if source_chat_id is not None and await is_already_saved(source_chat_id, msg.id, dest_thread_id):
        if report:
            report("⏭ allaqachon saqlangan")
        if not silent:
            try:
                await status_msg.edit_text(
                    "⏭ Allaqachon saqlangan, o'tkazib yuborildi.",
                    reply_markup=_refresh_kb(status_msg.message_id),
                )
            except Exception:
                pass
        return True

    filename = _resolve_filename(msg)
    short_name = filename if len(filename) <= 22 else filename[:19] + "..."
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
        if report:
            report(f"⬇️ {short_name} {pct}%")
        if silent:
            return
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

        if report:
            report(f"📤 {short_name} yuborilmoqda")
        if not silent:
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

        if source_chat_id is not None:
            await mark_saved(source_chat_id, msg.id, dest_chat_id, dest_thread_id)
        return True

    except Exception as e:
        logger.error("_download_and_send xato: %s", e, exc_info=True)
        if not silent:
            try:
                await status_msg.edit_text(
                    f"⚠️ *{filename}* yuborilmadi:\n`{e}`",
                    parse_mode="Markdown",
                    reply_markup=_refresh_kb(status_msg.message_id),
                )
                await asyncio.sleep(2)
            except Exception:
                pass
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
    silent: bool = False,
    report=None,
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
                dest_chat_id, dest_thread_id, bot, silent=silent, report=report,
            ):
                ok_any = True
            await asyncio.sleep(0.8)
        return ok_any

    except FloodWait as e:
        if report:
            report(f"⏳ flood {e.value}s kutilmoqda")
        if not silent:
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
            dest_chat_id, dest_thread_id, bot, _retry, silent=silent, report=report,
        )
    except OSError as e:
        if _retry < 3:
            await asyncio.sleep(2 * (_retry + 1))
            return await _download_and_send_one(
                client, from_chat, msg_id, status_msg, user_id,
                dest_chat_id, dest_thread_id, bot, _retry + 1, silent=silent, report=report,
            )
        logger.error("msg %s OSError: %s", msg_id, e)
        return False
    except Exception as e:
        logger.error("msg %s xato: %s", msg_id, e, exc_info=True)
        return False


async def _send_batch(
    client: Client, from_chat, ids: list, status_msg,
    user_id: int, dest_chat_id: int, dest_thread_id: int | None, bot,
    bot_data: dict | None = None,
):
    total = len(ids)
    if total == 0:
        await status_msg.edit_text("❌ Yuboriladigan media yo'q.")
        return

    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)
    done = 0
    sent = 0
    failed_ids: list[int] = []
    lock = asyncio.Lock()
    cancelled_flag = False
    slots: dict[int, str] = {}  # slot raqami → o'sha slotdagi joriy holat matni

    async def _worker(mid: int, slot: int):
        nonlocal done, sent, cancelled_flag

        def _report(text: str):
            slots[slot] = text

        async with sem:
            if is_cancelled(user_id):
                cancelled_flag = True
                return
            slots[slot] = "⏳ navbatda..."
            ok = await _download_and_send_one(
                client, from_chat, mid, status_msg, user_id,
                dest_chat_id, dest_thread_id, bot, silent=True, report=_report,
            )
            slots.pop(slot, None)
            async with lock:
                done += 1
                if ok:
                    sent += 1
                else:
                    failed_ids.append(mid)

    async def _progress_reporter():
        last_render = ""
        while done < total and not cancelled_flag:
            if is_cancelled(user_id):
                return
            bar = _progress_bar(int(done / total * 100))
            lines = [f"📦 *{done}/{total}* yuklandi ({_BATCH_CONCURRENCY} parallel)", bar]
            for s in sorted(slots.keys()):
                lines.append(f"`{slots[s]}`")
            render = "\n".join(lines)
            if render != last_render:
                last_render = render
                _progress_state[status_msg.message_id] = render
                try:
                    await status_msg.edit_text(
                        render, parse_mode="Markdown",
                        reply_markup=_refresh_kb(status_msg.message_id),
                    )
                except Exception:
                    pass
            await asyncio.sleep(2.0)

    reporter_task = asyncio.create_task(_progress_reporter())
    try:
        await asyncio.gather(*[
            _worker(mid, i % _BATCH_CONCURRENCY) for i, mid in enumerate(ids)
        ])
    finally:
        reporter_task.cancel()
        try:
            await reporter_task
        except asyncio.CancelledError:
            pass

    if cancelled_flag or is_cancelled(user_id):
        await status_msg.edit_text(f"❌ Bekor qilindi. {sent}/{total} yuborildi.")
        return

    archive_note = f"\n☁️ Arxiv guruhi: `{dest_chat_id}`" if ARCHIVE_GROUP_ID else ""
    fail_note = f"\n⚠️ Muvaffaqiyatsiz: *{len(failed_ids)}* ta" if failed_ids else ""

    if failed_ids and bot_data is not None:
        retry_key = f"sr_retry_{secrets.token_hex(4)}"
        bot_data[retry_key] = {
            "chat_id": from_chat, "ids": failed_ids,
            "user_id": user_id, "dest_chat": dest_chat_id, "dest_thread": dest_thread_id,
        }
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🔁 Qayta urinish ({len(failed_ids)})", callback_data=f"sr_retry|{retry_key}"),
        ]])
        await status_msg.edit_text(
            f"✅ {sent}/{total} ta media yuborildi.{archive_note}{fail_note}",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    else:
        await status_msg.edit_text(f"✅ {sent}/{total} ta media yuborildi.{archive_note}{fail_note}", parse_mode="Markdown")


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

        if not ARCHIVE_GROUP_ID:
            # Arxiv guruh sozlanmagan — tanlov mantiqsiz, joriy chatga saqlaymiz
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
            return True

        # Arxiv guruh bor — qaysi topicga saqlashni so'raymiz
        clear_task(uid)
        key = _new_pending_key()
        context.bot_data[key] = {
            "kind": "link",
            "chat_id": chat_id,
            "msg_id": msg_id,
            "user_id": uid,
            "status_chat_id": status.chat_id,
            "status_message_id": status.message_id,
        }
        await status.edit_text(
            f"📁 *{_resolve_filename(msg)}*\n\n📌 Qaysi topicga saqlaymiz?",
            parse_mode="Markdown",
            reply_markup=_dest_choice_kb(key),
        )

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

    chat_id, thread_id, from_msg_id = parse_topic_link(args[1])
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
        scanned = 0
        last_update = time.monotonic()
        async for m in client.get_discussion_replies(chat_id, thread_id):
            scanned += 1
            if m.media:
                media_ids.append(m.id)
            now = time.monotonic()
            if now - last_update >= 2.0:
                last_update = now
                try:
                    await status.edit_text(
                        f"🔍 *{scanned}* xabar tekshirildi, *{len(media_ids)}* ta media topildi..."
                        + (f"\n📍 {from_msg_id} xabardan boshlab" if from_msg_id else ""),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
        media_ids = sorted(set(media_ids))

        # Havolada konkret xabar ko'rsatilgan bo'lsa (3-segment) —
        # faqat shu xabardan boshlab oxirigacha bo'lganlarini olamiz.
        if from_msg_id:
            media_ids = [mid for mid in media_ids if mid >= from_msg_id]

        if not media_ids:
            note = " (berilgan xabardan keyin)" if from_msg_id else ""
            await status.edit_text(f"❌ Topikda media topilmadi{note}.")
            return

        count = len(media_ids)
        range_note = f"\n📍 *{from_msg_id}* xabardan boshlab" if from_msg_id else ""
        label = f"Topic_{thread_id}_{count}files"

        if not ARCHIVE_GROUP_ID:
            # Arxiv guruh sozlanmagan — eski xatti-harakat (tanlovsiz)
            uid, dest_chat, dest_thread = await _prepare_destination(update, context, label)
            if count <= 30:
                await status.edit_text(
                    f"📦 {count} ta media yuklanmoqda...",
                    reply_markup=_refresh_kb(status.message_id),
                )
                await _send_batch(client, chat_id, media_ids, status, uid, dest_chat, dest_thread, context.bot, context.bot_data)
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
                    f"⚠️ Topikda *{count}* ta media bor.{range_note}\nHammasi yuklab yuborilsinmi?",
                    reply_markup=kb, parse_mode="Markdown",
                )
            clear_task(uid)
            return

        # Arxiv guruh bor — qaysi topicga saqlashni so'raymiz
        key = _new_pending_key()
        context.bot_data[key] = {
            "kind": "topic",
            "from_chat": chat_id,
            "ids": media_ids,
            "label": label,
            "user_id": user_id,
            "status_chat_id": status.chat_id,
            "status_message_id": status.message_id,
        }
        await status.edit_text(
            f"📦 *{count}* ta media topildi.{range_note}\n\n📌 Qaysi topicga saqlaymiz?",
            parse_mode="Markdown",
            reply_markup=_dest_choice_kb(key),
        )
    except Exception as e:
        logger.error("save_topic_handler: %s", e, exc_info=True)
        clear_task(user_id)
        await status.edit_text(f"❌ Xato: {e}")


async def _continue_link_save(key: str, dest_chat: int, dest_thread: int | None, bot, status_ref, bot_data: dict):
    """Topic tanlangandan keyin — bitta havola (link) saqlashni davom ettiradi."""
    pending = bot_data.pop(key, None)
    if not pending:
        await status_ref.edit_text("❌ Ma'lumot topilmadi yoki eskirgan.")
        return

    client = await get_user_client()
    if client is None:
        await status_ref.edit_text("⚠️ Userbot ulanmagan.")
        return

    user_id = pending["user_id"]
    register_task(user_id, label="Save link")
    _progress_state[status_ref.message_id] = "⬇️ *Yuklanmoqda...*"
    try:
        await status_ref.edit_text(
            "⬇️ *Yuklanmoqda...*", parse_mode="Markdown",
            reply_markup=_refresh_kb(status_ref.message_id),
        )
    except Exception:
        pass

    ok = await _download_and_send_one(
        client, pending["chat_id"], pending["msg_id"], status_ref, user_id,
        dest_chat, dest_thread, bot,
    )
    _progress_state.pop(status_ref.message_id, None)
    clear_task(user_id)

    if ok:
        try:
            await status_ref.delete()
        except Exception:
            pass
    else:
        err = "Bekor qilindi." if is_cancelled(user_id) else "Yuklab bo'lmadi."
        await status_ref.edit_text(f"❌ {err}")


async def _continue_topic_save(key: str, dest_chat: int, dest_thread: int | None, bot, status_ref, bot_data: dict):
    """Topic tanlangandan keyin — butun topikdan saqlashni davom ettiradi."""
    pending = bot_data.pop(key, None)
    if not pending:
        await status_ref.edit_text("❌ Ma'lumot topilmadi yoki eskirgan.")
        return

    client = await get_user_client()
    if client is None:
        await status_ref.edit_text("⚠️ Userbot ulanmagan.")
        return

    user_id = pending["user_id"]
    ids = pending["ids"]
    from_chat = pending["from_chat"]
    count = len(ids)
    register_task(user_id, label=f"Save: {pending.get('label', '')}")

    if count <= 30:
        try:
            await status_ref.edit_text(
                f"📦 {count} ta media yuklanmoqda...",
                reply_markup=_refresh_kb(status_ref.message_id),
            )
        except Exception:
            pass
        await _send_batch(client, from_chat, ids, status_ref, user_id, dest_chat, dest_thread, bot, bot_data)
        clear_task(user_id)
    else:
        confirm_key = f"sr_ids_{status_ref.chat_id}_{user_id}_{secrets.token_hex(3)}"
        bot_data[confirm_key] = {
            "chat_id": from_chat, "ids": ids,
            "user_id": user_id, "dest_chat": dest_chat, "dest_thread": dest_thread,
        }
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Ha, yuborsin", callback_data=f"sr_confirm|{confirm_key}"),
            InlineKeyboardButton("❌ Bekor", callback_data="sr_cancel"),
        ]])
        await status_ref.edit_text(
            f"⚠️ Topikda *{count}* ta media bor.\nHammasi yuklab yuborilsinmi?",
            reply_markup=kb, parse_mode="Markdown",
        )
        clear_task(user_id)


async def handle_save_new_topic_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'🆕 Yangi topicga' bosilgandan keyin foydalanuvchi yozgan nomni qabul qiladi,
    yangi forum topic yaratadi va kutilayotgan saqlashni davom ettiradi."""
    name = (update.message.text or "").strip()
    key = context.user_data.get("save_pending_key")
    pending = context.bot_data.get(key) if key else None

    if not pending:
        context.user_data.pop("state", None)
        context.user_data.pop("save_pending_key", None)
        await update.message.reply_text("❌ Ma'lumot topilmadi yoki eskirgan. Havolani qaytadan yuboring.")
        return

    if not name or name.startswith("/"):
        await update.message.reply_text("❗ Iltimos, topic uchun matn ko'rinishida nom yuboring.")
        return

    context.user_data.pop("state", None)
    context.user_data.pop("save_pending_key", None)

    try:
        topic = await context.bot.create_forum_topic(chat_id=ARCHIVE_GROUP_ID, name=name[:128])
        thread_id = topic.message_thread_id
    except Exception as e:
        logger.warning("Yangi topic yaratish xato: %s", e)
        err_text = str(e).lower()
        if "not a forum" in err_text:
            await update.message.reply_text(
                "❌ Bu guruhda *Topics (Mavzular)* funksiyasi yoqilmagan.\n\n"
                "Tuzatish:\n"
                "1. Guruhni Telegram'da oching (admin sifatida)\n"
                "2. Guruh nomi → ✏️ Edit → *Topics* ni yoqing\n"
                "3. Qaytadan urinib ko'ring",
                parse_mode="Markdown",
            )
        elif "not enough rights" in err_text or "chat_admin_required" in err_text:
            await update.message.reply_text(
                "❌ Botda topic yaratish huquqi yo'q.\n\n"
                "Tuzatish: Botni guruhda *admin* qiling va "
                "*\"Manage Topics\"* huquqini yoqing.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Topic yaratilmadi: {e}")
        return

    _add_topic(thread_id, name)

    status_ref = _MsgRef(context.bot, pending["status_chat_id"], pending["status_message_id"])
    try:
        await status_ref.edit_text(f"✅ Topic yaratildi: *{name[:60]}*", parse_mode="Markdown")
    except Exception:
        pass

    if pending["kind"] == "link":
        await _continue_link_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, status_ref, context.bot_data)
    else:
        await _continue_topic_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, status_ref, context.bot_data)


async def save_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data.startswith("sr_retry|"):
        await query.answer()
        key = query.data.split("|", 1)[1]
        data = context.bot_data.pop(key, None)
        if not data:
            await query.edit_message_text("❌ Ma'lumot topilmadi yoki eskirgan.")
            return
        client = await get_user_client()
        if client is None:
            await query.edit_message_text("⚠️ Userbot ulanmagan.")
            return
        uid = data.get("user_id", query.from_user.id)
        register_task(uid, label="Save retry")
        await query.edit_message_text(
            f"🔁 {len(data['ids'])} ta faylni qayta urinish boshlandi...",
            reply_markup=_refresh_kb(query.message.message_id),
        )
        await _send_batch(
            client, data["chat_id"], data["ids"], query.message,
            uid, data["dest_chat"], data["dest_thread"], context.bot, context.bot_data,
        )
        clear_task(uid)
        return

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

    # ── "Qaysi topicga?" tanlovi ────────────────────────────────────────────
    if query.data.startswith("sr_dest_list|"):
        await query.answer()
        key = query.data.split("|", 1)[1]
        if key not in context.bot_data:
            await query.edit_message_text("❌ Ma'lumot topilmadi yoki eskirgan.")
            return
        await _render_topics_list(query, key)
        return

    if query.data.startswith("sr_dest_refresh|"):
        await query.answer("🔄 Yangilanmoqda...")
        key = query.data.split("|", 1)[1]
        if key not in context.bot_data:
            await query.edit_message_text("❌ Ma'lumot topilmadi yoki eskirgan.")
            return
        client = await get_user_client()
        if client is None:
            await query.edit_message_text("⚠️ Userbot ulanmagan.")
            return
        live = await _fetch_live_topics(client, ARCHIVE_GROUP_ID)
        if live is None:
            await _render_topics_list(
                query, key,
                note="⚠️ Telegram'dan yangilab bo'lmadi — joriy ro'yxat ko'rsatilmoqda.",
            )
            return
        _save_topics(live[:50])
        await _render_topics_list(query, key, note="✅ Ro'yxat yangilandi.")
        return

    if query.data.startswith("sr_dest_rm|"):
        await query.answer("🗑 Ro'yxatdan olib tashlandi")
        _, key, thread_id_s = query.data.split("|", 2)
        if key not in context.bot_data:
            await query.edit_message_text("❌ Ma'lumot topilmadi yoki eskirgan.")
            return
        thread_id = int(thread_id_s)
        _save_topics([t for t in _load_topics() if t.get("thread_id") != thread_id])
        await _render_topics_list(query, key)
        return

    if query.data.startswith("sr_dest_back|"):
        await query.answer()
        key = query.data.split("|", 1)[1]
        if key not in context.bot_data:
            await query.edit_message_text("❌ Ma'lumot topilmadi yoki eskirgan.")
            return
        await query.edit_message_text(
            "📌 Qaysi topicga saqlaymiz?",
            reply_markup=_dest_choice_kb(key),
        )
        return

    if query.data.startswith("sr_dest_cancel|"):
        await query.answer("❌ Bekor qilindi")
        key = query.data.split("|", 1)[1]
        context.bot_data.pop(key, None)
        await query.edit_message_text("❌ Bekor qilindi.")
        return

    if query.data.startswith("sr_dest_new|"):
        await query.answer()
        key = query.data.split("|", 1)[1]
        if key not in context.bot_data:
            await query.edit_message_text("❌ Ma'lumot topilmadi yoki eskirgan.")
            return
        context.user_data["state"] = "save_new_topic_name"
        context.user_data["save_pending_key"] = key
        await query.edit_message_text(
            "🆕 Yangi topic uchun nom yuboring (matn sifatida):",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Bekor", callback_data=f"sr_dest_cancel|{key}"),
            ]]),
        )
        return

    if query.data.startswith("sr_dest_pick|"):
        await query.answer()
        _, key, thread_id_s = query.data.split("|", 2)
        if key not in context.bot_data:
            await query.edit_message_text("❌ Ma'lumot topilmadi yoki eskirgan.")
            return
        pending = context.bot_data[key]
        thread_id = int(thread_id_s)
        if pending["kind"] == "link":
            await _continue_link_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, query.message, context.bot_data)
        else:
            await _continue_topic_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, query.message, context.bot_data)
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
        uid, data["dest_chat"], data["dest_thread"], context.bot, context.bot_data,
    )
    clear_task(uid)
