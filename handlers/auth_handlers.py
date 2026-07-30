"""Whitelist boshqaruvi: /allow, /deny, /users
Studiya menejerlari uchun login/parol boshqaruvi: /studiya_yarat, /studiyalar,
/studiya_chiqar, /studiya_ochir"""

from telegram import Update
from telegram.ext import ContextTypes
from config import ARCHIVE_GROUP_ID
from utils.auth import (
    is_admin, is_allowed, allow_user, deny_user,
    list_allowed, list_admins, reload_auth,
)
from utils.studio_auth import (
    is_studio_manager, get_studio_for_user, verify_login,
    create_studio, list_studios, unbind_studio, delete_studio, set_api_token,
)


LOGIN_PROMPT = (
    "🔐 *Kirish talab qilinadi*\n\n"
    "Bu bot faqat ruxsat berilgan foydalanuvchilar uchun.\n\n"
    "Agar siz *studiya menejeri* bo'lsangiz, Bosh admindan olgan login va "
    "parolni BITTA xabarda, bo'sh joy bilan ajratib yuboring:\n\n"
    "`Studiya_nomi Parol`\n\n"
    "_Masalan: `Eleven Studio aB3xQ9pLmZ`_"
)


async def auth_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1: ruxsatsiz foydalanuvchilarni to'xtatadi, studiya login oqimini boshqaradi."""
    from telegram.ext import ApplicationHandlerStop

    chat = update.effective_chat
    if ARCHIVE_GROUP_ID and chat and chat.id == ARCHIVE_GROUP_ID:
        # Arxiv guruhi — bot faqat shu yerga fayl/topic tashlaydi.
        # Guruhga kim nima yozsa/tashlasa ham (video, matn, h.k.) bot
        # umuman e'tibor bermaydi — auth javobi ham, boshqa handler ham ishlamaydi.
        raise ApplicationHandlerStop

    user = update.effective_user
    if not user:
        return

    # 1) Bosh admin va admin tasdiqlagan foydalanuvchilar — to'liq ruxsat
    if is_admin(user.id) or is_allowed(user.id):
        return

    # 2) Allaqachon tizimga kirgan (bog'langan) studiya menejeri — ruxsat
    if is_studio_manager(user.id):
        return

    # 3) Hali tizimga kirmagan — faqat shaxsiy chatda login urinishini qabul qilamiz
    if chat and chat.type == "private" and update.message and update.message.text \
            and not update.message.text.startswith("/"):
        text = update.message.text.strip()
        parts = text.rsplit(" ", 1)
        if len(parts) == 2 and parts[1]:
            name_part, pwd = parts
            slug = verify_login(name_part, pwd, user.id)
            if slug:
                studio = get_studio_for_user(user.id)
                await update.message.reply_text(
                    f"✅ *Muvaffaqiyatli kirish!*\n\n"
                    f"🏷 Studiya: *{studio['name']}*\n\n"
                    f"Endi video yuboring — konvertatsiyadan so'ng "
                    f"to'g'ridan-to'g'ri studiyangizga yuklashingiz mumkin.",
                    parse_mode="Markdown",
                )
                raise ApplicationHandlerStop

        await update.message.reply_text(
            "⛔ Login yoki parol xato. Qaytadan urinib ko'ring:\n\n"
            "`Studiya_nomi Parol`",
            parse_mode="Markdown",
        )
        raise ApplicationHandlerStop

    try:
        if update.callback_query:
            await update.callback_query.answer("⛔ Avval tizimga kiring (/start)", show_alert=True)
        elif update.message:
            await update.message.reply_text(LOGIN_PROMPT, parse_mode="Markdown")
    except Exception:
        pass
    raise ApplicationHandlerStop


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


# ── Studiya menejerlari boshqaruvi (faqat Bosh Admin) ─────────────────────────

async def studio_create_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/studiya_yarat <Studiya nomi> — yangi studiya + xavfsiz parol yaratadi."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat Bosh admin.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "❗ Foydalanish:\n`/studiya_yarat Studiya nomi`\n\n"
            "Misol: `/studiya_yarat Eleven Studio`",
            parse_mode="Markdown",
        )
        return

    name = " ".join(args)
    slug, password = create_studio(name)
    await update.message.reply_text(
        f"✅ *Studiya yaratildi!*\n\n"
        f"🏷 Nomi: `{name}`\n"
        f"🔑 Slug: `{slug}`\n\n"
        f"👤 *Menejerga shu ma'lumotni yuboring:*\n"
        f"Login: `{name}`\n"
        f"Parol: `{password}`\n\n"
        f"Ular botga `/start` bosib, keyin bitta xabarda\n"
        f"`{name} {password}`\n"
        f"deb yozishlari kerak — bir marta kiritilgach abadiy eslab qolinadi.\n\n"
        f"⚠️ Bu parol *faqat shu safar* ko'rsatiladi, keyin qayta ko'rib bo'lmaydi — saqlab qo'ying.",
        parse_mode="Markdown",
    )


async def studios_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/studiyalar — barcha studiyalar va ularning holatini ko'rsatadi."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat Bosh admin.")
        return

    studios = list_studios()
    if not studios:
        await update.message.reply_text("📭 Hali birorta studiya yaratilmagan.\n\n`/studiya_yarat Nomi` bilan yarating.", parse_mode="Markdown")
        return

    lines = ["🏢 *Studiyalar:*\n"]
    for s in studios:
        status = f"✅ bog'langan (`{s['telegram_id']}`)" if s.get("telegram_id") else "⏳ hali kirmagan"
        lines.append(f"  • *{s['name']}* (`{s['slug']}`) — {status}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def studio_unbind_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/studiya_chiqar <slug> — menejerni studiyadan chiqaradi (qayta login talab qilinadi)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat Bosh admin.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "❗ Foydalanish:\n`/studiya_chiqar slug`\n\nSlug'larni `/studiyalar` orqali ko'ring.",
            parse_mode="Markdown",
        )
        return

    slug = args[0]
    if unbind_studio(slug):
        await update.message.reply_text(f"✅ `{slug}` studiyasi bog'lanishdan chiqarildi — menejer qayta login qilishi kerak.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ `{slug}` topilmadi.", parse_mode="Markdown")


async def studio_token_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/studiya_token <slug> <token> — Afsona mini-app'dagi Studiya paneli →
    Termux/Kompyuter bo'limidan olingan CLI yuklash tokenini shu studiyaga bog'laydi.
    Shu token orqali bot studiya nomidan videoni Afsona platformasiga yuklaydi."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat Bosh admin.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❗ Foydalanish:\n`/studiya_token slug eyJhbGci...`\n\n"
            "Tokenni mini-app → Studiya paneli → Termux/Kompyuter bo'limidan oling.",
            parse_mode="Markdown",
        )
        return

    slug, token = args[0], args[1]
    # Xabarni darhol o'chirish tavsiya qilinadi (token maxfiy), lekin bot
    # o'z xabarini o'chira olmaydi -- shu sababli faqat tasdiq beramiz.
    if set_api_token(slug, token):
        await update.message.reply_text(f"✅ `{slug}` uchun API token saqlandi.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ `{slug}` topilmadi. Avval `/studiya_yarat` bilan yarating.", parse_mode="Markdown")
