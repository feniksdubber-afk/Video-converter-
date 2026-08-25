"""
url_downloader.py — Istalgan URLdan video/audio/fayl yuklab olish.

Qo'llab-quvvatlaydi:
  - YouTube, TikTok, Instagram, Twitter/X, Facebook, Vimeo va 1000+ sayt (yt-dlp)
  - To'g'ridan HTTP/HTTPS URL (mp4, mkv, mp3, pdf va boshqalar)
  - M3U8 HLS streamlar (ffmpeg orqali)

Foydalanish:
  /dl https://youtube.com/watch?v=xxx           — video
  /dl https://youtube.com/watch?v=xxx audio     — faqat audio
  /dl https://example.com/file.mp4              — to'g'ridan yuklab olish
  /dl https://example.com/stream.m3u8           — HLS stream
"""

import asyncio
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx
from pyrogram.enums import ParseMode
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID, TEMP_DIR
from handlers.save_restricted import get_user_client

logger = logging.getLogger(__name__)

# ── Konstantlar ───────────────────────────────────────────────────────────────

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024   # 2 GB
CHUNK_SIZE    = 65536                     # 64 KB
PROGRESS_INTERVAL = 3.0                  # sekund

YTDLP_SUPPORTED_DOMAINS = {
    "youtube.com", "youtu.be", "m.youtube.com",
    "tiktok.com", "vm.tiktok.com",
    "instagram.com",
    "twitter.com", "x.com", "t.co",
    "facebook.com", "fb.watch", "fb.com",
    "vimeo.com",
    "dailymotion.com",
    "reddit.com", "v.redd.it",
    "twitch.tv",
    "ok.ru", "vk.com",
    "bilibili.com",
    "soundcloud.com",
    "spotify.com",
    "rutube.ru",
    "odnoklassniki.ru",
}

DIRECT_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts", ".wmv",
    ".mp3", ".m4a", ".aac", ".opus", ".flac", ".wav", ".ogg",
    ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".srt", ".vtt", ".ass",
}

SESSION_TTL = 600  # 10 daqiqa

# ── Ma'lumot sinflari ─────────────────────────────────────────────────────────

@dataclass
class MediaInfo:
    """yt-dlp dan olingan media ma'lumotlari."""
    title: str
    url: str
    duration: int = 0
    filesize: int = 0
    ext: str = "mp4"
    uploader: str = ""
    thumbnail: str = ""
    formats: list = field(default_factory=list)

@dataclass
class DownloadSession:
    url: str
    mode: str         # "ytdlp" | "direct" | "hls"
    audio_only: bool
    media_info: Optional[MediaInfo]
    formats: list     # [{id, label, ext, filesize, height}]
    created_at: float = field(default_factory=time.monotonic)

_sessions: dict[int, DownloadSession] = {}


# ── Yordamchi funksiyalar ─────────────────────────────────────────────────────

def _fmt_size(b: int) -> str:
    if not b:
        return "?"
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


def _fmt_dur(seconds: int) -> str:
    if not seconds:
        return ""
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _progress_bar(pct: int, length: int = 14) -> str:
    filled = int(length * pct / 100)
    return "▰" * filled + "▱" * (length - filled)


def _safe_filename(name: str, max_len: int = 60) -> str:
    name = re.sub(r'[^\w\s\-.]', '', name).strip()
    name = re.sub(r'\s+', '_', name)
    return name[:max_len] or f"file_{uuid.uuid4().hex[:8]}"


