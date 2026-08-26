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
import subprocess
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

# Standart parallel yuklash soni — foydalanuvchi sozlamasidan o'qiladi,
# bu qiymat faqat zaxira (fallback) sifatida ishlatiladi.
_DEFAULT_BATCH_CONCURRENCY = 2

# bot_session uchun "Peer id invalid" xatosini oldini olish: muvaffaqiyatli
# resolve qilingan chat_id'larni xotirada saqlaymiz, har xabarda qayta
# get_chat() chaqirmaslik uchun. (Konteyner qayta tiklanganda/process qayta
# ishga tushganda bu to'plam tozalanadi va birinchi yuborishda qayta
# resolve qilinadi — bu normal holat.)
_resolved_peers: set[int] = set()

# ── Foydalanuvchi Save Restricted sozlamalarini yuklash ─────────────────────

async def _load_sr_settings(user_id: int) -> dict:
    """Foydalanuvchining sr_parallel sozlamasini qaytaradi.
    (chunk_delay endi qo'lda sozlanmaydi — _smart_chunk_delay() avtomatik
    hisoblaydi, _FLOOD_THRESHOLD/_smart_chunk_delay() ga qarang.)"""
    from utils.db import db_load, DEFAULTS
    try:
        settings = await db_load(user_id)
        return {
            "parallel": bool(int(settings.get("sr_parallel", DEFAULTS.get("sr_parallel", 1)))),
        }
    except Exception:
        return {"parallel": True}

# Bot allaqachon a'zo bo'lgan, lekin username/invite-link orqali "tanishtirib"
# bo'lmaydigan (BOT_METHOD_INVALID: messages.CheckChatInvite faqat user
# akkauntlar uchun) guruhlar/kanallar uchun: get_dialogs() chaqirilganda
# pyrogram bot turgan BARCHA chatlarning peer (access_hash) ma'lumotini
# avtomatik cache'ga yozadi. Shu sababli ARCHIVE_GROUP_ID kabi private va
# username'siz guruhlar uchun ham ishlaydi — chunki bot u yerda a'zo.
async def _bootstrap_bot_peer_cache(bot_client) -> None:
    """bot_session uchun barcha a'zo bo'lgan chatlarning peer cache'ini
    bir martalik to'ldiradi (get_dialogs orqali). Xatolar yutiladi —
    chaqiruvchi keyin resolve_peer/get_chat orqali baribir tekshiradi."""
    try:
        async for _ in bot_client.get_dialogs():
            pass
    except Exception as e:
        logger.warning("get_dialogs bootstrap xato: %s", e)


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
        try:
            peer = await client.resolve_peer(chat_id)
        except Exception:
            # client (user_session) bu guruhni hali "tanimasa" — get_dialogs()
            # orqali u a'zo bo'lgan barcha chatlarning peer'ini cache'laymiz.
            await _bootstrap_bot_peer_cache(client)
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
_user_client_premium: bool | None = None

_progress_state: dict = {}

# Limitdan katta fayllar uchun "R2'ga yuklaymizmi?" tasdiqlash kutilmoqda.
# {confirm_key: {"chat_id", "msg_id", "filename", "file_size", "user_id", "ts"}}
_big_file_pending: dict[str, dict] = {}
_BIG_FILE_PENDING_TTL = 3600  # 1 soat


async def get_user_client() -> Client | None:
    global _user_client, _user_client_premium
    if not SESSION_STRING:
        return None
    async with _user_lock:
        if _user_client is None or not _user_client.is_connected:
            _user_client = Client(
                "user_session",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=SESSION_STRING,
                # MUHIM: bu qiymat _smart_batch_concurrency() qaytarishi mumkin
                # bo'lgan eng katta parallel sondan past bo'lmasligi kerak —
                # aks holda Pyrogram fayllarni MTProto darajasida (media
                # session pool) navbatda kutib, ketma-ket bajaradi va
                # yuqori darajadagi "parallel" sozlama amalda ishlamaydi.
                max_concurrent_transmissions=4,
            )

            await _user_client.start()
            try:
                me = await _user_client.get_me()
                _user_client_premium = bool(getattr(me, "is_premium", False))
                logger.info(
                    "Userbot ulandi: %s (Premium: %s)",
                    getattr(me, "first_name", "?"), _user_client_premium,
                )
            except Exception as e:
                logger.warning("Userbot Premium holatini tekshirib bo'lmadi: %s", e)
                _user_client_premium = False
    return _user_client


async def is_user_premium() -> bool:
    """Userbot Telegram Premium akkauntmi — bir martalik tekshirib keshlaydi.
    Premium bo'lsa: 4GB yuklab yuborish limiti va ko'proq parallel oqim
    xavfsiz hisoblanadi."""
    global _user_client_premium
    if _user_client_premium is None:
        await get_user_client()
    return bool(_user_client_premium)


# ── Aqlli (adaptiv) tezlik boshqaruvi ────────────────────────────────────────
# Foydalanuvchi qo'lda "chunk oraliq" tanlashi o'rniga bot o'zi:
#   - oxirgi bir necha daqiqada FloodWait qancha tez-tez tushganini kuzatadi
#     va shunga moslab juda kichik xavfsizlik kutishini avtomatik yoqadi —
#     aks holda doim 0.0 (to'liq tezlik);
#   - userbot Premium ekanligiga qarab parallel yuklab olish sonini tanlaydi.
_flood_events: list[float] = []
_FLOOD_WINDOW = 60.0   # shu oraliqdagi flood'lar hisobga olinadi (soniya)
_FLOOD_THRESHOLD = 2   # shu oraliqda kamida shuncha flood bo'lsa — sekinlashtiramiz


def _record_flood_event() -> None:
    """FloodWait tushganda chaqiriladi — adaptiv sekinlashtirish uchun."""
    now = time.time()
    _flood_events.append(now)
    cutoff = now - _FLOOD_WINDOW
    while _flood_events and _flood_events[0] < cutoff:
        _flood_events.pop(0)


def _smart_chunk_delay() -> float:
    """So'nggi _FLOOD_WINDOW soniyada _FLOOD_THRESHOLD'dan ko'p (yoki teng)
    flood bo'lgan bo'lsa — kichik xavfsizlik kutishi qo'shamiz, aks holda
    0.0 (sun'iy sekinlashtirishsiz, to'liq tezlik)."""
    now = time.time()
    cutoff = now - _FLOOD_WINDOW
    recent = sum(1 for t in _flood_events if t >= cutoff)
    return 1.0 if recent >= _FLOOD_THRESHOLD else 0.0


async def _smart_batch_concurrency(parallel: bool) -> int:
    """Foydalanuvchi sozlamadan "parallel" yoqgan bo'lsa, userbot Premium
    ekanligiga qarab bir vaqtda nechta fayl yuklanishini tanlaydi."""
    if not parallel:
        return 1
    premium = await is_user_premium()
    return 4 if premium else 2



def _refresh_kb(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Yangilash", callback_data=f"sr_progress|{msg_id}"),
            InlineKeyboardButton("❌ Bekor", callback_data="sr_cancel_run"),
        ],
    ])


