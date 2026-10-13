"""
kino_sender.py — Istalgan BOTdan xabar/videoni mirror qilish va arxiv guruhiga yuborish.

Foydalanish:
  /kino @BotNomi payload                       — bot javobini mirror qiladi
  /kino @BotNomi payload 2                     — to'g'ridan 2-tugmani bosadi
  /kino https://t.me/BotNomi?start=payload [2] — deep-link ham ishlaydi

Muhit o'zgaruvchilari (ixtiyoriy):
  KINO_TRY_COPY=0   — "tezkor nusxalash"ni o'chiradi (har doim yuklab-qayta yuklaydi)

Asosiy yaxshilanishlar (eski versiyaga nisbatan):
  * Userbotning O'Z yuborgan "/start ..." xabari endi "bot javobi" deb yig'ilmaydi
    (filters.incoming + outgoing tekshiruvi). Sekin javob beradigan botlar (UZPLAY va h.k.)
    endi to'g'ri kutiladi.
  * Bot avval matn ("504", "Yuklanmoqda...") yuborib, videoni keyin yuborsa — video uchun
    qo'shimcha kutish (VIDEO_GRACE) bor; placeholder -> edit holati ham ushlanadi.
  * Faqat BOT ekanligi tekshiriladi (userbot orqali odamlarga/kanallarga xabar ketmaydi)
    va faqat admin/ruxsat berilgan foydalanuvchilar ishlata oladi.
  * Callback ma'lumotlari endi stateless va indeksga asoslangan (split xatosi, 64 bayt
    limiti, binary callback_data buzilishi yo'q).
  * URL tugmalar (majburiy obuna havolalari) ham ko'rsatiladi; rasm (poster) ko'rsatiladi.
  * Barcha dinamik matn HTML-escape qilinadi; asl caption formatlashi saqlanadi.
  * Himoyalangan bo'lmasa — yuklamasdan tezkor nusxalash (copy_message), aks holda
    yuklab-yuborish; disk/limit tekshiruvi; ext tozalanadi; MKV -> MP4 remux.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from pyrogram.enums import ParseMode as PyroParseMode
from pyrogram.errors import FloodWait
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID, TEMP_DIR
from handlers.save_restricted import get_user_client, is_user_premium
from utils.auth import is_admin, is_allowed

logger = logging.getLogger(__name__)

# ── Sozlamalar ────────────────────────────────────────────────────────────────

WAIT_TIMEOUT = 90          # botdan birinchi mazmunli javobni kutish (soniya)
COLLECT_DELAY = 3.0        # oxirgi xabardan keyingi "jimlik" oynasi (soniya)
VIDEO_GRACE = 30.0         # faqat matn kelganda video/tugma uchun qo'shimcha kutish
BUTTON_WAIT_TIMEOUT = 90   # tugma bosilgach media kutish
CALLBACK_ANSWER_TIMEOUT = 15
STATUS_INTERVAL = 3.0      # status xabarini yangilash oralig'i (flood'dan saqlanish)
PROGRESS_INTERVAL = 3.0
MAX_MIRROR = 8             # bir so'rovda ko'rsatiladigan maksimal xabar soni
CAPTION_LIMIT = 1024
LIMIT_FREE = 2000 * 1024 * 1024
LIMIT_PREMIUM = 4000 * 1024 * 1024
KINO_TRY_COPY = os.environ.get("KINO_TRY_COPY", "1").strip().lower() not in ("0", "false", "no")

_HANDLER_GROUP = 50        # boshqa userbot handlerlari bilan to'qnashmaslik uchun alohida guruh

_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LINK_RE = re.compile(
    r"^(?:https?://)?(?:t|telegram)\.me/([A-Za-z][A-Za-z0-9_]{3,31})\?start=([A-Za-z0-9_-]{1,64})$",
    re.IGNORECASE,
)
_EXT_RE = re.compile(r"^[a-z0-9]{1,5}$")

_VIDEO_EXTS = {"mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "ts", "wmv"}
_NATIVE_EXTS = {"mp4", "m4v", "mov"}          # Telegram ichida to'g'ridan o'ynaydi
_REMUXABLE_EXTS = {"mkv", "webm", "avi", "ts", "flv"}
_MIME_EXT = {
    "video/mp4": "mp4", "video/x-matroska": "mkv", "video/webm": "webm",
    "video/quicktime": "mov", "video/x-msvideo": "avi", "video/mp2t": "ts",
    "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/flac": "flac",
}

_kino_lock = asyncio.Lock()
_job: dict = {}


class KinoError(Exception):
    """Foydalanuvchiga ko'rsatiladigan (oddiy matnli) xato."""


class _UsageError(Exception):
    """Buyruq argumentlari noto'g'ri."""


@dataclass(frozen=True)
class _Request:
    bot_username: str
    payload: str
    button_index: int | None


_h = html.escape


# ── Format yordamchilari ──────────────────────────────────────────────────────

def _progress_bar(percent: int, length: int = 14) -> str:
    percent = max(0, min(100, int(percent)))
    filled = int(length * percent / 100)
    return "▰" * filled + "▱" * (length - filled)