def _detect_mode(url: str) -> str:
    """URL turi asosida yuklab olish rejimini aniqlaydi."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")

    # HLS stream
    path = parsed.path.lower()
    if path.endswith(".m3u8") or "m3u8" in url.lower():
        return "hls"

    # yt-dlp qo'llab-quvvatlaydigan saytlar
    for d in YTDLP_SUPPORTED_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return "ytdlp"

    # To'g'ridan fayl
    for ext in DIRECT_EXTENSIONS:
        if path.endswith(ext):
            return "direct"

    # Noma'lum — avval yt-dlp bilan urinamiz
    return "ytdlp"


def _cleanup_sessions() -> None:
    now = time.monotonic()
    expired = [uid for uid, s in _sessions.items() if now - s.created_at > SESSION_TTL]
    for uid in expired:
        del _sessions[uid]


# ── yt-dlp bilan ma'lumot olish ───────────────────────────────────────────────

async def _ytdlp_info(url: str) -> Optional[MediaInfo]:
    """yt-dlp orqali media haqida ma'lumot oladi (yuklab olmaydi)."""
    def _run():
        try:
            r = subprocess.run(
                ["yt-dlp", "--dump-json", "--no-playlist",
                 "--socket-timeout", "20", url],
                capture_output=True, text=True, timeout=40,
            )
            if r.returncode != 0:
                logger.warning("yt-dlp --dump-json xato: %s", r.stderr[-500:])
                return None
            import json
            return json.loads(r.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.warning("_ytdlp_info xato: %s", e)
            return None

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _run)
    if not info:
        return None

    # Formatlarni tartiblaymiz
    formats = []
    seen_heights = set()
    for f in info.get("formats", []):
        if not f.get("url"):
            continue
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        if vcodec == "none" and acodec == "none":
            continue
        h = f.get("height") or 0
        ext = f.get("ext", "mp4")
        fsize = f.get("filesize") or f.get("filesize_approx") or 0
        fmt_id = f.get("format_id", "")
        note = f.get("format_note", "")

        label_parts = []
        if h:
            label_parts.append(f"{h}p")
        if note:
            label_parts.append(note)
        if ext:
            label_parts.append(ext)
        label = " ".join(label_parts) or fmt_id or "format"

        if vcodec != "none":  # video format
            if h and h in seen_heights:
                continue
            if h:
                seen_heights.add(h)
            formats.append({
                "id": fmt_id,
                "label": f"🎬 {label}",
                "ext": ext,
                "filesize": fsize,
                "height": h,
                "type": "video",
            })
        else:  # audio only
            formats.append({
                "id": fmt_id,
                "label": f"🔊 {label}",
                "ext": ext,
                "filesize": fsize,
                "height": 0,
                "type": "audio",
            })

    # Sifat bo'yicha tartiblaymiz (eng yaxshidan)
    formats.sort(key=lambda x: (x["type"] == "video", x.get("height", 0)), reverse=True)

    # Eng yaxshi formatni oldindan tanlaymiz
    best_fmt = info.get("format_id") or (formats[0]["id"] if formats else "best")

    return MediaInfo(
        title=info.get("title", "Video")[:80],
        url=url,
        duration=int(info.get("duration") or 0),
        filesize=int(info.get("filesize") or info.get("filesize_approx") or 0),
        ext=info.get("ext", "mp4"),
        uploader=info.get("uploader") or info.get("channel") or "",
        thumbnail=info.get("thumbnail", ""),
        formats=formats,
    )


# ── yt-dlp bilan yuklab olish ─────────────────────────────────────────────────