def _progress_bar(percent: int, length: int = 14) -> str:
    filled = int(length * percent / 100)
    return "\u25b0" * filled + "\u25b1" * (length - filled)


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
             t.me/c/123/12616        → thread_id=12616, from_msg_id=None
    Ochiq (username) kanallar uchun ham ishlaydi:
             t.me/Minxotv_Arxiv/9352/9360 → chat_id="Minxotv_Arxiv" (username),
             thread_id=9352, from_msg_id=9360
    chat_id qaytishi mumkin: int (yopiq guruh/kanal, -100... ko'rinishida)
    yoki str (ochiq kanal username'i) — ikkalasi ham Pyrogram uchun to'g'ri
    identifikator."""
    m = re.search(r"https?://t\.me/c/(\d+)/(\d+)(?:/(\d+))?", text)
    if m:
        chat_id = int("-100" + m.group(1))
        thread_id = int(m.group(2))
        from_msg_id = int(m.group(3)) if m.group(3) else None
        return chat_id, thread_id, from_msg_id
    m = re.search(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/(\d+)(?:/(\d+))?", text)
    if m and m.group(1).lower() not in ("c", "s", "share", "joinchat"):
        chat_id = "@" + m.group(1)
        thread_id = int(m.group(2))
        from_msg_id = int(m.group(3)) if m.group(3) else None
        return chat_id, thread_id, from_msg_id
    return None, None, None


async def _resolve_peer_safe(client: Client, chat_id):
    if isinstance(chat_id, str):
        # Username (masalan "@Minxotv_Arxiv") — resolve_peer buni ham
        # to'g'ridan-to'g'ri qabul qiladi, kanal ochiq bo'lsa userbot
        # a'zo bo'lmasa ham ishlaydi.
        try:
            await client.resolve_peer(chat_id)
            return True
        except Exception:
            pass
        try:
            await client.get_chat(chat_id)
            return True
        except Exception:
            return False
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


def _md_escape(text) -> str:
    """Telegram Markdown (legacy) uchun maxsus belgilarni escape qiladi.

    Fayl nomi, topic nomi kabi tashqi manbadan (Telegram xabari metadata'si,
    foydalanuvchi kiritgan matn) keladigan har qanday matnni Markdown
    formatlangan xabar ichiga qo'yishdan oldin shundan o'tkazish kerak —
    aks holda `_`, `*`, `` ` ``, `[` kabi belgilar parse xatosiga olib
    kelishi mumkin (masalan "Invalid parse mode" yoki "can't parse entities").
    """
    s = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


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
    chunk_delay: float = 0.0,
    send_gate=None,
    caption_override: str | None = None,
) -> bool:
    from utils.sender import send_file, _r2_pending
    from utils.db import is_already_saved, mark_saved

    # MUHIM: send_gate har qanday chiqish yo'lida (erta return, istisno,
    # muvaffaqiyatli yuborish) albatta bo'shatilishi shart — aks holda undan
    # keyingi navbatdagi barcha workerlar abadiy kutib qoladi. _gate_released
    # bayrog'i orqali advance() faqat bir marta chaqirilishi ta'minlanadi.
    _gate_released = False

    def _release_gate():
        nonlocal _gate_released
        if send_gate is not None and not _gate_released:
            _gate_released = True
            send_gate.advance()

    tmp_path = None
    filename = "fayl"
    try:
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

        # ── Fayl hajmi userbot limitidan katta bo'lsa (Premium: 4GB, oddiy: 2GB) ──
        # Telegram MTProto darajasida bu limitdan katta faylni userbot session
        # yuklab (va/yoki qayta yuborib) bo'lmaydi. Shu sababli download
        # boshlanishidan OLDIN tekshiramiz va foydalanuvchidan R2'ga
        # yuklashni xohlaydimi-yo'qmi so'raymiz — jim skip qilib qo'ymaymiz.
        if file_size:
            from utils.sender import PYROGRAM_LIMIT, PYROGRAM_PREMIUM_LIMIT
            from utils.r2_manager import is_configured as _r2_ok
            _limit = PYROGRAM_PREMIUM_LIMIT if await is_user_premium() else PYROGRAM_LIMIT
            if file_size > _limit:
                gb = file_size / 1024 / 1024 / 1024
                limit_gb = _limit / 1024 / 1024 / 1024
                if report:
                    report(f"⚠️ {short_name} {limit_gb:.0f}GB limitdan katta")
                # MUHIM: bu xabar status_msg'ni EDIT qilmaydi — batch rejimida
                # (silent=True) status_msg umumiy progress xabari, uni bosib
                # yozib qo'ysak umumiy progress ko'rinishi buziladi. Shu sababli
                # doim ALOHIDA xabar sifatida yuboriladi, silent'dan qat'i nazar
                # — bu chindan ham muhim/harakat talab qiladigan holat, oddiy
                # progress emas.
                if _r2_ok():
                    confirm_key = secrets.token_hex(4)
                    _big_file_pending[confirm_key] = {
                        "chat_id": source_chat_id,
                        "msg_id": msg.id,
                        "filename": filename,
                        "file_size": file_size,
                        "user_id": user_id,
                        "ts": time.time(),
                    }
                    try:
                        await bot.send_message(
                            chat_id=status_msg.chat_id,
                            text=(
                                f"⚠️ *{_md_escape(filename)}*\n"
                                f"Hajmi: `{gb:.2f} GB` — userbot limiti (`{limit_gb:.0f} GB`) dan katta, "
                                f"Telegram orqali yuklab bo'lmaydi.\n\n"
                                f"☁️ R2'ga yuklashni istaysizmi?"
                            ),
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("☁️ Ha, R2'ga yukla", callback_data=f"sr_r2big|{confirm_key}"),
                                InlineKeyboardButton("❌ Yo'q", callback_data=f"sr_r2big_no|{confirm_key}"),
                            ]]),
                        )
                    except Exception:
                        pass
                else:
                    try:
                        await bot.send_message(
                            chat_id=status_msg.chat_id,
                            text=(
                                f"❌ *{_md_escape(filename)}* hajmi (`{gb:.2f} GB`) userbot limitidan "
                                f"(`{limit_gb:.0f} GB`) katta — o'tkazib yuborildi."
                            ),
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass
                return False

        async def _dl_progress(current, total):
            if is_cancelled(user_id):
                return
            if not total:
                return
            # chunk_delay > 0 bo'lsa — har bir chunk yuklanib bo'lgandan keyin
            # shu qancha soniya kutiladi. Bu Telegram flood limit bilan kurashadi:
            # progress callback kutilayotganda Pyrogram keyingi GetFile so'rovini
            # YUBORMAYDI, ya'ni biz download tezligini sun'iy pasaytiramiz.
            if chunk_delay > 0.0:
                await asyncio.sleep(chunk_delay)
            pct = min(int(current / total * 100), 99)
            cur_mb = current / 1024 / 1024
            bar = _progress_bar(pct)
            if report:
                report(f"⬇️ {short_name} {pct}%")
            if silent:
                return
            txt = f"⬇️ *Yuklanmoqda...*\n\n`{bar}` `{pct}%`\n`{cur_mb:.1f} MB` / `{total_mb:.1f} MB`"
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

        tmp_path = os.path.join(TEMP_DIR, f"sr_{msg.id}_{user_id}_{int(time.time()*1000)}.{ext}")
        await pyro_client.download_media(media_obj, file_name=tmp_path, progress=_dl_progress)

        if is_cancelled(user_id):
            return False

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            return False

        # Yuklangan fayl hajmi Telegram bergan asl hajmdan sezilarli kichik
        # bo'lsa — yuklash to'liq tugamagan (uzilib qolgan) degani. Bunday
        # faylni jim yuborib yubormaymiz, xato deb hisoblaymiz.
        downloaded_size = os.path.getsize(tmp_path)
        if file_size and downloaded_size < file_size * 0.95:
            logger.error(
                "Tugallanmagan yuklash: %s — kutilgan %s bayt, olingan %s bayt",
                filename, file_size, downloaded_size,
            )
            if report:
                report(f"❌ {short_name} to'liq yuklanmadi")
            if not silent:
                try:
                    await status_msg.edit_text(
                        f"❌ *{_md_escape(filename)}* to'liq yuklanmadi "
                        f"({downloaded_size/1024/1024:.1f} / {file_size/1024/1024:.1f} MB) — qayta urinib ko'ring.",
                        parse_mode="Markdown",
                        reply_markup=_refresh_kb(status_msg.message_id),
                    )
                except Exception:
                    pass
            return False

        # ── Tartibni saqlash uchun "navbat darvozasi" ───────────────────────
        # Download paralleldir (tez), lekin guruhga jo'natish (send_file)
        # qat'iy ketma-ket bo'lishi shart — eski guruh mavzusidagi tartib
        # buzilmasligi uchun. send_gate (_TurnGate) shu yerda o'z navbatini
        # kutadi: undan oldingi barcha xabarlar yuborilib bo'lgunicha
        # to'xtab turadi, kichik fayl tezroq yuklansa ham guruhga otilib
        # ketmaydi.
        if send_gate is not None:
            if report:
                report(f"⏳ {short_name} navbatda (yuborish uchun)")
            await send_gate.wait_turn()

        if report:
            report(f"📤 {short_name} yuborilmoqda")
        if not silent:
            try:
                await status_msg.edit_text(
                    f"📤 *Yuborilmoqda:* `{_md_escape(filename)}`",
                    parse_mode="Markdown",
                    reply_markup=_refresh_kb(status_msg.message_id),
                )
            except Exception:
                pass

        caption = caption_override if caption_override is not None else (msg.caption or "")
        from utils.db import db_load
        settings = await db_load(user_id)

        # /save orqali yuborilganda video fayllar 🎬 video pleyer ko'rinishida
        # ketsin (force_document emas) — bu yerda foydalanuvchining shaxsiy
        # upload_mode sozlamasidan qat'i nazar, video kengaytmasi bo'lsa
        # "video" rejimini majburlaymiz.
        video_ext = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".wmv"}
        is_video_file = os.path.splitext(filename)[1].lower() in video_ext
        if is_video_file:
            settings = dict(settings)
            settings["upload_mode"] = "video"

        class _FakeCtx:
            user_data = {"settings": settings, "_settings_loaded": True, "_user_id": user_id}

        target_chat = dest_chat_id or status_msg.chat_id

        # bot_session (Pyrogram) dest_chat peer'ini bilmasa send_video/send_document
        # "Peer id invalid" xatosi beradi. send_file() chaqirishdan oldin
        # bot_session'ga bu chat'ni cache'laymiz.
        #
        # MUHIM: ARCHIVE_GROUP_ID kabi -100... (kanal/supergroup) turidagi
        # chatlar uchun bu DOIM muvaffaqiyatsiz bo'ladi — get_dialogs()
        # botlar uchun taqiqlangan (BOT_METHOD_INVALID), va kanal turidagi
        # peer'lar faqat avval resolve qilingan bo'lsagina o'z update
        # oqimiga (channel_pts) "obuna" bo'ladi — bot bu oqimga hech qachon
        # ulanmagani uchun o'z-o'zidan ham hech qachon o'rganib ololmaydi
        # (tuxum-tovuq holati). Shu sababli muvaffaqiyatsizlik kutilgan holat
        # va warning yutiladi, keyin pastda userbot (user_session) ishlatiladi
        # — chunki u GetDialogs orqali bu chatni allaqachon biladi.
        _use_user_client_for_send = False
        if target_chat != status_msg.chat_id and int(target_chat) not in _resolved_peers:
            try:
                from handlers.video_handler import get_pyrogram_client
                _bot_pyro = await get_pyrogram_client()
                await _bot_pyro.get_chat(int(target_chat))
                _resolved_peers.add(int(target_chat))
            except Exception as _pe:
                logger.warning(
                    "bot_session peer cache xato (kutilgan holat -100 kanallar "
                    "uchun): %s — userbot orqali yuboriladi.", _pe,
                )
                _use_user_client_for_send = True

        _send_kwargs = {}
        if _use_user_client_for_send:
            _user_pyro = await get_user_client()
            if _user_pyro is not None:
                _send_kwargs["pyro_client_override"] = _user_pyro

        await send_file(
            message=status_msg,
            file_path=tmp_path,
            filename=filename,
            caption=caption,
            context=_FakeCtx(),
            force_document=not is_video_file,
            target_chat_id=target_chat if target_chat != status_msg.chat_id else None,
            message_thread_id=dest_thread_id,
            **_send_kwargs,
        )

        # Yuborish muvaffaqiyatli tugadi — navbatni darhol keyingi workerga
        # uzatamiz (boshqa worker tmp_path tozalanishini kutib o'tirmasin).
        _release_gate()

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
                # Exception matni escape qilinmagan bo'lishi mumkin — parse_mode'siz
                # yuborib, ikkinchi Markdown xatosining oldini olamiz.
                await status_msg.edit_text(
                    f"⚠️ {_md_escape(filename)} yuborilmadi:\n{e}",
                    reply_markup=_refresh_kb(status_msg.message_id),
                )
                await asyncio.sleep(2)
            except Exception:
                pass
        return False
    finally:
        # Xavfsizlik to'ri: yuqorida hech qaysi yo'lda advance() chaqirilmagan
        # bo'lsa (masalan media yo'q, allaqachon saqlangan, FloodWait, yoki
        # boshqa xato send_file dan OLDIN sodir bo'lsa), navbat shu yerda
        # baribir bo'shatiladi — aks holda qolgan barcha workerlar abadiy
        # kutib qoladi.
        _release_gate()
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


async def _try_fast_copy(
    client: Client, from_chat, msg, dest_chat_id: int, dest_thread_id: int | None,
    caption_override: str | None, report=None,
) -> bool:
    """Ba'zi manba guruh/kanallarda kontent ulashish CHEKLANMAGAN (himoyalanmagan
    emas). Bunday holda Telegram serverlari orqali to'g'ridan-to'g'ri nusxalash
    mumkin (file_id asosida, copy_message/copy_media_group) — bizning serverimiz
    faylni umuman yuklab olmaydi va qayta yubormaydi, shuning uchun bir necha
    barobar tezroq va disk/trafik sarflamaydi. Manba "himoyalangan kontent"
    (forward taqiqlangan) bo'lsa Telegram xato qaytaradi — False qaytariladi va
    chaqiruvchi avvalgi (yuklab-qayta yuborish) usulga o'tadi."""
    try:
        kwargs = {}
        if dest_thread_id:
            # MUHIM: shu Pyrogram versiyasida (2.0.106) forum topic'ga
            # reply_to_message_id orqali yo'naltiriladi (sender.py'dagi
            # xuddi shu izohga qarang).
            kwargs["reply_to_message_id"] = dest_thread_id
        if caption_override is not None:
            kwargs["caption"] = caption_override
        if getattr(msg, "media_group_id", None):
            await client.copy_media_group(
                chat_id=dest_chat_id, from_chat_id=from_chat, message_id=msg.id, **kwargs,
            )
        else:
            await client.copy_message(
                chat_id=dest_chat_id, from_chat_id=from_chat, message_id=msg.id, **kwargs,
            )
        if report:
            report("⚡ to'g'ridan nusxalandi (disksiz)")
        return True
    except Exception as e:
        logger.info(
            "Tez nusxalash muvaffaqiyatsiz (%s uchun) — sekin (yuklab-qayta "
            "yuborish) usulga o'tiladi: %s", msg.id, e,
        )
        return False


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
    chunk_delay: float = 0.0,
    send_gate=None,
    caption_override: str | None = None,
) -> bool:
    try:
        await _resolve_peer_safe(client, from_chat)
        msg = await client.get_messages(from_chat, msg_id)
        if not msg or msg.empty or not msg.media:
            # Bu msg_id uchun hech qachon send_file chaqirilmaydi — navbat
            # shu yerda bo'shatiladi, aks holda keyingilar abadiy kutadi.
            if send_gate is not None:
                send_gate.advance()
            return False

        # ⚡ Avval tez (disksiz) yo'lni sinaymiz. Guruhdagi tartibni saqlash
        # uchun bu yerda ham o'z navbatimizni kutamiz — muvaffaqiyatli
        # bo'lsa navbat shu yerda bo'shatiladi va pastdagi sekin yo'lga
        # umuman tushmaymiz.
        if send_gate is not None:
            if report:
                report("⏳ navbatda (nusxalash uchun)")
            await send_gate.wait_turn()
        if await _try_fast_copy(client, from_chat, msg, dest_chat_id, dest_thread_id, caption_override, report=report):
            if send_gate is not None:
                send_gate.advance()
            try:
                from utils.db import mark_saved
                source_chat_id = getattr(getattr(msg, "chat", None), "id", None)
                if source_chat_id is not None:
                    await mark_saved(source_chat_id, msg.id, dest_chat_id, dest_thread_id)
            except Exception:
                pass
            return True

        album = await _get_album_messages(client, from_chat, msg)
        ok_any = False
        for i, part in enumerate(album):
            if is_cancelled(user_id):
                # Bekor qilinganda ham navbat tiqilib qolmasligi kerak.
                if send_gate is not None:
                    send_gate.advance()
                break
            # Albom bir nechta qismdan iborat bo'lsa ham, ular bitta worker
            # ichida allaqachon ketma-ket yuboriladi (parallel emas) — shu
            # sababli faqat BIRINCHI qism navbatni kutadi/bo'shatadi. Qolgan
            # qismlar send_gate'siz, lekin baribir tartibli yuboriladi.
            # (Bu yerga faqat tez nusxalash muvaffaqiyatsiz bo'lganda
            # yetib kelamiz — navbat allaqachon shu turn uchun ochilgan,
            # shuning uchun wait_turn() darhol o'tadi, qayta bloklanmaydi.)
            part_gate = send_gate if i == 0 else None
            if await _download_and_send(
                client, part, status_msg, user_id,
                dest_chat_id, dest_thread_id, bot, silent=silent, report=report,
                chunk_delay=chunk_delay, send_gate=part_gate,
                caption_override=caption_override if i == 0 else None,
            ):
                ok_any = True
            await asyncio.sleep(0.8)
        return ok_any

    except FloodWait as e:
        _record_flood_event()
        if _retry >= 5:
            logger.error("msg %s FloodWait: retry limiti tugadi (%s s)", msg_id, e.value)
            return False
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
            dest_chat_id, dest_thread_id, bot, _retry + 1, silent=silent, report=report,
            chunk_delay=chunk_delay, caption_override=caption_override,
        )
    except OSError as e:
        if _retry < 3:
            await asyncio.sleep(2 * (_retry + 1))
            return await _download_and_send_one(
                client, from_chat, msg_id, status_msg, user_id,
                dest_chat_id, dest_thread_id, bot, _retry + 1, silent=silent, report=report,
                chunk_delay=chunk_delay, caption_override=caption_override,
            )
        logger.error("msg %s OSError: %s", msg_id, e)
        return False
    except Exception as e:
        logger.error("msg %s xato: %s", msg_id, e, exc_info=True)
        return False


class _TurnGate:
    """Tartibli yuborish uchun "navbat darvozasi".

    Download'lar (asyncio.Semaphore orqali) parallel ketadi — qaysi fayl
    avval tugasa, o'sha avval tayyor bo'ladi. Lekin guruhga JO'NATISH
    (send_file) eski guruh mavzusidagi ketma-ketlikni saqlashi kerak.

    Har bir worker o'zining "turn" raqamiga ega (ids ro'yxatidagi indeksi).
    Har bir turn uchun alohida asyncio.Event yaratiladi: wait_turn(turn) shu
    turn'ning Event'i set bo'lguncha kutadi (oldingi barcha turnlar
    advance() chaqirganda navbat bilan set bo'ladi). Bu yondashuv har bir
    turn mustaqil signalga ega bo'lgani uchun "barcha kutuvchilarni
    uyg'otib, keyin qaytadan yopish" kabi noziklik bilan bog'liq xatolardan
    holi — eng oddiy va ishonchli variant.
    """

    def __init__(self, total: int):
        self._total = total
        self._events = [asyncio.Event() for _ in range(total)]
        if total > 0:
            self._events[0].set()  # turn=0 darhol yuborishi mumkin

    async def wait_turn(self, turn: int) -> None:
        if 0 <= turn < self._total:
            await self._events[turn].wait()

    def advance(self, turn: int) -> None:
        """turn yuborilib bo'ldi (yoki o'tkazib yuborildi) — keyingi turnga yashil chiroq beradi."""
        nxt = turn + 1
        if 0 <= nxt < self._total:
            self._events[nxt].set()

    def release_all_from(self, turn: int) -> None:
        """Bekor qilish kabi holatlarda: turn'dan boshlab BARCHA qolgan
        eventlarni ochib yuboradi — aks holda turn'dan oldingi (hali
        navbatida turgan, lekin hali tugamagan) workerlar bu workerning
        alohida advance() chaqirishini abadiy kutib qolishi mumkin edi.
        Masalan: workerlar tasodifiy tartibda tugaydi, va agar 5-turn
        worker'i bekor qilingani uchun faqat self._events[6] ni ochsa-yu,
        3 va 4-turnlar hali navbatda bo'lsa — ular hech qachon signalini
        olmaydi. Shu sababli bekor qilishda butun qolgan zanjir bo'shatiladi."""
        for i in range(max(turn, 0), self._total):
            self._events[i].set()

    def bind(self, turn: int) -> "_BoundTurnGate":
        """Berilgan turn raqamiga "bog'langan" kichik obyekt qaytaradi —
        shunda _download_and_send kabi pastki funksiyalar turn raqamini
        o'zlari hisoblab yurishi shart emas, faqat .wait_turn()/.advance()
        chaqirsa kifoya."""
        return _BoundTurnGate(self, turn)


class _BoundTurnGate:
    """_TurnGate'ning bitta turn'ga bog'langan ingichka wrapper'i."""

    def __init__(self, gate: "_TurnGate", turn: int):
        self._gate = gate
        self._turn = turn

    async def wait_turn(self) -> None:
        await self._gate.wait_turn(self._turn)

    def advance(self) -> None:
        self._gate.advance(self._turn)


async def _send_batch(
    client: Client, from_chat, ids: list, status_msg,
    user_id: int, dest_chat_id: int, dest_thread_id: int | None, bot,
    bot_data: dict | None = None,
    batch_concurrency: int = _DEFAULT_BATCH_CONCURRENCY,
    chunk_delay: float = 0.0,
    caption_map: dict[int, str] | None = None,
):
    total = len(ids)
    if total == 0:
        await status_msg.edit_text("❌ Yuboriladigan media yo'q.")
        return

    sem = asyncio.Semaphore(batch_concurrency)
    # Eski guruh mavzusidagi tartibni saqlash uchun: ids ro'yxatidagi
    # ketma-ketlik (ya'ni asl xabar tartibi) "turn" raqami sifatida
    # ishlatiladi. Worker 0 darhol yuboradi, Worker 1 Worker 0 tugaguncha
    # kutadi, va h.k. — kichik fayl tezroq yuklansa ham otilib ketmaydi.
    send_gate = _TurnGate(total)
    done = 0
    sent = 0
    failed_ids: list[int] = []
    lock = asyncio.Lock()
    cancelled_flag = False
    slots: dict[int, str] = {}  # slot raqami → o'sha slotdagi joriy holat matni

    async def _worker(mid: int, slot: int, turn: int):
        nonlocal done, sent, cancelled_flag

        def _report(text: str):
            slots[slot] = text

        async with sem:
            if is_cancelled(user_id):
                cancelled_flag = True
                # Bekor qilinganda barcha qolgan navbatlarni bo'shatamiz —
                # global bekor qilish bo'lgani uchun baribir hech kim
                # send_file'ga yetib bormaydi, lekin deadlock bo'lmasligi
                # uchun zanjir to'liq ochilishi kerak.
                send_gate.release_all_from(turn)
                return
            slots[slot] = "⏳ navbatda..."
            ok = await _download_and_send_one(
                client, from_chat, mid, status_msg, user_id,
                dest_chat_id, dest_thread_id, bot, silent=True, report=_report,
                chunk_delay=chunk_delay, send_gate=send_gate.bind(turn),
                caption_override=(caption_map or {}).get(mid),
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
            pct = int(done / total * 100)
            bar = _progress_bar(pct)
            lines = [
                f"📦 *{done}/{total}* fayl yuklandi",
                f"`{bar}` `{pct}%`",
            ]
            for s in sorted(slots.keys()):
                lines.append(f"  • `{slots[s]}`")
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
            _worker(mid, i % batch_concurrency, i) for i, mid in enumerate(ids)
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
            "caption_map": caption_map,
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
                    f"📁 Arxiv: topic *{_md_escape(label[:60])}* ga saqlanmoqda...",
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

        # Foydalanuvchi sozlamalarini yuklab olamiz
        sr_cfg = await _load_sr_settings(uid)
        chunk_delay = _smart_chunk_delay()

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
                chunk_delay=chunk_delay,
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
            f"📁 *{_md_escape(_resolve_filename(msg))}*\n\n📌 Qaysi topicga saqlaymiz?",
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
            # Foydalanuvchi sozlamalarini yuklab olamiz
            sr_cfg = await _load_sr_settings(uid)
            batch_conc = await _smart_batch_concurrency(sr_cfg["parallel"])
            chunk_delay = _smart_chunk_delay()
            if count <= 30:
                await status.edit_text(
                    f"📦 {count} ta media yuklanmoqda...",
                    reply_markup=_refresh_kb(status.message_id),
                )
                await _send_batch(
                    client, chat_id, media_ids, status, uid, dest_chat, dest_thread, context.bot,
                    context.bot_data, batch_concurrency=batch_conc, chunk_delay=chunk_delay,
                )
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


_EPISODE_PATTERNS = [
    re.compile(r"(\d{1,4})\s*[-–—]\s*qism", re.IGNORECASE),
    re.compile(r"(\d{1,4})\s*[-–—]\s*epizod", re.IGNORECASE),
    re.compile(r"(\d{1,4})\s*[-–—]\s*seriya", re.IGNORECASE),
    re.compile(r"(\d{1,4})\s*[-–—]\s*son\b", re.IGNORECASE),
    re.compile(r"(\d{1,4})\s*[-–—]\s*final", re.IGNORECASE),  # "8-Final" — oxirgi qism, raqami baribir 8
    re.compile(r"\bS\d{1,3}E(\d{1,4})\b", re.IGNORECASE),      # S01E08 uslubi (ustuvor — E12 emas, aniq S..E.. juftlik)
    re.compile(r"(?<![A-Za-z0-9])E(\d{1,4})\b", re.IGNORECASE),  # yolg'iz "E08" (S bo'lmasa ham)
    re.compile(r"[\[\(]\s*(\d{1,4})\s*[\]\)]"),                # "[08]" kabi qavs ichidagi raqam
]

_SEASON_PATTERNS = [
    re.compile(r"(\d{1,3})\s*[-–—]\s*fasl", re.IGNORECASE),
    re.compile(r"(\d{1,3})\s*[-–—]\s*mavsum", re.IGNORECASE),
    re.compile(r"\bS(\d{1,3})E\d{1,4}\b", re.IGNORECASE),      # S01E08 uslubi
]


def _detect_episode(caption: str) -> int | None:
    if not caption:
        return None
    for pat in _EPISODE_PATTERNS:
        m = pat.search(caption)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _detect_season(caption: str) -> int | None:
    if not caption:
        return None
    for pat in _SEASON_PATTERNS:
        m = pat.search(caption)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


async def save_series_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/fasl_import <havola> [fasl_raqami]

    Boshqa (ochiq yoki yopiq) guruhning bitta topic'idagi barcha
    qismlarni skanlab, JORIY topic'ga (buyruq qaysi topicda yozilgan
    bo'lsa o'shanga) "<fasl>-<qism>" (masalan 1-1, 1-2, 1-3...)
    sarlavhasi bilan joylaydi.

    Har bir faylning ASL tavsifidan (caption) fasl/qism raqami avtomatik
    aniqlanishga harakat qilinadi ("8-Final", "3-fasl 5-qism", "S01E08"
    kabi formatlar tushuniladi). Agar caption'dan hech narsa topilmasa —
    berilgan <fasl_raqami> (yoki topilmasa 1) va ketma-ket qism raqami
    ishlatiladi.

    Foydalanish (ElevenStudio guruhidagi kerakli topic ichida):
      /fasl_import https://t.me/Minxotv_Arxiv/9352/9360
      /fasl_import https://t.me/Minxotv_Arxiv/9352/9360 1   (fasl aniq bo'lmasa shu ishlatiladi)
    """
    args = update.message.text.split(maxsplit=2)
    if len(args) < 2:
        await update.message.reply_text(
            "❗ Foydalanish:\n"
            "`/fasl_import <havola> [fasl_raqami]`\n\n"
            "Misol:\n`/fasl_import https://t.me/Minxotv_Arxiv/9352/9360`\n\n"
            "Fasl/qism raqami tavsifdan (caption) avtomatik aniqlanadi. "
            "Aniqlanmasa, siz bergan fasl_raqami (yoki 1) va ketma-ket "
            "qism raqami ishlatiladi.\n\n"
            "Bu buyruqni albatta filmingiz/serialingiz *o'z topic'i ichida* yozing.",
            parse_mode="Markdown",
        )
        return

    link_text = args[1]
    default_season = 1
    if len(args) >= 3 and args[2].strip().isdigit():
        default_season = int(args[2].strip())

    dest_thread = getattr(update.message, "message_thread_id", None)
    if not dest_thread:
        await update.message.reply_text(
            "⚠️ Bu buyruqni umumiy chatda emas, kerakli *topic ichida* yozing — "
            "aks holda qaysi topicga joylashni bilmayman.",
            parse_mode="Markdown",
        )
        return
    dest_chat = update.effective_chat.id

    client = await get_user_client()
    if client is None:
        await update.message.reply_text("⚠️ Save Restricted sozlanmagan.")
        return

    src_chat, src_thread, from_msg_id = parse_topic_link(link_text)
    if not src_chat or not src_thread:
        await update.message.reply_text("❌ Havola xato. `t.me/c/...` yoki `t.me/username/...` formatida bo'lishi kerak.", parse_mode="Markdown")
        return

    status = await update.message.reply_text("🔍 Manba topic skanlanmoqda...")
    user_id = update.effective_user.id
    register_task(user_id, label=f"Fasl import: {default_season}-fasl")

    try:
        if not await _resolve_peer_safe(client, src_chat):
            await status.edit_text("❌ Manba guruh/kanalga ulanib bo'lmadi — userbot u yerga a'zo emas yoki username xato.")
            clear_task(user_id)
            return

        media_ids = []
        captions: dict[int, str] = {}
        root_error = None
        try:
            root = await client.get_messages(src_chat, src_thread)
            if root and not root.empty and root.media:
                media_ids.append(root.id)
                captions[root.id] = root.caption or ""
        except Exception as e:
            root_error = repr(e)

        scanned = 0
        dr_error = None
        type_samples = []
        stub_ids = []
        last_update = time.monotonic()
        try:
            async for m in client.get_discussion_replies(src_chat, src_thread):
                scanned += 1
                if m.media:
                    media_ids.append(m.id)
                    captions[m.id] = m.caption or ""
                else:
                    # get_discussion_replies topic uchun ko'pincha
                    # QISQARTIRILGAN (stub) xabar qaytaradi — faqat ID/sana
                    # bor, media/caption yo'q. Shu ID'larni keyinroq
                    # get_messages bilan TO'LIQ qayta so'raymiz.
                    stub_ids.append(m.id)
                    if len(type_samples) < 5:
                        kinds = []
                        if getattr(m, "video", None): kinds.append("video")
                        if getattr(m, "document", None): kinds.append("document")
                        if getattr(m, "photo", None): kinds.append("photo")
                        if getattr(m, "web_page", None): kinds.append("web_page")
                        if getattr(m, "text", None): kinds.append("text")
                        if getattr(m, "reply_markup", None): kinds.append("has_buttons")
                        if getattr(m, "service", None): kinds.append("service")
                        type_samples.append(f"#{m.id}:{','.join(kinds) or 'none'}")
                now = time.monotonic()
                if now - last_update >= 2.0:
                    last_update = now
                    try:
                        await status.edit_text(f"🔍 {scanned} xabar tekshirildi, {len(media_ids)} ta media topildi...")
                    except Exception:
                        pass
        except Exception as e:
            dr_error = repr(e)

        # Stub xabarlarni to'liq mazmuni bilan qayta so'raymiz (100 tadan
        # bo'lib, Telegram limitiga mos).
        refetch_error = None
        if stub_ids:
            try:
                for i in range(0, len(stub_ids), 100):
                    batch = stub_ids[i:i + 100]
                    full_msgs = await client.get_messages(src_chat, batch)
                    if not isinstance(full_msgs, list):
                        full_msgs = [full_msgs]
                    for fm in full_msgs:
                        if fm and not fm.empty and fm.media:
                            media_ids.append(fm.id)
                            captions[fm.id] = fm.caption or ""
            except Exception as e:
                refetch_error = repr(e)

        media_ids = sorted(set(media_ids))
        if from_msg_id:
            media_ids = [mid for mid in media_ids if mid >= from_msg_id]

        # Fallback: agar get_discussion_replies hech narsa bermasa (bu odatda
        # manba ODDIY KANAL bo'lib, forum-topic yoki izohlar (comments)
        # yoqilmagan bo'lsa yuz beradi). Bunday kanallarda bitta serialning
        # qismlari ID bo'yicha KETMA-KET emas — boshqa filmlar postlari
        # orasida sochilib yotadi. Shuning uchun ID oralig'ini emas,
        # muqova-postning sarlavhasini (caption) olib, o'sha nom bo'yicha
        # BUTUN KANALNI qidiramiz (search_messages) va topilgan barcha
        # mediali xabarlarni yig'amiz.
        if not media_ids:
            title_query = None
            probe_error = None
            try:
                # src_thread'ning o'zida caption bo'lmasligi mumkin (masalan
                # albomning caption'siz a'zosi). Shu sabab atrofdagi bir
                # nechta xabarni tekshirib, birinchi topilgan captiondan
                # sarlavhani chiqarib olamiz.
                probe_ids = list(range(src_thread, src_thread + 20))
                if from_msg_id:
                    probe_ids += list(range(from_msg_id, from_msg_id + 5))
                probe_msgs = await client.get_messages(src_chat, probe_ids)
                if not isinstance(probe_msgs, list):
                    probe_msgs = [probe_msgs]
                for pm in probe_msgs:
                    if not pm or pm.empty:
                        continue
                    cap = pm.caption or pm.text or ""
                    if not cap.strip():
                        continue
                    m_title = re.search(r'[“"]([^"”]{2,60})[”"]', cap)
                    if m_title:
                        title_query = m_title.group(1).strip()
                        break
                    if title_query is None:
                        title_query = cap.strip().splitlines()[0][:60]
            except Exception as e:
                probe_error = repr(e)

            search_error = None
            scanned_fb = 0
            if title_query:
                try:
                    async for m in client.search_messages(src_chat, query=title_query):
                        scanned_fb += 1
                        if m and not m.empty and m.media:
                            media_ids.append(m.id)
                            captions[m.id] = m.caption or ""
                        if scanned_fb % 20 == 0:
                            try:
                                await status.edit_text(
                                    f"🔍 \"{title_query}\" bo'yicha qidirilmoqda... {scanned_fb} natija, {len(media_ids)} ta media"
                                )
                            except Exception:
                                pass
                        if scanned_fb >= 500:
                            break
                except Exception as e:
                    search_error = repr(e)
                media_ids = sorted(set(media_ids))

        if not media_ids:
            diag_lines = ["❌ Manba topicda media topilmadi.", "", "🩺 Diagnostika:"]
            diag_lines.append(f"• get_messages(root): {'xato: ' + root_error if root_error else 'OK'}")
            diag_lines.append(f"• get_discussion_replies: {scanned} ta xabar ko'rildi" + (f", xato: {dr_error}" if dr_error else ""))
            if refetch_error:
                diag_lines.append(f"• qayta so'rash xatosi: {refetch_error}")
            if type_samples:
                diag_lines.append("• namunalar: " + " | ".join(type_samples))
            diag_lines.append(f"• qidiruv sarlavhasi: {title_query or 'topilmadi'}" + (f" (probe xato: {probe_error})" if probe_error else ""))
            if title_query:
                diag_lines.append(f"• search_messages: {scanned_fb} ta natija" + (f", xato: {search_error}" if search_error else ""))
            await status.edit_text("\n".join(diag_lines))
            clear_task(user_id)
            return

        # Har bir fayl uchun avval ASL caption'dan fasl/qism raqamini
        # aniqlashga harakat qilamiz. Topilmasa — berilgan/standart fasl
        # va shu fasl ichidagi navbatdagi bo'sh qism raqami ishlatiladi
        # (band qilingan raqamlar bilan to'qnashmasligi uchun).
        caption_map: dict[int, str] = {}
        used_pairs: set[tuple[int, int]] = set()
        auto_detected = 0

        # Birinchi o'tishda — caption'dan aniq topilgan (fasl, qism)
        # juftliklarini oldindan "band" qilib qo'yamiz, shunda ikkinchi
        # o'tishdagi avtomatik hisoblagich ularga to'qnashmaydi.
        detected: dict[int, tuple[int, int]] = {}
        for mid in media_ids:
            cap = captions.get(mid, "")
            ep = _detect_episode(cap)
            if ep is not None:
                ssn = _detect_season(cap) or default_season
                detected[mid] = (ssn, ep)
                used_pairs.add((ssn, ep))

        next_ep_for_season: dict[int, int] = {}
        for mid in media_ids:
            if mid in detected:
                ssn, ep = detected[mid]
                auto_detected += 1
            else:
                ssn = _detect_season(captions.get(mid, "")) or default_season
                ep = next_ep_for_season.get(ssn, 1)
                while (ssn, ep) in used_pairs:
                    ep += 1
                used_pairs.add((ssn, ep))
                next_ep_for_season[ssn] = ep + 1
            caption_map[mid] = f"{ssn}-{ep}"

        count = len(media_ids)

        sr_cfg = await _load_sr_settings(user_id)
        batch_conc = await _smart_batch_concurrency(sr_cfg["parallel"])
        chunk_delay = _smart_chunk_delay()

        preview = ", ".join(list(caption_map.values())[:5]) + ("..." if count > 5 else "")
        detect_note = f"\n🔎 {auto_detected}/{count} tasida fasl/qism tavsifdan avtomatik aniqlandi." if auto_detected else "\n⚠️ Tavsiflardan aniqlanmadi — ketma-ket raqamlanadi."
        await status.edit_text(
            f"📦 {count} ta qism topildi: {preview}{detect_note}",
            parse_mode="Markdown",
            reply_markup=_refresh_kb(status.message_id),
        )
        await _send_batch(
            client, src_chat, media_ids, status, user_id, dest_chat, dest_thread, context.bot,
            context.bot_data, batch_concurrency=batch_conc, chunk_delay=chunk_delay,
            caption_map=caption_map,
        )
        clear_task(user_id)
    except Exception as e:
        logger.error("save_series_handler: %s", e, exc_info=True)
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

    # Foydalanuvchi sozlamalarini yuklab olamiz
    sr_cfg = await _load_sr_settings(user_id)
    chunk_delay = _smart_chunk_delay()

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
        dest_chat, dest_thread, bot, chunk_delay=chunk_delay,
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

    # Foydalanuvchi sozlamalarini yuklab olamiz
    sr_cfg = await _load_sr_settings(user_id)
    batch_conc = await _smart_batch_concurrency(sr_cfg["parallel"])
    chunk_delay = _smart_chunk_delay()

    if count <= 30:
        try:
            await status_ref.edit_text(
                f"📦 {count} ta media yuklanmoqda...",
                reply_markup=_refresh_kb(status_ref.message_id),
            )
        except Exception:
            pass
        await _send_batch(
            client, from_chat, ids, status_ref, user_id, dest_chat, dest_thread, bot,
            bot_data, batch_concurrency=batch_conc, chunk_delay=chunk_delay,
        )
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
        await status_ref.edit_text(f"✅ Topic yaratildi: *{_md_escape(name[:60])}*", parse_mode="Markdown")
    except Exception:
        pass

    if pending["kind"] == "link":
        await _continue_link_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, status_ref, context.bot_data)
    elif pending["kind"] == "audio_link":
        await _continue_audio_link_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, status_ref, context.bot_data)
    elif pending["kind"] == "audio_topic":
        await _continue_audio_topic_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, status_ref, context.bot_data)
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
        sr_cfg = await _load_sr_settings(uid)
        await _send_batch(
            client, data["chat_id"], data["ids"], query.message,
            uid, data["dest_chat"], data["dest_thread"], context.bot, context.bot_data,
            batch_concurrency=await _smart_batch_concurrency(sr_cfg["parallel"]),
            chunk_delay=_smart_chunk_delay(),
            caption_map=data.get("caption_map"),
        )
        clear_task(uid)
        return

    if query.data.startswith("sr_r2big_no|"):
        await query.answer("❌ Bekor qilindi")
        key = query.data.split("|", 1)[1]
        _big_file_pending.pop(key, None)
        try:
            await query.edit_message_text("⏭ O'tkazib yuborildi.")
        except Exception:
            pass
        return

    if query.data.startswith("sr_r2big|"):
        await query.answer()
        key = query.data.split("|", 1)[1]
        data = _big_file_pending.pop(key, None)
        if not data:
            await query.edit_message_text("❌ Ma'lumot topilmadi yoki eskirgan (1 soatdan eski).")
            return

        client = await get_user_client()
        if client is None:
            await query.edit_message_text("⚠️ Userbot ulanmagan.")
            return

        filename = data["filename"]
        file_size = data["file_size"]
        from utils.sender import _fmt_size
        try:
            await query.edit_message_text(
                f"⬇️ *{_md_escape(filename)}* (`{_fmt_size(file_size)}`) yuklab olinmoqda...",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        tmp_path = None
        prepared_path = None
        try:
            src_msg = await client.get_messages(data["chat_id"], data["msg_id"])
            media_obj = _media_obj(src_msg)
            if not media_obj:
                await query.edit_message_text("❌ Fayl topilmadi (o'chirilgan bo'lishi mumkin).")
                return

            ext = os.path.splitext(filename)[1].lstrip(".") or "bin"
            tmp_path = os.path.join(TEMP_DIR, f"sr_big_{data['msg_id']}_{data['user_id']}_{int(time.time()*1000)}.{ext}")
            await client.download_media(media_obj, file_name=tmp_path)

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                await query.edit_message_text("❌ Yuklab bo'lmadi, qayta urinib ko'ring.")
                return

            # Avvalgidek: mp4 konteyner + h264 + yuv420p + aac bo'lsa faqat
            # faststart (moov atom boshiga), aks holda to'liq qayta kodlanadi.
            video_ext = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".wmv"}
            if os.path.splitext(filename)[1].lower() in video_ext:
                try:
                    await query.edit_message_text(
                        f"🎬 *{_md_escape(filename)}*\nmp4/faststart tekshirilmoqda...",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
                from utils.ffmpeg_utils import prepare_for_telegram, _run_in_executor
                prepared_path, changed = await _run_in_executor(prepare_for_telegram, tmp_path)
                if changed and prepared_path != tmp_path:
                    filename = os.path.splitext(filename)[0] + ".mp4"

            upload_path = prepared_path or tmp_path
            upload_size = os.path.getsize(upload_path)

            from utils.sender import _upload_to_r2
            from utils.r2_manager import user_upload_key
            from config import R2_USER_PREFIX
            r2_key = user_upload_key(data["user_id"], filename, R2_USER_PREFIX)
            await _upload_to_r2(
                query.message, upload_path, filename, upload_size,
                user_id=data["user_id"], r2_object_key=r2_key,
            )
        except Exception as e:
            logger.error("sr_r2big xato: %s", e, exc_info=True)
            try:
                await query.edit_message_text(f"❌ Xato:\n{e}")
            except Exception:
                pass
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
            if prepared_path and prepared_path != tmp_path and os.path.exists(prepared_path):
                os.remove(prepared_path)
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
        elif pending["kind"] == "audio_link":
            await _continue_audio_link_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, query.message, context.bot_data)
        elif pending["kind"] == "audio_topic":
            await _continue_audio_topic_save(key, ARCHIVE_GROUP_ID, thread_id, context.bot, query.message, context.bot_data)
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
    sr_cfg = await _load_sr_settings(uid)
    await _send_batch(
        client, data["chat_id"], data["ids"], query.message,
        uid, data["dest_chat"], data["dest_thread"], context.bot, context.bot_data,
        batch_concurrency=await _smart_batch_concurrency(sr_cfg["parallel"]),
        chunk_delay=_smart_chunk_delay(),
    )
    clear_task(uid)


