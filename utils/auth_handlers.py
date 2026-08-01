"""Whitelist boshqaruvi: /allow, /deny, /users
Studiya menejerlarini avtomatik aniqlash (asosiy platforma bazasidan) +
admin uchun studiya API token va bog'lanishni boshqarish buyruqlari."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import ARCHIVE_GROUP_ID
from utils.auth import (
    is_admin, is_allowed, allow_user, deny_user,
    list_allowed, list_admins, reload_auth,
)
from utils.shared_db import get_manager_studios
from utils.studio_auth import (
    get_bound_studio, bind_user, clear_binding, set_api_token, list_tokens,
)


def _studio_pick_keyboard(studios: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🏢 {s['name']}", callback_data=f"studio_pick_{s['id']}")]
        for s in studios
    ]
    return InlineKeyboardMarkup(rows)


def _studio_switch_keyboard(studios: list[dict]) -> InlineKeyboardMarkup:
    """studio_pick_ bilan to'qnashmasligi uchun alohida callback prefix."""
    rows = [
        [InlineKeyboardButton(f"🏢 {s['name']}", callback_data=f"studio_switch_{s['id']}")]
        for s in studios
    ]
    return InlineKeyboardMarkup(rows)


async def auth_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1: ruxsatsiz foydalanuvchilarni to'xtatadi, studiya menejerlarini
    asosiy platforma bazasidan avtomatik aniqlaydi."""
    from telegram.ext import ApplicationHandlerStop

    chat = update.effective_chat
    if ARCHIVE_GROUP_ID and chat and chat.id == ARCHIVE_GROUP_ID:
        # Arxiv guruhi — bot faqat shu yerga fayl/topic tashlaydi.
        raise ApplicationHandlerStop

    user = update.effective_user
    if not user:
        return

    # 1) Bosh admin va admin tasdiqlagan foydalanuvchilar — to'liq ruxsat
    if is_admin(user.id) or is_allowed(user.id):
        return

    # 2) Allaqachon studiyaga bog'langan menejer — ruxsat
    if get_bound_studio(user.id):
        return

    # 3) studio_pick_ tugmasi bosilgan bo'lsa — callback_handler o'zi qayta ishlaydi
    if update.callback_query and update.callback_query.data and update.callback_query.data.startswith("studio_pick_"):
        return

    # 4) Hali bog'lanmagan — asosiy platforma bazasidan tekshiramiz
    studios = get_manager_studios(user.id)

    if not studios:
        text = (
            "⛔ *Sizga ruxsat berilmagan.*\n\n"
            "Bu bot faqat Afsona TV platformasida studiya menejeri sifatida "
            "tasdiqlangan shaxslar uchun. Agar siz menejer bo'lsangiz, "
            "Bosh admin bilan bog'laning."
        )
        try:
            if update.callback_query:
                await update.callback_query.answer("⛔ Ruxsat yo'q", show_alert=True)
            elif update.message:
                await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            pass
        raise ApplicationHandlerStop

    if len(studios) == 1:
        # Bitta studiyaga menejer — hech narsa so'ramasdan avtomatik bog'laymiz
        bind_user(user.id, studios[0])
        if update.message:
            await update.message.reply_text(
                f"✅ *{studios[0]['name']}* studiyasi avtomatik aniqlandi va bog'landi.",
                parse_mode="Markdown",
            )
        return

    # Bir nechta studiyaga menejer — tanlashni so'raymiz
    try:
        if update.message:
            await update.message.reply_text(
                "🏢 Siz bir nechta studiyaga menejersiz. Qaysi studiya nomidan ishlaysiz?",
                reply_markup=_studio_pick_keyboard(studios),
            )
        elif update.callback_query:
            await update.callback_query.answer("⛔ Avval studiyani tanlang (/start)", show_alert=True)
    except Exception:
        pass
    raise ApplicationHandlerStop


async def handle_studio_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bir nechta studiyaga menejer bo'lgan foydalanuvchi tugma orqali tanlaganda."""
    query = update.callback_query
    await query.answer()
    studio_id = int(query.data.split("_")[-1])

    studios = get_manager_studios(query.from_user.id)
    studio = next((s for s in studios if s["id"] == studio_id), None)
    if not studio:
        await query.edit_message_text("⛔ Bu studiyaga sizning ruxsatingiz topilmadi.")
        return

    bind_user(query.from_user.id, studio)
    await query.edit_message_text(
        f"✅ *{studio['name']}* studiyasi bilan bog'landingiz.\n\n"
        f"Endi video yuboring — konvertatsiyadan so'ng to'g'ridan-to'g'ri "
        f"studiyangizga yuklashingiz mumkin.",
        parse_mode="Markdown",
    )


async def _studio_switch_core(user_id: int, reply) -> None:
    """/studiya_almashtirish va '🔄 Studiyani almashtirish' tugmasi uchun
    umumiy mantiq. `reply` — message.reply_text yoki query.edit_message_text."""
    studios = get_manager_studios(user_id)

    if not studios:
        await reply("⛔ Siz hech qanday studiyaga menejer sifatida topilmadingiz.")
        return

    if len(studios) == 1:
        await reply(
            f"ℹ️ Sizda faqat bitta studiya bor — *{studios[0]['name']}*.\n"
            f"Almashtirish shart emas.",
            parse_mode="Markdown",
        )
        return

    await reply(
        "🔄 Qaysi studiya nomidan ishlashni xohlaysiz?",
        reply_markup=_studio_switch_keyboard(studios),
    )


async def studio_switch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/studiya_almashtirish — menejerning o'zi bog'langan studiyani almashtirishi
    uchun (adminni chaqirmasdan). Faqat 2+ studiyaga menejer bo'lganlarga foydali."""
    user = update.effective_user
    if not user:
        return
    await _studio_switch_core(user.id, update.message.reply_text)


