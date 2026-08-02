"""
Studiya bazasida allaqachon mavjud bo'lgan film/seriallarni ham guruh
topic'lariga "orqaga qarab" (backfill) joylash: /kontent_toldirish.

Muhim tamoyillar:
  - Flood-limitga tegib qolmaslik uchun sekin-asta, orada pauza bilan ishlaydi.
  - Davom ettiriladigan (resumable): studio_topics.json va studio_posted.json
    orqali kuzatiladi.
  - HAR SAFAR TEKSHIRADI (statik keshga ko'r-ko'rona ishonmaydi):
      * Har bir kino/epizod uchun oldin joylangan xabar hali ham
        guruhda mavjudligi va VIDEO (document emas) ekanligi tekshiriladi.
        Yo'q bo'lsa yoki noto'g'ri formatda bo'lsa -- qayta yuboriladi.
      * Topic (mavzu) Telegramda qo'lda o'chirilgan bo'lsa, keyingi safar
        avtomatik qaytadan ochiladi va o'sha kontent qayta joylanadi.
  - Video studiyaning R2 bulutidan yuklab olinadi, so'ng mavjud
    `send_file` orqali (hajmiga qarab PTB/Pyrogram/R2) topicga TG video
    sifatida joylanadi.
"""

import asyncio
import logging
import os
import tempfile

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

from config import STUDIO_API_BASE
from utils.studio_auth import get_bound_studio
from utils.studio_group import (
    get_group, is_episode_posted, mark_episode_posted,
    get_posted_message_id, clear_topic_id,
)
from handlers.studio_group import ensure_topic, quality_label
from handlers.studio_content import _fetch_list, _fetch_episodes
from utils.sender import send_file, PYROGRAM_LIMIT, PYROGRAM_PREMIUM_LIMIT
from utils.ffmpeg_utils import get_video_resolution, prepare_for_telegram, _run_in_executor

logger = logging.getLogger(__name__)

_THROTTLE_SECONDS = 2.0
_running: set[str] = set()  # slug'lar -- bir vaqtda ikkita backfill yurmasligi uchun
_cancelled: set[str] = set()  # bekor qilish so'ralgan slug'lar

# MUHIM: /tmp odatda tmpfs (RAM ustida, ~1.9GB) bo'ladi -- katta video fayllar
# yozilganda server RAM'ini yeb qo'yishi yoki joy yetmasligi mumkin.
# Shuning uchun haqiqiy diskdagi papkadan foydalanamiz.
_DOWNLOAD_DIR = os.environ.get("BACKFILL_TMP_DIR", "/opt/videobot/tmp")
os.makedirs(_DOWNLOAD_DIR, exist_ok=True)

_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)

# Telegram xato matnida shu so'zlardan biri bo'lsa -- topic (forum thread)
# o'chirilgan/yaroqsiz degani, oddiy yuborish xatosi emas.
_THREAD_ERROR_HINTS = (
    "thread not found",
    "message thread not found",
    "topic_deleted",
    "topic deleted",
    "TOPIC_ID_INVALID",
)


def _is_thread_error(e: TelegramError) -> bool:
    msg = str(e).lower()
    return any(hint.lower() in msg for hint in _THREAD_ERROR_HINTS)


async def _safe_edit(message, text, **kwargs):
    """message.edit_text() ni RetryAfter/TelegramError'dan himoyalab chaqiradi.
    Flood-control tegsa -- bitta marta kutib qayta urinadi, aks holda
    xatoni yutib, jarayonni to'xtatib qo'ymaydi."""
    try:
        await message.edit_text(text, **kwargs)
    except RetryAfter as e:
        wait = min(e.retry_after, 30) + 1
        logger.warning("Flood control (edit_text): %.0fs kutilmoqda", e.retry_after)
        await asyncio.sleep(wait)
        try:
            await message.edit_text(text, **kwargs)
        except TelegramError as e2:
            logger.warning("Qayta urinishda ham xato: %s", e2)
    except TelegramError as e:
        logger.warning("edit_text xatosi: %s", e)


