"""
Studiya menejerlari uchun: mavjud film/seriallar ro'yxatini ko'rish va
ID'ni qo'lda kiritish o'rniga ro'yxatdan tanlash.

Ikki rejimda ishlaydi:
  - "v" (view)   -> 📋 Mening kontentim: sof ko'rish, statistikani ko'rsatadi
  - "u" (upload) -> studiyaga video yuklash oqimida ID tanlash uchun

Callback data namespace ("studio_" bilan boshlanadi -- bot.py'dagi studiya
menejeri whitelist filtridan o'tishi uchun):
  studio_browse                  -> ko'rish rejimi kirish nuqtasi
  studio_bkind_{mode}_{kind}     -> Film/Serial tanlash
  studio_list_{mode}_{kind}_{p}  -> sahifalash
  studio_item_{mode}_{kind}_{id} -> element tanlash
  studio_search_{mode}_{kind}    -> qidiruv so'rash
  studio_clr_{mode}_{kind}       -> qidiruv filtrini tozalash
  studio_manual_{kind}           -> ID'ni qo'lda kiritish (faqat upload)
"""

import logging

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import STUDIO_API_BASE
from utils.studio_auth import get_bound_studio

logger = logging.getLogger(__name__)

PAGE_SIZE = 6


def _auth_headers(studio: dict) -> dict:
    return {"Authorization": f"Bearer {studio['api_token']}"}


def _kind_name(kind: str) -> str:
    return "Filmlar" if kind == "m" else "Seriallar"


async def _fetch_list(studio: dict, kind: str, page: int, search: str) -> dict | None:
    endpoint = "movies" if kind == "m" else "series"
    params = {"page": page, "limit": PAGE_SIZE}
    if search:
        params["search"] = search

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{STUDIO_API_BASE}/studios/{studio['slug']}/{endpoint}",
                headers=_auth_headers(studio),
                params=params,
            )
    except httpx.HTTPError as e:
        logger.warning("Kontent ro'yxatini olishda tarmoq xatosi: %s", e)
        return None

    if resp.status_code >= 300:
        logger.warning("Kontent ro'yxati xato: %s %s", resp.status_code, resp.text[:200])
        return None
    return resp.json()


def _kind_choice_keyboard(mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Filmlar", callback_data=f"studio_bkind_{mode}_m"),
            InlineKeyboardButton("📺 Seriallar", callback_data=f"studio_bkind_{mode}_s"),
        ],
        [InlineKeyboardButton("⬅️ Bekor qilish", callback_data="cancel")],
    ])


def _item_label(kind: str, item: dict) -> str:
    title = item.get("title") or "—"
    year = f" ({item['year']})" if item.get("year") else ""
    mark = ""
    if kind == "m":
        mark = "✅ " if item.get("hasVideo") else "⚠️ "
    return f"{mark}{title}{year}".strip()


