"""
netfilm_handler.py — netfilm.world dan video/audio/subtitr yuklab olish.

Foydalanish:
  /netfilm https://netfilm.world/doctor-on-the-edge-EGz1AWsEG34
  /netfilm https://netfilm.world/doctor-on-the-edge-EGz1AWsEG34?se=1&ep=2
"""

import asyncio
import logging
import os
import re
import time
import uuid

import httpx
from pyrogram.enums import ParseMode
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID, TEMP_DIR
from handlers.save_restricted import get_user_client

logger = logging.getLogger(__name__)


def _md_escape(text) -> str:
    """Telegram Markdown (legacy) uchun maxsus belgilarni escape qiladi."""
    s = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


# ── Konstantlar ───────────────────────────────────────────────────────────────

API_BASE = "https://netfilm.world/wefeed-h5api-bff"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Referer": "https://netfilm.world/",
    "Origin": "https://netfilm.world",
}

# Pending streamlar: {user_id: {streams, title, episode_label}}
_pending: dict = {}


# ── URL parse ─────────────────────────────────────────────────────────────────

def _parse_url(url: str) -> dict | None:
    """
    Quyidagi URL formatlarini qabul qiladi:
      /doctor-on-the-edge-EGz1AWsEG34
      /doctor-on-the-edge-EGz1AWsEG34?se=1&ep=2
      /spa/videoPlayPage/?...subjectId=...
      /wefeed-h5api-bff/subject/play?subjectId=...&se=1&ep=1&detailPath=...
    """
    from urllib.parse import urlparse, parse_qs

    p = urlparse(url)
    qs = parse_qs(p.query)

    # To'liq API URL berilgan bo'lsa
    if "subjectId" in qs:
        return {
            "subjectId": qs["subjectId"][0],
            "se": int(qs.get("se", ["1"])[0]),
            "ep": int(qs.get("ep", ["1"])[0]),
            "detailPath": qs.get("detailPath", [""])[0],
        }

    # /slug-EGz1AWsEG34 yoki /slug-EGz1AWsEG34?se=1&ep=2
    path = p.path.rstrip("/").split("/")[-1]
    # detailPath = slug, subjectId keyinroq API dan topiladi
    return {
        "subjectId": None,
        "se": int(qs.get("se", ["1"])[0]),
        "ep": int(qs.get("ep", ["1"])[0]),
        "detailPath": path,
    }


# ── API so'rovlari ────────────────────────────────────────────────────────────