async def handle_studio_switch_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Studiya menyusidagi '🔄 Studiyani almashtirish' tugmasi (callback versiyasi)."""
    query = update.callback_query
    await query.answer()
    await _studio_switch_core(query.from_user.id, query.edit_message_text)


async def handle_studio_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """studio_switch_{id} tugmasi bosilganda — eski bog'lanishni tozalab,
    yangisini o'rnatadi. handle_studio_pick bilan bir xil logikadan foydalanadi,
    lekin alohida callback_data prefiksga ega (auth_gate() bilan to'qnashmasligi uchun)."""
    query = update.callback_query
    await query.answer()
    studio_id = int(query.data.split("_")[-1])

    studios = get_manager_studios(query.from_user.id)
    studio = next((s for s in studios if s["id"] == studio_id), None)
    if not studio:
        await query.edit_message_text("⛔ Bu studiyaga sizning ruxsatingiz topilmadi.")
        return

    clear_binding(query.from_user.id)
    bind_user(query.from_user.id, studio)
    await query.edit_message_text(
        f"✅ Endi *{studio['name']}* studiyasi nomidan ishlaysiz.\n\n"
        f"Video yuboring — konvertatsiyadan so'ng shu studiyaga yuklashingiz mumkin.",
        parse_mode="Markdown",
    )


async def allow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat admin.")
        return

    args = context.args
    if not args or not args[0].lstrip("-").isdigit():
        await update.message.reply_text(
            "❗ Foydalanish:\n`/allow 123456789`",
            parse_mode="Markdown",
        )
        return

    target = int(args[0])
    if allow_user(target):
        await update.message.reply_text(f"✅ `{target}` qo'shildi.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ `{target}` allaqachon ro'yxatda.", parse_mode="Markdown")


async def deny_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat admin.")
        return

    args = context.args
    if not args or not args[0].lstrip("-").isdigit():
        await update.message.reply_text(
            "❗ Foydalanish:\n`/deny 123456789`",
            parse_mode="Markdown",
        )
        return

    target = int(args[0])
    if deny_user(target):
        await update.message.reply_text(f"✅ `{target}` olib tashlandi.", parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"❌ `{target}` ro'yxatda emas yoki admin (o'chirib bo'lmaydi).",
            parse_mode="Markdown",
        )


async def users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat admin.")
        return

    reload_auth()
    allowed = list_allowed()
    admins = list_admins()
    lines = ["👥 *Ruxsatli foydalanuvchilar:*\n"]
    for uid in allowed:
        mark = " 👑" if uid in admins else ""
        lines.append(f"  • `{uid}`{mark}")
    if not allowed:
        lines.append("  _(hech kim — faqat adminlar)_")
    lines.append(f"\n📊 Jami: {len(allowed)} | Admin: {len(admins)}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Studiya menejerlari: admin buyruqlari ─────────────────────────────────

async def studios_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/studiyalar — Afsona platformasidagi barcha studiyalarni va API token
    holatini ko'rsatadi (studiyalarning o'zi shu yerda YARATILMAYDI —
    ular AfsonaMovieBot platformasida allaqachon mavjud)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat Bosh admin.")
        return

    import sqlite3
    from config import SHARED_DB_PATH as DB_PATH

    if not DB_PATH:
        await update.message.reply_text(
            "⚠️ `SHARED_DB_PATH` sozlanmagan — asosiy platforma bazasi ulanmagan.",
            parse_mode="Markdown",
        )
        return

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, slug, name FROM studios WHERE is_active = 1 ORDER BY name"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        await update.message.reply_text(f"❌ Bazani o'qishda xato: {e}")
        return

    if not rows:
        await update.message.reply_text("📭 Platformada hali birorta faol studiya yo'q.")
        return

    tokens = list_tokens()
    lines = ["🏢 *Afsona platformasidagi studiyalar:*\n"]
    for r in rows:
        token_status = "🔑 token bor" if r["slug"] in tokens else "❌ token yo'q"
        lines.append(f"  • *{r['name']}* (`{r['slug']}`) — {token_status}")
    lines.append("\n`/studiya_token slug token` bilan yuklash tokenini bog'lang.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def studio_unbind_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/studiya_chiqar <telegram_id> — foydalanuvchining studiya bog'lanishini
    bekor qiladi (masalan noto'g'ri studiya tanlangan bo'lsa, qayta tanlashi uchun)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat Bosh admin.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "❗ Foydalanish:\n`/studiya_chiqar 123456789`\n\n(Telegram ID kiriting)",
            parse_mode="Markdown",
        )
        return

    target = int(args[0])
    if clear_binding(target):
        await update.message.reply_text(f"✅ `{target}` bog'lanishi bekor qilindi — /start bosganda qayta tanlaydi.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ `{target}` hech qanday studiyaga bog'lanmagan edi.", parse_mode="Markdown")


async def studio_token_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/studiya_token <slug> <token> — Afsona mini-app'dagi Studiya paneli →
    Termux/Kompyuter bo'limidan olingan CLI yuklash tokenini shu studiyaga bog'laydi."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat Bosh admin.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❗ Foydalanish:\n`/studiya_token slug eyJhbGci...`\n\n"
            "Slug'larni `/studiyalar` orqali ko'ring. Tokenni mini-app → "
            "Studiya paneli → Termux/Kompyuter bo'limidan oling.",
            parse_mode="Markdown",
        )
        return

    slug, token = args[0], args[1]
    set_api_token(slug, token)
    await update.message.reply_text(f"✅ `{slug}` uchun API token saqlandi.", parse_mode="Markdown")
