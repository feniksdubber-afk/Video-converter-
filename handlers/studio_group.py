"""
Studiya guruhini botga bog'lash (/guruh_biriktirish) va har bir
film/serial uchun shu guruhda alohida topic (mavzu) ochib, kontentni
professional tarzda saqlash.
"""

import asyncio
import logging

from telegram import Update, InputFile
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from utils.studio_auth import get_bound_studio
from utils.studio_group import (
    bind_group, get_group, get_slug_by_chat_id, set_topic_id, get_topic_id,
)


def quality_label(height: int) -> str | None:
    if not height:
        return None
    if height >= 2000:
        return "4K"
    if height >= 1000:
        return "1080p"
    if height >= 700:
        return "720p"
    if height >= 460:
        return "480p"
    if height >= 340:
        return "360p"
    return f"{height}p"

logger = logging.getLogger(__name__)


async def bind_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text(
            "⚠️ Bu buyruq faqat guruh ichida ishlaydi.\n"
            "Avval studiyangiz uchun yangi guruh yarating (Topics/Mavzular "
            "rejimini yoqing), botni admin qilib qo'shing, so'ng shu yerda "
            "/guruh_biriktirish deb yozing."
        )
        return

    studio = get_bound_studio(user.id)
    if not studio:
        await message.reply_text(
            "⛔ Siz studiya menejeri sifatida aniqlanmadingiz. "
            "Botga shaxsiy chatda /start bosib tekshiring."
        )
        return

    if not chat.is_forum:
        await message.reply_text(
            "⚠️ Bu guruhda *Topics (Mavzular)* rejimi yoqilmagan.\n"
            "Guruh sozlamalaridan yoqib, qaytadan urinib ko'ring.",
            parse_mode="Markdown",
        )
        return

    try:
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except TelegramError as e:
        logger.warning("get_chat_member xato: %s", e)
        await message.reply_text("❌ Bot huquqlarini tekshirib bo'lmadi.")
        return

    if member.status != "administrator":
        await message.reply_text("⚠️ Botni avval guruhda *admin* qiling.", parse_mode="Markdown")
        return

    if not getattr(member, "can_manage_topics", False):
        await message.reply_text(
            "⚠️ Botga admin huquqlaridan *\"Mavzularni boshqarish\"* "
            "(Manage Topics) huquqini bering.",
            parse_mode="Markdown",
        )
        return

    existing_slug = get_slug_by_chat_id(chat.id)
    if existing_slug and existing_slug != studio["slug"]:
        await message.reply_text("⚠️ Bu guruh allaqachon boshqa studiyaga bog'langan.")
        return

    bind_group(studio["slug"], chat.id, chat.title or "", user.id)
    await message.reply_text(
        f"✅ \"{chat.title}\" guruhi \"{studio['name']}\" studiyasiga muvaffaqiyatli bog'landi!\n\n"
        "Bundan buyon botga yuklangan har bir film/serial uchun shu guruhda "
        "avtomatik alohida mavzu (topic) ochiladi va kontent shu yerda saqlanadi."
    )


def _content_key(kind: str, content_id) -> str:
    return f"{kind}_{content_id}"


# (slug, content_key) -> asyncio.Lock -- bitta kontent uchun topic yaratish
# hech qachon ikki marta parallel bajarilmasligi kerak, aks holda Telegram'da
# DUBLIKAT topic yaratilib, biri "yetim" bo'lib qoladi (unga tashlangan
# videolar bot xaritasidan chiqib ketadi).
_ensure_topic_locks: dict[tuple[str, str], asyncio.Lock] = {}


async def ensure_topic(
    context: ContextTypes.DEFAULT_TYPE, studio: dict, kind: str, content_id, title: str,
) -> tuple[int, int] | None:
    """Kontent uchun topic borligini tekshiradi, bo'lmasa yaratadi.
    Qaytaradi: (chat_id, topic_id) yoki None (guruh bog'lanmagan/xato)."""
    group = get_group(studio["slug"])
    if not group:
        return None

    chat_id = group["chat_id"]
    key = _content_key(kind, content_id)
    topic_id = get_topic_id(studio["slug"], key)
    if topic_id:
        return chat_id, topic_id

    lock_key = (studio["slug"], key)
    lock = _ensure_topic_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        # Lock kutish paytida boshqa coroutine allaqachon topic yaratgan
        # bo'lishi mumkin -- shuning uchun qayta tekshiramiz (double-check).
        topic_id = get_topic_id(studio["slug"], key)
        if topic_id:
            return chat_id, topic_id

        icon = "🎬" if kind == "m" else "📺"
        name = f"{icon} {title}"[:128]
        try:
            forum_topic = await context.bot.create_forum_topic(chat_id=chat_id, name=name)
        except TelegramError as e:
            logger.warning("Topic yaratishda xato: %s", e)
            return None

        topic_id = forum_topic.message_thread_id
        set_topic_id(studio["slug"], key, topic_id)
        return chat_id, topic_id


async def _send_message_safe(context, chat_id, topic_id, text):
    try:
        await context.bot.send_message(chat_id=chat_id, message_thread_id=topic_id, text=text, parse_mode="Markdown")
    except TelegramError:
        await context.bot.send_message(chat_id=chat_id, message_thread_id=topic_id, text=text)


async def _send_video_safe(context, chat_id, topic_id, caption, *, tg_file_id=None, file_obj=None):
    kwargs = dict(chat_id=chat_id, message_thread_id=topic_id, caption=caption)
    try:
        if tg_file_id:
            await context.bot.send_video(video=tg_file_id, parse_mode="Markdown", **kwargs)
        else:
            await context.bot.send_video(video=InputFile(file_obj), parse_mode="Markdown", **kwargs)
    except TelegramError:
        if tg_file_id:
            await context.bot.send_video(video=tg_file_id, **kwargs)
        else:
            file_obj.seek(0)
            await context.bot.send_video(video=InputFile(file_obj), **kwargs)


async def post_text_to_topic(
    context: ContextTypes.DEFAULT_TYPE, studio: dict, kind: str, content_id, title: str, text: str,
) -> None:
    dest = await ensure_topic(context, studio, kind, content_id, title)
    if not dest:
        return
    chat_id, topic_id = dest
    try:
        await _send_message_safe(context, chat_id, topic_id, text)
    except TelegramError as e:
        logger.warning("Topic'ga xabar yuborishda xato: %s", e)


async def post_video_to_topic(
    context: ContextTypes.DEFAULT_TYPE, studio: dict, kind: str, content_id, title: str,
    caption: str, video_path: str = None, tg_file_id: str = None,
) -> None:
    dest = await ensure_topic(context, studio, kind, content_id, title)
    if not dest:
        return
    chat_id, topic_id = dest
    try:
        if tg_file_id:
            await _send_video_safe(context, chat_id, topic_id, caption, tg_file_id=tg_file_id)
        elif video_path:
            with open(video_path, "rb") as f:
                await _send_video_safe(context, chat_id, topic_id, caption, file_obj=f)
    except TelegramError as e:
        logger.warning("Topic'ga video yuborishda xato: %s", e)