# ══════════════════════════════════════════════════════════════════════════
# Audio strim ajratuvchi: /a (bitta xabar) va /savea (butun topic)
#
# Vazifasi bir xil: restricted kanal/guruhdagi videoni TO'LIQ yuborish
# o'rniga, undagi audio strimlarni (ko'pincha bir nechta til/dublyaj
# bo'lishi mumkin) ffprobe orqali aniqlab, har birini ffmpeg bilan
# (-map 0:a:N -c copy, qayta kodlashsiz — tez) alohida audio faylga
# ajratib, o'shalarni yuboradi.
#
#   /a <link>      — link qanday bo'lishidan qat'i nazar (bitta yoki ikkita
#                     raqamli), ENG OXIRGI (eng konkret) xabarni oladi va
#                     FAQAT o'sha bitta faylning audio strimlarini yuboradi.
#   /savea <link>  — /save bilan bir xil: topic ichidagi BARCHA mediani
#                     skanlaydi, lekin har birini to'liq yubormasdan,
#                     audio strimlarini ajratib yuboradi.
#
# Ikkalasi ham xuddi /save kabi ARCHIVE_GROUP_ID/topic tanlash oqimidan
# foydalanadi (agar arxiv guruh sozlanmagan bo'lsa — joriy chatga).
# ══════════════════════════════════════════════════════════════════════════

