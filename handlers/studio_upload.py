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
from handlers.studio_group import post_video_to_topic, quality_label
from utils.ffmpeg_utils import get_video_resolution

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
        logger.warning("Film'ga biriktirish xato: %s %s", resp.status_code, resp.text[:300])
        await status.edit_text("❌ Filmga biriktirishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring yoki admin bilan bog'laning.")
        return

    await status.edit_text("✅ Tayyor — film videosi yuklandi va faollashtirildi.")

    title = context.user_data.get("studio_items_cache", {}).get(movie_id, {}).get("title") or f"Film #{movie_id}"
    caption = f"🎬 *{title}*\n✅ Video yuklandi."
    _w, _h = get_video_resolution(video_path)
    _q = quality_label(_h)
    if _q:
        caption += f"\n🖼 Sifat: {_q}"
    if public_url:
        caption += f"\n\n🔗 R2: {public_url}"
    await post_video_to_topic(
        context, studio, kind="m", content_id=movie_id, title=title,
        caption=caption,
        video_path=video_path, tg_file_id=context.user_data.get("video_tg_file_id"),
    )

    await _offer_tg_video(update, context, kind="m", movie_id=movie_id)


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
        logger.warning("Qismni qo'shishda xato: %s %s", resp.status_code, resp.text[:300])
        await status.edit_text("❌ Qismni qo'shishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring yoki admin bilan bog'laning.")
        return

    ep_id = None
    try:
        ep_id = resp.json().get("episode", {}).get("id")
    except Exception:
        pass

    await status.edit_text(f"✅ Tayyor — {season}-fasl {episode}-qism qo'shildi.")

    series_title = context.user_data.get("studio_items_cache", {}).get(series_id, {}).get("title") or f"Serial #{series_id}"
    caption = f"📺 *{series_title}*\n{season}-fasl {episode}-qism yuklandi."
    _w, _h = get_video_resolution(video_path)
    _q = quality_label(_h)
    if _q:
        caption += f"\n🖼 Sifat: {_q}"
    if public_url:
        caption += f"\n\n🔗 R2: {public_url}"
    await post_video_to_topic(
        context, studio, kind="s", content_id=series_id, title=series_title,
        caption=caption,
        video_path=video_path, tg_file_id=context.user_data.get("video_tg_file_id"),
    )

    await _offer_tg_video(update, context, kind="s", movie_id=None, series_id=series_id, episode_id=ep_id)


async def _offer_tg_video(
    update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str,
    movie_id: str = None, series_id: str = None, episode_id=None,
):
    """R2 yuklangandan keyin, agar video Telegram video sifatida yuborilgan
    bo'lsa, uni TG video (file_id) sifatida ham biriktirishni taklif qiladi
    -- bu ba'zi mijozlarda tezroq ochilishi uchun ishlatiladi."""
    message = update.effective_message
    tg_file_id = context.user_data.get("video_tg_file_id")

    if not tg_file_id or (kind == "s" and not episode_id):
        await message.reply_text("Yana amal tanlang:", reply_markup=studio_menu_keyboard())
        return

    studio = get_bound_studio(update.effective_user.id)

    # Foydalanuvchi avval "bundan buyon avtomatik" tanlagan bo'lsa -- har safar
    # qayta so'ramasdan, to'g'ridan-to'g'ri biriktiramiz.
    if context.user_data.get("studio_tg_auto") and studio:
        ok, err = await _attach_tg_video(studio, tg_file_id, kind, movie_id, series_id, episode_id)
        if ok:
            await message.reply_text("✅ TG Video ham avtomatik biriktirildi.", reply_markup=studio_menu_keyboard())
        else:
            logger.warning("Avto TG video xato: %s", err)
            await message.reply_text(
                "⚠️ TG Videoni avtomatik biriktirishda xatolik bo'ldi, o'tkazib yuborildi.",
                reply_markup=studio_menu_keyboard(),
            )
        return

    if kind == "m":
        cb = f"studio_tgv_m_{movie_id}"
        cb_auto = f"studio_tgva_m_{movie_id}"
    else:
        cb = f"studio_tgv_s_{series_id}_{episode_id}"
        cb_auto = f"studio_tgva_s_{series_id}_{episode_id}"

    await message.reply_text(
        "🎬 Bu videoni *TG Video* sifatida ham biriktiraylikmi?\n"
        "(ba'zi qurilmalarda tezroq ochiladi)",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Ha, biriktirish", callback_data=cb)],
            [InlineKeyboardButton("🔁 Ha, bundan buyon avtomatik", callback_data=cb_auto)],
            [InlineKeyboardButton("O'tkazib yuborish", callback_data="studio_upload")],
        ]),
        parse_mode="Markdown",
    )


async def _attach_tg_video(
    studio: dict, tg_file_id: str, kind: str,
    movie_id: str = None, series_id: str = None, episode_id=None,
) -> tuple[bool, str | None]:
    """TG video (file_id) biriktirishning umumiy tarmoq mantiqi -- qo'lda
    tasdiqlashda ham, avtomatik rejimda ham shu funksiya ishlatiladi."""
    if kind == "m":
        url = f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/movies/{movie_id}/tg-video"
    else:
        url = f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/series/{series_id}/episodes/{episode_id}/tg-video"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=_auth_headers(studio), json={"fileId": tg_file_id})
    except httpx.HTTPError as e:
        return False, f"tarmoq xatosi: {e}"

    if resp.status_code >= 300:
        return False, f"{resp.status_code} {resp.text[:200]}"
    return True, None


async def handle_tg_video_attach(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    await query.answer()

    tg_file_id = context.user_data.get("video_tg_file_id")
    if not tg_file_id:
        await query.edit_message_text("⚠️ Video topilmadi, qaytadan yuboring.")
        return

    studio = get_bound_studio(update.effective_user.id)
    if not studio:
        await query.edit_message_text("⛔ Studiya sifatida aniqlanmadingiz.")
        return

    auto = data.startswith("studio_tgva_")
    if auto:
        context.user_data["studio_tg_auto"] = True

    parts = data.split("_")  # studio_tgv_m_{id} | studio_tgv_s_{seriesId}_{episodeId} (+ "a" variant)
    kind = parts[2]
    if kind == "m":
        movie_id, series_id, episode_id = parts[3], None, None
    else:
        movie_id, series_id, episode_id = None, parts[3], parts[4]

    ok, err = await _attach_tg_video(studio, tg_file_id, kind, movie_id, series_id, episode_id)
    if not ok:
        logger.warning("TG video xato: %s", err)
        await query.edit_message_text("❌ Biriktirishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")
        return

    text = "✅ TG Video ham biriktirildi."
    if auto:
        text += "\n🔁 Bundan buyon shu sessiyada avtomatik biriktiriladi."
    await query.edit_message_text(text)
    await update.effective_message.reply_text("Yana amal tanlang:", reply_markup=studio_menu_keyboard())


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
        context.user_data["state"] = None
        from handlers.studio_content import show_episodes_entry_msg
        await show_episodes_entry_msg(update, context, text)
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