def _fmt_size(b: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def _fmt_dur(seconds: int) -> str:
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _rm(path: str | None) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            logger.debug("Faylni o'chirib bo'lmadi %s: %s", path, e)


class _Throttle:
    """Status xabarini juda tez-tez tahrirlamaslik uchun."""
    __slots__ = ("interval", "_last")

    def __init__(self, interval: float):
        self.interval = interval
        self._last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self._last < self.interval:
            return False
        self._last = now
        return True


# ── Telegram (PTB) yordamchilari ──────────────────────────────────────────────

async def _edit(status, text: str, markup=None) -> bool:
    """Status xabarini xavfsiz tahrirlaydi. Xato bo'lsa False qaytaradi (hech qachon raise qilmaydi)."""
    if status is None:
        return False
    try:
        await status.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return True
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return True
        logger.debug("edit_text BadRequest: %s", e)
    except RetryAfter:
        pass
    except TelegramError as e:
        logger.debug("edit_text xato: %s", e)
    return False


async def _fail(status, reply, text: str) -> None:
    """Xatoni ko'rsatadi: avval status'ni tahrirlaydi, bo'lmasa (masalan o'chirilgan) yangi xabar yuboradi."""
    if await _edit(status, text):
        return
    try:
        await reply(text)
    except TelegramError as e:
        logger.warning("Xatoni foydalanuvchiga yetkazib bo'lmadi: %s", e)


def _is_authorized(user_id: int) -> bool:
    return is_admin(user_id) or is_allowed(user_id)


def _describe_error(e: BaseException) -> str:
    if isinstance(e, KinoError):
        return str(e)
    if isinstance(e, FloodWait):
        wait = getattr(e, "value", None) or getattr(e, "x", "?")
        return f"Telegram cheklovi (FloodWait): {wait} soniyadan keyin urinib ko'ring."
    if isinstance(e, asyncio.TimeoutError):
        return "Kutish vaqti tugadi."
    return _clip(f"{type(e).__name__}: {e}", 300)


# ── Lock (bir vaqtda bitta so'rov) ────────────────────────────────────────────

async def _try_lock(reply, what: str) -> bool:
    if _kino_lock.locked():
        since = _job.get("since")
        elapsed = f" ({int(time.monotonic() - since)}s)" if since else ""
        try:
            await reply(f"⏳ Oldingi so'rov bajarilmoqda{elapsed}. Tugagach qayta urinib ko'ring.")
        except TelegramError:
            pass
        return False
    await _kino_lock.acquire()          # bo'sh lock'da kutmaydi — tekshiruv va olish orasida race yo'q
    _job.clear()
    _job.update(since=time.monotonic(), what=what)
    return True


def _unlock() -> None:
    _job.clear()
    if _kino_lock.locked():
        _kino_lock.release()


# ── Buyruq argumentlarini tahlil qilish ───────────────────────────────────────

def _usage_text(extra: str = "") -> str:
    head = f"❗ {_h(extra)}\n\n" if extra else "❗ "
    return (
        f"{head}<b>Foydalanish:</b>\n"
        "<code>/kino @BotNomi payload</code> — mirror qiladi\n"
        "<code>/kino @BotNomi payload 2</code> — 2-tugmani bosadi\n"
        "<code>/kino https://t.me/BotNomi?start=payload</code> — deep-link"
    )


def _parse_request(args: list[str]) -> _Request:
    if not args:
        raise _UsageError()

    link = _LINK_RE.match(args[0])
    if link:
        username, payload = link.group(1), link.group(2)
        rest = args[1:]
    else:
        if len(args) < 2:
            raise _UsageError()
        username = args[0][1:] if args[0].startswith("@") else args[0]
        payload = args[1].strip()
        rest = args[2:]

    if not _USERNAME_RE.match(username):
        raise _UsageError("Bot username noto'g'ri (5–32 belgi: harf, raqam, _).")
    if not _PAYLOAD_RE.match(payload):
        raise _UsageError("Payload faqat harf, raqam, _ va - dan iborat bo'lishi kerak (1–64 belgi).")

    button_index = None
    if rest:
        if not rest[0].isdigit() or not (1 <= int(rest[0]) <= 50):
            raise _UsageError("Tugma raqami 1 dan 50 gacha bo'lishi kerak.")
        button_index = int(rest[0]) - 1
    return _Request(username, payload, button_index)


# ── Xabar/tugma yordamchilari ─────────────────────────────────────────────────

def _media_of(m):
    for attr in ("video", "document", "audio", "animation", "video_note"):
        obj = getattr(m, attr, None)
        if obj:
            return obj
    return None


def _is_meaningful(m) -> bool:
    return bool(
        (m.text and m.text.strip())
        or (m.caption and m.caption.strip())
        or _media_of(m)
        or m.photo
    )


def _rows(m) -> list:
    rm = getattr(m, "reply_markup", None)
    return list(getattr(rm, "inline_keyboard", None) or [])


def _flatten_buttons(m) -> list:
    """Faqat callback tugmalar (indekslash shu ro'yxat bo'yicha)."""
    return [
        b for row in _rows(m) for b in row
        if getattr(b, "callback_data", None) is not None
    ]


def _url_ok(url: str | None) -> bool:
    return bool(url) and url.lower().startswith(("http://", "https://", "tg://"))


def _has_any_buttons(m) -> bool:
    return any(
        getattr(b, "callback_data", None) is not None or _url_ok(getattr(b, "url", None))
        for row in _rows(m) for b in row
    )


def _extract_links(m) -> list[str]:
    """Yashirin (text_link) havolalarni ajratib oladi — oddiy matnda ko'rinmaydi."""
    out: list[str] = []
    for ent in (m.entities or []) + (m.caption_entities or []):
        url = getattr(ent, "url", None)
        if _url_ok(url) and url not in out:
            out.append(url)
    return out


def _guess_ext(m) -> str:
    media = _media_of(m)
    fname = getattr(media, "file_name", "") or ""
    mime = (getattr(media, "mime_type", "") or "").lower()
    if "." in fname:
        ext = fname.rsplit(".", 1)[-1].lower()
        if _EXT_RE.match(ext):                 # masofadagi nomga ishonmaymiz (yo'l/belgilar)
            return ext
    if mime in _MIME_EXT:
        return _MIME_EXT[mime]
    if "matroska" in mime:
        return "mkv"
    if m.audio:
        return "mp3"
    if m.video or m.animation or m.video_note:
        return "mp4"
    return "bin"


def _display_name(media, ext: str, title: str) -> str:
    base = os.path.basename(getattr(media, "file_name", "") or "")
    stem = os.path.splitext(base)[0].strip()
    if not stem:
        stem = re.sub(r"[^\w\-]+", "_", title).strip("_")[:60] or "video"
    return f"{stem}.{ext}"


# ── Bot va userbot bilan ishlash ──────────────────────────────────────────────

async def _get_client(status, reply):
    try:
        client = await get_user_client()
    except Exception as e:
        logger.exception("Userbotni ulashda xato")
        await _fail(status, reply, f"❌ Userbot ulanmadi:\n<code>{_h(_describe_error(e))}</code>")
        return None
    if not client:
        await _fail(status, reply, "❌ Userbot (<code>SESSION_STRING</code>) ulangmagan!")
        return None
    return client


async def _resolve_bot(client, username: str):
    """Username haqiqatan BOT ekanini tekshiradi (odam/kanalga xabar ketmasligi uchun)."""
    try:
        user = await client.get_users(username)
    except FloodWait:
        raise
    except Exception as e:
        raise KinoError(f"@{username} topilmadi.") from e
    if isinstance(user, list):
        user = user[0] if user else None
    if user is None:
        raise KinoError(f"@{username} topilmadi.")
    if not getattr(user, "is_bot", False):
        raise KinoError(f"@{username} bot emas — xavfsizlik uchun faqat botlarga ruxsat beriladi.")
    return user


async def _last_message_id(client, chat_id: int) -> int:
    try:
        async for h in client.get_chat_history(chat_id, limit=1):
            return h.id
    except Exception as e:
        logger.debug("Oxirgi xabar ID olinmadi (yangi chat bo'lishi mumkin): %s", e)
    return 0


async def _refresh(client, chat_id: int, msgs: list) -> list:
    """Placeholder -> edit holatini ushlash uchun xabarlarni qayta o'qiydi; o'chirilganlarini tashlaydi."""
    try:
        fresh = await client.get_messages(chat_id, [m.id for m in msgs])
        if not isinstance(fresh, list):
            fresh = [fresh]
        fresh = [m for m in fresh if m and not getattr(m, "empty", False)]
        return fresh or msgs
    except FloodWait:
        raise
    except Exception as e:
        logger.warning("Xabarlarni qayta olishda xato (eski holat ishlatiladi): %s", e)
        return msgs


def _remove_handlers(client, *handles) -> None:
    for hnd in handles:
        try:
            client.remove_handler(*hnd)
        except Exception:
            pass


# ── Botdan xabarlarni yig'ish ─────────────────────────────────────────────────

async def _collect_new_messages(client, bot_id: int, after_id: int, timeout: float,
                                collect_delay: float, status_msg, trigger_fn=None,
                                satisfied: Callable[[list], bool] | None = None,
                                grace: float = VIDEO_GRACE) -> list:
    """
    Bot yuborgan YANGI (kiruvchi) xabarlarni yig'adi.

    1) Mazmunli xabar kelguncha kutadi (bo'sh placeholder'lar hisobga olinmaydi).
    2) Mazmunli xabar kelgach `collect_delay` jimlik oynasini kutadi.
    3) `satisfied(msgs)` False bo'lsa (masalan faqat "504" matni keldi) — video/tugma
       uchun yana `grace` soniya kutadi.
    Handler `trigger_fn` dan OLDIN o'rnatiladi (race yo'q), faqat kiruvchi xabarlar
    olinadi, tahrirlangan xabarlar ham yangilanadi.
    """
    from pyrogram import filters
    from pyrogram.handlers import EditedMessageHandler, MessageHandler

    loop = asyncio.get_running_loop()
    store: dict[int, object] = {}
    arrival = asyncio.Event()
    last_arrival = [loop.time()]
    hard_limit = [loop.time() + timeout]
    th = _Throttle(STATUS_INTERVAL)

    def _accept(m) -> None:
        if getattr(m, "outgoing", False):          # userbotning o'z xabari — e'tiborsiz
            return
        if not getattr(m, "chat", None) or m.chat.id != bot_id or m.id <= after_id:
            return
        store[m.id] = m
        last_arrival[0] = loop.time()
        arrival.set()

    async def _on_new(c, m):
        _accept(m)

    async def _on_edit(c, m):
        _accept(m)

    flt = filters.incoming & filters.private
    h_new = client.add_handler(MessageHandler(_on_new, flt), group=_HANDLER_GROUP)
    h_edit = client.add_handler(EditedMessageHandler(_on_edit, flt), group=_HANDLER_GROUP)

    def _ordered() -> list:
        return [store[k] for k in sorted(store)]

    def _has_meaningful() -> bool:
        return any(_is_meaningful(m) for m in store.values())

    async def _wait_until(pred, limit: float, note) -> bool:
        while not pred() and loop.time() < limit:
            arrival.clear()
            try:
                await asyncio.wait_for(arrival.wait(), timeout=max(0.05, min(1.0, limit - loop.time())))
            except asyncio.TimeoutError:
                pass
            if not pred() and th.ready():
                await _edit(status_msg, note())
        return pred()

    async def _settle() -> None:
        while loop.time() < hard_limit[0]:
            remaining = last_arrival[0] + collect_delay - loop.time()
            if remaining <= 0:
                return
            arrival.clear()
            try:
                await asyncio.wait_for(arrival.wait(), timeout=min(remaining, hard_limit[0] - loop.time()))
            except asyncio.TimeoutError:
                return

    def _note1() -> str:
        waited = int(timeout - (hard_limit[0] - loop.time()))
        what = "Javob kutilmoqda" if not store else "Bot javob berdi, mazmun kutilmoqda"
        return f"⏳ {what}...\n<code>{max(waited, 0)}s</code> / <code>{int(timeout)}s</code>"

    try:
        if trigger_fn is not None:
            await trigger_fn()
        last_arrival[0] = loop.time()

        await _wait_until(_has_meaningful, hard_limit[0], _note1)
        if not store:
            return []

        await _edit(status_msg, "📨 Xabar keldi, to'liq yuklanmoqda...")
        await _settle()

        if satisfied is not None and not satisfied(_ordered()):
            grace_end = loop.time() + grace

            def _note3() -> str:
                texts = [(m.text or m.caption or "").strip() for m in _ordered()]
                texts = [t for t in texts if t]
                preview = f"\n💬 <i>{_h(_clip(texts[-1], 120))}</i>" if texts else ""
                left = max(int(grace_end - loop.time()), 0)
                return f"⏳ Bot matn yubordi, video/tugma kutilmoqda...{preview}\n<code>{left}s</code> qoldi"

            if await _wait_until(lambda: satisfied(_ordered()), grace_end, _note3):
                hard_limit[0] = loop.time() + collect_delay + 1
                await _settle()

        # Polling zaxirasi (event handler o'tkazib yuborgan bo'lishi mumkin)
        try:
            async for m in client.get_chat_history(bot_id, limit=15):
                if m.id <= after_id:
                    break
                if getattr(m, "outgoing", False) or getattr(m, "empty", False):
                    continue
                store.setdefault(m.id, m)
        except FloodWait:
            raise
        except Exception as e:
            logger.warning("Polling fallback xato: %s", e)

        return _ordered()
    finally:
        _remove_handlers(client, h_new, h_edit)


async def _wait_for_media(client, bot_id: int, after_id: int, pressed_id: int,
                          allow_edit_media: bool, timeout: float, trigger_fn, status_msg):
    """Tugma bosilgach media xabarini kutadi. (media_msg | None, bot_matnlari) qaytaradi."""
    from pyrogram import filters
    from pyrogram.handlers import EditedMessageHandler, MessageHandler

    loop = asyncio.get_running_loop()
    found: list = []
    texts: list[str] = []
    arrival = asyncio.Event()

    def _ok_chat(m) -> bool:
        return (not getattr(m, "outgoing", False)
                and getattr(m, "chat", None) is not None and m.chat.id == bot_id)

    async def _on_new(c, m):
        if not _ok_chat(m) or m.id <= after_id:
            return
        if _media_of(m):
            found.append(m)
            arrival.set()
        else:
            t = (m.text or m.caption or "").strip()
            if t:
                texts.append(t)
                arrival.set()

    async def _on_edit(c, m):
        if not _ok_chat(m):
            return
        if allow_edit_media and m.id == pressed_id and _media_of(m):
            found.append(m)
            arrival.set()
        elif m.id > after_id and _media_of(m):
            found.append(m)
            arrival.set()

    flt = filters.incoming & filters.private
    h_new = client.add_handler(MessageHandler(_on_new, flt), group=_HANDLER_GROUP)
    h_edit = client.add_handler(EditedMessageHandler(_on_edit, flt), group=_HANDLER_GROUP)

    try:
        await trigger_fn()
        start = loop.time()
        deadline = start + timeout
        th = _Throttle(STATUS_INTERVAL)
        while not found and loop.time() < deadline:
            arrival.clear()
            try:
                await asyncio.wait_for(arrival.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            if not found and th.ready():
                extra = f"\n💬 <i>{_h(_clip(texts[-1], 120))}</i>" if texts else ""
                await _edit(
                    status_msg,
                    f"⏳ Video yuborilishi kutilmoqda...{extra}\n"
                    f"<code>{int(loop.time() - start)}s</code> / <code>{int(timeout)}s</code>",
                )
        if found:
            return found[0], texts

        # Polling zaxirasi
        try:
            async for m in client.get_chat_history(bot_id, limit=8):
                if m.id <= after_id:
                    break
                if not getattr(m, "outgoing", False) and _media_of(m):
                    return m, texts
        except FloodWait:
            raise
        except Exception as e:
            logger.warning("Media polling fallback xato: %s", e)
        if allow_edit_media:
            try:
                m = await client.get_messages(bot_id, pressed_id)
                if m and not getattr(m, "empty", False) and _media_of(m):
                    return m, texts
            except Exception:
                pass
        return None, texts
    finally:
        _remove_handlers(client, h_new, h_edit)


# ── Asosiy flow ───────────────────────────────────────────────────────────────

async def _run_flow(client, req: _Request, dest_chat, status, reply, user_msg) -> None:
    from pyrogram.raw import functions

    bot = await _resolve_bot(client, req.bot_username)
    after_id = await _last_message_id(client, bot.id)
    uname = _h(req.bot_username)

    await _edit(
        status,
        f"📨 <b>@{uname}</b> ga <code>/start {_h(req.payload)}</code> yuborilmoqda\n⏳ Javob kutilmoqda...",
    )

    async def _send_start():
        try:
            await client.send_message(bot.id, f"/start {req.payload}")
        except FloodWait:
            raise
        except Exception as e:
            logger.warning("send_message /start xato, StartBot bilan urinamiz: %s", e)
            peer = await client.resolve_peer(bot.id)
            await client.invoke(functions.messages.StartBot(
                bot=peer, peer=peer, random_id=client.rnd_id(), start_param=req.payload,
            ))

    if req.button_index is not None:
        def satisfied(ms):
            return any(_media_of(m) or _flatten_buttons(m) for m in ms)
    else:
        def satisfied(ms):
            return any(_media_of(m) or _has_any_buttons(m) for m in ms)

    msgs = await _collect_new_messages(
        client=client, bot_id=bot.id, after_id=after_id, timeout=WAIT_TIMEOUT,
        collect_delay=COLLECT_DELAY, status_msg=status, trigger_fn=_send_start,
        satisfied=satisfied,
    )
    if not msgs:
        await _fail(status, reply,
                    f"❌ <b>@{uname}</b> javob bermadi (timeout {WAIT_TIMEOUT}s).\n\n"
                    "Payload to'g'riligini tekshirib, qaytadan urinib ko'ring.")
        return

    msgs = await _refresh(client, bot.id, msgs)
    await _edit(status, f"✅ <b>@{uname}</b> dan {len(msgs)} ta xabar keldi\n⏳ Qayta ishlanmoqda...")

    # ── To'g'ridan tugmani bosish rejimi ──
    if req.button_index is not None:
        btn_msg = next((m for m in msgs if _flatten_buttons(m)), None)
        if not btn_msg:
            await _fail(status, reply, "❌ Tugmali xabar topilmadi!")
            return
        buttons = _flatten_buttons(btn_msg)
        if req.button_index >= len(buttons):
            lst = "\n".join(f"  {i + 1}. {_h(b.text or '-')}" for i, b in enumerate(buttons))
            await _fail(
                status, reply,
                f"❌ {req.button_index + 1}-tugma mavjud emas\n\n📋 Tugmalar ({len(buttons)} ta):\n{lst}\n\n"
                f"Masalan: <code>/kino @{uname} {_h(req.payload)} 1</code>",
            )
            return
        await _press_and_deliver(client, bot, btn_msg, buttons[req.button_index], dest_chat, status, reply)
        return

    # ── Mirror rejimi ──
    meaningful = [m for m in msgs if _is_meaningful(m)]
    show = meaningful or msgs
    failed = 0
    for pyro_msg in show[:MAX_MIRROR]:
        try:
            await _mirror_message(client, pyro_msg, req.bot_username, user_msg)
        except Exception:
            failed += 1
            logger.exception("Mirror xato (msg %s)", getattr(pyro_msg, "id", "?"))
    if len(show) > MAX_MIRROR:
        await _safe_reply(reply, f"… va yana {len(show) - MAX_MIRROR} ta xabar ko'rsatilmadi.")

    if failed:
        await _edit(status, f"⚠️ {failed} ta xabarni ko'rsatib bo'lmadi (batafsil logda).")
    else:
        try:
            await status.delete()
        except TelegramError:
            pass


async def _safe_reply(reply, text: str) -> None:
    try:
        await reply(text)
    except TelegramError as e:
        logger.warning("Javob yuborilmadi: %s", e)


# ── Xabarni mirror qilish ─────────────────────────────────────────────────────

def _build_markup(pyro_msg, bot_username: str, include_urls: bool = True,
                  with_download: bool = True) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    idx = 0
    for row in _rows(pyro_msg):
        out: list[InlineKeyboardButton] = []
        for btn in row:
            text = ((getattr(btn, "text", "") or "•")[:60])
            if getattr(btn, "callback_data", None) is not None:
                cb = f"kinoi|{bot_username}|{pyro_msg.id}|{idx}"
                idx += 1                                  # indeks har doim _flatten_buttons bilan mos
                if len(cb.encode("utf-8")) <= 64:
                    out.append(InlineKeyboardButton(text, callback_data=cb))
            elif include_urls and _url_ok(getattr(btn, "url", None)):
                out.append(InlineKeyboardButton(text, url=btn.url))
        if out:
            rows.append(out)

    if with_download and _media_of(pyro_msg):
        if pyro_msg.video or pyro_msg.animation or pyro_msg.video_note:
            label = "📥 Videoni yuborish"
        elif pyro_msg.audio:
            label = "📥 Audioni yuborish"
        else:
            label = "📥 Faylni yuborish"
        rows.append([InlineKeyboardButton(label, callback_data=f"kinodl|{bot_username}|{pyro_msg.id}")])
    return InlineKeyboardMarkup(rows) if rows else None


async def _reply_html(user_msg, html_text: str, markup_factory):
    """HTML + tugmalar; BadRequest bo'lsa — URL'siz va oddiy matn bilan qayta urinadi."""
    opts = LinkPreviewOptions(is_disabled=True)
    try:
        return await user_msg.reply_text(
            _clip(html_text, 4000), parse_mode=ParseMode.HTML,
            reply_markup=markup_factory(True), link_preview_options=opts,
        )
    except BadRequest as e:
        logger.warning("Mirror HTML/tugma xato (%s) — soddalashtirilgan variant", e)
    return await user_msg.reply_text(
        _clip(_strip_html(html_text), 4000),
        reply_markup=markup_factory(False), link_preview_options=opts,
    )


async def _mirror_message(client, pyro_msg, bot_username: str, user_msg) -> None:
    """Pyrogram xabarini PTB orqali foydalanuvchiga mirror qiladi."""
    text = str(pyro_msg.text or pyro_msg.caption or "").strip()
    links = _extract_links(pyro_msg)
    media = _media_of(pyro_msg)

    def factory(include_urls: bool):
        return _build_markup(pyro_msg, bot_username, include_urls=include_urls)

    body = _h(_clip(text, 2800)) if text else ""
    if links:
        body += ("\n\n" if body else "") + "\n".join(f"🔗 {_h(u)}" for u in links[:5])

    if media:
        size = int(getattr(media, "file_size", 0) or 0)
        fname = os.path.basename(getattr(media, "file_name", "") or "")
        mime = getattr(media, "mime_type", "") or ""
        dur = int(getattr(media, "duration", 0) or 0)
        w, hh = getattr(media, "width", 0) or 0, getattr(media, "height", 0) or 0
        info = []
        if fname:
            info.append(f"📎 <code>{_h(fname)}</code>")
        info.append(f"📦 Hajmi: <code>{_fmt_size(size) if size else '?'}</code>")
        if dur:
            info.append(f"⏱ Davomiyligi: <code>{_fmt_dur(dur)}</code>")
        if w and hh:
            info.append(f"📐 O'lchami: <code>{w}×{hh}</code>")
        if mime:
            info.append(f"🎞 MIME: <code>{_h(mime)}</code>")
        text_out = (body + "\n\n" if body else "") + "\n".join(info)
        await _reply_html(user_msg, text_out, factory)
        return

    if pyro_msg.photo:
        try:
            bio = await client.download_media(pyro_msg, in_memory=True)
            data = bio.getvalue() if hasattr(bio, "getvalue") else None
            if data:
                cap = _h(_clip(text, 900)) if text else None
                await user_msg.reply_photo(
                    photo=data, caption=cap, parse_mode=ParseMode.HTML,
                    reply_markup=factory(True),
                )
                return
        except FloodWait:
            raise
        except Exception as e:
            logger.warning("Rasmni mirror qilib bo'lmadi, matn ko'rsatiladi: %s", e)
        body = body or "🖼 (rasm)"

    await _reply_html(user_msg, body or "📨 (Bo'sh xabar)", factory)


# ── Callback (tugma bosilganda) ───────────────────────────────────────────────

def _parse_callback(data: str):
    """('dl'|'btn'|'legacy', bot_username, msg_id, extra) yoki None."""
    try:
        if data.startswith("kinodl|"):
            _, bot, mid = data.split("|", 2)
            return "dl", bot, int(mid), None
        if data.startswith("kinoi|"):
            _, bot, mid, idx = data.split("|", 3)
            return "btn", bot, int(mid), int(idx)
        if data.startswith("kino|"):                       # eski xabarlar bilan moslik
            _, bot, mid, cb = data.split("|", 3)           # maxsplit=3: cb ichida "|" bo'lishi mumkin
            return "legacy", bot, int(mid), cb
    except ValueError:
        pass
    return None


class _RawBtn:
    def __init__(self, text: str, cb):
        self.text = text
        self.callback_data = cb


async def kino_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    if not _is_authorized(user.id):
        await query.answer("⛔ Ruxsat yo'q", show_alert=True)
        return

    parsed = _parse_callback(query.data or "")
    if parsed is None or not _USERNAME_RE.match(parsed[1]):
        await query.answer("⚠️ Tugma eskirgan. /kino ni qayta yuboring.", show_alert=True)
        return
    await query.answer()

    action, bot_username, msg_id, extra = parsed
    chat_id = query.message.chat.id if query.message else user.id

    async def reply(text, **kw):
        return await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, **kw)

    if not ARCHIVE_GROUP_ID:
        await _safe_reply(reply, "❌ <code>ARCHIVE_GROUP_ID</code> <code>.env</code>da sozlanmagan!")
        return
    if not await _try_lock(reply, f"cb:{user.id}:{bot_username}"):
        return

    status = None
    try:
        status = await reply(f"⏳ <b>@{_h(bot_username)}</b> bilan ishlanmoqda...")
        client = await _get_client(status, reply)
        if client is None:
            return
        bot = await _resolve_bot(client, bot_username)
        pyro_msg = await client.get_messages(bot.id, msg_id)
        if not pyro_msg or getattr(pyro_msg, "empty", False):
            raise KinoError("Xabar topilmadi (bot o'chirib yuborgan bo'lishi mumkin). /kino ni qayta yuboring.")

        if action == "dl":
            if not _media_of(pyro_msg):
                raise KinoError("Bu xabarda video/fayl yo'q.")
            await _deliver_media(client, pyro_msg, ARCHIVE_GROUP_ID, bot_username, status)
            return

        if action == "btn":
            buttons = _flatten_buttons(pyro_msg)
            if extra >= len(buttons):
                raise KinoError("Tugma topilmadi (bot xabarni o'zgartirgan). /kino ni qayta yuboring.")
            chosen = buttons[extra]
        else:  # legacy
            chosen = _RawBtn(extra, extra)

        await _press_and_deliver(client, bot, pyro_msg, chosen, ARCHIVE_GROUP_ID, status, reply)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("kino callback xato")
        await _fail(status, reply, f"❌ <b>Xato</b>\n{_h(_describe_error(e))}")
    finally:
        _unlock()


# ── Tugmani bosib video kutish ────────────────────────────────────────────────

async def _press_and_deliver(client, bot, btn_msg, chosen, dest_chat, status, reply) -> None:
    from pyrogram.raw import functions

    cb_data = chosen.callback_data
    if isinstance(cb_data, str):
        cb_data = cb_data.encode("utf-8")
    btn_text = getattr(chosen, "text", "") or bot.username or "video"
    answer: dict = {}

    await _edit(status, f"🔘 <code>{_h(_clip(btn_text, 60))}</code> tanlandi\n⏳ Javob kutilmoqda...")
    peer = await client.resolve_peer(bot.id)

    async def _press():
        try:
            res = await asyncio.wait_for(
                client.invoke(functions.messages.GetBotCallbackAnswer(
                    peer=peer, msg_id=btn_msg.id, data=cb_data)),
                timeout=CALLBACK_ANSWER_TIMEOUT,
            )
            answer["message"] = getattr(res, "message", None)
            answer["url"] = getattr(res, "url", None)
        except FloodWait:
            raise
        except Exception as e:                             # BotResponseTimeout va h.k. — odatiy holat
            logger.info("GetBotCallbackAnswer: %s (odatiy)", type(e).__name__)

    media_msg, texts = await _wait_for_media(
        client=client, bot_id=bot.id, after_id=btn_msg.id, pressed_id=btn_msg.id,
        allow_edit_media=not _media_of(btn_msg), timeout=BUTTON_WAIT_TIMEOUT,
        trigger_fn=_press, status_msg=status,
    )

    if media_msg is None:
        notes = []
        if answer.get("message"):
            notes.append(f"🔔 Bot ogohlantirishi: <i>{_h(_clip(answer['message'], 200))}</i>")
        if answer.get("url"):
            notes.append(f"🔗 {_h(answer['url'])}")
        if texts:
            notes.append(f"💬 Bot xabari: <i>{_h(_clip(texts[-1], 200))}</i>")
        extra = ("\n\n" + "\n".join(notes)) if notes else ""
        await _fail(status, reply,
                    f"❌ Bot video yubormadi (timeout {BUTTON_WAIT_TIMEOUT}s){extra}\n\nQaytadan urinib ko'ring.")
        return

    await _deliver_media(client, media_msg, dest_chat, btn_text, status)


# ── Yetkazish: nusxalash yoki yuklab-yuborish ─────────────────────────────────

def _make_progress(status, title: str, label: str):
    th = _Throttle(PROGRESS_INTERVAL)
    last = [-1]

    async def _cb(current, total):
        if not total or not th.ready():
            return
        pct = min(int(current * 100 / total), 99)
        if pct == last[0]:
            return
        last[0] = pct
        await _edit(
            status,
            f"🎬 <b>{_h(title)}</b>\n\n{label}\n"
            f"<code>{_progress_bar(pct)}</code> <code>{pct}%</code>\n"
            f"<code>{current / 1048576:.1f} MB</code> / <code>{total / 1048576:.1f} MB</code>",
        )
    return _cb


def _build_caption(media_msg, title: str) -> str:
    """Asl caption formatlashi (HTML) saqlanadi; juda uzun bo'lsa — oddiy matn bilan qisqartiriladi."""
    cap = media_msg.caption
    plain = str(cap).strip() if cap else ""
    if not plain:
        return f"🎬 {_h(title)}"
    if len(plain) > CAPTION_LIMIT:
        return _h(_clip(plain, CAPTION_LIMIT))
    rich = getattr(cap, "html", None)
    return rich if rich else _h(plain)


def _disk_ok(needed: float) -> bool:
    try:
        return shutil.disk_usage(TEMP_DIR).free > needed
    except OSError:
        return True


async def _premium() -> bool:
    try:
        return bool(await is_user_premium())
    except Exception:
        return False


async def _deliver_media(client, media_msg, dest_chat, title: str, status) -> None:
    started = time.monotonic()
    media = _media_of(media_msg)
    if media is None:
        raise KinoError("Xabarda video/fayl yo'q.")

    size = int(getattr(media, "file_size", 0) or 0)
    size_str = _fmt_size(size) if size else "?"
    dur = int(getattr(media, "duration", 0) or 0)
    w, hh = getattr(media, "width", 0) or 0, getattr(media, "height", 0) or 0

    def _final(mode: str, size_s: str, meta_dur: int, mw: int, mh: int) -> str:
        parts = [f"✅ <b>{_h(title)}</b>\n", f"📦 Hajmi: <code>{size_s}</code>"]
        if meta_dur:
            parts.append(f"⏱ Davomiyligi: <code>{_fmt_dur(meta_dur)}</code>")
        if mw and mh:
            parts.append(f"📐 O'lchami: <code>{mw}×{mh}</code>")
        parts.append(f"🕒 Sarflangan vaqt: <code>{_fmt_dur(time.monotonic() - started)}</code>")
        parts.append(f"\n🗂 Guruhga muvaffaqiyatli yuborildi! ({mode})")
        return "\n".join(parts)

    # dest_chat ni Pyrogram peer cache'ga kiritamiz (-100... supergroup "Peer id invalid" bermasligi uchun)
    try:
        await client.get_chat(dest_chat)
    except Exception as e:
        logger.warning("dest_chat get_chat xato (normal): %s", e)

    # 1) Tezkor nusxalash: yuklamasdan, Telegram serverining o'zida (himoyalangan bo'lsa ishlamaydi)
    if KINO_TRY_COPY:
        await _edit(status, f"🎬 <b>{_h(title)}</b>\n\n⚡ Tezkor nusxalash urinilmoqda...")
        try:
            await client.copy_message(
                chat_id=dest_chat, from_chat_id=media_msg.chat.id, message_id=media_msg.id)
        except FloodWait:
            raise
        except Exception as e:
            logger.info("copy_message ishlamadi (%s) — yuklab-yuborishga o'tamiz", type(e).__name__)
        else:
            await _edit(status, _final("⚡ tezkor nusxa", size_str, dur, w, hh))
            return

    # 2) Limit va disk tekshiruvi
    limit = LIMIT_PREMIUM if await _premium() else LIMIT_FREE
    if size and size > limit:
        raise KinoError(f"Fayl juda katta ({size_str}). Userbot limiti: {_fmt_size(limit)}.")

    ext = _guess_ext(media_msg)
    needed = size * (2.3 if ext in _REMUXABLE_EXTS else 1.1) + 200 * 1024 * 1024
    if size and not _disk_ok(needed):
        raise KinoError(f"Diskda joy yetarli emas (kerak ≈ {_fmt_size(needed)}).")

    tmp_path = os.path.join(TEMP_DIR, f"kino_{media_msg.id}_{uuid.uuid4().hex[:8]}.{ext}")
    cleanup = [tmp_path]
    thumb = None
    try:
        # 3) Yuklab olish
        await _edit(status, f"🎬 <b>{_h(title)}</b>\n\n⬇️ Yuklab olinmoqda...\n"
                            f"<code>{_progress_bar(0)}</code> <code>0%</code>\n<code>0.0 MB</code> / <code>{size_str}</code>")
        dl = await client.download_media(
            media_msg, file_name=tmp_path,
            progress=_make_progress(status, title, "⬇️ <b>Yuklab olinmoqda...</b>"),
        )
        path = dl if isinstance(dl, str) and os.path.exists(dl) else tmp_path
        if path not in cleanup:
            cleanup.append(path)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise KinoError("Fayl yuklanmadi (bo'sh yoki yo'q).")

        actual = os.path.getsize(path)
        actual_str = _fmt_size(actual)
        is_video = ext in _VIDEO_EXTS or bool(media_msg.video or media_msg.animation or media_msg.video_note)

        # 4) Telegram ichida o'ynashi uchun MKV/WEBM/AVI... -> MP4 (faqat konteyner almashadi)
        send_path, send_ext = path, ext
        if is_video and ext in _REMUXABLE_EXTS:
            await _edit(status, f"🎬 <b>{_h(title)}</b>\n\n🔄 MP4 formatiga o'tkazilmoqda...")
            remuxed = await _remux_to_mp4(path, actual)
            if remuxed:
                cleanup.append(remuxed)
                send_path, send_ext = remuxed, "mp4"
                actual_str = _fmt_size(os.path.getsize(remuxed))

        as_video = is_video and send_ext in _NATIVE_EXTS
        meta = {"duration": dur, "width": w, "height": hh}
        if as_video:
            await _edit(status, f"🎬 <b>{_h(title)}</b>\n\n✅ Yuklab olindi: <code>{actual_str}</code>\n🔄 Metadata tayyorlanmoqda...")
            probed = await _get_video_meta(send_path)
            meta = {k: (probed.get(k) or meta[k]) for k in meta}
            thumb = await _make_thumb(send_path, meta["duration"])
            if thumb:
                cleanup.append(thumb)

        # 5) Yuborish
        await _edit(status, f"🎬 <b>{_h(title)}</b>\n\n📤 <b>Guruhga yuborilmoqda...</b>\n"
                            f"<code>{_progress_bar(0)}</code> <code>0%</code>\n<code>0.0 MB</code> / <code>{actual_str}</code>")
        common = dict(
            chat_id=dest_chat,
            caption=_build_caption(media_msg, title),
            parse_mode=PyroParseMode.HTML,
            file_name=_display_name(media, send_ext, title),
            progress=_make_progress(status, title, "📤 <b>Guruhga yuborilmoqda...</b>"),
        )
        if as_video:
            await client.send_video(
                video=send_path, supports_streaming=True,
                duration=meta["duration"] or None, width=meta["width"] or None,
                height=meta["height"] or None, thumb=thumb or None, **common)
        else:
            await client.send_document(document=send_path, thumb=thumb or None, **common)

        await _edit(status, _final("⬆️ yuklab-yuborildi", actual_str,
                                   meta["duration"], meta["width"], meta["height"]))
    finally:
        for p in cleanup:
            _rm(p)


# ── ffmpeg / ffprobe ──────────────────────────────────────────────────────────

async def _run(cmd: list[str], timeout: float):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out, err


async def _get_video_meta(path: str) -> dict:
    meta = {"duration": 0, "width": 0, "height": 0}
    try:
        rc, out, _ = await _run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_streams", "-show_format", "-of", "json", path], 60)
        if rc != 0:
            return meta
        data = json.loads(out.decode("utf-8", "replace") or "{}")
        stream = (data.get("streams") or [{}])[0]
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)

        rotation = 0
        try:
            rotation = int((stream.get("tags") or {}).get("rotate") or 0)
        except (TypeError, ValueError):
            pass
        for sd in stream.get("side_data_list") or []:
            if "rotation" in sd:
                try:
                    rotation = int(float(sd["rotation"]))
                except (TypeError, ValueError):
                    pass
        if abs(rotation) % 180 == 90:                       # portret videolar: eni/bo'yi almashadi
            width, height = height, width

        raw_dur = (data.get("format") or {}).get("duration") or stream.get("duration")
        try:
            meta["duration"] = int(float(raw_dur))
        except (TypeError, ValueError):
            pass
        meta["width"], meta["height"] = width, height
    except Exception as e:
        logger.warning("ffprobe xato: %s", e)
    return meta


