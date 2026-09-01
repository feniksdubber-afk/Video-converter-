"""
Studiya guruhi topic'lari orqali ommaviy (bulk) video yuklash.

Oqim:
  1. Har bir film/serial uchun topic avtomatik ochiladi (utils/studio_autotopic.py).
  2. Manager topic ichiga video(lar)ni tashlaydi. Serial bo'lsa captionga
     "fasl-qism" (masalan "2-5") deb yozadi. Film bo'lsa caption shart emas.
  3. Bot HAR BIR videoni darhol ishlamaydi -- faqat navbatga ("eslab qoladi").
  4. Manager topic ichida /joylash yozgach, bot navbatni ketma-ket qayta
     ishlaydi: mp4/faststart tekshiruvi -> R2'ga yuklash -> backend'ga
     ro'yxatga olish -> asl xabar caption'ini formatlangan matnga almashtiradi
     (video allaqachon Telegram'da turgani uchun qayta yuklab-yuborish
     shart emas -- faqat caption tahrirlanadi).
"""

import asyncio
import logging
import os
import re
import time

import httpx
from telegram import Update
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import ContextTypes

from config import STUDIO_API_BASE
from utils.shared_db import get_manager_studios
from utils.studio_auth import get_bound_studio, bind_user
from utils.studio_group import get_slug_by_chat_id, get_content_key_by_topic, set_topic_id
from utils.studio_topic_queue import add_item, get_queue, remove_item
from utils.ffmpeg_utils import prepare_for_telegram, _run_in_executor, make_temp_path
from handlers.studio_group import quality_label
from handlers.studio_upload import _presign_and_put, _auth_headers
from handlers.studio_backfill import _fetch_movie_detail
from handlers.studio_content import _fetch_episodes

logger = logging.getLogger(__name__)

# ── /joylash progress UI ────────────────────────────────────────────────────

_BAR_LEN = 14
_STAGE_LABEL = {
    "download": "📥  Telegram'dan yuklab olinmoqda",
    "prepare":  "🔄  Formatga tayyorlanmoqda (ffmpeg)",
    "upload":   "☁️  R2 bulutiga yuklanmoqda",
    "register": "📝  Studiya bazasiga ro'yxatga olinmoqda",
}
# Har bir bosqich item ichida qancha ulush (0..1) egallashi -- bar/foiz shu
# yordamida bitta katta video ishlanayotganda ham sekin-asta oldinga siljiydi
# (faqat item to'liq tugaganda emas, "muzlab qolgandek" ko'rinmasligi uchun).
_STAGE_FRACTION = {
    None:       0.0,
    "download": 0.05,
    "prepare":  0.35,
    "upload":   0.55,
    "register": 0.90,
}


def _progress_bar(fraction: float) -> str:
    filled = round(_BAR_LEN * fraction)
    filled = max(0, min(_BAR_LEN, filled))
    return "🟩" * filled + "⬜️" * (_BAR_LEN - filled)


def _item_label(kind: str, title: str, item: dict) -> str:
    if kind == "m":
        return f"🎬 {title}"
    return f"{item['season']}-fasl, {item['episode']}-qism"


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── Tarmoq xatolariga chidamli qayta urinish (retry) infratuzilmasi ────────
#
# ConnectionResetError / OSError [Errno 0] kabi xatolar deyarli har doim
# VAQTINCHALIK tarmoq uzilishidan kelib chiqadi (Telegram/R2/API bilan TCP
# ulanish uzilishi). Bunday xatolarni "kutilmagan xato" deb bir marta log
# qilib qo'yib yubormasdan, avtomatik va qat'iyat bilan qayta urinamiz --
# lekin faqat RETRYABLE (vaqtinchalik) xato turlari uchun. Mantiqiy xatolar
# (masalan noto'g'ri caption format, 4xx javoblar) qayta urinilmaydi --
# ular takrorlansa ham natija o'zgarmaydi va foydalanuvchi vaqtini yeydi.

