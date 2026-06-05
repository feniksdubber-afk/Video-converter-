import os
import asyncio
import subprocess
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


def _get_video_meta(file_path: str) -> dict:
    """Video davomiyligi, eni, balandligini qaytaradi."""
    meta = {"duration": 0, "width": 0, "height": 0}
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1",
                file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        for line in r.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if key == "duration":
                    try:
                        meta["duration"] = int(float(val))
                    except Exception:
                        pass
                elif key == "width":
                    try:
                        meta["width"] = int(val)
                    except Exception:
                        pass
                elif key == "height":
                    try:
                        meta["height"] = int(val)
                    except Exception:
                        pass
    except Exception:
        pass
    return meta


def _make_thumb(file_path: str, duration: int) -> str | None:
    """Video o'rtasidan thumbnail (JPEG) oladi. Muvaffaqiyatli bo'lsa path qaytaradi."""
    try:
        from config import TEMP_DIR
        import uuid
        thumb_path = os.path.join(TEMP_DIR, f"thumb_{uuid.uuid4().hex}.jpg")
        seek = max(1, duration // 4) if duration > 4 else 1
        r = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(seek),
                "-i", file_path,
                "-frames:v", "1",
                "-vf", "scale=320:-1",
                "-q:v", "5",
                thumb_path,
            ],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
    except Exception:
        pass
    return None


async def _upload_to_gofile(file_path: str) -> str:
    """Gofile.io ga yuklaydi va download link qaytaradi."""
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
    context=None,
):
    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(filename)[1].lower()
    is_video = ext in VIDEO_EXTENSIONS
    is_audio = ext in AUDIO_EXTENSIONS

    upload_mode = "document"
    if context is not None:
        from utils.user_settings import get as get_setting
        upload_mode = get_setting(context, "upload_mode")

    # Video metadata va thumbnail olish
    meta = {}
    thumb_path = None
    custom_thumb_tmp = None

    if is_video:
        meta = _get_video_meta(file_path)

        # 1. Custom thumbnail — disk yo'li sifatida saqlanган
        if context is not None:
            from utils.user_settings import ensure_loaded as _ensure, get as get_setting
            await _ensure(context.user_data.get("_user_id", 0), context)
            custom_path = get_setting(context, "custom_thumbnail")
            if custom_path and isinstance(custom_path, str) and os.path.exists(custom_path):
                thumb_path = custom_path

        # 2. Custom yo'q bo'lsa — videoning o'zidan auto thumbnail
        if not thumb_path and meta.get("duration", 0) > 0:
            thumb_path = _make_thumb(file_path, meta["duration"])
            custom_thumb_tmp = thumb_path  # auto thumb — o'chiriladi

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
        finally:
            if thumb_path and os.path.exists(thumb_path):
                if custom_thumb_tmp and os.path.exists(custom_thumb_tmp): os.remove(custom_thumb_tmp)
        return

    # ─── 50 MB gacha → PTB ────────────────────────────────────────────────
    if file_size <= TELEGRAM_LIMIT:
        try:
            with open(file_path, "rb") as f:
                if upload_mode == "video" and is_video:
                    thumb_file = open(thumb_path, "rb") if thumb_path else None
                    try:
                        await message.reply_video(
                            video=f,
                            filename=filename,
                            caption=caption,
                            duration=meta.get("duration", 0) or None,
                            width=meta.get("width", 0) or None,
                            height=meta.get("height", 0) or None,
                            thumbnail=thumb_file,
                            supports_streaming=True,
                        )
                    finally:
                        if thumb_file:
                            thumb_file.close()
                elif upload_mode == "audio" and is_audio:
                    await message.reply_audio(audio=f, filename=filename, caption=caption)
                else:
                    await message.reply_document(document=f, filename=filename, caption=caption)
        finally:
            if thumb_path and os.path.exists(thumb_path):
                if custom_thumb_tmp and os.path.exists(custom_thumb_tmp): os.remove(custom_thumb_tmp)
        return

    # ─── 50 MB – 2 GB → Pyrogram MTProto, progress bilan ─────────────────
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

    try:
        if upload_mode == "video" and is_video:
            await client.send_video(
                chat_id=message.chat_id,
                video=file_path,
                file_name=filename,
                caption=caption,
                duration=meta.get("duration", 0) or None,
                width=meta.get("width", 0) or None,
                height=meta.get("height", 0) or None,
                thumb=thumb_path,
                supports_streaming=True,
                progress=progress,
            )
        elif upload_mode == "audio" and is_audio:
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
    finally:
        if thumb_path and os.path.exists(thumb_path):
            if custom_thumb_tmp and os.path.exists(custom_thumb_tmp): os.remove(custom_thumb_tmp)

    try:
        await status_msg.edit_text(
            f"📤 *Yuborilmoqda...*\n\n{_progress_bar(100)} `100%`",
            parse_mode="Markdown",
        )
    except Exception:
        pass