async def _make_thumb(path: str, duration: int) -> str | None:
    thumb = os.path.join(TEMP_DIR, f"kthumb_{uuid.uuid4().hex}.jpg")
    seek = max(1, duration // 4) if duration > 4 else 1
    try:
        rc, _, _ = await _run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(seek), "-i", path, "-frames:v", "1",
             "-vf", "scale=320:320:force_original_aspect_ratio=decrease", "-q:v", "5", thumb], 60)
        if rc == 0 and os.path.exists(thumb) and os.path.getsize(thumb) > 0:
            return thumb
    except Exception as e:
        logger.warning("Thumbnail xato: %s", e)
    _rm(thumb)
    return None


async def _remux_to_mp4(src: str, size: int) -> str | None:
    """Faqat konteynerni almashtiradi (-c copy). Muvaffaqiyatsiz bo'lsa None (asl fayl yuboriladi)."""
    dst = os.path.splitext(src)[0] + "_r.mp4"
    timeout = 300 + size // (20 * 1024 * 1024)
    try:
        rc, _, err = await _run(
            ["ffmpeg", "-y", "-v", "error", "-i", src, "-map", "0:v:0", "-map", "0:a?",
             "-c", "copy", "-movflags", "+faststart", dst], timeout)
        if rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst
        logger.info("Remux muvaffaqiyatsiz (%s): %s", rc, (err or b"")[-200:].decode("utf-8", "replace"))
    except Exception as e:
        logger.warning("Remux xato: %s", e)
    _rm(dst)
    return None


