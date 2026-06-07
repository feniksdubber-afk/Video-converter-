"""
sender.py — Fayl yuborish logikasi.

Hajm bo'yicha yo'naltirish:
  <= 50 MB          → PTB (python-telegram-bot) to'g'ridan-to'g'ri
  50 MB – 2 GB      → Pyrogram MTProto (progress bilan)
  > 2 GB            → Cloudflare R2 (agar sozlangan), aks holda Gofile.io
"""

import os
import asyncio
import subprocess
import hashlib
import time
import aiohttp
from telegram import Message, InlineKeyboardButton, InlineKeyboardMarkup
from handlers.video_handler import get_pyrogram_client
from utils.r2_manager import upload_file as r2_upload, is_configured as r2_ok, R2_THRESHOLD, fmt_size as r2_fmt
from utils.ffmpeg_utils import sanitize_filename

# Telegram callback_data 64 bayt bilan cheklangan.
# Fayl nomini to'g'ridan-to'g'ri ishlatish o'rniga qisqa hash saqlаymiz.
# {short_key: {"filename": ..., "url": ..., "file_path": ..., "ts": unix_time}}
_r2_pending: dict[str, dict] = {}
_R2_PENDING_TTL = 3600  # 1 soat


def _cleanup_r2_pending():
    """1 soatdan eski yozuvlarni _r2_pending dan tozalaydi."""
    now = time.time()
    stale = [k for k, v in _r2_pending.items() if now - v.get("ts", 0) > _R2_PENDING_TTL]
    for k in stale:
        _r2_pending.pop(k, None)

TELEGRAM_LIMIT = 50 * 1024 * 1024        # 50 MB
PYROGRAM_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GB

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


def _get_video_meta_sync(file_path: str) -> dict:
    """Sinxron versiya — faqat executor ichida ishlatiladi."""
    meta = {"duration": 0, "width": 0, "height": 0}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1",
             file_path],
            capture_output=True, text=True, timeout=30,
        )
        for line in r.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
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


async def _get_video_meta(file_path: str) -> dict:
    """Async: event loop ni bloklamasdan video meta ma'lumotlarini oladi."""
    return await asyncio.get_running_loop().run_in_executor(None, _get_video_meta_sync, file_path)


