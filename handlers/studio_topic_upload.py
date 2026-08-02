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

import logging
import os
import re

import httpx
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import STUDIO_API_BASE
from utils.studio_auth import get_bound_studio
from utils.studio_group import get_slug_by_chat_id, get_content_key_by_topic
from utils.studio_topic_queue import add_item, get_queue, clear_queue
from utils.ffmpeg_utils import prepare_for_telegram, _run_in_executor, make_temp_path
from handlers.studio_group import quality_label
from handlers.studio_upload import _presign_and_put, _auth_headers
from handlers.studio_backfill import _fetch_movie_detail

logger = logging.getLogger(__name__)

_SE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


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


def _resolve_topic_context(update: Update):
    """Guruh/topic'dan slug + content_key'ni aniqlaydi.
    Qaytaradi: (studio, slug, chat_id, topic_id, kind, content_id) yoki
    None qiymatlar bilan tuple, agar mos kelmasa."""
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

    studio = get_bound_studio(update.effective_user.id) if update.effective_user else None
    if not studio or studio.get("slug") != slug:
        return None

    return studio, slug, chat.id, topic_id, kind, content_id


async def on_topic_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Topic ichiga tashlangan video(lar)ni navbatga qo'shadi (darhol ishlamaydi)."""
    ctx = _resolve_topic_context(update)
    if not ctx:
        return  # bog'liq bo'lmagan guruh/topic yoki tanilmagan foydalanuvchi -- e'tibor bermaymiz
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
        if not m:
            await message.reply_text(
                "⚠️ Serial qismi uchun caption \"fasl-qism\" shaklida bo'lishi kerak "
                "(masalan: 2-5 = 2-fasl 5-qism). Bu video navbatga qo'shilmadi."
            )
            return
        season, episode = int(m.group(1)), int(m.group(2))

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
        await update.effective_message.reply_text(
            "⚠️ Bu buyruq faqat studiyangizga bog'langan guruhning kontent topic'ida ishlaydi."
        )
        return
    studio, slug, chat_id, topic_id, kind, content_id = ctx

    queue = get_queue(slug, topic_id)
    if not queue:
        await update.effective_message.reply_text("ℹ️ Navbatda video yo'q.")
        return

    status = await update.effective_message.reply_text(f"⏳ {len(queue)} ta video qayta ishlanmoqda...")

    detail = None
    title = "Kontent"
    if kind == "m":
        detail = await _fetch_movie_detail(studio, content_id)
    else:
        detail = await _fetch_series_detail(studio, content_id)
    title = _title_from_detail(detail, title)

    done, errors = 0, 0
    for item in queue:
        dl_path = None
        prepared_path = None
        try:
            tg_file = await context.bot.get_file(item["file_id"])
            dl_path = make_temp_path("mp4")
            await tg_file.download_to_drive(dl_path)

            prepared_path, _changed = await _run_in_executor(prepare_for_telegram, dl_path)

            if kind == "m":
                filename = f"{title}.mp4"
                public_url = await _presign_and_put(studio, prepared_path, "movies", filename)
                if not public_url or public_url == "cancelled":
                    errors += 1
                    continue
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.patch(
                        f"{STUDIO_API_BASE}/studios/{slug}/content/movies/{content_id}",
                        headers=_auth_headers(studio),
                        json={"r2Url": public_url},
                    )
                caption_label = f"🎬 {title}"
            else:
                filename = f"{title}_S{item['season']}E{item['episode']}.mp4"
                public_url = await _presign_and_put(studio, prepared_path, "series", filename)
                if not public_url or public_url == "cancelled":
                    errors += 1
                    continue
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        f"{STUDIO_API_BASE}/studios/{slug}/content/series/{content_id}/episodes",
                        headers=_auth_headers(studio),
                        json={"season": item["season"], "episode": item["episode"], "r2Url": public_url},
                    )
                caption_label = f"📺 {title}\n{item['season']}-fasl {item['episode']}-qism"

            if resp.status_code >= 300:
                logger.warning("Ro'yxatga olishda xato: %s %s", resp.status_code, resp.text[:300])
                errors += 1
                continue

            new_caption = caption_label + f"\n\n🔗 Video: {public_url}"
            try:
                await context.bot.edit_message_caption(
                    chat_id=chat_id, message_id=item["message_id"], caption=new_caption,
                )
            except TelegramError as e:
                logger.warning("Caption tahrirlashda xato (message_id=%s): %s", item["message_id"], e)

            done += 1
        except TelegramError as e:
            logger.warning("Topic video qayta ishlashda xato (message_id=%s): %s", item["message_id"], e)
            errors += 1
        except Exception as e:
            logger.warning("Kutilmagan xato (message_id=%s): %s", item["message_id"], e)
            errors += 1
        finally:
            for p in (dl_path, prepared_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    clear_queue(slug, topic_id)
    await status.edit_text(f"✅ Tugadi!\n✔️ Joylandi: {done}\n⚠️ Xatolar: {errors}")