_RETRYABLE_EXC = (
    OSError,                # ConnectionResetError, [Errno 0] va shu kabi TCP xatolar
    httpx.TransportError,   # ulanish, o'qish/yozish, timeout xatolari (httpx)
    TimedOut,
    NetworkError,
    asyncio.TimeoutError,
)

# Bosqich nomi -> (urinishlar soni, boshlang'ich kutish soniyasi).
# Kutish har urinishda ikki baravar oshadi (exponential backoff):
# masalan 3 urinish, 4s boshlang'ich -> 4s, 8s, 16s.
_STAGE_RETRY_POLICY = {
    "download": (4, 4.0),
    "upload":   (4, 4.0),
    "register": (3, 3.0),
}


async def _run_with_retry(stage: str, coro_factory, *, on_retry=None):
    """`coro_factory()` har chaqirilganda yangi coroutine yaratadi va uni
    ishga tushiradi. Faqat RETRYABLE tarmoq xatolarida qayta urinadi,
    boshqa (mantiqiy) xatolarni darhol yuqoriga uzatadi.

    `on_retry(attempt, max_attempts, exc, wait)` -- har muvaffaqiyatsiz
    urinishdan keyin (oxirgisidan tashqari) chaqiriladi, foydalanuvchiga
    progress xabarida "qayta urinilmoqda" ko'rsatish uchun.
    """
    max_attempts, base_wait = _STAGE_RETRY_POLICY.get(stage, (1, 0.0))
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except _RETRYABLE_EXC as e:
            last_exc = e
            if attempt >= max_attempts:
                break
            wait = base_wait * (2 ** (attempt - 1))
            logger.warning(
                "[%s] tarmoq xatosi (urinish %s/%s): %r -- %.0fs dan keyin qayta urinamiz",
                stage, attempt, max_attempts, e, wait,
            )
            if on_retry:
                try:
                    await on_retry(attempt, max_attempts, e, wait)
                except Exception:
                    pass
            await asyncio.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _short_error(text: str, limit: int = 150) -> str:
    """Xato matnini progress xabariga sig'diradigan qilib qisqartiradi."""
    text = " ".join(str(text).split())  # ko'p qatorli/ortiqcha probellarni yig'ish
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or "noma'lum xato"


def _render_progress(
    *, title: str, kind: str, total: int, done: int, errors: int, skipped: int = 0,
    current_item: dict | None, stage: str | None,
    recent: list[tuple[str, bool | None, str | None]], elapsed: float,
    retry_note: str | None = None,
) -> str:
    icon = "🎬" if kind == "m" else "📺"
    processed = done + errors + skipped
    # "Silliq" progress: to'liq tugagan itemlar + joriy item ichidagi bosqich ulushi
    fractional = processed + (_STAGE_FRACTION.get(stage, 0.0) if current_item is not None else 0.0)
    fraction = (fractional / total) if total else 0.0
    pct = int(round(100 * fraction))

    lines = [
        "✨━━━━━━━━━━━━━━━━━━━━━✨",
        "     🚀 *STUDIYAGA JOYLASH*",
        "✨━━━━━━━━━━━━━━━━━━━━━✨",
        "",
        f"{icon} *{title}*",
        "",
        f"{_progress_bar(fraction)}",
        f"*{pct}%*   ·   {processed}/{total} video",
        "",
    ]

    if current_item is not None and stage:
        lines += [
            "┌─ 🎯 *Joriy video* ─────────────",
            f"│  ▶️ {_item_label(kind, title, current_item)}",
            f"│  {_STAGE_LABEL.get(stage, stage)}...",
        ]
        if retry_note:
            lines.append(f"│  {retry_note}")
        lines += [
            "└─────────────────────────────────",
            "",
        ]

    if recent:
        lines.append("📋 *Oxirgi natijalar:*")
        for label, ok, detail in recent[-5:]:
            icon_r = "✅" if ok else ("⏭️" if ok is None else "⚠️")
            lines.append(f"  {icon_r} {label}")
            if detail:
                lines.append(f"     └ {detail}")
        lines.append("")

    eta_text = ""
    if fractional > 0:
        avg_per_unit = elapsed / fractional
        eta = avg_per_unit * (total - fractional)
        eta_text = f"   ·   ⏱ Qoldi: ~{_fmt_duration(eta)}"
    lines.append(f"🕓 O'tgan: {_fmt_duration(elapsed)}{eta_text}")
    skip_text = f"   ⏭️ O'tkazildi: *{skipped}*" if skipped else ""
    lines.append(f"✔️ Joylandi: *{done}*   ⚠️ Xatolar: *{errors}*{skip_text}   ⏳ Navbatda: *{total - processed}*")
    return "\n".join(lines)