def _make_thumb_sync(file_path: str, duration: int) -> str | None:
    """Sinxron versiya — faqat executor ichida ishlatiladi."""
    try:
        from config import TEMP_DIR
        import uuid
        thumb_path = os.path.join(TEMP_DIR, f"thumb_{uuid.uuid4().hex}.jpg")
        seek = max(1, duration // 4) if duration > 4 else 1
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", file_path,
             "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "5", thumb_path],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
    except Exception:
        pass
    return None


async def _make_thumb(file_path: str, duration: int) -> str | None:
    """Async: event loop ni bloklamasdan thumbnail yaratadi."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _make_thumb_sync, file_path, duration
    )


async def _upload_to_gofile(file_path: str) -> str:
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


async def _upload_to_r2(message: Message, file_path: str, filename: str, file_size: int, user_id: int = 0) -> str | None:
    """R2 ga yuklab, tugmali xabar yuboradi. URL qaytaradi yoki None."""
    status_msg = await message.reply_text(
        f"☁️ *R2 ga yuklanmoqda...*\n\n"
        f"`[░░░░░░░░░░░░]` `0%`\n"
        f"`0` / `{_fmt_size(file_size)}`",
        parse_mode="Markdown",
    )

    last_pct = [-1]

    async def progress_cb(uploaded, total, pct):
        if pct - last_pct[0] < 5:
            return
        last_pct[0] = pct
        bar = _progress_bar(pct)
        try:
            await status_msg.edit_text(
                f"☁️ *R2 ga yuklanmoqda...*\n\n"
                f"{bar} `{pct}%`\n"
                f"`{_fmt_size(uploaded)}` / `{_fmt_size(total)}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    try:
        # S3 object key uchun fayl nomini tozalash (bo'shliq/qavslar muammo qilmasligi uchun)
        safe_filename = sanitize_filename(filename)
        url = await r2_upload(file_path, safe_filename, progress_cb=progress_cb)

        # Eski yozuvlarni tozalash (xotira sizintisini oldini olish)
        _cleanup_r2_pending()

        # callback_data Telegram da 64 bayt bilan cheklangan.
        # user_id + fayl nomi kombinatsiyasi — foydalanuvchilar orasida izolyatsiya.
        short_key = hashlib.md5(f"{user_id}:{filename}".encode()).hexdigest()[:8]
        _r2_pending[short_key] = {
            "filename": filename,
            "url": url,
            "file_path": file_path,
            "ts": time.time(),
        }

        # Telegram ga ham yuborish tugmasi chiqar
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Telegramga yuklash", callback_data=f"r2_send_tg__{short_key}")],
            [InlineKeyboardButton("🔗 Havolani nusxalash", url=url)],
        ])

        await status_msg.edit_text(
            f"✅ *R2 ga yuklandi!*\n\n"
            f"📁 Fayl: `{filename}`\n"
            f"📦 Hajmi: `{_fmt_size(file_size)}`\n\n"
            f"🔗 Havola:\n`{url}`",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return url
    except Exception as e:
        await status_msg.edit_text(
            f"❌ R2 ga yuklashda xato:\n`{e}`",
            parse_mode="Markdown",
        )
        return None


async def send_file(
    message: Message,
    file_path: str,
    filename: str,
    caption: str = "",
    context=None,
    force_r2: bool = False,
):
    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(filename)[1].lower()
    is_video = ext in VIDEO_EXTENSIONS
    is_audio = ext in AUDIO_EXTENSIONS

    upload_mode = "document"
    if context is not None:
        from utils.user_settings import get as get_setting
        upload_mode = get_setting(context, "upload_mode")

    # Thumbnail
    meta = {}
    thumb_path = None
    custom_thumb_tmp = None

    if is_video:
        meta = await _get_video_meta(file_path)
        if context is not None:
            from utils.user_settings import ensure_loaded as _ensure, get as get_setting
            await _ensure(context.user_data.get("_user_id", 0), context)
            custom_path = get_setting(context, "custom_thumbnail")
            if custom_path and isinstance(custom_path, str) and os.path.exists(custom_path):
                thumb_path = custom_path
        if not thumb_path and meta.get("duration", 0) > 0:
            thumb_path = await _make_thumb(file_path, meta["duration"])
            custom_thumb_tmp = thumb_path

    # ─── > 2 GB → R2 yoki Gofile ─────────────────────────────────────────
    _user_id = context.user_data.get("_user_id", 0) if context else 0
    if file_size > PYROGRAM_LIMIT or force_r2:
        if r2_ok():
            await _upload_to_r2(message, file_path, filename, file_size, user_id=_user_id)
        else:
            # Fallback: Gofile
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
                    f"🔗 {link}\n\n_(Link 10 kun faol)_",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await status_msg.edit_text(
                    f"❌ Gofile.io ga yuklashda xato:\n`{e}`",
                    parse_mode="Markdown",
                )
        if custom_thumb_tmp and os.path.exists(custom_thumb_tmp):
            os.remove(custom_thumb_tmp)
        return

    # ─── <= 50 MB → PTB ──────────────────────────────────────────────────
    if file_size <= TELEGRAM_LIMIT:
        try:
            with open(file_path, "rb") as f:
                if upload_mode == "video" and is_video:
                    thumb_file = open(thumb_path, "rb") if thumb_path else None
                    try:
                        await message.reply_video(
                            video=f, filename=filename, caption=caption,
                            duration=meta.get("duration") or None,
                            width=meta.get("width") or None,
                            height=meta.get("height") or None,
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
            if custom_thumb_tmp and os.path.exists(custom_thumb_tmp):
                os.remove(custom_thumb_tmp)
        return

    # ─── 50 MB – 2 GB → Pyrogram MTProto ─────────────────────────────────
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
                chat_id=message.chat_id, video=file_path,
                file_name=filename, caption=caption,
                duration=meta.get("duration") or None,
                width=meta.get("width") or None,
                height=meta.get("height") or None,
                thumb=thumb_path, supports_streaming=True,
                progress=progress,
            )
        elif upload_mode == "audio" and is_audio:
            await client.send_audio(
                chat_id=message.chat_id, audio=file_path,
                file_name=filename, caption=caption,
                progress=progress,
            )
        else:
            await client.send_document(
                chat_id=message.chat_id, document=file_path,
                file_name=filename, caption=caption,
                progress=progress,
            )
    finally:
        if custom_thumb_tmp and os.path.exists(custom_thumb_tmp):
            os.remove(custom_thumb_tmp)

    try:
        await status_msg.delete()
    except Exception:
        pass