def _build_list_keyboard(
    items: list[dict], kind: str, mode: str, page: int, has_more: bool, search: str
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_item_label(kind, item), callback_data=f"studio_item_{mode}_{kind}_{item['id']}")]
        for item in items
    ]

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"studio_list_{mode}_{kind}_{page - 1}"))
    if has_more:
        nav.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"studio_list_{mode}_{kind}_{page + 1}"))
    if nav:
        rows.append(nav)

    bottom = [InlineKeyboardButton("🔍 Qidirish", callback_data=f"studio_search_{mode}_{kind}")]
    if search:
        bottom.append(InlineKeyboardButton("✖️ Filtrni tozalash", callback_data=f"studio_clr_{mode}_{kind}"))
    rows.append(bottom)

    if mode == "u":
        rows.append([InlineKeyboardButton("✏️ ID orqali kiritish", callback_data=f"studio_manual_{kind}")])
    rows.append([InlineKeyboardButton("⬅️ Bekor qilish", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


async def _render_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, kind: str, page: int, search: str
):
    query = update.callback_query
    if query:
        await query.answer()

    studio = get_bound_studio(update.effective_user.id)
    reply = query.edit_message_text if query else update.message.reply_text
    if not studio:
        await reply("⛔ Studiya sifatida aniqlanmadingiz.")
        return

    data = await _fetch_list(studio, kind, page, search)
    if data is None:
        await reply("❌ Ro'yxatni olishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")
        return

    items = (data.get("movies") if kind == "m" else data.get("series")) or []
    total = data.get("total", 0)
    has_more = data.get("hasMore", False)

    # Keyingi tanlov uchun elementlarni keshda saqlaymiz (qayta so'rov yubormaslik uchun)
    cache = context.user_data.setdefault("studio_items_cache", {})
    cache.update({str(item["id"]): item for item in items})
    context.user_data["studio_list_search"] = search

    kind_name = _kind_name(kind)

    if not items:
        empty_note = f"\n\n🔍 Qidiruv: _{search}_ bo'yicha hech narsa topilmadi." if search else "\n\nHozircha bo'sh."
        rows = []
        if search:
            rows.append([InlineKeyboardButton("✖️ Filtrni tozalash", callback_data=f"studio_clr_{mode}_{kind}")])
        if mode == "u":
            rows.append([InlineKeyboardButton("✏️ ID orqali kiritish", callback_data=f"studio_manual_{kind}")])
        rows.append([InlineKeyboardButton("⬅️ Bekor qilish", callback_data="cancel")])
        await reply(f"📋 *{kind_name}*{empty_note}", reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
        return

    header = f"📋 *{kind_name}* — {total} ta"
    if total > PAGE_SIZE:
        header += f" ({page}-sahifa)"
    if search:
        header += f"\n🔍 Qidiruv: _{search}_"
    hint = "\n✅ video bor · ⚠️ video kerak" if kind == "m" else ""
    text = f"{header}{hint}\n\nKerakli kontentni tanlang:"

    await reply(text, reply_markup=_build_list_keyboard(items, kind, mode, page, has_more, search), parse_mode="Markdown")


# ── Kirish nuqtalari ────────────────────────────────────────────────────────

async def show_browse_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📋 Mening kontentim tugmasi (sof ko'rish rejimi)."""
    query = update.callback_query
    await query.answer()
    studio = get_bound_studio(update.effective_user.id)
    if not studio:
        await query.edit_message_text("⛔ Studiya sifatida aniqlanmadingiz.")
        return
    await query.edit_message_text(
        f"📋 *{studio['name']}* — qaysi turdagi kontentni ko'ramiz?",
        reply_markup=_kind_choice_keyboard("v"),
        parse_mode="Markdown",
    )


async def show_pick_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    """Yuklash oqimida ID tanlash uchun ro'yxat (upload rejimi)."""
    await _render_list(update, context, mode="u", kind=kind, page=1, search="")


# ── Callback dispatch handlerlari ──────────────────────────────────────────

async def handle_bkind_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, kind: str):
    await _render_list(update, context, mode=mode, kind=kind, page=1, search="")


async def handle_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, kind: str, page: int):
    search = context.user_data.get("studio_list_search", "")
    await _render_list(update, context, mode=mode, kind=kind, page=page, search=search)


async def handle_clear_search(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, kind: str):
    await _render_list(update, context, mode=mode, kind=kind, page=1, search="")


async def prompt_search(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, kind: str):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "studio_search_text"
    context.user_data["studio_search_mode"] = mode
    context.user_data["studio_search_kind"] = kind
    await query.edit_message_text(
        "🔍 Qidirish uchun nom kiriting (kamida 2 harf):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bekor qilish", callback_data="cancel")]]),
    )


async def handle_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """text_handler dispatch qatoridan chaqiriladi."""
    if context.user_data.get("state") != "studio_search_text":
        return False

    text = (update.message.text or "").strip()
    if len(text) < 2:
        await update.message.reply_text("❗ Kamida 2 ta harf kiriting.")
        return True

    mode = context.user_data.get("studio_search_mode", "v")
    kind = context.user_data.get("studio_search_kind", "m")
    context.user_data["state"] = None
    await _render_list(update, context, mode=mode, kind=kind, page=1, search=text)
    return True