def _render_finished(
    *, title: str, kind: str, total: int, done: int, errors: int, skipped: int = 0, elapsed: float,
) -> str:
    icon = "🎬" if kind == "m" else "📺"
    all_ok = errors == 0
    header = "🎉━━━━━━━━━━━━━━━━━━━━━🎉" if all_ok else "⚠️━━━━━━━━━━━━━━━━━━━━━⚠️"
    title_line = "   ✅ *JOYLASH YAKUNLANDI!*" if all_ok else "   ☑️ *JOYLASH TUGADI (xatolar bilan)*"
    lines = [
        header,
        title_line,
        header,
        "",
        f"{icon} *{title}*",
        "",
        _progress_bar(1.0),
        "*100%*",
        "",
        f"✔️ Muvaffaqiyatli joylandi: *{done}*",
    ]
    if skipped:
        lines.append(f"⏭️ Bazada mavjud bo'lgani uchun o'tkazildi: *{skipped}*")
    lines += [
        f"⚠️ Xatolar: *{errors}*",
        f"🕓 Jami vaqt: {_fmt_duration(elapsed)}",
    ]
    if all_ok:
        lines.append("")
        lines.append("🥳 Barcha videolar studiyaga muvaffaqiyatli joylandi!")
    else:
        lines.append("")
        lines.append("ℹ️ Xatolik bergan videolarni qayta navbatga qo'shib, /joylash ni qayta yuboring.")
    return "\n".join(lines)


class _ProgressPainter:
    """Telegram flood-controliga tegib qolmaslik uchun tez-tez kelgan
    edit_message_text so'rovlarini vaqt bo'yicha siqib (throttle) yuboradi --
    stage o'zgarganda darhol emas, kamida _MIN_INTERVAL soniyada bir marta
    (force=True bo'lsa har doim darhol)."""

    _MIN_INTERVAL = 1.2

    def __init__(self, context, chat_id: int, message_id: int, *, title: str, kind: str, total: int):
        self._context = context
        self._chat_id = chat_id
        self._message_id = message_id
        self._title = title
        self._kind = kind
        self._total = total
        self._done = 0
        self._errors = 0
        self._skipped = 0
        self._recent: list[tuple[str, bool | None, str | None]] = []
        self._last_edit = 0.0
        self._last_text = ""
        self._started = time.monotonic()

    async def update(
        self, *, current_item: dict | None, stage: str | None, force: bool = False,
        retry_note: str | None = None,
    ) -> None:
        text = _render_progress(
            title=self._title, kind=self._kind, total=self._total,
            done=self._done, errors=self._errors, skipped=self._skipped,
            current_item=current_item, stage=stage, recent=self._recent,
            elapsed=time.monotonic() - self._started, retry_note=retry_note,
        )
        await self._send(text, force=force)

    async def finish(self) -> None:
        text = _render_finished(
            title=self._title, kind=self._kind, total=self._total,
            done=self._done, errors=self._errors, skipped=self._skipped,
            elapsed=time.monotonic() - self._started,
        )
        await self._send(text, force=True)

    async def _send(self, text: str, *, force: bool) -> None:
        if text == self._last_text:
            return
        now = time.monotonic()
        if not force and (now - self._last_edit) < self._MIN_INTERVAL:
            return
        self._last_edit = now
        self._last_text = text
        try:
            await self._context.bot.edit_message_text(
                chat_id=self._chat_id, message_id=self._message_id, text=text, parse_mode="Markdown",
            )
        except RetryAfter as e:
            wait = min(e.retry_after, 20) + 1
            await asyncio.sleep(wait)
            try:
                await self._context.bot.edit_message_text(
                    chat_id=self._chat_id, message_id=self._message_id, text=text, parse_mode="Markdown",
                )
            except TelegramError:
                pass
        except TelegramError:
            pass

    def mark_done(self, label: str) -> None:
        self._done += 1
        self._recent.append((label, True, None))

    def mark_error(self, label: str, detail: str | None = None) -> None:
        self._errors += 1
        self._recent.append((label, False, _short_error(detail) if detail else None))

    def mark_skip(self, label: str, detail: str | None = None) -> None:
        self._skipped += 1
        self._recent.append((label, None, detail))

