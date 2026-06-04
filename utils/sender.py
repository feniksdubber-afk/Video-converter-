import os
import asyncio
from telegram import Message
from handlers.video_handler import get_pyrogram_client

TELEGRAM_LIMIT = 50 * 1024 * 1024  # 50MB


def _progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


def _fmt_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


async def send_file(message: Message, file_path: str, filename: str, caption: str = ""):
    file_size = os.path.getsize(file_path)

    if file_size <= TELEGRAM_LIMIT:
        # Kichik fayl — oddiy PTB usuli, progress yo'q (tez ketadi)
        with open(file_path, "rb") as f:
            await message.reply_document(document=f, filename=filename, caption=caption)
    else:
        # Katta fayl — Pyrogram MTProto, progress bilan
        status_msg = await message.reply_text("📤 Yuborilmoqda... 0%")
        client = await get_pyrogram_client()

        last_percent = [-1]
        total_mb = file_size / 1024 / 1024

        async def progress(current, total):
            if total == 0:
                return
            percent = min(int(current / total * 100), 99)
            if percent - last_percent[0] >= 5:
                last_percent[0] = percent
                cur_mb = current / 1024 / 1024
                bar = _progress_bar(percent)
                try:
                    await status_msg.edit_text(
                        f"📤 *Yuborilmoqda...*\n\n"
                        f"{bar} `{percent}%`\n"
                        f"`{cur_mb:.1f}` / `{total_mb:.1f}` MB",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

        await client.send_document(
            chat_id=message.chat_id,
            document=file_path,
            file_name=filename,
            caption=caption,
            progress=progress,
        )

        try:
            await status_msg.edit_text(
                f"📤 *Yuborilmoqda...*\n\n{_progress_bar(100)} `100%`",
                parse_mode="Markdown",
            )
        except Exception:
            pass