async def handle_manual_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    """✏️ ID orqali kiritish -- eski qo'lda kiritish oqimiga qaytish."""
    query = update.callback_query
    await query.answer()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bekor qilish", callback_data="cancel")]])
    if kind == "m":
        context.user_data["state"] = "studio_movie_id"
        await query.edit_message_text("🎬 *Film ID'sini kiriting.*", parse_mode="Markdown", reply_markup=cancel_kb)
    else:
        context.user_data["state"] = "studio_series_id"
        await query.edit_message_text("📺 *Serial ID'sini kiriting.*", parse_mode="Markdown", reply_markup=cancel_kb)


async def _show_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, item_id: str):
    query = update.callback_query
    item = context.user_data.get("studio_items_cache", {}).get(item_id)
    if not item:
        await query.edit_message_text("⚠️ Ma'lumot eskirgan, ro'yxatni qayta oching.")
        return

    title = item.get("title") or "—"
    lines = [f"{'🎬' if kind == 'm' else '📺'} *{title}*"]
    if item.get("year"):
        lines.append(f"📅 Yil: {item['year']}")
    lines.append(f"⭐ Reyting: {item.get('rating', 0)}")
    lines.append(f"👁 Ko'rishlar: {item.get('views', 0)}")
    if kind == "m":
        lines.append(f"🎞 R2 Video: {'✅ mavjud' if item.get('hasVideo') else '⚠️ hali yuklanmagan'}")
        tg_status = "✅ mavjud" if item.get("hasTgVideo") else "⚠️ yo'q"
        lines.append(f"🤖 TG Video: {tg_status}")
    if item.get("isPremium"):
        lines.append("💎 Premium kontent")

    rows = []
    rows.append([InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"studio_edit_{kind}_{item_id}")])
    if kind == "s":
        rows.append([InlineKeyboardButton("📺 Qismlar", callback_data=f"studio_eps_{item_id}")])
    video_path = context.user_data.get("video_path")
    tg_file_id = context.user_data.get("video_tg_file_id")
    if kind == "m":
        if video_path:
            btn_label = "🔁 R2 Video almashtirish" if item.get("hasVideo") else "📤 R2 Video biriktirish"
            rows.append([InlineKeyboardButton(btn_label, callback_data=f"studio_item_u_{kind}_{item_id}")])
            if tg_file_id:
                tg_label = "🔁 TG Video almashtirish" if item.get("hasTgVideo") else "🎬 TG Video biriktirish"
                rows.append([InlineKeyboardButton(tg_label, callback_data=f"studio_tgv_m_{item_id}")])
        else:
            lines.append("\nℹ️ Video biriktirish uchun avval botga video yuboring.")
    rows.append([InlineKeyboardButton("⬅️ Ro'yxatga qaytish", callback_data=f"studio_bkind_v_{kind}")])

    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")


# ── Serial qismlari (fasllar/qismlar) ro'yxati ──────────────────────────────