_SE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_E_ONLY_RE = re.compile(r"^\s*(\d+)\s*$")


def _title_from_detail(detail: dict | None, fallback: str) -> str:
    if not detail:
        return fallback
    return detail.get("title_uz") or detail.get("title") or fallback


async def _fetch_series_detail(studio: dict, series_id: str) -> dict | None:
    """/content/series/:id/detail -- serialning to'liq ma'lumotini (sarlavha
    va h.k.) oladi. movies uchun _fetch_movie_detail bilan bir xil naqsh."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/series/{series_id}/detail",
                headers=_auth_headers(studio),
            )
    except httpx.HTTPError as e:
        logger.warning("Serial detalini olishda tarmoq xatosi (id=%s): %s", series_id, e)
        return None
    if resp.status_code >= 300:
        logger.warning("Serial detali xato (id=%s): %s %s", series_id, resp.status_code, resp.text[:200])
        return None
    data = resp.json()
    return data.get("series") if isinstance(data, dict) and "series" in data else data


def _resolve_topic_context(update: Update) -> tuple | None:
    """Guruh/topic'dan slug + content_key'ni aniqlaydi.
    Qaytaradi: (studio, slug, chat_id, topic_id, kind, content_id) yoki
    None -- bu guruh/topic botga umuman aloqador emasligini bildiradi
    (bunday holatda jim turish to'g'ri, chunki bot ko'p begona guruhda ham
    bo'lishi mumkin)."""
    message = update.effective_message
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return None
    topic_id = message.message_thread_id
    if not topic_id:
        return None
    slug = get_slug_by_chat_id(chat.id)
    if not slug:
        return None
    content_key = get_content_key_by_topic(slug, topic_id)
    if not content_key:
        return None
    kind, content_id = content_key.split("_", 1)

    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return None

    studio = get_bound_studio(user_id)
    if not studio or studio.get("slug") != slug:
        # Bu guruh aniq shu studiyaga bog'langan -- agar foydalanuvchi asosiy
        # platformada (AfsonaMovieBot) haqiqatan ham shu studiyaga menejer
        # bo'lsa, lekin botda hali "bog'lanmagan" bo'lsa (masalan hech qachon
        # shaxsiy chatda /start bosmagan) -- shu yerning o'zida avtomatik
        # bog'laymiz. Aks holda guruhga video tashlashning o'zi ishlamay,
        # sababi tushunarsiz qolib ketardi.
        candidates = get_manager_studios(user_id)
        match = next((s for s in candidates if s["slug"] == slug), None)
        if not match:
            return None
        bind_user(user_id, match)
        studio = get_bound_studio(user_id)
        if not studio:
            return None

    return studio, slug, chat.id, topic_id, kind, content_id


def _explain_unresolved(update: Update) -> str | None:
    """_resolve_topic_context() None qaytarganda, sababini foydalanuvchiga
    tushuntirish uchun qisqa matn tanlaydi. Agar bu guruh/topic botga umuman
    aloqador bo'lmasa (masalan begona guruh) -- None qaytaradi, chaqiruvchi
    hech narsa yozmasligi kerak."""
    message = update.effective_message
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return None
    topic_id = message.message_thread_id
    if not topic_id:
        return None
    slug = get_slug_by_chat_id(chat.id)
    if not slug:
        return None  # bu guruh botga umuman bog'lanmagan -- jim turamiz
    content_key = get_content_key_by_topic(slug, topic_id)
    if not content_key:
        return (
            "⚠️ Bu topic hali hech qanday film/serialga bog'lanmagan.\n"
            "`/bogla m 45` yoki `/bogla s 123` bilan bog'lang "
            "(ID'ni MiniApp -> Studiya paneli -> kontent ro'yxatidan oling)."
        )
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return None
    candidates = get_manager_studios(user_id)
    if not any(s["slug"] == slug for s in candidates):
        return (
            "⛔ Siz bu studiyaning menejeri sifatida aniqlanmadingiz.\n"
            "Agar menejer bo'lsangiz, Bosh admin bilan bog'laning."
        )
    return None  # boshqa noaniq holat -- jim turamiz


async def bogla_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qo'lda ochilgan (avtomatik yaratilmagan) topic'ni film/serialga bog'laydi.
    Foydalanish: /bogla s 123   (serial, ID=123)
                 /bogla m 45    (film, ID=45)"""
    message = update.effective_message
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup") or not message.message_thread_id:
        await message.reply_text("⚠️ Bu buyruq faqat guruh topic'i ichida ishlaydi.")
        return

    slug = get_slug_by_chat_id(chat.id)
    if not slug:
        await message.reply_text("⚠️ Bu guruh hech qanday studiyaga bog'lanmagan.")
        return

    studio = get_bound_studio(update.effective_user.id) if update.effective_user else None
    if not studio or studio.get("slug") != slug:
        await message.reply_text("⛔ Siz bu studiyaning menejeri sifatida aniqlanmadingiz.")
        return

    parts = (message.text or "").split()
    if len(parts) != 3 or parts[1] not in ("m", "s") or not parts[2].isdigit():
        await message.reply_text(
            "Foydalanish:\n`/bogla s 123` — serial (ID=123)\n`/bogla m 45` — film (ID=45)\n\n"
            "ID'ni MiniApp -> Studiya paneli -> kontent ro'yxatidan bilib olishingiz mumkin.",
            parse_mode="Markdown",
        )
        return
    kind, content_id = parts[1], parts[2]

    if kind == "m":
        detail = await _fetch_movie_detail(studio, content_id)
    else:
        detail = await _fetch_series_detail(studio, content_id)
    if not detail:
        await message.reply_text("❌ Shu ID bilan kontent topilmadi (yoki bu studiyaga tegishli emas).")
        return
    title = _title_from_detail(detail, f"#{content_id}")

    set_topic_id(slug, f"{kind}_{content_id}", message.message_thread_id)
    await message.reply_text(f"✅ Bu topic endi bog'landi: {'📺' if kind == 's' else '🎬'} {title}\n\nEndi video tashlab, /joylash yuborishingiz mumkin.")


async def on_topic_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Topic ichiga tashlangan video(lar)ni navbatga qo'shadi (darhol ishlamaydi)."""
    ctx = _resolve_topic_context(update)
    if not ctx:
        hint = _explain_unresolved(update)
        if hint:
            await update.effective_message.reply_text(hint, parse_mode="Markdown")
        return  # bog'liq bo'lmagan guruh/topic -- jim turamiz
    studio, slug, chat_id, topic_id, kind, content_id = ctx

    message = update.effective_message
    video = message.video or (message.document if message.document and (message.document.mime_type or "").startswith("video/") else None)
    if not video:
        return
    file_id = video.file_id
    caption = (message.caption or "").strip()

    if kind == "m":
        season, episode = 0, 0
    else:
        m = _SE_RE.match(caption)
        if m:
            season, episode = int(m.group(1)), int(m.group(2))
        else:
            m2 = _E_ONLY_RE.match(caption)
            if m2:
                season, episode = 1, int(m2.group(1))  # faqat raqam -> 1-fasl deb olinadi
            else:
                await message.reply_text(
                    "⚠️ Serial qismi uchun caption'ga qism raqamini yozing (masalan: 3), "
                    "yoki bir nechta mavsum bo'lsa \"fasl-qism\" shaklida (masalan: 2-5 = "
                    "2-fasl 5-qism). Bu video navbatga qo'shilmadi."
                )
                return

    ok, err = add_item(slug, topic_id, message.message_id, season, episode, file_id)
    if not ok:
        await message.reply_text(f"❌ {err}")
        return

    queued_count = len(get_queue(slug, topic_id))
    label = "video" if kind == "m" else f"{season}-fasl {episode}-qism"
    await message.reply_text(f"✅ Navbatga qo'shildi: {label} (jami: {queued_count} ta). Tugagach /joylash yuboring.")


async def joylash_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Navbatdagi barcha videolarni ketma-ket qayta ishlaydi va joylaydi."""
    ctx = _resolve_topic_context(update)
    if not ctx:
        hint = _explain_unresolved(update) or (
            "⚠️ Bu buyruq faqat studiyangizga bog'langan guruhning kontent topic'ida ishlaydi."
        )
        await update.effective_message.reply_text(hint, parse_mode="Markdown")
        return
    studio, slug, chat_id, topic_id, kind, content_id = ctx

    # ── Bir vaqtda faqat bitta /joylash: bir xil topic uchun ikkinchi
    # menejer (yoki qo'shaloq bosilgan tugma) navbatni parallel qayta
    # ishlab qo'ymasligi uchun. Aks holda bitta video ikki marta R2'ga
    # yuklanishi va bazaga IKKI MARTA yozilishi (dublikat epizod) mumkin. ──
    lock_key = (slug, topic_id)
    lock = _joylash_locks.setdefault(lock_key, asyncio.Lock())
    if lock.locked():
        await update.effective_message.reply_text(
            "⏳ Bu kontent uchun joylash allaqachon boshqa jarayon tomonidan "
            "bajarilmoqda (ehtimol boshqa menejer /joylash yuborgan). "
            "Iltimos, u tugashini kuting va keyin qayta urinib ko'ring.",
        )
        return

    async with lock:
        await _do_joylash(update, context, studio, slug, chat_id, topic_id, kind, content_id)


# (slug, topic_id) -> asyncio.Lock -- har bir kontent topic'i uchun alohida
# bloklash, turli topic/studio'lar bir-biriga xalaqit bermasdan parallel
# ishlashi mumkin bo'lishi uchun.
_joylash_locks: dict[tuple[str, int], asyncio.Lock] = {}


def active_joylash_count() -> int:
    """Hozirgi vaqtda /joylash jarayoni bajarilayotgan (slug, topic) juftlar
    soni -- /status kabi diagnostika uchun. Bo'shab qolgan (endi hech kim
    ishlatmayotgan) lock'lar hisoblanmaydi."""
    return sum(1 for lock in _joylash_locks.values() if lock.locked())