def _audio_stream_ext(codec: str) -> str:
    """streams.py dagi _audio_ext bilan bir xil mantiq — ffmpeg orqali
    qayta kodlashsiz (-c copy) ajratib bo'ladigan formatlarga mos kengaytma."""
    return {
        "aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "ogg",
        "flac": "flac", "pcm_s16le": "wav", "ac3": "ac3", "eac3": "eac3",
        "dts": "dts", "truehd": "thd",
    }.get(codec, "mka")


def _probe_audio_streams(file_path: str) -> list[dict]:
    """Berilgan faylning faqat audio strimlarini ffprobe orqali qaytaradi.
    Sinxron (blocking) — chaqiruvchi run_in_executor orqali ishlatishi kerak."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index,codec_type,codec_name,channels,bit_rate",
                "-show_entries", "stream_tags=language,title",
                "-of", "json", file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception as e:
        logger.warning("_probe_audio_streams xato: %s", e)
        return []


async def _extract_audio_streams(file_path: str) -> list[dict]:
    """_probe_audio_streams ni non-blocking qilib chaqiradi."""
    return await asyncio.get_running_loop().run_in_executor(None, _probe_audio_streams, file_path)


def _extract_one_audio_stream(src_path: str, stream_index: int, out_path: str) -> tuple[bool, str]:
    """Bitta audio strimni ffmpeg bilan -c copy (qayta kodlashsiz) ajratadi.
    Sinxron — run_in_executor orqali chaqiriladi. (ok, stderr_tail) qaytaradi."""
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-map", f"0:{stream_index}",
        "-vn", "-c", "copy",
        out_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return False, result.stderr[-800:]
        return True, ""
    except Exception as e:
        return False, str(e)


async def _download_to_temp(
    client: Client,
    from_chat,
    msg_id: int,
    status_msg,
    user_id: int,
    report=None,
) -> tuple[str | None, str | None, object | None]:
    """Faylni vaqtinchalik diskka yuklaydi (SEND QILMASDAN).

    _download_and_send'dagi yuklash bosqichi bilan bir xil mantiq (progress,
    "to'liq yuklanmadimi" tekshiruvi), lekin Telegramga yuborish qismi yo'q —
    chunki bu funksiya audio-ajratish oqimi uchun ishlatiladi: avval butun
    faylni diskka tushiramiz, keyin undan audio strimlarni ajratib alohida
    yuboramiz, asl (katta) faylning o'zi hech qachon yuborilmaydi.

    Qaytaradi: (tmp_path, filename, msg) — xato bo'lsa (None, None, None).
    Chaqiruvchi tmp_path faylini ishlatib bo'lgach albatta o'chirishi kerak.
    """
    try:
        await _resolve_peer_safe(client, from_chat)
        msg = await client.get_messages(from_chat, msg_id)
        if not msg or msg.empty or not msg.media:
            return None, None, None

        media_obj = _media_obj(msg)
        # _media_obj video/document/audio/voice/video_note/photo'dan birortasi
        # topilmasa, oxirgi holatda msg'ning o'zini qaytaradi (eski kodda bu
        # "umumiy holat" sifatida ishlatilgan edi). Lekin bu yerda — audio
        # ajratish oqimida — agar tanish media turi topilmasa, demak bu
        # webpage preview, poll, location kabi yuklab bo'lmaydigan narsa.
        # media_obj ni msg bilan solishtirib, bunday holatni rad etamiz —
        # aks holda Pyrogram "This message doesn't contain any downloadable
        # media" xatosini beradi.
        if not media_obj or media_obj is msg:
            return None, None, None

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
            if report:
                report(f"⬇️ {short_name} {pct}%")
            cur_mb = current / 1024 / 1024
            txt = f"⬇️ *Yuklanmoqda (audio uchun)...*\n\n`{pct}%`\n`{cur_mb:.1f}` / `{total_mb:.1f}` MB"
            if pct - last_pct[0] < 10:
                return
            last_pct[0] = pct
            try:
                await status_msg.edit_text(txt, parse_mode="Markdown")
            except Exception:
                pass

        tmp_path = os.path.join(TEMP_DIR, f"sra_{msg.id}_{user_id}_{int(time.time()*1000)}.{ext}")
        await client.download_media(media_obj, file_name=tmp_path, progress=_dl_progress)

        if is_cancelled(user_id):
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return None, None, None

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            return None, None, None

        downloaded_size = os.path.getsize(tmp_path)
        if file_size and downloaded_size < file_size * 0.95:
            logger.error(
                "Audio uchun yuklash tugallanmagan: %s — kutilgan %s, olingan %s",
                filename, file_size, downloaded_size,
            )
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return None, None, None

        return tmp_path, filename, msg

    except FloodWait as e:
        _record_flood_event()
        if report:
            report(f"⏳ flood {e.value}s kutilmoqda")
        await asyncio.sleep(e.value)
        return await _download_to_temp(client, from_chat, msg_id, status_msg, user_id, report=report)
    except Exception as e:
        logger.error("_download_to_temp xato (msg %s): %s", msg_id, e, exc_info=True)
        return None, None, None


async def _extract_and_send_audio_for_one(
    client: Client,
    from_chat,
    msg_id: int,
    status_msg,
    user_id: int,
    dest_chat_id: int,
    dest_thread_id: int | None,
    bot,
    silent: bool = False,
    report=None,
    send_gate=None,
    mark_db: bool = True,
) -> bool:
    """Bitta xabarni yuklab, undagi BARCHA audio strimlarni alohida fayl
    sifatida ajratib, ketma-ket yuboradi. Kamida bitta audio muvaffaqiyatli
    yuborilsa True qaytaradi.

    send_gate berilgan bo'lsa (_BoundTurnGate), faqat haqiqiy yuborish
    bosqichida navbatini kutadi — eski guruh mavzusidagi tartib /savea
    ishlatilganda ham buzilmaydi (xuddi oddiy /save kabi)."""
    from utils.sender import send_file
    from utils.db import is_already_saved, mark_saved

    _gate_released = False

    def _release_gate():
        nonlocal _gate_released
        if send_gate is not None and not _gate_released:
            _gate_released = True
            send_gate.advance()

    tmp_path = None
    extracted_paths: list[str] = []
    try:
        if is_cancelled(user_id):
            return False

        source_chat_id = from_chat if isinstance(from_chat, int) else None
        if mark_db and source_chat_id is not None and await is_already_saved(source_chat_id, msg_id, dest_thread_id):
            if report:
                report("⏭ allaqachon saqlangan")
            return True

        tmp_path, filename, msg = await _download_to_temp(
            client, from_chat, msg_id, status_msg, user_id, report=report,
        )
        if not tmp_path:
            return False

        if report:
            report("🔎 audio strimlar qidirilmoqda...")
        audio_streams = await _extract_audio_streams(tmp_path)
        if not audio_streams:
            if report:
                report("⚠️ audio strim topilmadi")
            if not silent:
                try:
                    await status_msg.edit_text(f"⚠️ *{_md_escape(filename)}* ichida audio strim topilmadi.", parse_mode="Markdown")
                except Exception:
                    pass
            return False

        base = os.path.splitext(filename)[0]
        loop = asyncio.get_running_loop()
        sent_any = False

        # ── Navbat darvozasi: ajratilgan audio strimlar ham TARTIB BILAN
        # yuborilishi kerak (masalan 1-til, 2-til ketma-ketligi saqlansin).
        # send_gate faqat tashqi (xabarlar orasidagi) tartibni boshqaradi —
        # shu sababli bitta xabar ichidagi bir nechta audio strim faqat
        # BIRINCHISI uchun navbatni kutadi, qolganlari o'sha worker ichida
        # baribir ketma-ket (parallelsiz) yuboriladi.
        for i, s in enumerate(audio_streams):
            if is_cancelled(user_id):
                break
            idx = s.get("index")
            codec = s.get("codec_name", "")
            ext = _audio_stream_ext(codec)
            out_path = os.path.join(TEMP_DIR, f"sra_out_{msg_id}_{idx}_{int(time.time()*1000)}.{ext}")

            if report:
                report(f"🎚 audio #{idx} ajratilmoqda...")
            ok, err = await loop.run_in_executor(None, _extract_one_audio_stream, tmp_path, idx, out_path)
            if not ok:
                logger.error("Audio strim ajratish xato (msg %s, stream %s): %s", msg_id, idx, err)
                if os.path.exists(out_path):
                    os.remove(out_path)
                continue

            extracted_paths.append(out_path)

            tags = s.get("tags", {})
            lang = tags.get("language", "")
            title = tags.get("title", "")
            name_suffix = f"_{lang}" if lang else (f"_{idx}" if len(audio_streams) > 1 else "")
            out_name = f"{base}{name_suffix}.{ext}"
            caption_bits = [f"🎧 {base}"]
            if lang:
                caption_bits.append(f"[{lang}]")
            if title:
                caption_bits.append(title)
            caption = " ".join(caption_bits)

            part_gate = send_gate if i == 0 else None
            if part_gate is not None:
                if report:
                    report(f"⏳ audio #{idx} navbatda...")
                await part_gate.wait_turn()

            if report:
                report(f"📤 audio #{idx} yuborilmoqda...")
            try:
                target_chat = dest_chat_id or status_msg.chat_id

                # bot_session (Pyrogram) dest_chat peer'ini bilmasa
                # send_document "Peer id invalid" xatosi beradi — xuddi
                # _download_and_send'dagi kabi: ARCHIVE_GROUP_ID kabi
                # -100... guruhlar uchun bot hech qachon get_dialogs() orqali
                # o'rganolmaydi (botlarga taqiqlangan), shu sababli avval
                # bot_session orqali cache'lashga urinamiz, muvaffaqiyatsiz
                # bo'lsa userbot (user_session) clientini ishlatamiz.
                _use_user_client_for_send = False
                if target_chat != status_msg.chat_id and int(target_chat) not in _resolved_peers:
                    try:
                        from handlers.video_handler import get_pyrogram_client
                        _bot_pyro = await get_pyrogram_client()
                        await _bot_pyro.get_chat(int(target_chat))
                        _resolved_peers.add(int(target_chat))
                    except Exception as _pe:
                        logger.warning(
                            "bot_session peer cache xato (kutilgan holat -100 kanallar "
                            "uchun): %s — userbot orqali yuboriladi.", _pe,
                        )
                        _use_user_client_for_send = True

                _send_kwargs = {}
                if _use_user_client_for_send:
                    _user_pyro = await get_user_client()
                    if _user_pyro is not None:
                        _send_kwargs["pyro_client_override"] = _user_pyro

                await send_file(
                    message=status_msg,
                    file_path=out_path,
                    filename=out_name,
                    caption=caption,
                    context=None,
                    force_document=False,
                    target_chat_id=target_chat if target_chat != status_msg.chat_id else None,
                    message_thread_id=dest_thread_id,
                    **_send_kwargs,
                )
                sent_any = True
            except Exception as e:
                logger.error("Audio yuborish xato (msg %s, stream %s): %s", msg_id, idx, e, exc_info=True)
            finally:
                if part_gate is not None:
                    _release_gate()
                if out_path in extracted_paths:
                    extracted_paths.remove(out_path)
                if os.path.exists(out_path):
                    os.remove(out_path)

        if sent_any and mark_db and source_chat_id is not None:
            await mark_saved(source_chat_id, msg_id, dest_chat_id, dest_thread_id)

        return sent_any

    except Exception as e:
        logger.error("_extract_and_send_audio_for_one xato (msg %s): %s", msg_id, e, exc_info=True)
        return False
    finally:
        _release_gate()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        for p in extracted_paths:
            if os.path.exists(p):
                os.remove(p)


async def _send_audio_batch(
    client: Client, from_chat, ids: list, status_msg,
    user_id: int, dest_chat_id: int, dest_thread_id: int | None, bot,
    bot_data: dict | None = None,
    batch_concurrency: int = _DEFAULT_BATCH_CONCURRENCY,
):
    """_send_batch bilan bir xil arxitektura (parallel download + Event
    zanjiri orqali tartibli yuborish), lekin har bir worker to'liq faylni
    emas, undan ajratilgan audio strimlarni yuboradi. /savea shu yerga
    keladi."""
    total = len(ids)
    if total == 0:
        await status_msg.edit_text("❌ Audio ajratish uchun media yo'q.")
        return

    sem = asyncio.Semaphore(batch_concurrency)
    send_gate = _TurnGate(total)
    done = 0
    sent = 0
    failed_ids: list[int] = []
    lock = asyncio.Lock()
    cancelled_flag = False
    slots: dict[int, str] = {}

    async def _worker(mid: int, slot: int, turn: int):
        nonlocal done, sent, cancelled_flag

        def _report(text: str):
            slots[slot] = text

        async with sem:
            if is_cancelled(user_id):
                cancelled_flag = True
                send_gate.release_all_from(turn)
                return
            slots[slot] = "⏳ navbatda..."
            ok = await _extract_and_send_audio_for_one(
                client, from_chat, mid, status_msg, user_id,
                dest_chat_id, dest_thread_id, bot, silent=True, report=_report,
                send_gate=send_gate.bind(turn),
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
            lines = [f"🎧 *{done}/{total}* fayl ishlandi ({batch_concurrency} parallel, tartib bilan)", bar]
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
            _worker(mid, i % batch_concurrency, i) for i, mid in enumerate(ids)
        ])
    finally:
        reporter_task.cancel()
        try:
            await reporter_task
        except asyncio.CancelledError:
            pass

    if cancelled_flag or is_cancelled(user_id):
        await status_msg.edit_text(f"❌ Bekor qilindi. {sent}/{total} fayldan audio yuborildi.")
        return

    archive_note = f"\n☁️ Arxiv guruhi: `{dest_chat_id}`" if ARCHIVE_GROUP_ID else ""
    fail_note = f"\n⚠️ Audio topilmadi/xato: *{len(failed_ids)}* ta" if failed_ids else ""
    await status_msg.edit_text(
        f"✅ {sent}/{total} fayldan audio strimlar yuborildi.{archive_note}{fail_note}",
        parse_mode="Markdown",
    )


async def audio_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/a <link> — link ichidagi ENG OXIRGI (eng konkret) xabar ID'sini
    oladi va FAQAT o'sha bitta faylning audio strimlarini ajratib yuboradi.
    Topic bo'lishi shart emas — oddiy guruh/kanal xabariga ham ishlaydi."""
    args = update.message.text.split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text(
            "❗ Foydalanish:\n`/a https://t.me/c/1234567890/456`\n"
            "(bitta xabardagi faylning audio strimlarini ajratib yuboradi)",
            parse_mode="Markdown",
        )
        return

    client = await get_user_client()
    if client is None:
        await update.message.reply_text("⚠️ Save Restricted sozlanmagan.")
        return

    # parse_tme_link ikkala formatni ham qo'llab-quvvatlaydi:
    #   t.me/c/CHAT/MSG          → msg_id = MSG
    #   t.me/c/CHAT/A/B          → msg_id = B (eng oxirgi/konkret segment)
    chat_id, msg_id = parse_tme_link(args[1])
    if not chat_id or not msg_id:
        await update.message.reply_text("❌ Havola xato.", parse_mode="Markdown")
        return

    user_id = update.effective_user.id
    register_task(user_id, label="Audio extract (1 file)")
    status = await update.message.reply_text("⏳ Fayl tekshirilmoqda...")

    if not ARCHIVE_GROUP_ID:
        dest_chat = update.effective_chat.id
        dest_thread = getattr(update.message, "message_thread_id", None)
        ok = await _extract_and_send_audio_for_one(
            client, chat_id, msg_id, status, user_id, dest_chat, dest_thread, context.bot,
        )
        clear_task(user_id)
        if ok:
            try:
                await status.delete()
            except Exception:
                pass
        else:
            err = "Bekor qilindi." if is_cancelled(user_id) else "Audio strim topilmadi yoki yuklab bo'lmadi."
            await status.edit_text(f"❌ {err}")
        return

    # Arxiv guruh bor — qaysi topicga saqlashni so'raymiz (xuddi /save kabi)
    clear_task(user_id)
    key = _new_pending_key()
    context.bot_data[key] = {
        "kind": "audio_link",
        "chat_id": chat_id,
        "msg_id": msg_id,
        "user_id": user_id,
        "status_chat_id": status.chat_id,
        "status_message_id": status.message_id,
    }
    await status.edit_text(
        "🎧 *Audio ajratish*\n\n📌 Qaysi topicga yuboramiz?",
        parse_mode="Markdown",
        reply_markup=_dest_choice_kb(key),
    )