async def _fetch_episodes(studio: dict, series_id: str) -> list[dict] | None:
    """Serialning barcha qismlarini oladi. Kutilgan javob shakli:
    {"episodes": [{"id", "season", "episode", "hasVideo", "hasTgVideo", ...}, ...]}
    Backend hali shu shaklda javob bermasa, bu funksiya None qaytaradi va
    foydalanuvchiga xatolik ko'rsatiladi -- API javobini moslashtirish kerak
    bo'lishi mumkin."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/series/{series_id}/episodes",
                headers=_auth_headers(studio),
            )
    except httpx.HTTPError as e:
        logger.warning("Qismlar ro'yxatini olishda tarmoq xatosi: %s", e)
        return None

    if resp.status_code >= 300:
        logger.warning("Qismlar ro'yxati xato: %s %s", resp.status_code, resp.text[:200])
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    logger.info("Qismlar API javobi (series=%s): %s", series_id, str(data)[:1500])
    if isinstance(data, list):
        return [_normalize_episode(e) for e in data]
    if not isinstance(data, dict):
        return []
    eps = data.get("episodes")
    if eps is None:
        # ba'zi API'lar {"seasons": [{"episodes": [...]}]} yoki {"data": [...]}
        # kabi ichma-ich shaklda qaytarishi mumkin -- keng qidiruv qilamiz
        for key in ("data", "items", "results"):
            if isinstance(data.get(key), list):
                eps = data[key]
                break
        if eps is None and isinstance(data.get("seasons"), list):
            eps = []
            for season_obj in data["seasons"]:
                season_num = (
                    season_obj.get("season")
                    or season_obj.get("seasonNumber")
                    or season_obj.get("number")
                )
                for e in season_obj.get("episodes", []):
                    e = dict(e)
                    e.setdefault("season", season_num)
                    eps.append(e)
    return [_normalize_episode(e) for e in eps] if isinstance(eps, list) else []


def _normalize_episode(e: dict) -> dict:
    """Turli xil maydon nomlari bo'lishi mumkin bo'lgan backend javobini
    botning kutgan kanonik kalitlariga moslaydi (season/episode/hasVideo/
    hasTgVideo/id), asl maydonlarni ham saqlab qoladi."""
    if not isinstance(e, dict):
        return {}
    out = dict(e)
    if "season" not in out:
        out["season"] = e.get("seasonNumber") or e.get("season_number") or 1
    if "episode" not in out:
        out["episode"] = e.get("episodeNumber") or e.get("episode_number") or e.get("number")
    if "hasVideo" not in out:
        out["hasVideo"] = bool(e.get("r2Url") or e.get("videoUrl") or e.get("hasR2Video"))
    if "hasTgVideo" not in out:
        out["hasTgVideo"] = bool(e.get("tgFileId") or e.get("tgVideoId") or e.get("telegramFileId"))
    return out


def _ep_mark(ep: dict) -> str:
    if ep.get("hasVideo"):
        mark = "✅"
    else:
        mark = "⚠️"
    if ep.get("hasTgVideo"):
        mark += "🤖"
    return mark


async def _render_seasons(reply, user_id: int, context: ContextTypes.DEFAULT_TYPE, series_id: str, back_cb: str):
    studio = get_bound_studio(user_id)
    if not studio:
        await reply("⛔ Studiya sifatida aniqlanmadingiz.")
        return

    episodes = await _fetch_episodes(studio, series_id)
    if episodes is None:
        await reply(
            "❌ Qismlar ro'yxatini olishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data=back_cb)]]),
        )
        return

    context.user_data.setdefault("studio_eps_cache", {})[str(series_id)] = episodes

    if not episodes:
        rows = [[InlineKeyboardButton("➕ 1-faslga qism qo'shish", callback_data=f"studio_epnew_{series_id}_1")]]
        rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=back_cb)])
        await reply(
            "📺 Hali birorta qism qo'shilmagan.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    seasons = sorted({ep.get("season", 1) for ep in episodes})
    rows = []
    for season in seasons:
        season_eps = [ep for ep in episodes if ep.get("season", 1) == season]
        done = sum(1 for ep in season_eps if ep.get("hasVideo"))
        rows.append([InlineKeyboardButton(
            f"📁 {season}-fasl ({done}/{len(season_eps)})",
            callback_data=f"studio_epss_{series_id}_{season}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=back_cb)])

    await reply(
        "📺 *Fasllar*\n✅ video bor · ⚠️ video kerak · 🤖 TG video bor\n\nFaslni tanlang:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
    )


async def show_episodes_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: str):
    """📺 Qismlar tugmasi -- fasllar bo'yicha guruhlab ko'rsatadi (ko'rish rejimi
    va yuklash rejimi uchun umumiy — ikkalasida ham tugma orqali tanlanadi)."""
    query = update.callback_query
    await query.answer()
    await _render_seasons(
        query.edit_message_text, update.effective_user.id, context, series_id,
        back_cb=f"studio_item_v_s_{series_id}",
    )


async def show_episodes_entry_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: str):
    """Qo'lda Serial ID kiritilgandan keyin (yuklash oqimi) — xuddi shu fasllar
    ro'yxatini yangi xabar sifatida yuboradi (edit qilinadigan callback yo'q)."""
    await _render_seasons(
        update.message.reply_text, update.effective_user.id, context, series_id,
        back_cb="cancel",
    )


async def show_season_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: str, season: int):
    query = update.callback_query
    await query.answer()

    episodes = context.user_data.get("studio_eps_cache", {}).get(str(series_id))
    if episodes is None:
        studio = get_bound_studio(update.effective_user.id)
        if not studio:
            await query.edit_message_text("⛔ Studiya sifatida aniqlanmadingiz.")
            return
        episodes = await _fetch_episodes(studio, series_id)
        if episodes is None:
            await query.edit_message_text("❌ Ma'lumot eskirgan, qaytadan urinib ko'ring.")
            return
        context.user_data.setdefault("studio_eps_cache", {})[str(series_id)] = episodes

    season_eps = sorted(
        (ep for ep in episodes if ep.get("season", 1) == season),
        key=lambda e: e.get("episode", 0),
    )
    rows = [
        [InlineKeyboardButton(
            f"{_ep_mark(ep)} {ep.get('episode')}-qism",
            callback_data=f"studio_epi_{series_id}_{season}_{ep.get('episode')}",
        )]
        for ep in season_eps
    ]
    rows.append([InlineKeyboardButton("➕ Yangi qism qo'shish", callback_data=f"studio_epnew_{series_id}_{season}")])
    rows.append([InlineKeyboardButton("⬅️ Fasllarga qaytish", callback_data=f"studio_eps_{series_id}")])

    await query.edit_message_text(
        f"📁 *{season}-fasl* — qismni tanlang:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
    )


async def show_episode_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: str, season: int, episode: int,
):
    query = update.callback_query
    await query.answer()

    episodes = context.user_data.get("studio_eps_cache", {}).get(str(series_id), [])
    ep = next((e for e in episodes if e.get("season", 1) == season and e.get("episode") == episode), None)

    lines = [f"📺 *{season}-fasl, {episode}-qism*"]
    if ep:
        lines.append(f"🎞 R2 Video: {'✅ mavjud' if ep.get('hasVideo') else '⚠️ hali yuklanmagan'}")
        tg_ok = ep.get("hasTgVideo")
        lines.append("🤖 TG Video: " + ("✅ mavjud" if tg_ok else "⚠️ yo'q"))

    video_path = context.user_data.get("video_path")
    tg_file_id = context.user_data.get("video_tg_file_id")
    rows = []
    if video_path:
        btn_label = "🔁 R2 Video almashtirish" if (ep and ep.get("hasVideo")) else "📤 R2 Video biriktirish"
        rows.append([InlineKeyboardButton(btn_label, callback_data=f"studio_epup_{series_id}_{season}_{episode}")])
        if tg_file_id and ep and ep.get("id"):
            tg_label = "🔁 TG Video almashtirish" if ep.get("hasTgVideo") else "🎬 TG Video biriktirish"
            rows.append([InlineKeyboardButton(tg_label, callback_data=f"studio_tgv_s_{series_id}_{ep['id']}")])
    else:
        lines.append("\nℹ️ Video biriktirish uchun avval botga video yuboring.")
    rows.append([InlineKeyboardButton("⬅️ Qismlarga qaytish", callback_data=f"studio_epss_{series_id}_{season}")])

    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")


async def handle_episode_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: str, season: int, episode: int,
):
    """Qismlar ro'yxatidan video biriktirish -- mavjud _do_episode_upload
    oqimini qayta ishlatadi (season/episode qo'lda kiritilmasdan)."""
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path:
        await query.edit_message_text("⚠️ Avval video yuboring (yoki konvertatsiya qiling)!")
        return

    from handlers.studio_upload import _do_episode_upload
    context.user_data["studio_series_id"] = series_id
    context.user_data["studio_season"] = season
    context.user_data["studio_episode"] = episode
    context.user_data.get("studio_eps_cache", {}).pop(str(series_id), None)
    await _do_episode_upload(update, context)


async def handle_new_episode_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: str, season: int):
    """➕ Yangi qism qo'shish -- qism raqamini so'raydi, keyin yuklashni davom ettiradi."""
    query = update.callback_query
    await query.answer()
    context.user_data["studio_series_id"] = series_id
    context.user_data["studio_season"] = season
    context.user_data["state"] = "studio_episode"
    await query.edit_message_text(
        f"🔢 *{season}-fasl* uchun yangi qism raqamini kiriting (masalan: 1):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bekor qilish", callback_data="cancel")]]),
    )


