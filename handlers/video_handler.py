import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from config import TEMP_DIR, BOT_TOKEN

CHUNK_SIZE = 10 * 1024 * 1024   # 10MB per chunk
MAX_PARALLEL = 8                  # 8 ta parallel yuklab olish


async def video_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    file = None
    file_name = "video"

    if message.video:
        file = message.video
        file_name = message.video.file_name or "video.mp4"
    elif message.document:
        doc = message.document
        mime = doc.mime_type or ""
        if not (mime.startswith("video/") or doc.file_name and
                any(doc.file_name.lower().endswith(ext) for ext in
                    [".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".wmv"])):
            await message.reply_text("❌ Bu fayl video emas. Iltimos video fayl yuboring.")
            return
        file = doc
        file_name = doc.file_name or "video.mp4"
    else:
        await message.reply_text("❌ Noto'g'ri fayl. Iltimos video yuboring.")
        return

    if file.file_size and file.file_size > 2_000_000_000:
        await message.reply_text("❌ Fayl juda katta (2 GB dan ortiq).")
        return

    status_msg = await message.reply_text("⏳ Video yuklanmoqda...")

    try:
        ext = os.path.splitext(file_name)[1].lstrip(".").lower() or "mp4"
        local_path = os.path.join(TEMP_DIR, f"{file.file_unique_id}.{ext}")

        if file.file_size and file.file_size <= 20 * 1024 * 1024:
            tg_file = await file.get_file()
            await tg_file.download_to_drive(local_path)
        else:
            await _download_fast(file.file_id, file.file_size, local_path, status_msg)

        context.user_data["video_path"] = local_path
        context.user_data["video_name"] = file_name
        context.user_data["state"] = None

        await status_msg.edit_text(
            f"✅ *Video qabul qilindi!*\n\n"
            f"📁 Fayl: `{file_name}`\n"
            f"📦 Hajmi: {_format_size(file.file_size)}\n\n"
            f"Quyidagi amallardan birini tanlang:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ Video yuklashda xato: {str(e)}\n"
            "Qaytadan urinib ko'ring."
        )


async def _get_download_url(session: aiohttp.ClientSession, file_id: str) -> str:
    async with session.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
        params={"file_id": file_id}
    ) as resp:
        data = await resp.json()
        if not data.get("ok"):
            raise Exception("Fayl URL olishda xato")
        file_path = data["result"]["file_path"]
        return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"


async def _download_chunk(session: aiohttp.ClientSession, url: str,
                           start: int, end: int, buf: bytearray, offset: int):
    headers = {"Range": f"bytes={start}-{end}"}
    async with session.get(url, headers=headers) as resp:
        data = await resp.read()
        buf[offset:offset + len(data)] = data


async def _download_fast(file_id: str, file_size: int, local_path: str, status_msg):
    connector = aiohttp.TCPConnector(limit=MAX_PARALLEL, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=3600, connect=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        url = await _get_download_url(session, file_id)

        # Fayl hajmini tekshirish (agar file_size noto'g'ri bo'lsa)
        async with session.head(url) as resp:
            total = int(resp.headers.get("Content-Length", file_size or 0))

        if total == 0:
            # Range so'rovlari ishlamasa — oddiy usul
            await _download_simple(session, url, local_path)
            return

        # Chunklar bo'yicha parallel yuklab olish
        chunks = []
        pos = 0
        while pos < total:
            end = min(pos + CHUNK_SIZE - 1, total - 1)
            chunks.append((pos, end))
            pos = end + 1

        buf = bytearray(total)
        downloaded = 0
        last_reported = -1

        sem = asyncio.Semaphore(MAX_PARALLEL)

        async def fetch_chunk(start, end):
            nonlocal downloaded
            async with sem:
                await _download_chunk(session, url, start, end, buf, start)
                downloaded += (end - start + 1)

                # Progress yangilash
                nonlocal last_reported
                percent = int(downloaded / total * 100)
                if percent - last_reported >= 5:
                    last_reported = percent
                    speed_hint = "🚀" if percent > 0 else "⏳"
                    try:
                        await status_msg.edit_text(
                            f"{speed_hint} Yuklanmoqda... {percent}%\n"
                            f"({downloaded // 1024 // 1024} / {total // 1024 // 1024} MB)"
                        )
                    except Exception:
                        pass

        await asyncio.gather(*[fetch_chunk(s, e) for s, e in chunks])

        with open(local_path, "wb") as f:
            f.write(buf)


async def _download_simple(session: aiohttp.ClientSession, url: str, local_path: str):
    """Fallback: Range so'rovlari ishlamasa."""
    async with session.get(url) as resp:
        with open(local_path, "wb") as f:
            async for chunk in resp.content.iter_chunked(4 * 1024 * 1024):
                f.write(chunk)


def _format_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "Noma'lum"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} GB"