async def _do_joylash(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    studio: dict, slug: str, chat_id: int, topic_id: int, kind: str, content_id: str,
):
    queue = get_queue(slug, topic_id)
    if not queue:
        await update.effective_message.reply_text("ℹ️ Navbatda video yo'q.")
        return

    detail = None
    title = "Kontent"
    if kind == "m":
        detail = await _fetch_movie_detail(studio, content_id)
    else:
        detail = await _fetch_series_detail(studio, content_id)
    title = _title_from_detail(detail, title)

    # ── Oldindan tekshiruv: bazada allaqachon video bor bo'lgan
    # film/qismlarni aniqlaymiz -- ularni Telegram'dan yuklab olishning
    # o'zi shart emas, shuning uchun navbatdan darhol o'tkazib yuboramiz. ──
    movie_already_has_video = False
    existing_episodes: set[tuple[int, int]] = set()
    if kind == "m":
        movie_already_has_video = bool(
            detail and (detail.get("r2Url") or detail.get("videoUrl") or detail.get("hasVideo"))
        )
    else:
        eps = await _fetch_episodes(studio, content_id)
        if eps:
            for ep in eps:
                if ep.get("hasVideo") and ep.get("season") is not None and ep.get("episode") is not None:
                    existing_episodes.add((int(ep["season"]), int(ep["episode"])))

    status = await update.effective_message.reply_text(
        _render_progress(title=title, kind=kind, total=len(queue), done=0, errors=0,
                          current_item=None, stage=None, recent=[], elapsed=0),
        parse_mode="Markdown",
    )
    painter = _ProgressPainter(context, chat_id, status.message_id, title=title, kind=kind, total=len(queue))

    for item in queue:
        label = _item_label(kind, title, item)

        already_exists = (
            movie_already_has_video if kind == "m"
            else (item.get("season"), item.get("episode")) in existing_episodes
        )
        if already_exists:
            painter.mark_skip(label, "Bazada allaqachon mavjud")
            await painter.update(current_item=None, stage=None)
            remove_item(slug, topic_id, item["message_id"])
            continue

        error_text = await _process_one_item(
            context=context, painter=painter, studio=studio, slug=slug, chat_id=chat_id,
            kind=kind, content_id=content_id, title=title, item=item,
        )
        if error_text:
            # Bitta xatodan keyin darhol 1 marta avtomatik qayta urinamiz --
            # ko'p xatolar vaqtinchalik tarmoq/flood muammolaridan bo'ladi.
            await painter.update(
                current_item=item, stage="download", force=True,
                retry_note=f"⚠️ Xato: {_short_error(error_text)}\n│  🔁 Qayta urinilmoqda...",
            )
            await asyncio.sleep(3)
            error_text_2 = await _process_one_item(
                context=context, painter=painter, studio=studio, slug=slug, chat_id=chat_id,
                kind=kind, content_id=content_id, title=title, item=item,
            )
            if error_text_2:
                painter.mark_error(label, error_text_2)
            else:
                painter.mark_done(label)
        else:
            painter.mark_done(label)
        await painter.update(current_item=None, stage=None)

        # Faqat hozir qayta ishlangan itemni navbatdan olib tashlaymiz --
        # butun navbatni tozalamaymiz. Aks holda, /joylash ishlab turgan
        # paytda boshqa menejer topic'ga yangi video tashlasa, u navbatga
        # ulgurib qo'shilgan bo'lsa ham oxirida bekitdan yo'qolib ketadi.
        remove_item(slug, topic_id, item["message_id"])

    await painter.finish()


