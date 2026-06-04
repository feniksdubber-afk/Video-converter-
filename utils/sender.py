import os
import asyncio
import aiohttp
from telegram import Message
from handlers.video_handler import get_pyrogram_client

TELEGRAM_LIMIT = 50 * 1024 * 1024       # 50 MB — PTB chegarasi
PYROGRAM_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GB — Telegram chegarasi

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".wmv"}
AUDIO_EXTENSIONS = {".mp3", ".aac", ".ogg", ".wav", ".flac", ".m4a", ".opus", ".wma"}


def _progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


def _fmt_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


async def _upload_to_gofile(file_path: str) -> str:
    """Gofile.io ga yuklaydi va download link qaytaradi."""
    # Avval eng yaqin serverni olamiz
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.gofile.io/servers") as r:
            data = await r.json()
            server = data["data"]["servers"][0]["name"]

        with open(file_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("file", f, filename=os.path.basename(file_path))
            async with session.post(
                f"https://{server}.gofile.io/contents/uploadfile", data=form
            ) as r:
                result = await r.json()
                if result.get("status") != "ok":
                    raise Exception(f"Gofile xato: {result}")
                return result["data"]["downloadPage"]


async def send_file(
    message: Message,
    file_path: str,
    filename: str,
    caption: str = "",
    context=None,  # user_settings uchun
):
    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(filename)[1].lower()

    # Upload mode ni aniqlash
    upload_mode = "document"
    if context is not None:
        from utils.user_settings import get as get_setting
        upload_mode = get_setting(context, "upload_mode")

    # ─── 2 GB dan katta → Gofile.io ───────────────────────────────────────
    if file_size > PYROGRAM_LIMIT:
        status_msg = await message.reply_text(
            "☁️ *Fayl 2 GB dan katta!*\n\n"
            "`[░░░░░░░░░░░░]` Gofile.io ga yuklanmoqda...",
            parse_mode="Markdown",
        )
        try:
            link = await _upload_to_gofile(file_path)
            await status_msg.edit_text(
                f"✅ *Fayl tayyor!*\n\n"
                f"📦 Hajmi: `{_fmt_size(file_size)}`\n"
                f"📁 Nom: `{filename}`\n\n"
                f"Fayl 2 GB dan katta bo'lgani uchun link orqali yuklab oling:\n"
                f"🔗 {link}\n\n"
                f"_(Link 10 kun davomida faol bo'ladi)_",
                parse_mode="Markdown",
            )
        except Exception as e:
            await status_msg.edit_text(
                f"❌ Gofile.io ga yuklashda xato:\n`{e}`\n\n"
                f"Faylni boshqa usulda olishga harakat qiling.",
                parse_mode="Markdown",
            )
        return

    # ─── 50 MB gacha → PTB (tez, progress shart emas) ─────────────────────
    if file_size <= TELEGRAM_LIMIT:
        with open(file_path, "rb") as f:
            if upload_mode == "video" and ext in VIDEO_EXTENSIONS:
                await message.reply_video(video=f, filename=filename, caption=caption)
            elif upload_mode == "audio" and ext in AUDIO_EXTENSIONS:
                await message.reply_audio(audio=f, filename=filename, caption=caption)
            else:
                await message.reply_document(document=f, filename=filename, caption=caption)
        return

    # ─── 50 MB – 2 GB → Pyrogram MTProto, progress bilan ──────────────────
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

    # Upload mode ga qarab yuborish usulini tanlaymiz
    if upload_mode == "video" and ext in VIDEO_EXTENSIONS:
        await client.send_video(
            chat_id=message.chat_id,
            video=file_path,
            file_name=filename,
            caption=caption,
            progress=progress,
        )
    elif upload_mode == "audio" and ext in AUDIO_EXTENSIONS:
        await client.send_audio(
            chat_id=message.chat_id,
            audio=file_path,
            file_name=filename,
            caption=caption,
            progress=progress,
        )
    else:
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
