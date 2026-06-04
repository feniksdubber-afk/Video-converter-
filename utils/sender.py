"""
Fayl yuborish utility.
50MB gacha — oddiy Telegram Bot API
50MB dan katta — Pyrogram MTProto
"""
import os
from telegram import Message
from video_handler import get_pyrogram_client

TELEGRAM_LIMIT = 50 * 1024 * 1024  # 50MB


async def send_file(message: Message, file_path: str, filename: str, caption: str = ""):
    file_size = os.path.getsize(file_path)

    if file_size <= TELEGRAM_LIMIT:
        with open(file_path, "rb") as f:
            await message.reply_document(document=f, filename=filename, caption=caption)
    else:
        client = await get_pyrogram_client()
        await client.send_document(
            chat_id=message.chat_id,
            document=file_path,
            file_name=filename,
            caption=caption,
            progress=None,
        )