async def save_audio_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/savea <link> — /save bilan bir xil tarzda butun topicni skanlaydi,
    lekin har bir faylning to'liq nusxasi o'rniga audio strimlarini
    ajratib yuboradi."""
    args = update.message.text.split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text(
            "❗ Foydalanish:\n`/savea https://t.me/c/1234567890/456`\n"
            "(topicdagi barcha fayllarning audio strimlarini ajratib yuboradi)",
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

    status = await update.message.reply_text("🔍 Topik skanlanmoqda (audio uchun)...")
    user_id = update.effective_user.id

    try:
        await _resolve_peer_safe(client, chat_id)
        media_ids = []

        try:
            root = await client.get_messages(chat_id, thread_id)
            if root and not root.empty and root.media:
                media_ids.append(root.id)
        except Exception:
            pass

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

        if from_msg_id:
            media_ids = [mid for mid in media_ids if mid >= from_msg_id]

        if not media_ids:
            note = " (berilgan xabardan keyin)" if from_msg_id else ""
            await status.edit_text(f"❌ Topikda media topilmadi{note}.")
            return

        count = len(media_ids)
        range_note = f"\n📍 *{from_msg_id}* xabardan boshlab" if from_msg_id else ""
        label = f"AudioTopic_{thread_id}_{count}files"

        if not ARCHIVE_GROUP_ID:
            uid, dest_chat, dest_thread = await _prepare_destination(update, context, label)
            sr_cfg = await _load_sr_settings(uid)
            batch_conc = await _smart_batch_concurrency(sr_cfg["parallel"])
            await status.edit_text(
                f"🎧 {count} ta fayldan audio ajratilmoqda...",
                reply_markup=_refresh_kb(status.message_id),
            )
            await _send_audio_batch(
                client, chat_id, media_ids, status, uid, dest_chat, dest_thread, context.bot,
                context.bot_data, batch_concurrency=batch_conc,
            )
            clear_task(uid)
            return

        # Arxiv guruh bor — qaysi topicga saqlashni so'raymiz
        key = _new_pending_key()
        context.bot_data[key] = {
            "kind": "audio_topic",
            "from_chat": chat_id,
            "ids": media_ids,
            "label": label,
            "user_id": user_id,
            "status_chat_id": status.chat_id,
            "status_message_id": status.message_id,
        }
        await status.edit_text(
            f"🎧 *{count}* ta fayl topildi (audio ajratiladi).{range_note}\n\n📌 Qaysi topicga yuboramiz?",
            parse_mode="Markdown",
            reply_markup=_dest_choice_kb(key),
        )
    except Exception as e:
        logger.error("save_audio_topic_handler: %s", e, exc_info=True)
        clear_task(user_id)
        await status.edit_text(f"❌ Xato: {e}")


async def _continue_audio_link_save(key: str, dest_chat: int, dest_thread: int | None, bot, status_ref, bot_data: dict):
    """Topic tanlangandan keyin — /a (bitta fayl, audio) ni davom ettiradi."""
    pending = bot_data.pop(key, None)
    if not pending:
        await status_ref.edit_text("❌ Ma'lumot topilmadi yoki eskirgan.")
        return

    client = await get_user_client()
    if client is None:
        await status_ref.edit_text("⚠️ Userbot ulanmagan.")
        return

    user_id = pending["user_id"]
    register_task(user_id, label="Audio extract (1 file)")

    try:
        await status_ref.edit_text("⬇️ *Yuklanmoqda (audio uchun)...*", parse_mode="Markdown")
    except Exception:
        pass

    ok = await _extract_and_send_audio_for_one(
        client, pending["chat_id"], pending["msg_id"], status_ref, user_id,
        dest_chat, dest_thread, bot,
    )
    clear_task(user_id)

    if ok:
        try:
            await status_ref.delete()
        except Exception:
            pass
    else:
        err = "Bekor qilindi." if is_cancelled(user_id) else "Audio strim topilmadi yoki yuklab bo'lmadi."
        await status_ref.edit_text(f"❌ {err}")


async def _continue_audio_topic_save(key: str, dest_chat: int, dest_thread: int | None, bot, status_ref, bot_data: dict):
    """Topic tanlangandan keyin — /savea (butun topic, audio) ni davom ettiradi."""
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
    register_task(user_id, label=f"Audio extract: {pending.get('label', '')}")

    sr_cfg = await _load_sr_settings(user_id)
    batch_conc = await _smart_batch_concurrency(sr_cfg["parallel"])

    try:
        await status_ref.edit_text(
            f"🎧 {count} ta fayldan audio ajratilmoqda...",
            reply_markup=_refresh_kb(status_ref.message_id),
        )
    except Exception:
        pass
    await _send_audio_batch(
        client, from_chat, ids, status_ref, user_id, dest_chat, dest_thread, bot,
        bot_data, batch_concurrency=batch_conc,
    )
    clear_task(user_id)