async def cancel_backfill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    studio = get_bound_studio(update.effective_user.id)
    if not studio:
        await message.reply_text("⛔ Siz studiya menejeri sifatida aniqlanmadingiz.")
        return

    if studio["slug"] not in _running:
        await message.reply_text("ℹ️ Hozir sizning studiyangiz uchun ishlayotgan backfill jarayoni yo'q.")
        return

    _cancelled.add(studio["slug"])
    await message.reply_text(
        "🛑 To'xtatish so'raldi — joriy video yuklanishi tugagach jarayon to'xtaydi.\n"
        "Keyinroq /kontent_toldirish bilan qolganidan davom ettirishingiz mumkin."
    )


async def backfill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    studio = get_bound_studio(update.effective_user.id)
    if not studio:
        await message.reply_text("⛔ Siz studiya menejeri sifatida aniqlanmadingiz.")
        return

    group = get_group(studio["slug"])
    if not group:
        await message.reply_text(
            "⚠️ Avval guruhingizni bog'lang: guruhda /guruh_biriktirish yozing."
        )
        return

    # Bir foydalanuvchi buyruqni qayta-qayta bosib yubormasligi uchun --
    # aks holda har safar 1-2 tadan himoyasiz edit_text chaqiriladi va
    # tezda Telegram flood-control'iga (429 RetryAfter) tegib qolamiz.
    if studio["slug"] in _running:
        await message.reply_text("⏳ Backfill allaqachon shu studiya uchun ishlamoqda.")
        return

    status = await message.reply_text("🔎 Bazadagi kontent soni tekshirilmoqda...")
    movies_data = await _fetch_list(studio, "m", 1, "")
    series_data = await _fetch_list(studio, "s", 1, "")
    if movies_data is None or series_data is None:
        await _safe_edit(status, "❌ Kontent sonini olishda xatolik. Birozdan so'ng qayta urinib ko'ring.")
        return

    movies_total = movies_data.get("total", 0)
    series_total = series_data.get("total", 0)

    await _safe_edit(
        status,
        f"📊 Bazada topildi:\n🎬 {movies_total} ta film\n📺 {series_total} ta serial\n\n"
        f"Bu jarayon \"{group['title']}\" guruhida har biriga alohida mavzu ochib, "
        "videolarni ketma-ket joylaydi. Har bir kontent uchun avval joylangan "
        "xabar hali mavjudligi va to'g'ri formatda ekani tekshiriladi -- yo'q "
        "yoki noto'g'ri bo'lsa qayta yuboriladi. Kontent ko'p bo'lsa, uzoq davom "
        "etishi mumkin (flood-limitga tegmaslik uchun sekin ishlaydi) va istalgan "
        "vaqt /kontent_toldirish bilan qayta ishga tushirilsa, davom ettiradi.\n\n"
        "Boshlaymizmi?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Ha, boshlash", callback_data="backfill_go")],
            [InlineKeyboardButton("❌ Bekor qilish", callback_data="backfill_no")],
        ]),
    )


async def handle_backfill_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, go: bool):
    query = update.callback_query
    await query.answer()

    studio = get_bound_studio(update.effective_user.id)
    if not studio:
        await query.edit_message_text("⛔ Studiya sifatida aniqlanmadingiz.")
        return

    if not go:
        await query.edit_message_text("Bekor qilindi.")
        return

    if studio["slug"] in _running:
        await query.edit_message_text("⏳ Backfill allaqachon shu studiya uchun ishlamoqda.")
        return

    await query.edit_message_text("🚀 Boshlandi... Progress shu xabarda yangilanib turadi.")
    context.application.create_task(
        _run_backfill(context, studio, query.message.chat_id, query.message.message_id)
    )


async def _edit_progress(context, chat_id, message_id, text):
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
    except RetryAfter as e:
        logger.warning("Flood control (progress edit): %.0fs kutilmoqda", e.retry_after)
        await asyncio.sleep(min(e.retry_after, 30) + 1)
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
        except TelegramError:
            pass
    except TelegramError:
        pass