async def _ytdlp_download(
    url: str,
    fmt_id: Optional[str],
    audio_only: bool,
    status_msg,
    title: str,
) -> Optional[str]:
    """
    yt-dlp orqali yuklab oladi. Fayl yo'lini qaytaradi yoki None.
    """
    uid = uuid.uuid4().hex[:8]
    out_tmpl = os.path.join(TEMP_DIR, f"dl_{uid}.%(ext)s")

    cmd = ["yt-dlp", "--no-playlist", "--socket-timeout", "20",
           "--output", out_tmpl]

    if audio_only:
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    elif fmt_id:
        # Video + audio birlashtirish
        cmd += ["-f", f"{fmt_id}+bestaudio/{fmt_id}/best"]
    else:
        cmd += ["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"]

    cmd += ["--merge-output-format", "mp4"] if not audio_only else []
    cmd.append(url)

    def _run():
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
            )
            return proc
        except subprocess.TimeoutExpired:
            return None
        except Exception as e:
            logger.warning("yt-dlp download xato: %s", e)
            return None

    # Progress xabarlarini yangilash
    last_edit = [time.monotonic()]

    async def _progress_loop():
        dots = ["", ".", "..", "..."]
        i = 0
        while True:
            await asyncio.sleep(4)
            i = (i + 1) % 4
            try:
                await status_msg.edit_text(
                    f"⬇️ *{title}*\n"
                    f"yt-dlp yuklab olinmoqda{dots[i]}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    task = asyncio.create_task(_progress_loop())
    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, _run)
    task.cancel()

    if not proc or proc.returncode != 0:
        err = proc.stderr[-600:] if proc else "timeout"
        logger.warning("yt-dlp download muvaffaqiyatsiz: %s", err)
        return None

    # Yuklab olingan faylni topamiz
    prefix = f"dl_{uid}"
    for f in os.listdir(TEMP_DIR):
        if f.startswith(prefix):
            return os.path.join(TEMP_DIR, f)
    return None


# ── To'g'ridan HTTP yuklab olish ──────────────────────────────────────────────