_EDIT_FIELDS = {
    "t": ("titleUz", "📝 Nomi"),
    "y": ("year", "📅 Yil"),
    "c": ("country", "🌍 Mamlakat"),
    "d": ("description", "🧾 Tavsif"),
    "g": ("genres", "🎭 Janr"),
}


def _edit_field_keyboard(kind: str, item_id: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"studio_ef_{kind}_{item_id}_{code}")]
        for code, (_, label) in _EDIT_FIELDS.items()
    ]
    rows.append([InlineKeyboardButton("⬅️ Bekor qilish", callback_data=f"studio_item_v_{kind}_{item_id}")])
    return InlineKeyboardMarkup(rows)


async def handle_edit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, item_id: str):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ Qaysi maydonni tahrirlaymiz?",
        reply_markup=_edit_field_keyboard(kind, item_id),
    )


async def handle_edit_field_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, item_id: str, field_code: str
):
    query = update.callback_query
    await query.answer()
    api_field, label = _EDIT_FIELDS.get(field_code, (None, None))
    if not api_field:
        return
    context.user_data["state"] = "studio_edit_text"
    context.user_data["studio_edit_kind"] = kind
    context.user_data["studio_edit_item_id"] = item_id
    context.user_data["studio_edit_field"] = api_field
    await query.edit_message_text(
        f"{label} uchun yangi qiymatni yozing:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bekor qilish", callback_data=f"studio_item_v_{kind}_{item_id}")]]),
    )


