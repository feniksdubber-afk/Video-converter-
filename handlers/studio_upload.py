"""
Studiya menejerlari uchun: botda konvertatsiya qilingan videoni
to'g'ridan-to'g'ri o'z studiyasiga (Afsona platformasiga) yuklash.

Oqim:
  1. "📤 Studiyaga yuklash" bosiladi -> Film / Serial tanlanadi
  2. Film/Serial ID kiritiladi (yoki nomi bilan qidiriladi)
  3. (Serial bo'lsa) Fasl va Qism raqami so'raladi
  4. Video R2'ga presign orqali yuklanadi, keyin API'ga biriktiriladi
"""

import logging
import os

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import STUDIO_API_BASE
from utils.studio_auth import get_bound_studio
from utils.keyboards import studio_menu_keyboard

logger = logging.getLogger(__name__)


def _kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Film", callback_data="studio_kind_movie"),
            InlineKeyboardButton("📺 Serial", callback_data="studio_kind_series"),
        ],
        [InlineKeyboardButton("⬅️ Bekor qilish", callback_data="cancel")],
    ])


async def show_studio_upload_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("⚠️ Avval video yuboring (yoki konvertatsiya qiling)!")
        return

    studio = get_bound_studio(query.from_user.id)
    if not studio:
        await query.edit_message_text("⛔ Studiya sifatida aniqlanmadingiz.")
        return
    if not studio.get("api_token"):
        await query.edit_message_text(
            "⚠️ Studiyangiz uchun hali yuklash tokeni sozlanmagan.\n"
            "Bosh admin bilan bog'laning."
        )
        return

    await query.edit_message_text(
        f"📤 *Studiyaga yuklash* — {studio['name']}\n\nQaysi turdagi kontentga yuklaymiz?",
        reply_markup=_kind_keyboard(),
        parse_mode="Markdown",
    )


async def handle_kind_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    query = update.callback_query
    await query.answer()
    context.user_data["studio_kind"] = kind  # "movies" | "series"

    from handlers.studio_content import show_pick_entry
    await show_pick_entry(update, context, "m" if kind == "movies" else "s")


def _auth_headers(studio: dict) -> dict:
    return {"Authorization": f"Bearer {studio['api_token']}"}


async def _presign_and_put(
    studio: dict, file_path: str, kind: str, filename: str,
    user_id: int = 0, status_msg=None,
) -> str | None:
    from utils.task_queue import new_ticket, acquire_slot, release_slot

    ticket_id = new_ticket()
    if status_msg is not None:
        got_slot = await acquire_slot(ticket_id, user_id, status_msg, label="📤 Studiyaga yuklash")
        if not got_slot:
            try:
                await status_msg.edit_text("❌ Navbatdan chiqarildingiz.")
            except Exception:
                pass
            return "cancelled"

    try:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp4"
        content_type = {
            "mkv": "video/x-matroska", "mov": "video/quicktime",
            "webm": "video/webm", "avi": "video/x-msvideo",
        }.get(ext, "video/mp4")

        if status_msg is not None:
            try:
                await status_msg.edit_text("⏳ Yuklanmoqda...")
            except Exception:
                pass

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{STUDIO_API_BASE}/studios/{studio['slug']}/uploads/presign",
                headers=_auth_headers(studio),
                json={"contentType": content_type, "filename": filename, "kind": kind},
            )
            data = resp.json()
            upload_url = data.get("uploadUrl")
            public_url = data.get("publicUrl")
            if not upload_url:
                logger.warning("Presign xato: %s", data)
                return None

            with open(file_path, "rb") as f:
                put_resp = await client.put(
                    upload_url,
                    headers={"Content-Type": content_type},
                    content=f.read(),
                )
            if put_resp.status_code >= 300:
                logger.warning("R2 upload xato: %s %s", put_resp.status_code, put_resp.text[:300])
                return None

        return public_url
    finally:
        if status_msg is not None:
            release_slot()


async def _do_movie_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    message = update.effective_message
    studio = get_bound_studio(update.effective_user.id)
    video_path = context.user_data.get("video_path")
    video_name = context.user_data.get("video_name", "video.mp4")

    status = await message.reply_text("⏳ Yuklanmoqda...")
    public_url = await _presign_and_put(
        studio, video_path, "movies", video_name,
        user_id=update.effective_user.id, status_msg=status,
    )
    if public_url == "cancelled":
        return
    if not public_url:
        await status.edit_text("❌ Yuklashda xatolik yuz berdi. Qaytadan urinib ko'ring.")
        return

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.patch(
            f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/movies/{movie_id}",
            headers=_auth_headers(studio),
            json={"r2Url": public_url},
        )
    if resp.status_code >= 300:
        await status.edit_text(f"❌ Film'ga biriktirishda xatolik: {resp.text[:200]}")
        return

    await status.edit_text("✅ Tayyor — film videosi yuklandi va faollashtirildi.")
    await message.reply_text("Yana amal tanlang:", reply_markup=studio_menu_keyboard())


async def _do_episode_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    studio = get_bound_studio(update.effective_user.id)
    video_path = context.user_data.get("video_path")
    video_name = context.user_data.get("video_name", "video.mp4")
    series_id = context.user_data["studio_series_id"]
    season = context.user_data["studio_season"]
    episode = context.user_data["studio_episode"]

    status = await message.reply_text("⏳ Yuklanmoqda...")
    public_url = await _presign_and_put(
        studio, video_path, "series", video_name,
        user_id=update.effective_user.id, status_msg=status,
    )
    if public_url == "cancelled":
        return
    if not public_url:
        await status.edit_text("❌ Yuklashda xatolik yuz berdi. Qaytadan urinib ko'ring.")
        return

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/series/{series_id}/episodes",
            headers=_auth_headers(studio),
            json={"season": season, "episode": episode, "r2Url": public_url},
        )
    if resp.status_code >= 300:
        await status.edit_text(f"❌ Qismni qo'shishda xatolik: {resp.text[:200]}")
        return

    await status.edit_text(f"✅ Tayyor — {season}-fasl {episode}-qism qo'shildi.")
    await message.reply_text("Yana amal tanlang:", reply_markup=studio_menu_keyboard())


async def handle_studio_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """text_handler dispatch qatoridan chaqiriladi. True qaytarsa -- xabar
    shu yerda ishlandi, boshqa hech narsa qilinmasin."""
    state = context.user_data.get("state")
    message = update.message
    text = (message.text or "").strip()

    if state == "studio_movie_id":
        if not text.isdigit():
            await message.reply_text("❗ Iltimos faqat raqamli ID kiriting.")
            return True
        context.user_data["state"] = None
        await _do_movie_upload(update, context, text)
        return True

    if state == "studio_series_id":
        if not text.isdigit():
            await message.reply_text("❗ Iltimos faqat raqamli ID kiriting.")
            return True
        context.user_data["studio_series_id"] = text
        context.user_data["state"] = "studio_season"
        await message.reply_text("📁 Fasl raqamini kiriting (masalan: 1):")
        return True

    if state == "studio_season":
        if not text.isdigit():
            await message.reply_text("❗ Iltimos faqat raqam kiriting.")
            return True
        context.user_data["studio_season"] = int(text)
        context.user_data["state"] = "studio_episode"
        await message.reply_text("🎞 Qism raqamini kiriting (masalan: 5):")
        return True

    if state == "studio_episode":
        if not text.isdigit():
            await message.reply_text("❗ Iltimos faqat raqam kiriting.")
            return True
        context.user_data["studio_episode"] = int(text)
        context.user_data["state"] = None
        await _do_episode_upload(update, context)
        return True

    return False