async def _direct_download(url: str, status_msg, title: str) -> Optional[str]:
    """To'g'ridan HTTP/HTTPS URLdan yuklab oladi."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    # Kengaytmani URLdan aniqlaymiz
    path = urlparse(url).path.lower()
    ext = "bin"
    for e in DIRECT_EXTENSIONS:
        if path.endswith(e):
            ext = e.lstrip(".")
            break

    uid = uuid.uuid4().hex[:8]
    tmp_path = os.path.join(TEMP_DIR, f"dl_{uid}.{ext}")

    try:
        total = 0
        try:
            async with httpx.AsyncClient(headers=headers, timeout=10) as c:
                head = await c.head(url, follow_redirects=True)
                total = int(head.headers.get("content-length", 0))
                # Content-Disposition dan kengaytma olishga urinamiz
                cd = head.headers.get("content-disposition", "")
                if "filename=" in cd:
                    fn_match = re.search(r'filename=["\']?([^"\';\n]+)', cd)
                    if fn_match:
                        fn = fn_match.group(1).strip()
                        if "." in fn:
                            ext = fn.rsplit(".", 1)[-1][:10]
                            tmp_path = os.path.join(TEMP_DIR, f"dl_{uid}.{ext}")
        except Exception:
            pass

        if total and total > MAX_FILE_SIZE:
            await status_msg.edit_text(
                f"❌ Fayl juda katta: `{_fmt_size(total)}`\n"
                f"Maksimal: `{_fmt_size(MAX_FILE_SIZE)}`",
                parse_mode="Markdown",
            )
            return None

        total_str = _fmt_size(total)
        start_time = time.monotonic()
        downloaded = [0]
        last_edit_t = [0.0]
        last_dl = [0]
        last_speed_t = [start_time]

        async with httpx.AsyncClient(headers=headers, timeout=300, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    async for chunk in r.aiter_bytes(CHUNK_SIZE):
                        f.write(chunk)
                        downloaded[0] += len(chunk)

                        now = time.monotonic()
                        if now - last_edit_t[0] < PROGRESS_INTERVAL:
                            continue
                        last_edit_t[0] = now

                        elapsed = now - last_speed_t[0]
                        speed = (downloaded[0] - last_dl[0]) / max(elapsed, 0.1)
                        last_dl[0] = downloaded[0]
                        last_speed_t[0] = now
                        speed_str = f"{_fmt_size(int(speed))}/s"

                        if total:
                            pct = min(int(downloaded[0] / total * 100), 99)
                            bar = _progress_bar(pct)
                            cur = _fmt_size(downloaded[0])
                            eta_str = ""
                            if speed > 0 and total > downloaded[0]:
                                eta = int((total - downloaded[0]) / speed)
                                eta_str = f"\n⏱ Qoldi: `{_fmt_dur(eta)}`"
                            try:
                                await status_msg.edit_text(
                                    f"⬇️ *{title}*\n\n"
                                    f"`{bar}` `{pct}%`\n"
                                    f"`{cur}` / `{total_str}`  🚀 `{speed_str}`{eta_str}",
                                    parse_mode="Markdown",
                                )
                            except Exception:
                                pass
                        else:
                            try:
                                await status_msg.edit_text(
                                    f"⬇️ *{title}*\n\n"
                                    f"Yuklab olinmoqda: `{_fmt_size(downloaded[0])}`  🚀 `{speed_str}`",
                                    parse_mode="Markdown",
                                )
                            except Exception:
                                pass

        return tmp_path if os.path.getsize(tmp_path) > 0 else None

    except httpx.TimeoutException:
        await status_msg.edit_text("❌ Yuklab olish vaqti tugadi!", parse_mode="Markdown")
        return None
    except httpx.HTTPStatusError as e:
        await status_msg.edit_text(
            f"❌ Server xatosi: `{e.response.status_code}`",
            parse_mode="Markdown",
        )
        return None
    except Exception as e:
        logger.exception("_direct_download xato: %s", e)
        await status_msg.edit_text(f"❌ Xato: `{e}`", parse_mode="Markdown")
        return None
    finally:
        # Agar fayl bo'sh bo'lsa o'chiramiz
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) == 0:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ── HLS (M3U8) yuklab olish ───────────────────────────────────────────────────

async def _hls_download(url: str, status_msg, title: str) -> Optional[str]:
    """ffmpeg orqali HLS streamni yuklab oladi."""
    uid = uuid.uuid4().hex[:8]
    tmp_path = os.path.join(TEMP_DIR, f"hls_{uid}.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-headers", "User-Agent: Mozilla/5.0\r\n",
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        tmp_path,
    ]

    start_time = time.monotonic()
    last_edit = [0.0]

    def _run():
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return None
        except Exception as e:
            logger.warning("HLS download xato: %s", e)
            return None

    async def _progress_loop():
        elapsed = 0
        while True:
            await asyncio.sleep(5)
            elapsed = int(time.monotonic() - start_time)
            try:
                await status_msg.edit_text(
                    f"📡 *{title}*\n"
                    f"HLS stream yuklab olinmoqda...\n"
                    f"⏱ Ketgan vaqt: `{_fmt_dur(elapsed)}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    task = asyncio.create_task(_progress_loop())
    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, _run)
    task.cancel()

    if not proc or proc.returncode != 0:
        err = proc.stderr[-500:] if proc else "timeout"
        logger.warning("ffmpeg HLS muvaffaqiyatsiz: %s", err)
        await status_msg.edit_text(
            f"❌ HLS yuklab olish muvaffaqiyatsiz!\n```\n{err[:300]}\n```",
            parse_mode="Markdown",
        )
        return None

    if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
        return None

    return tmp_path


# ── Guruhga yuborish ──────────────────────────────────────────────────────────

async def _send_to_archive(
    tmp_path: str,
    title: str,
    extra_caption: str,
    status_msg,
    target_chat_id: Optional[int] = None,
) -> bool:
    """Faylni belgilangan chatga (standart: arxiv guruhi) yuboradi."""
    ext = os.path.splitext(tmp_path)[1].lower()
    size = os.path.getsize(tmp_path)
    size_str = _fmt_size(size)
    filename = _safe_filename(title) + ext

    caption = f"📥 *{title}*\n📦 {size_str}"
    if extra_caption:
        caption += f"\n{extra_caption}"

    chat_id = target_chat_id if target_chat_id is not None else ARCHIVE_GROUP_ID

    client = await get_user_client()
    if not client:
        await status_msg.edit_text(
            "❌ Userbot ulangmagan! Admin bilan bog'laning.",
            parse_mode="Markdown",
        )
        return False

    try:
        await client.get_chat(chat_id)
    except Exception:
        pass

    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts"}
    AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".flac", ".wav"}

    try:
        if ext in VIDEO_EXTS:
            meta = await _get_video_meta(tmp_path)
            thumb = None
            if meta.get("duration", 0) > 0:
                thumb = await _make_thumb(tmp_path, meta["duration"])
            try:
                await client.send_video(
                    chat_id=chat_id,
                    video=tmp_path,
                    caption=caption,
                    supports_streaming=True,
                    duration=meta.get("duration") or None,
                    width=meta.get("width") or None,
                    height=meta.get("height") or None,
                    thumb=thumb or None,
                    parse_mode=ParseMode.MARKDOWN,
                )
            finally:
                if thumb and os.path.exists(thumb):
                    try:
                        os.remove(thumb)
                    except Exception:
                        pass
        elif ext in AUDIO_EXTS:
            await client.send_audio(
                chat_id=chat_id,
                audio=tmp_path,
                caption=caption,
                title=title,
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await client.send_document(
                chat_id=chat_id,
                document=tmp_path,
                caption=caption,
                file_name=filename,
                parse_mode=ParseMode.MARKDOWN,
            )
        return True
    except Exception as e:
        logger.exception("Guruhga yuborish xato: %s", e)
        await status_msg.edit_text(
            f"✅ Yuklab olindi, lekin guruhga yuborishda xato:\n`{e}`",
            parse_mode="Markdown",
        )
        return False


# ── Asosiy handler ────────────────────────────────────────────────────────────

async def dl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /dl URL [audio]
    """
    msg = update.effective_message
    args = context.args

    if not args:
        await msg.reply_text(
            "❗ *Foydalanish:*\n\n"
            "`/dl https://youtube.com/watch?v=xxx`\n"
            "↳ YouTube, TikTok, Instagram, Twitter va 1000+ sayt\n\n"
            "`/dl https://youtube.com/watch?v=xxx audio`\n"
            "↳ Faqat audio (MP3)\n\n"
            "`/dl https://example.com/film.mp4`\n"
            "↳ To'g'ridan HTTP fayl\n\n"
            "`/dl https://example.com/stream.m3u8`\n"
            "↳ HLS stream",
            parse_mode="Markdown",
        )
        return

    url = args[0].strip()
    audio_only = len(args) > 1 and args[1].lower() in ("audio", "mp3", "a", "ovoz")

    # URL tekshiruv
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed_url = urlparse(url)
    if not parsed_url.netloc:
        await msg.reply_text("❌ Noto'g'ri URL!", parse_mode="Markdown")
        return

    _cleanup_sessions()
    user_id = update.effective_user.id
    mode = _detect_mode(url)

    status = await msg.reply_text(
        f"🔍 URL tahlil qilinmoqda...\n`{url[:80]}`",
        parse_mode="Markdown",
    )

    # ── yt-dlp rejimi: avval ma'lumot olamiz, formatlarni ko'rsatamiz ──────
    if mode == "ytdlp" and not audio_only:
        await status.edit_text(
            f"🔍 Media ma'lumotlari olinmoqda...",
            parse_mode="Markdown",
        )
        info = await _ytdlp_info(url)

        if not info:
            # yt-dlp ishlamasa to'g'ridan urinamiz
            await status.edit_text(
                "⚠️ yt-dlp ishlamadi, to'g'ridan urinilmoqda...",
                parse_mode="Markdown",
            )
            mode = "direct"
        elif not info.formats:
            # Format yo'q — eng yaxshisini yuklab olamiz
            await status.edit_text(
                f"⬇️ *{info.title}* yuklab olinmoqda...",
                parse_mode="Markdown",
            )
            tmp = await _ytdlp_download(url, None, False, status, info.title)
            await _finish_download(tmp, info.title, info, status)
            return
        else:
            # Formatlarni ko'rsatamiz
            _sessions[user_id] = DownloadSession(
                url=url, mode="ytdlp", audio_only=False,
                media_info=info, formats=info.formats,
            )
            keyboard = _build_format_keyboard(info.formats, user_id)
            dur_str = f" ⏱ `{_fmt_dur(info.duration)}`" if info.duration else ""
            up_str = f"\n👤 {info.uploader}" if info.uploader else ""

            await status.edit_text(
                f"✅ *{info.title}*{up_str}\n"
                f"🎬 {len([f for f in info.formats if f['type']=='video'])} video  "
                f"🔊 {len([f for f in info.formats if f['type']=='audio'])} audio"
                f"{dur_str}\n\n"
                f"Sifat tanlang:",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            return

    # ── Audio only rejimi ──────────────────────────────────────────────────
    if audio_only:
        await status.edit_text(
            f"🎵 Audio yuklab olinmoqda...",
            parse_mode="Markdown",
        )
        info = await _ytdlp_info(url) if mode == "ytdlp" else None
        title = info.title if info else urlparse(url).path.split("/")[-1] or "Audio"
        tmp = await _ytdlp_download(url, None, True, status, title)
        await _finish_download(tmp, title, info, status)
        return

    # ── HLS rejimi ─────────────────────────────────────────────────────────
    if mode == "hls":
        title = urlparse(url).path.split("/")[-1].replace(".m3u8", "") or "HLS Stream"
        await status.edit_text(
            f"📡 HLS stream yuklab olinmoqda...\n`{title}`",
            parse_mode="Markdown",
        )
        tmp = await _hls_download(url, status, title)
        await _finish_download(tmp, title, None, status)
        return

    # ── To'g'ridan HTTP ────────────────────────────────────────────────────
    path = urlparse(url).path
    title = path.split("/")[-1] or "Fayl"
    await status.edit_text(
        f"⬇️ Yuklab olinmoqda...\n`{title}`",
        parse_mode="Markdown",
    )
    tmp = await _direct_download(url, status, title)
    await _finish_download(tmp, title, None, status)


# ── Callback handler ──────────────────────────────────────────────────────────

async def dl_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "dl_cancel":
        user_id = query.from_user.id
        _sessions.pop(user_id, None)
        await query.edit_message_text("❌ Bekor qilindi.")
        return

    if not data.startswith("dl_fmt|"):
        return

    parts = data.split("|")
    if len(parts) < 3:
        return

    try:
        owner_id = int(parts[1])
        fmt_idx = int(parts[2])
    except ValueError:
        return

    session = _sessions.get(owner_id)
    if not session:
        await query.edit_message_text(
            "❌ Session muddati tugadi! Qaytadan `/dl` yuboring.",
            parse_mode="Markdown",
        )
        return

    if time.monotonic() - session.created_at > SESSION_TTL:
        _sessions.pop(owner_id, None)
        await query.edit_message_text(
            "❌ Session muddati tugadi! Qaytadan `/dl` yuboring.",
            parse_mode="Markdown",
        )
        return

    if fmt_idx >= len(session.formats):
        await query.edit_message_text("❌ Format topilmadi!")
        return

    fmt = session.formats[fmt_idx]
    info = session.media_info
    title = info.title if info else "Video"

    # Klaviaturani olib tashlaymiz
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    status = await query.message.reply_text(
        f"⬇️ *{title}*\n"
        f"`{fmt['label']}` yuklab olinmoqda...",
        parse_mode="Markdown",
    )

    tmp = await _ytdlp_download(
        session.url,
        fmt["id"] if fmt["type"] == "video" else None,
        fmt["type"] == "audio",
        status,
        title,
    )
    await _finish_download(tmp, title, info, status)


# ── Yakunlash ─────────────────────────────────────────────────────────────────

async def _finish_download(
    tmp_path: Optional[str],
    title: str,
    info: Optional[MediaInfo],
    status_msg,
) -> None:
    """Fayl muvaffaqiyatli yuklab olinganidan so'ng arxivga yuboradi."""
    if not tmp_path or not os.path.exists(tmp_path):
        try:
            cur_text = status_msg.text or ""
            if "❌" not in cur_text:
                await status_msg.edit_text(
                    "❌ Fayl yuklab olinmadi!\n\n"
                    "Mumkin sabablar:\n"
                    "• URL ochiq emas yoki login talab qiladi\n"
                    "• yt-dlp bu saytni qo'llab-quvvatlamaydi\n"
                    "• Server bilan muammo",
                    parse_mode="Markdown",
                )
        except Exception:
            pass
        return

    size = os.path.getsize(tmp_path)
    size_str = _fmt_size(size)

    extra = ""
    if info:
        if info.uploader:
            extra += f"👤 {info.uploader}"
        if info.duration:
            extra += f"  ⏱ `{_fmt_dur(info.duration)}`"

    await status_msg.edit_text(
        f"✅ Yuklab olindi: `{size_str}`\n"
        f"📤 Yuborilmoqda...",
        parse_mode="Markdown",
    )

    ok = await _send_to_archive(
        tmp_path, title, extra, status_msg,
        target_chat_id=status_msg.chat_id,
    )

    if ok:
        ext = os.path.splitext(tmp_path)[1].lower()
        meta_str = ""
        if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
            meta = await _get_video_meta(tmp_path)
            if meta.get("duration"):
                meta_str += f"\n⏱ `{_fmt_dur(meta['duration'])}`"
            if meta.get("width") and meta.get("height"):
                meta_str += f"\n📐 `{meta['width']}×{meta['height']}`"

        await status_msg.edit_text(
            f"✅ *{title}*\n\n"
            f"📦 `{size_str}`{meta_str}\n"
            f"🗂 Guruhga yuborildi!",
            parse_mode="Markdown",
        )

    try:
        os.remove(tmp_path)
    except Exception:
        pass


def _build_format_keyboard(formats: list, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for i, f in enumerate(formats[:10]):  # Max 10 format
        size_str = f" ({_fmt_size(f['filesize'])})" if f.get("filesize") else ""
        rows.append([InlineKeyboardButton(
            f"{f['label']}{size_str}",
            callback_data=f"dl_fmt|{user_id}|{i}",
        )])
    rows.append([InlineKeyboardButton("❌ Bekor", callback_data="dl_cancel")])
    return InlineKeyboardMarkup(rows)


# ── Video meta & thumbnail ────────────────────────────────────────────────────

def _get_video_meta_sync(path: str) -> dict:
    meta = {"duration": 0, "width": 0, "height": 0}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, text=True, timeout=30,
        )
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "duration":
                    try:
                        meta["duration"] = int(float(v))
                    except ValueError:
                        pass
                elif k == "width":
                    try:
                        meta["width"] = int(v)
                    except ValueError:
                        pass
                elif k == "height":
                    try:
                        meta["height"] = int(v)
                    except ValueError:
                        pass
    except Exception as e:
        logger.warning("ffprobe xato: %s", e)
    return meta


async def _get_video_meta(path: str) -> dict:
    return await asyncio.get_running_loop().run_in_executor(None, _get_video_meta_sync, path)


def _make_thumb_sync(path: str, duration: int) -> Optional[str]:
    try:
        thumb = os.path.join(TEMP_DIR, f"thumb_{uuid.uuid4().hex[:8]}.jpg")
        seek = max(1, duration // 4)
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", path,
             "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "5", thumb],
            capture_output=True, timeout=30,
        )
        return thumb if r.returncode == 0 and os.path.exists(thumb) else None
    except Exception:
        return None


async def _make_thumb(path: str, duration: int) -> Optional[str]:
    return await asyncio.get_running_loop().run_in_executor(None, _make_thumb_sync, path, duration)