async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """text_handler dispatch qatoridan chaqiriladi."""
    if context.user_data.get("state") != "studio_edit_text":
        return False

    text = (update.message.text or "").strip()
    kind = context.user_data.get("studio_edit_kind")
    item_id = context.user_data.get("studio_edit_item_id")
    api_field = context.user_data.get("studio_edit_field")
    context.user_data["state"] = None

    if not (kind and item_id and api_field) or not text:
        await update.message.reply_text("❗ Nimadir xato ketdi, qaytadan urinib ko'ring.")
        return True

    studio = get_bound_studio(update.effective_user.id)
    if not studio:
        await update.message.reply_text("⛔ Studiya sifatida aniqlanmadingiz.")
        return True

    body = {api_field: int(text) if api_field == "year" else text}
    if kind == "m":
        url = f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/movies/{item_id}"
    else:
        url = f"{STUDIO_API_BASE}/studios/{studio['slug']}/content/series/{item_id}/metadata"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(url, headers=_auth_headers(studio), json=body)
    except httpx.HTTPError as e:
        logger.warning("Tahrirlashda tarmoq xatosi: %s", e)
        await update.message.reply_text("❌ Tarmoq xatosi. Qaytadan urinib ko'ring.")
        return True

    if resp.status_code >= 300:
        logger.warning("Tahrirlash xato: %s %s", resp.status_code, resp.text[:300])
        await update.message.reply_text("❌ Saqlashda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring yoki admin bilan bog'laning.")
        return True

    context.user_data.get("studio_items_cache", {}).pop(str(item_id), None)
    await update.message.reply_text("✅ Yangilandi.")
    return True


async def handle_item_pick(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, kind: str, item_id: str):
    query = update.callback_query
    await query.answer()

    if mode == "v":
        await _show_detail(update, context, kind, item_id)
        return

    # mode == "u" -> to'g'ridan-to'g'ri yuklash oqimiga o'tamiz
    video_path = context.user_data.get("video_path")
    if not video_path:
        await query.edit_message_text("⚠️ Avval video yuboring (yoki konvertatsiya qiling)!")
        return

    if kind == "m":
        from handlers.studio_upload import _do_movie_upload
        context.user_data["state"] = None
        await _do_movie_upload(update, context, item_id)
    else:
        # Fasl/qism endi matn bilan emas, mavjud tugma-asosli qismlar
        # ro'yxati orqali tanlanadi (📋 Mening kontentim bilan bir xil oqim).
        context.user_data["state"] = None
        await show_episodes_entry(update, context, item_id)
