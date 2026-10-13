"""Kino posti sinovi.

/fileid   — videoga javoban yozing: shu botdagi file_id ni qaytaradi
/testpost — videoga javoban yoki `/testpost <file_id>`: namunaviy kino posti yuboradi
"""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from handlers.post_builder import send_post

logger = logging.getLogger(__name__)

SAMPLE_MOVIE = {
    "title": "Sendan O'zga Muhabbat", "country": "Janubiy Koreya",
    "language": "O'zbek", "genres": ["Drama", "Romantika"],
    "quality": "1080p", "year": 2026, "code": 271, "kind": "serial",
    "views": 134, "rating": 6.6, "votes": 182,
}


def _video_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Javob berilgan xabardagi video/hujjat file_id si, bo'lmasa buyruq argumenti."""
    reply = update.message.reply_to_message
    if reply is not None:
        media = reply.video or reply.document or reply.animation
        if media is not None:
            return media.file_id
    if context.args:
        return context.args[0]
    return None


async def fileid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = update.message.reply_to_message
    media = (reply.video or reply.document or reply.animation) if reply else None
    if media is None:
        await update.message.reply_text("Videoga javoban /fileid yozing.")
        return
    await update.message.reply_text(f"<code>{media.file_id}</code>", parse_mode=ParseMode.HTML)


async def post_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = _video_file_id(update, context)
    if not file_id:
        await update.message.reply_text(
            "Videoga javoban /testpost yozing yoki: /testpost <file_id>")
        return
    try:
        await send_post(context.bot, update.effective_chat.id, SAMPLE_MOVIE, video=file_id)
    except Exception as e:
        logger.exception("testpost xato")
        await update.message.reply_text(f"❌ Post yuborilmadi: {type(e).__name__}: {str(e)[:200]}")
