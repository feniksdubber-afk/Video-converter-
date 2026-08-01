"""
Studiya bazasida allaqachon mavjud bo'lgan film/seriallarni ham guruh
topic'lariga "orqaga qarab" (backfill) joylash: /kontent_toldirish.

Muhim tamoyillar:
  - Flood-limitga tegib qolmaslik uchun sekin-asta, orada pauza bilan ishlaydi.
  - Davom ettiriladigan (resumable): allaqachon topic ochilgan/video
    joylangan elementlar qayta ishlanmaydi -- studio_topics.json va
    studio_posted.json orqali kuzatiladi.
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

from utils.studio_auth import get_bound_studio
from utils.studio_group import get_group, is_episode_posted, mark_episode_posted
from handlers.studio_group import ensure_topic, post_text_to_topic, quality_label
from handlers.studio_content import _fetch_list, _fetch_episodes
from utils.sender import send_file
from utils.ffmpeg_utils import get_video_resolution

logger = logging.getLogger(__name__)

_THROTTLE_SECONDS = 2.0
_running: set[str] = set()  # slug'lar -- bir vaqtda ikkita backfill yurmasligi uchun
_cancelled: set[str] = set()  # bekor qilish so'ralgan slug'lar


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

    status = await message.reply_text("🔎 Bazadagi kontent soni tekshirilmoqda...")
    movies_data = await _fetch_list(studio, "m", 1, "")
    series_data = await _fetch_list(studio, "s", 1, "")
    if movies_data is None or series_data is None:
        await status.edit_text("❌ Kontent sonini olishda xatolik. Birozdan so'ng qayta urinib ko'ring.")
        return

    movies_total = movies_data.get("total", 0)
    series_total = series_data.get("total", 0)

    await status.edit_text(
        f"📊 Bazada topildi:\n🎬 {movies_total} ta film\n📺 {series_total} ta serial\n\n"
        f"Bu jarayon \"{group['title']}\" guruhida har biriga alohida mavzu ochib, "
        "videolarni ketma-ket joylaydi. Kontent ko'p bo'lsa, uzoq davom etishi mumkin "
        "(flood-limitga tegmaslik uchun sekin ishlaydi) va istalgan vaqt "
        "/kontent_toldirish bilan qayta ishga tushirilsa, faqat qolganlarini davom ettiradi.\n\n"
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
    except TelegramError:
        pass


async def _download_to_temp(url: str) -> str | None:
    try:
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 300:
                    os.remove(path)
                    return None
                with open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        f.write(chunk)
        return path
    except Exception as e:
        logger.warning("Video yuklab olishda xato: %s", e)
        return None


def _video_url(item: dict) -> str | None:
    return item.get("r2Url") or item.get("videoUrl") or item.get("url") or None


def _hls_url(item: dict) -> str | None:
    return item.get("hlsUrl") or item.get("hls_url") or item.get("m3u8Url") or item.get("masterPlaylistUrl") or None


def _links_block(item: dict) -> str:
    lines = []
    r2 = _video_url(item)
    hls = _hls_url(item)
    if r2:
        lines.append(f"🔗 R2: {r2}")
    if hls:
        lines.append(f"📡 HLS: {hls}")
    return ("\n\n" + "\n".join(lines)) if lines else ""


async def _send_one_video(context, message, chat_id, topic_id, url, filename, caption):
    tmp_path = await _download_to_temp(url)
    if not tmp_path:
        await post_text_to_topic_raw(context, chat_id, topic_id, caption + "\n\n⚠️ Videoni yuklab olishda xatolik bo'ldi.")
        return
    try:
        _w, _h = get_video_resolution(tmp_path)
        _q = quality_label(_h)
        if _q:
            caption = caption + f"\n🖼 Sifat: {_q}"
        for attempt in range(3):
            try:
                await send_file(
                    message, tmp_path, filename, caption=caption, context=context,
                    target_chat_id=chat_id, message_thread_id=topic_id,
                )
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramError as e:
                logger.warning("send_file xato: %s", e)
                break
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


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
                dest = await ensure_topic(context, studio, "m", mid, f"{title}{year}")
                if not dest:
                    errors += 1
                    continue
                _, topic_id = dest
                if not is_episode_posted(slug, f"m_{mid}", "main") and (item.get("hasVideo") or _video_url(item)):
                    url = _video_url(item)
                    if url:
                        await _send_one_video(
                            context, quiet_msg, chat_id, topic_id, url,
                            f"{title}.mp4", f"🎬 *{title}{year}*{_links_block(item)}",
                        )
                        mark_episode_posted(slug, f"m_{mid}", "main")
                        await asyncio.sleep(_THROTTLE_SECONDS)
                elif not item.get("hasVideo") and not _video_url(item):
                    await post_text_to_topic_raw(context, chat_id, topic_id, f"🎬 *{title}{year}*\n⚠️ Video hali yuklanmagan.")
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
                dest = await ensure_topic(context, studio, "s", sid, f"{title}{year}")
                if not dest:
                    errors += 1
                    continue
                _, topic_id = dest

                episodes = await _fetch_episodes(studio, sid) or []
                for ep in episodes:
                    _check_cancel(slug)
                    ep_key = f"{ep.get('season')}x{ep.get('episode')}"
                    if is_episode_posted(slug, f"s_{sid}", ep_key):
                        continue
                    if not ep.get("hasVideo"):
                        continue
                    url = _video_url(ep)
                    if not url:
                        continue
                    caption = f"📺 *{title}{year}*\n{ep.get('season')}-fasl {ep.get('episode')}-qism{_links_block(ep)}"
                    await _send_one_video(
                        context, quiet_msg, chat_id, topic_id, url,
                        f"{title}_S{ep.get('season')}E{ep.get('episode')}.mp4", caption,
                    )
                    mark_episode_posted(slug, f"s_{sid}", ep_key)
                    done_eps += 1
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