async def _download_to_temp(url: str) -> str | None:
    fd, path = tempfile.mkstemp(suffix=".mp4", dir=_DOWNLOAD_DIR)
    os.close(fd)
    ok = False
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 300:
                    return None
                with open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        f.write(chunk)
        ok = True
        return path
    except Exception as e:
        logger.warning("Video yuklab olishda xato: %s", e)
        return None
    finally:
        # Muvaffaqiyatsiz/qisman yozilgan faylni diskda qoldirmaymiz.
        # Muvaffaqiyatli holatda fayl chaqiruvchi tomonidan ishlatilgach o'chiriladi.
        if not ok:
            try:
                os.remove(path)
            except OSError:
                pass


def _video_url(item: dict) -> str | None:
    return (
        item.get("r2Url") or item.get("videoUrl") or item.get("url")
        or item.get("r2_url") or item.get("video_url")
        or None
    )


async def _fetch_movie_detail(studio: dict, movie_id: str) -> dict | None:
    """/movies ro'yxat endpointi r2Url/videoUrl qaytarmaydi (faqat hasVideo
    bayrog'i beradi) -- shuning uchun bitta filmning to'liq ma'lumotini
    (r2Url bilan) alohida so'rov bilan olamiz."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/movies/{movie_id}",
                headers={"Authorization": f"Bearer {studio['api_token']}"},
            )
    except httpx.HTTPError as e:
        logger.warning("Film detalini olishda tarmoq xatosi (id=%s): %s", movie_id, e)
        return None
    if resp.status_code >= 300:
        logger.warning("Film detali xato (id=%s): %s %s", movie_id, resp.status_code, resp.text[:200])
        return None
    data = resp.json()
    # Endpoint {"movie": {...}} shaklida qaytaradi -- ichidagi obyektni olamiz.
    return data.get("movie") if isinstance(data, dict) and "movie" in data else data


async def _check_posted_message(context, chat_id: int, message_id: int, probe_chat_id: int) -> str:
    """Oldin joylangan xabar hali guruh topicida mavjudligini va video
    (document emas) ekanligini tekshiradi -- guruhga hech narsa yozmasdan,
    xabarni admin'ning shaxsiy (progress) chatiga forward qilib, darhol o'chiradi.

    Qaytaradi:
      "ok"           -- xabar mavjud va video sifatida joylangan
      "wrong_format" -- xabar mavjud, lekin video emas (masalan document) -- qayta yuborish kerak
      "missing"      -- xabar (yoki uni o'z ichiga olgan topic) o'chirilgan -- qayta yuborish kerak
    """
    try:
        fwd = await context.bot.forward_message(
            chat_id=probe_chat_id, from_chat_id=chat_id, message_id=message_id,
            disable_notification=True,
        )
    except TelegramError:
        return "missing"
    try:
        await context.bot.delete_message(chat_id=probe_chat_id, message_id=fwd.message_id)
    except TelegramError:
        pass
    return "ok" if fwd.video else "wrong_format"


async def _needs_resend(context, slug: str, content_key: str, sub_key: str, chat_id: int, probe_chat_id: int) -> tuple[bool, int | None]:
    """Shu kontent qismini qayta yuborish kerakmi -- tekshiradi.
    Qaytaradi: (kerakmi, eski_message_id_agar_bor_bolsa_va_ochirish_kerak_bolsa)"""
    if not is_episode_posted(slug, content_key, sub_key):
        return True, None

    stored_id = get_posted_message_id(slug, content_key, sub_key)
    if not stored_id:
        # Eski yozuv (message_id bilmaymiz, funksiya qo'shilishidan oldin
        # yuborilgan -- odatda document sifatida). Xavfsizroq: qayta yuboramiz.
        return True, None

    status = await _check_posted_message(context, chat_id, stored_id, probe_chat_id)
    if status == "ok":
        return False, None
    # "missing" yoki "wrong_format" -- qayta yuborish kerak.
    # "wrong_format" holida eski document xabarni tozalab qo'yamiz.
    old_id = stored_id if status == "wrong_format" else None
    return True, old_id


async def _send_one_video(context, message, chat_id, topic_id, url, filename, header, link):
    """Video yuklab, guruh topicga video sifatida joylaydi.

    Qaytaradi:
      int            -- muvaffaqiyatli joylandi, qiymat -- Telegram message_id
      "THREAD_GONE"  -- topic o'chirilgan; chaqiruvchi topic'ni qayta yaratib,
                         qaytadan chaqirishi kerak
      None           -- boshqa sabab bilan muvaffaqiyatsiz
    """
    tmp_path = await _download_to_temp(url)
    if not tmp_path:
        await post_text_to_topic_raw(context, chat_id, topic_id, header + "\n\n⚠️ Videoni yuklab olishda xatolik bo'ldi.")
        return None

    message_id = None
    thread_gone = False
    try:
        file_size = os.path.getsize(tmp_path)
        _w, _h = get_video_resolution(tmp_path)
        _q = quality_label(_h)
        caption = header
        if _q:
            caption += f"\n▸ {_q}"
        if link:
            caption += f"\n\n🔗 Video: {link}"

        # 2 GB dan katta fayllarni oddiy Bot API orqali yuborib bo'lmaydi.
        # send_file bu holda avtomatik R2/Gofile'ga *qayta* yuklab, natijani
        # faqat admin'ning shaxsiy chatiga yozadi -- topicga hech narsa
        # tushmaydi. Fayl allaqachon R2'da (`link`) turgani uchun uni qayta
        # yuklashning ma'nosi yo'q -- Premium userbot ulanmagan bo'lsa,
        # to'g'ridan-to'g'ri mavjud havolani matn sifatida joylaymiz.
        if file_size > PYROGRAM_LIMIT:
            premium_ok = False
            if file_size <= PYROGRAM_PREMIUM_LIMIT:
                try:
                    from handlers.save_restricted import get_user_client, is_user_premium
                    _client = await get_user_client()
                    premium_ok = bool(_client) and await is_user_premium()
                except Exception as e:
                    logger.warning("Premium userbot tekshiruvida xato: %s", e)
            if not premium_ok:
                await post_text_to_topic_raw(
                    context, chat_id, topic_id,
                    caption + "\n\nℹ️ Fayl 2 GB dan katta -- Telegram orqali video "
                    "sifatida yuborib bo'lmaydi, yuqoridagi havoladan ko'rish mumkin.",
                )
                return None

        # Termux/Kompyuter yuklash skriptidagi tekshiruv bilan bir xil:
        # fayl allaqachon mp4/h264/yuv420p/aac bo'lsa faqat faststart
        # qo'llaniladi (tez), aks holda to'liq qayta kodlanadi -- shunda
        # Telegram'da video darhol (oxirigacha yuklanmasdan) ijro etiladi.
        send_path, _prepared = await _run_in_executor(prepare_for_telegram, tmp_path)

        for attempt in range(3):
            try:
                message_id = await send_file(
                    message, send_path, filename, caption=caption, context=context,
                    target_chat_id=chat_id, message_thread_id=topic_id,
                    force_upload_mode="video",
                )
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramError as e:
                logger.warning("send_file xato: %s", e)
                if _is_thread_error(e):
                    thread_gone = True
                break
        if thread_gone:
            return "THREAD_GONE"
        if not message_id:
            await post_text_to_topic_raw(context, chat_id, topic_id, caption + "\n\n⚠️ Videoni joylashda xatolik bo'ldi.")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        if 'send_path' in locals() and send_path != tmp_path:
            try:
                os.remove(send_path)
            except OSError:
                pass
    return message_id


async def _send_with_topic_healing(context, studio, kind, content_id, title, message, chat_id, topic_id, url, filename, header, link):
    """_send_one_video'ni chaqiradi; agar topic o'chirilgan bo'lsa, uni qaytadan
    yaratib, bir marta qayta urinadi. Qaytaradi: (message_id | None, yangi_topic_id)."""
    result = await _send_one_video(context, message, chat_id, topic_id, url, filename, header, link)
    if result != "THREAD_GONE":
        return (result if isinstance(result, int) else None), topic_id

    slug = studio["slug"]
    content_key = f"{kind}_{content_id}"
    clear_topic_id(slug, content_key)
    dest = await ensure_topic(context, studio, kind, content_id, title)
    if not dest:
        return None, topic_id
    new_chat_id, new_topic_id = dest
    result2 = await _send_one_video(context, message, new_chat_id, new_topic_id, url, filename, header, link)
    return (result2 if isinstance(result2, int) else None), new_topic_id


async def post_text_to_topic_raw(context, chat_id, topic_id, text):
    try:
        await context.bot.send_message(chat_id=chat_id, message_thread_id=topic_id, text=text, parse_mode="Markdown")
    except TelegramError:
        try:
            await context.bot.send_message(chat_id=chat_id, message_thread_id=topic_id, text=text)
        except TelegramError:
            pass


class _Cancelled(Exception):
    pass


def _check_cancel(slug: str) -> None:
    if slug in _cancelled:
        raise _Cancelled()


async def _run_backfill(context: ContextTypes.DEFAULT_TYPE, studio: dict, progress_chat_id: int, progress_msg_id: int):
    slug = studio["slug"]
    _running.add(slug)

    done_movies = done_series = done_eps = errors = 0
    try:
        group = get_group(slug)
        if not group:
            await _edit_progress(context, progress_chat_id, progress_msg_id, "❌ Guruh bog'lanishi topilmadi.")
            return
        chat_id = group["chat_id"]

        # send_file status yangilanishlarini shu (jim) xabarga yozadi -- asosiy
        # video esa target_chat_id/message_thread_id orqali guruh topicga boradi.
        quiet_msg = await context.bot.send_message(chat_id=progress_chat_id, text="⏳ Ishga tushirilmoqda...")
    except Exception as e:
        logger.warning("Backfill boshlanishida xato: %s", e)
        _running.discard(slug)
        return

    try:
        # ── Filmlar ──────────────────────────────────────────────────────
        page = 1
        while True:
            data = await _fetch_list(studio, "m", page, "")
            if not data:
                break
            items = data.get("movies") or []
            for item in items:
                _check_cancel(slug)
                mid = str(item.get("id"))
                title = item.get("title") or f"Film #{mid}"
                year = f" ({item['year']})" if item.get("year") else ""
                content_key = f"m_{mid}"

                dest = await ensure_topic(context, studio, "m", mid, f"{title}{year}")
                if not dest:
                    errors += 1
                    continue
                chat_id, topic_id = dest

                if item.get("hasVideo") or _video_url(item):
                    resend, old_msg_id = await _needs_resend(context, slug, content_key, "main", chat_id, progress_chat_id)
                    if old_msg_id:
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
                        except TelegramError:
                            pass
                    if resend:
                        url = _video_url(item)
                        if not url:
                            # Ro'yxat javobida URL yo'q -- filmning to'liq
                            # detalini alohida so'rab, r2Url'ni shu yerdan olamiz.
                            detail = await _fetch_movie_detail(studio, mid)
                            url = _video_url(detail) if detail else None
                            if not url:
                                logger.warning(
                                    "Film '%s' (id=%s): hasVideo=True, lekin detail so'rovidan "
                                    "keyin ham video URL topilmadi. Detail keys: %s",
                                    title, mid, list(detail.keys()) if detail else None,
                                )
                        if url:
                            header = f"🎬 {title}{year}"
                            msg_id, topic_id = await _send_with_topic_healing(
                                context, studio, "m", mid, f"{title}{year}",
                                quiet_msg, chat_id, topic_id, url, f"{title}.mp4", header, url,
                            )
                            if msg_id:
                                mark_episode_posted(slug, content_key, "main", msg_id)
                            else:
                                errors += 1
                            await asyncio.sleep(_THROTTLE_SECONDS)
                        else:
                            errors += 1
                elif not item.get("hasVideo") and not _video_url(item):
                    await post_text_to_topic_raw(context, chat_id, topic_id, f"🎬 {title}{year}\n⚠️ Video hali yuklanmagan.")
                done_movies += 1
                if done_movies % 3 == 0:
                    await _edit_progress(
                        context, progress_chat_id, progress_msg_id,
                        f"⏳ Filmlar: {done_movies}/{data.get('total', 0)} | Seriallar: {done_series} | Qismlar: {done_eps}",
                    )
            if not data.get("hasMore"):
                break
            page += 1

        # ── Seriallar ────────────────────────────────────────────────────
        page = 1
        while True:
            data = await _fetch_list(studio, "s", page, "")
            if not data:
                break
            items = data.get("series") or []
            for item in items:
                _check_cancel(slug)
                sid = str(item.get("id"))
                title = item.get("title") or f"Serial #{sid}"
                year = f" ({item['year']})" if item.get("year") else ""
                content_key = f"s_{sid}"

                dest = await ensure_topic(context, studio, "s", sid, f"{title}{year}")
                if not dest:
                    errors += 1
                    continue
                chat_id, topic_id = dest

                episodes = await _fetch_episodes(studio, sid) or []
                for ep in episodes:
                    _check_cancel(slug)
                    ep_key = f"{ep.get('season')}x{ep.get('episode')}"
                    if not ep.get("hasVideo"):
                        continue
                    url = _video_url(ep)
                    if not url:
                        continue

                    resend, old_msg_id = await _needs_resend(context, slug, content_key, ep_key, chat_id, progress_chat_id)
                    if old_msg_id:
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
                        except TelegramError:
                            pass
                    if not resend:
                        continue

                    header = f"📺 {title}{year} — {ep.get('season')}-fasl, {ep.get('episode')}-qism"
                    filename = f"{title}_S{ep.get('season')}E{ep.get('episode')}.mp4"
                    msg_id, topic_id = await _send_with_topic_healing(
                        context, studio, "s", sid, f"{title}{year}",
                        quiet_msg, chat_id, topic_id, url, filename, header, url,
                    )
                    if msg_id:
                        mark_episode_posted(slug, content_key, ep_key, msg_id)
                        done_eps += 1
                    else:
                        errors += 1
                    await asyncio.sleep(_THROTTLE_SECONDS)

                done_series += 1
                await _edit_progress(
                    context, progress_chat_id, progress_msg_id,
                    f"⏳ Filmlar: {done_movies} | Seriallar: {done_series}/{data.get('total', 0)} | Qismlar: {done_eps}",
                )
            if not data.get("hasMore"):
                break
            page += 1

        await _edit_progress(
            context, progress_chat_id, progress_msg_id,
            f"✅ Backfill tugadi!\n🎬 Filmlar: {done_movies}\n📺 Seriallar: {done_series}\n"
            f"🎞 Qismlar: {done_eps}\n⚠️ Xatolar: {errors}",
        )
    except _Cancelled:
        await _edit_progress(
            context, progress_chat_id, progress_msg_id,
            f"🛑 Backfill bekor qilindi.\n🎬 Filmlar: {done_movies}\n📺 Seriallar: {done_series}\n"
            f"🎞 Qismlar: {done_eps}\n\nKeyinroq /kontent_toldirish bilan davom ettirishingiz mumkin.",
        )
    except Exception as e:
        logger.exception("Backfill jarayonida kutilmagan xato: %s", e)
        await _edit_progress(context, progress_chat_id, progress_msg_id, f"❌ Backfill to'xtadi: {e}\nQayta /kontent_toldirish bilan davom ettirishingiz mumkin.")
    finally:
        _running.discard(slug)
        _cancelled.discard(slug)
