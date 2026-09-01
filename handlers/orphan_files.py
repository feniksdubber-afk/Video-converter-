"""
orphan_files.py — BOSH ADMIN uchun (studiya menejeri emas): bekor qilingan
`/joylash` jarayonlaridan R2'da qolib ketgan "yetim" fayllarni ko'rish va
o'chirish.

DIQQAT: Afsona studiya backend API'sida bunday DELETE endpoint yo'q, va
bo'lganda ham u ataylab faqat `social/` prefiksli fayllarni o'chiradi --
studiya film/serial fayllari tasodifan o'chib ketmasligi uchun
(`handlers/studio_topic_upload.py`dagi `_record_orphan_upload` izohiga
qarang). Lekin BOTNING O'ZI (`utils/r2_manager.py`) alohida, to'g'ridan-
to'g'ri R2 hisob ma'lumotlariga (R2_ACCESS_KEY_ID va h.k.) ega -- bu
backend'ning `social/`-cheklovidan MUSTAQIL. Shuning uchun bu yerda
backend API orqali emas, to'g'ridan-to'g'ri R2'dan o'chiramiz.

XAVFSIZLIK: bu FAQAT bosh adminlarga ochiq (`is_admin`), studiya
menejerlariga emas -- noto'g'ri kalitni o'chirish qaytarib bo'lmaydigan
yo'qotish bo'lishi mumkin.
"""

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.auth import is_admin
from utils.orphan_uploads import list_orphan_uploads, remove_orphan_upload
from utils.r2_manager import get_public_url, delete_file, is_configured

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8


def _key_from_url(url: str) -> str:
    """Public URL'dan R2 obyekt kalitini ajratib oladi (r2_browser.py'dagi
    bilan bir xil andoza: `get_public_url("")` bazasini kesib tashlaymiz)."""
    base = get_public_url("").rstrip("/")
    if url and base and url.startswith(base):
        return url[len(base):].lstrip("/")
    return ""


def _fmt_entry(entry: dict) -> str:
    return f"{entry.get('label') or '(nomsiz)'} — {entry.get('studio_slug') or '?'}"


def _build_keyboard(entries: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for entry in entries[:_PAGE_SIZE]:
        btn_label = (entry.get("label") or entry["id"])[:40]
        rows.append([InlineKeyboardButton(f"🗑 {btn_label}", callback_data=f"orphan_del:{entry['id']}")])
    if entries:
        rows.append([InlineKeyboardButton("🗑🗑 Barchasini o'chirish", callback_data="orphan_del_all")])
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="orphan_refresh")])
    return InlineKeyboardMarkup(rows)


def _render_text(entries: list[dict]) -> str:
    if not entries:
        return "✅ Yetim (orphan) R2 fayllar yo'q — hammasi toza."
    lines = [f"🗂 <b>Yetim R2 fayllar</b> — jami {len(entries)} ta\n"]
    for i, entry in enumerate(entries[:_PAGE_SIZE], start=1):
        lines.append(f"{i}. {html.escape(_fmt_entry(entry))}")
        lines.append(f"   <code>{html.escape(entry.get('public_url', ''))}</code>")
    if len(entries) > _PAGE_SIZE:
        lines.append(f"\n… va yana {len(entries) - _PAGE_SIZE} ta (avval yuqoridagilarni tozalang, keyin \"🔄 Yangilash\").")
    return "\n".join(lines)


async def orphanfiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/orphanfiles` -- faqat bosh admin. Ro'yxatni va har bir yozuv
    yonida "🗑 O'chirish" tugmasini ko'rsatadi."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Bu buyruq faqat bosh admin uchun.")
        return
    entries = list_orphan_uploads()
    await update.message.reply_text(
        _render_text(entries), parse_mode="HTML", reply_markup=_build_keyboard(entries),
    )


async def orphanfiles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tugma bosilganda: bitta yozuvni yoki barchasini R2'dan o'chiradi va
    ro'yxatdan ham olib tashlaydi. Har bir amal alohida R2 chaqiruvi bilan
    amalga oshadi -- bittasi xato bersa ham qolganlariga ta'sir qilmaydi."""
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.answer("⛔ Faqat bosh admin.", show_alert=True)
        return

    data = query.data or ""

    if data == "orphan_refresh":
        entries = list_orphan_uploads()
        await query.edit_message_text(
            _render_text(entries), parse_mode="HTML", reply_markup=_build_keyboard(entries),
        )
        await query.answer()
        return

    if not is_configured():
        await query.answer("⚠️ Botda R2 sozlanmagan (R2_ACCESS_KEY_ID va h.k.) -- o'chirib bo'lmaydi.", show_alert=True)
        return

    if data == "orphan_del_all":
        entries = list_orphan_uploads()
        ok, fail = 0, 0
        for entry in list(entries):
            key = _key_from_url(entry.get("public_url", ""))
            if key and await delete_file(key):
                remove_orphan_upload(entry["id"])
                ok += 1
            else:
                fail += 1
                logger.warning("Yetim R2 faylni o'chirib bo'lmadi: %s", entry.get("public_url"))
        await query.answer(f"✅ {ok} ta o'chirildi" + (f", ❌ {fail} ta xato." if fail else "."), show_alert=True)
        entries = list_orphan_uploads()
        await query.edit_message_text(
            _render_text(entries), parse_mode="HTML", reply_markup=_build_keyboard(entries),
        )
        return

    if data.startswith("orphan_del:"):
        entry_id = data.split(":", 1)[1]
        entries = list_orphan_uploads()
        entry = next((e for e in entries if e.get("id") == entry_id), None)
        if not entry:
            await query.answer("Topilmadi (allaqachon tozalangan bo'lishi mumkin).", show_alert=True)
            return
        key = _key_from_url(entry.get("public_url", ""))
        if not key:
            await query.answer("⚠️ URL'dan R2 kalitini aniqlab bo'lmadi -- qo'lda tekshiring.", show_alert=True)
            return
        if await delete_file(key):
            remove_orphan_upload(entry_id)
            await query.answer("✅ R2'dan o'chirildi.")
        else:
            await query.answer("❌ R2'dan o'chirishda xato (log'ga qarang).", show_alert=True)
        entries = list_orphan_uploads()
        await query.edit_message_text(
            _render_text(entries), parse_mode="HTML", reply_markup=_build_keyboard(entries),
        )
        return

    await query.answer()