async def _process_one_item(
    *, context, painter: "_ProgressPainter", studio: dict, slug: str, chat_id: int,
    kind: str, content_id: str, title: str, item: dict,
) -> str | None:
    """Bitta videoni yuklab-joylaydi. Muvaffaqiyatli bo'lsa None, aks holda
    xato matnini qaytaradi (chaqiruvchi tomon buni ko'rsatish/qayta urinish
    uchun ishlatadi)."""
    dl_path = None
    prepared_path = None

    async def _note_retry(attempt, max_attempts, exc, wait):
        await painter.update(
            current_item=item, stage=stage_now[0], force=True,
            retry_note=(
                f"⚠️ Tarmoq xatosi: {_short_error(str(exc) or repr(exc))}\n"
                f"│  🔁 Qayta urinish {attempt}/{max_attempts - 1} — {wait:.0f}s dan keyin..."
            ),
        )

    stage_now = [None]  # yopiq (closure) o'zgaruvchi -- joriy bosqichni retry xabariga uzatish uchun

    try:
        # ── 1. Telegramdan yuklab olish ──────────────────────────────────
        stage_now[0] = "download"
        await painter.update(current_item=item, stage="download")

        async def _download():
            tg_file = await context.bot.get_file(item["file_id"], read_timeout=120, connect_timeout=30)
            path = make_temp_path("mp4")
            await tg_file.download_to_drive(
                path, read_timeout=1800, connect_timeout=30, write_timeout=1800,
            )
            return path

        dl_path = await _run_with_retry("download", _download, on_retry=_note_retry)

        # ── 2. Formatga tayyorlash (ffmpeg) -- lokal amal, tarmoqqa bog'liq emas ──
        await painter.update(current_item=item, stage="prepare")
        prepared_path, _changed = await _run_in_executor(prepare_for_telegram, dl_path)

        # ── 3. R2 bulutiga yuklash ────────────────────────────────────────
        stage_now[0] = "upload"
        await painter.update(current_item=item, stage="upload")
        if kind == "m":
            filename = f"{title}.mp4"
            kind_path, caption_label = "movies", f"🎬 {title}"
        else:
            filename = f"{title}_S{item['season']}E{item['episode']}.mp4"
            kind_path = "series"
            caption_label = f"📺 {title}\n{item['season']}-fasl {item['episode']}-qism"

        async def _upload():
            return await _presign_and_put(studio, prepared_path, kind_path, filename)

        public_url = await _run_with_retry("upload", _upload, on_retry=_note_retry)
        if not public_url or public_url == "cancelled":
            return "R2 bulutiga yuklashda xato bo'ldi"

        # ── 4. Studiya bazasiga ro'yxatga olish ──────────────────────────
        stage_now[0] = "register"
        await painter.update(current_item=item, stage="register")

        async def _register():
            async with httpx.AsyncClient(timeout=60) as client:
                if kind == "m":
                    return await client.patch(
                        f"{STUDIO_API_BASE}/studios/{slug}/content/movies/{content_id}",
                        headers=_auth_headers(studio),
                        json={"r2Url": public_url},
                    )
                return await client.post(
                    f"{STUDIO_API_BASE}/studios/{slug}/content/series/{content_id}/episodes",
                    headers=_auth_headers(studio),
                    json={"season": item["season"], "episode": item["episode"], "r2Url": public_url},
                )

        resp = await _run_with_retry("register", _register, on_retry=_note_retry)

        if resp.status_code >= 300:
            body_preview = _short_error(resp.text, limit=200) if resp.text else "(bo'sh javob)"
            err = f"Bazaga yozishda xato: HTTP {resp.status_code} — {body_preview}"
            logger.warning("Ro'yxatga olishda xato: %s %s", resp.status_code, body_preview)
            return err

        new_caption = caption_label + f"\n\n🔗 Video: {public_url}"
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id, message_id=item["message_id"], caption=new_caption,
            )
        except TelegramError as e:
            # Sarlavhani tahrirlab bo'lmasligi (masalan xabar juda eski) --
            # video allaqachon muvaffaqiyatli joylangan, shuning uchun bu
            # xato hisoblanmaydi, faqat log qilinadi.
            logger.warning("Caption tahrirlashda xato (message_id=%s): %s", item["message_id"], e)

        return None
    except _RETRYABLE_EXC as e:
        # Barcha qayta urinishlar ham tugagan -- vaqtinchalik tarmoq xatosi
        # sifatida aniq belgilaymiz, shunda tashqi (joylash_command) darajadagi
        # qo'shimcha qayta urinish ham ma'noli bo'ladi.
        logger.warning(
            "Tarmoq xatosi barcha urinishlardan keyin ham davom etdi (message_id=%s): %r",
            item["message_id"], e,
        )
        return f"Tarmoq xatosi (barcha urinishlar tugadi): {e!r}"
    except RetryAfter as e:
        # Telegram flood-control: aniq ko'rsatilgan vaqtni kutib, keyin
        # xato sifatida qaytaramiz -- tashqi qayta urinish darajasi buni yana
        # bir bor urinib ko'radi.
        logger.warning("Flood control (message_id=%s): %ss kutish talab qilindi", item["message_id"], e.retry_after)
        await asyncio.sleep(e.retry_after + 1)
        return f"Telegram flood-control: {e.retry_after}s kutish talab qilindi"
    except TelegramError as e:
        logger.warning("Topic video qayta ishlashda xato (message_id=%s): %s", item["message_id"], e)
        return f"Telegram xatosi: {e}"
    except Exception as e:
        logger.exception("Kutilmagan xato (message_id=%s)", item["message_id"])
        return f"Kutilmagan xato: {e!r}"
    finally:
        for p in (dl_path, prepared_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
