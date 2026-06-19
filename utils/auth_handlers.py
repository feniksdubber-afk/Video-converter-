"""Whitelist boshqaruvi: /allow, /deny, /users"""

from telegram import Update
from telegram.ext import ContextTypes
from utils.auth import (
    is_admin, is_allowed, allow_user, deny_user,
    list_allowed, list_admins, reload_auth,
)


async def auth_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1: ruxsatsiz foydalanuvchilarni to'xtatadi."""
    from telegram.ext import ApplicationHandlerStop

    user = update.effective_user
    if not user:
        return
    if is_allowed(user.id):
        return

    text = "⛔ *Sizga ruxsat berilmagan.*\n\nBot faqat admin tasdiqlagan foydalanuvchilar uchun."
    try:
        if update.callback_query:
            await update.callback_query.answer("⛔ Ruxsat yo'q", show_alert=True)
        elif update.message:
            await update.message.reply_text(text, parse_mode="Markdown")
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