async def _get_subject_id(detail_path: str) -> str | None:
    """detailPath orqali subjectId ni topadi."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as c:
            r = await c.get(
                f"{API_BASE}/subject/detail",
                params={"detailPath": detail_path},
            )
            r.raise_for_status()
            data = r.json()
            # Turli response strukturalari
            sid = (
                data.get("subjectId")
                or data.get("data", {}).get("subjectId")
                or data.get("result", {}).get("subjectId")
            )
            return str(sid) if sid else None
    except Exception as e:
        logger.warning("_get_subject_id xato: %s", e)
        return None


async def _fetch_play_info(subject_id: str, se: int, ep: int, detail_path: str = "") -> dict | None:
    """Play API dan stream ma'lumotlarini oladi."""
    params = {
        "subjectId": subject_id,
        "se": se,
        "ep": ep,
    }
    if detail_path:
        params["detailPath"] = detail_path

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(f"{API_BASE}/subject/play", params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("_fetch_play_info xato: %s", e)
        return None


async def _fetch_subject_info(subject_id: str) -> dict:
    """Kino nomi va ma'lumotlarini oladi."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as c:
            r = await c.get(
                f"{API_BASE}/subject/detail",
                params={"subjectId": subject_id},
            )
            r.raise_for_status()
            return r.json()
    except Exception:
        return {}


# ── Stream listni parse qilish ────────────────────────────────────────────────

def _parse_streams(data: dict) -> list[dict]:
    """
    API response dan barcha streamlarni oladi.
    Qaytaradi: [{"type": "video"|"audio"|"subtitle", "label": str, "url": str, "lang": str, "size": int}]
    """
    streams = []

    # Turli API strukturalarini qo'llab-quvvatlash
    root = data.get("data") or data.get("result") or data

    # ── Video streamlar ───────────────────────────────────────────────────────
    # Format 1: videoList / streamList
    for key in ("videoList", "streamList", "videos", "streams"):
        for item in root.get(key, []):
            url = item.get("url") or item.get("videoUrl") or item.get("src") or ""
            if not url:
                continue
            label = (
                item.get("label")
                or item.get("quality")
                or item.get("resolution")
                or item.get("name")
                or "video"
            )
            streams.append({
                "type": "video",
                "label": str(label),
                "url": url,
                "lang": item.get("lang") or item.get("language") or "",
                "size": item.get("size") or item.get("fileSize") or 0,
            })

    # Format 2: playInfo.streamInfo
    play_info = root.get("playInfo") or root.get("play") or {}
    stream_info = play_info.get("streamInfo") or play_info.get("streams") or []
    for item in stream_info:
        url = item.get("url") or item.get("videoUrl") or ""
        if not url:
            continue
        label = item.get("label") or item.get("quality") or item.get("resolution") or "video"
        streams.append({
            "type": "video",
            "label": str(label),
            "url": url,
            "lang": item.get("lang") or "",
            "size": item.get("size") or 0,
        })

    # Format 3: to'g'ridan videoUrl
    if not streams:
        for key in ("videoUrl", "url", "playUrl", "streamUrl"):
            url = root.get(key) or play_info.get(key) or ""
            if url and url.startswith("http"):
                streams.append({
                    "type": "video",
                    "label": "Video",
                    "url": url,
                    "lang": "",
                    "size": 0,
                })
                break

    # ── Audio streamlar ───────────────────────────────────────────────────────
    for key in ("audioList", "audios", "audioTracks"):
        for item in root.get(key, []):
            url = item.get("url") or item.get("audioUrl") or ""
            if not url:
                continue
            lang = item.get("lang") or item.get("language") or item.get("label") or "audio"
            streams.append({
                "type": "audio",
                "label": f"🔊 {lang}",
                "url": url,
                "lang": lang,
                "size": item.get("size") or 0,
            })

    # ── Subtitrlar ────────────────────────────────────────────────────────────
    for key in ("subtitleList", "subtitles", "captionList", "captions"):
        for item in root.get(key, []):
            url = item.get("url") or item.get("subtitleUrl") or item.get("src") or ""
            if not url:
                continue
            lang = item.get("lang") or item.get("language") or item.get("label") or "sub"
            streams.append({
                "type": "subtitle",
                "label": f"💬 {lang}",
                "url": url,
                "lang": lang,
                "size": item.get("size") or 0,
            })

    return streams


# ── Asosiy handler ────────────────────────────────────────────────────────────

async def netfilm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    args = context.args

    if not args:
        await msg.reply_text(
            "❗ *Foydalanish:*\n"
            "`/netfilm https://netfilm.world/film-nomi`\n"
            "`/netfilm https://netfilm.world/serial?se=1&ep=3`",
            parse_mode="Markdown",
        )
        return

    url = args[0].strip()
    if "netfilm.world" not in url:
        await msg.reply_text("❌ Faqat `netfilm.world` URL lari qabul qilinadi!", parse_mode="Markdown")
        return

    parsed = _parse_url(url)
    if not parsed:
        await msg.reply_text("❌ URL ni tahlil qilib bo'lmadi!", parse_mode="Markdown")
        return

    status = await msg.reply_text(
        f"🔍 Ma'lumot olinmoqda...\n`{url}`",
        parse_mode="Markdown",
    )

    subject_id = parsed["subjectId"]
    se = parsed["se"]
    ep = parsed["ep"]
    detail_path = parsed["detailPath"]

    # subjectId yo'q bo'lsa detailPath orqali topamiz
    if not subject_id:
        await status.edit_text("🔍 Film ID topilmoqda...", parse_mode="Markdown")
        subject_id = await _get_subject_id(detail_path)

    if not subject_id:
        # Oxirgi urinish — to'g'ridan play ga so'rov
        await status.edit_text("🔍 Stream ma'lumotlari olinmoqda...", parse_mode="Markdown")
        data = await _fetch_play_info_by_path(detail_path, se, ep)
    else:
        await status.edit_text(
            f"📡 Stream ma'lumotlari olinmoqda...\n"
            f"Se: {se} | Ep: {ep}",
            parse_mode="Markdown",
        )
        data = await _fetch_play_info(subject_id, se, ep, detail_path)

    if not data:
        await status.edit_text(
            "❌ API dan ma'lumot olib bo'lmadi!\n\n"
            "Sabab: sahifa token talab qilishi yoki URL noto'g'ri bo'lishi mumkin.",
            parse_mode="Markdown",
        )
        return

    streams = _parse_streams(data)

    if not streams:
        # Raw JSON ni ko'rsatamiz (debug uchun)
        import json
        raw = json.dumps(data, ensure_ascii=False, indent=2)[:500]
        await status.edit_text(
            f"⚠️ Stream topilmadi.\n\n"
            f"API javobi:\n```\n{raw}\n```",
            parse_mode="Markdown",
        )
        return

    # Kino nomi
    title = _extract_title(data, detail_path)
    ep_label = f"S{se}E{ep}" if se > 0 else f"Ep{ep}"

    # Streamlarni ko'rsatamiz
    user_id = update.effective_user.id
    _pending[user_id] = {
        "streams": streams,
        "title": title,
        "ep_label": ep_label,
        "subject_id": subject_id,
        "se": se,
        "ep": ep,
    }

    keyboard = _build_stream_keyboard(streams, user_id)
    video_count = sum(1 for s in streams if s["type"] == "video")
    audio_count = sum(1 for s in streams if s["type"] == "audio")
    sub_count = sum(1 for s in streams if s["type"] == "subtitle")

    parts = []
    if video_count:
        parts.append(f"🎬 {video_count} video")
    if audio_count:
        parts.append(f"🔊 {audio_count} audio")
    if sub_count:
        parts.append(f"💬 {sub_count} subtitr")

    await status.edit_text(
        f"✅ *{_md_escape(title)}* — {_md_escape(ep_label)}\n\n"
        f"{'  •  '.join(parts)}\n\n"
        f"Yuklab olmoqchi bo'lgan streamni tanlang:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def _fetch_play_info_by_path(detail_path: str, se: int, ep: int) -> dict | None:
    """detailPath orqali to'g'ridan play so'rovi."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(
                f"{API_BASE}/subject/play",
                params={"detailPath": detail_path, "se": se, "ep": ep},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("_fetch_play_info_by_path xato: %s", e)
        return None


def _extract_title(data: dict, detail_path: str) -> str:
    root = data.get("data") or data.get("result") or data
    for key in ("title", "name", "subjectName", "movieName", "filmName"):
        val = root.get(key) or (root.get("playInfo") or {}).get(key) or ""
        if val:
            return str(val)
    # detailPath dan taxmin
    if detail_path:
        slug = detail_path.rsplit("-", 1)[0] if "-" in detail_path else detail_path
        return slug.replace("-", " ").title()
    return "Film"


def _build_stream_keyboard(streams: list, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for i, s in enumerate(streams):
        size_str = f" ({_fmt_size(s['size'])})" if s.get("size") else ""
        label = f"{s['label']}{size_str}"
        rows.append([InlineKeyboardButton(label, callback_data=f"nf_dl|{user_id}|{i}")])
    rows.append([InlineKeyboardButton("❌ Bekor", callback_data="nf_cancel")])
    return InlineKeyboardMarkup(rows)


# ── Callback handler ──────────────────────────────────────────────────────────

async def netfilm_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "nf_cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        return

    if not data.startswith("nf_dl|"):
        return

    parts = data.split("|")
    if len(parts) < 3:
        return

    _, owner_id_str, idx_str = parts
    owner_id = int(owner_id_str)
    idx = int(idx_str)
    user_id = query.from_user.id

    pending = _pending.get(owner_id)
    if not pending:
        await query.edit_message_text("❌ Ma'lumot eskirgan, qaytadan /netfilm yuboring.")
        return

    streams = pending["streams"]
    if idx >= len(streams):
        await query.edit_message_text("❌ Stream topilmadi!")
        return

    stream = streams[idx]
    title = pending["title"]
    ep_label = pending["ep_label"]

    status = await query.message.reply_text(
        f"⏳ *{_md_escape(title)}* — {_md_escape(ep_label)}\n"
        f"`{stream['label']}` yuklab olinmoqda...",
        parse_mode="Markdown",
    )

    await _download_stream(stream, title, ep_label, status)


# ── Stream yuklab olish ───────────────────────────────────────────────────────

def _fmt_size(b: int) -> str:
    if not b:
        return "?"
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


def _fmt_dur(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _progress_bar(percent: int, length: int = 14) -> str:
    filled = int(length * percent / 100)
    return "▰" * filled + "▱" * (length - filled)


async def _download_stream(stream: dict, title: str, ep_label: str, status_msg):
    url = stream["url"]
    stream_type = stream["type"]
    label = stream["label"]

    # Fayl kengaytmasini aniqlash
    ext = _guess_ext_from_url(url, stream_type)
    safe_title = re.sub(r'[^\w\s-]', '', title)[:40].strip()
    filename = f"{safe_title}_{ep_label}_{label}.{ext}"
    filename = re.sub(r'\s+', '_', filename)
    tmp_path = os.path.join(TEMP_DIR, f"nf_{uuid.uuid4().hex[:8]}_{filename}")

    try:
        # Content-Length olish
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as c:
            head = await c.head(url)
            total = int(head.headers.get("content-length", 0))

        total_str = _fmt_size(total) if total else "?"

        await status_msg.edit_text(
            f"⬇️ *{_md_escape(title)}* — {_md_escape(ep_label)}\n"
            f"`{label}`\n\n"
            f"`{'▱' * 14}` `0%`\n"
            f"`0.0 MB` / `{total_str}`",
            parse_mode="Markdown",
        )

        # Yuklab olish
        last_edit = [0.0]
        downloaded = [0]

        async with httpx.AsyncClient(headers=HEADERS, timeout=300, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    async for chunk in r.aiter_bytes(65536):
                        f.write(chunk)
                        downloaded[0] += len(chunk)

                        now = time.monotonic()
                        if now - last_edit[0] < 3.0:
                            continue
                        last_edit[0] = now

                        if total:
                            pct = min(int(downloaded[0] / total * 100), 99)
                            bar = _progress_bar(pct)
                            cur = _fmt_size(downloaded[0])
                            try:
                                await status_msg.edit_text(
                                    f"⬇️ *{_md_escape(title)}* — {_md_escape(ep_label)}\n"
                                    f"`{label}`\n\n"
                                    f"`{bar}` `{pct}%`\n"
                                    f"`{cur}` / `{total_str}`",
                                    parse_mode="Markdown",
                                )
                            except Exception:
                                pass

        actual_size = os.path.getsize(tmp_path)
        if actual_size == 0:
            await status_msg.edit_text("❌ Fayl bo'sh yuklandi!")
            return

        size_str = _fmt_size(actual_size)

        await status_msg.edit_text(
            f"✅ Yuklab olindi: `{size_str}`\n"
            f"📤 Guruhga yuborilmoqda...",
            parse_mode="Markdown",
        )

        # Guruhga yuborish
        client = await get_user_client()
        if not client:
            await status_msg.edit_text("❌ Userbot ulangmagan!")
            return

        caption = f"🎬 *{_md_escape(title)}* — {_md_escape(ep_label)}\n`{_md_escape(label)}`\n📦 {size_str}"

        try:
            await client.get_chat(ARCHIVE_GROUP_ID)
        except Exception:
            pass

        if stream_type == "video":
            meta = await _get_video_meta(tmp_path)
            thumb_path = None
            if meta.get("duration", 0) > 0:
                thumb_path = await _make_thumb(tmp_path, meta["duration"])

            try:
                await client.send_video(
                    chat_id=ARCHIVE_GROUP_ID,
                    video=tmp_path,
                    caption=caption,
                    supports_streaming=True,
                    duration=meta.get("duration") or None,
                    width=meta.get("width") or None,
                    height=meta.get("height") or None,
                    thumb=thumb_path or None,
                    parse_mode=ParseMode.MARKDOWN,
                )
            finally:
                if thumb_path and os.path.exists(thumb_path):
                    try:
                        os.remove(thumb_path)
                    except Exception:
                        pass

        elif stream_type == "audio":
            await client.send_audio(
                chat_id=ARCHIVE_GROUP_ID,
                audio=tmp_path,
                caption=caption,
                title=f"{title} — {ep_label}",
                parse_mode=ParseMode.MARKDOWN,
            )

        else:  # subtitle
            await client.send_document(
                chat_id=ARCHIVE_GROUP_ID,
                document=tmp_path,
                caption=caption,
                file_name=filename,
                parse_mode=ParseMode.MARKDOWN,
            )

        dur_str = ""
        if stream_type == "video":
            dur = meta.get("duration", 0)
            if dur:
                dur_str = f"\n⏱ `{_fmt_dur(dur)}`"
            res = meta.get("width") and meta.get("height")
            if res:
                dur_str += f"\n📐 `{meta['width']}×{meta['height']}`"

        await status_msg.edit_text(
            f"✅ *{_md_escape(title)}* — {_md_escape(ep_label)}\n"
            f"`{label}`\n\n"
            f"📦 `{size_str}`{dur_str}\n\n"
            f"🗂 Guruhga yuborildi!",
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.exception("_download_stream xato: %s", e)
        await status_msg.edit_text(f"❌ Xato:\n`{e}`", parse_mode="Markdown")

    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ── Yordamchilar ─────────────────────────────────────────────────────────────

def _guess_ext_from_url(url: str, stream_type: str) -> str:
    path = url.split("?")[0].lower()
    if path.endswith(".mp4"):
        return "mp4"
    if path.endswith(".mkv"):
        return "mkv"
    if path.endswith(".m3u8"):
        return "m3u8"
    if path.endswith(".mp3"):
        return "mp3"
    if path.endswith(".m4a"):
        return "m4a"
    if path.endswith(".srt"):
        return "srt"
    if path.endswith(".vtt"):
        return "vtt"
    if path.endswith(".ass"):
        return "ass"
    # Taxmin
    if stream_type == "video":
        return "mp4"
    if stream_type == "audio":
        return "m4a"
    return "srt"


import subprocess


def _get_video_meta_sync(file_path: str) -> dict:
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
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
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


async def _get_video_meta(file_path: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_video_meta_sync, file_path)


def _make_thumb_sync(file_path: str, duration: int) -> str | None:
    try:
        thumb_path = os.path.join(TEMP_DIR, f"nfthumb_{uuid.uuid4().hex}.jpg")
        seek = max(1, duration // 4) if duration > 4 else 1
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", file_path,
             "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "5", thumb_path],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        logger.warning("Thumbnail xato: %s", e)
    return None


async def _make_thumb(file_path: str, duration: int) -> str | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _make_thumb_sync, file_path, duration)