# ── Asosiy handler ────────────────────────────────────────────────────────────

async def kino_sender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return

    if not _is_authorized(user.id):
        await msg.reply_text("⛔ Bu buyruq faqat adminlar va ruxsat berilgan foydalanuvchilar uchun.")
        return

    try:
        req = _parse_request(list(context.args or []))
    except _UsageError as e:
        await msg.reply_text(_usage_text(str(e)), parse_mode=ParseMode.HTML)
        return

    if not ARCHIVE_GROUP_ID:
        await msg.reply_text("❌ <code>ARCHIVE_GROUP_ID</code> <code>.env</code>da sozlanmagan!",
                             parse_mode=ParseMode.HTML)
        return

    async def reply(text, **kw):
        return await msg.reply_text(text, parse_mode=ParseMode.HTML, **kw)

    if not await _try_lock(reply, f"{user.id}:{req.bot_username}"):
        return

    status = None
    try:
        status = await reply(
            f"🔍 <b>@{_h(req.bot_username)}</b> ga so'rov yuborilmoqda...\n<code>{_h(req.payload)}</code>")
        client = await _get_client(status, reply)
        if client is None:
            return
        logger.info("/kino user=%s bot=@%s payload=%s btn=%s",
                    user.id, req.bot_username, req.payload, req.button_index)
        await _run_flow(client, req, ARCHIVE_GROUP_ID, status, reply, msg)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("kino_sender xato")
        await _fail(status, reply, f"❌ <b>Xato</b>\n{_h(_describe_error(e))}")
    finally:
        _unlock()
