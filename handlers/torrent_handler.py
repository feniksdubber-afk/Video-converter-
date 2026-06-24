"""
torrent_handler.py — Torrent qidirish va yuklab olish.

Buyruqlar:
  /torrent <qidiruv>          — torrent qidiradi (1337x, Rutor, YTS)
  /torrent magnet:<...>       — magnet linkni yuklab oladi
  /torrent https://...torrent — .torrent faylni yuklab oladi

Yuklab olish uchun aria2c ishlatiladi (yengil, tez, daemon shart emas).
"""

import asyncio
import logging
import os
import re
import time
import uuid
import json
import subprocess
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus, urlparse

import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID, TEMP_DIR
from handlers.save_restricted import get_user_client

logger = logging.getLogger(__name__)

# ── Konstantlar ───────────────────────────────────────────────────────────────

MAX_FILE_SIZE   = 4 * 1024 * 1024 * 1024   # 4 GB
SESSION_TTL     = 900                        # 15 daqiqa
SEARCH_TIMEOUT  = 15                         # sekund
DL_TIMEOUT      = 3600                       # 1 soat

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.6099.130 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Ma'lumot sinflari ─────────────────────────────────────────────────────────

@dataclass
class TorrentResult:
    title: str
    magnet: str
    size: str
    seeds: int
    leeches: int
    source: str
    category: str = ""
    info_url: str = ""

@dataclass
class TorrentSession:
    results: list[TorrentResult]
    query: str
    created_at: float = field(default_factory=time.monotonic)

_sessions: dict[int, TorrentSession] = {}


# ── Yordamchilar ─────────────────────────────────────────────────────────────

def _fmt_size(b_str: str) -> str:
    """'1.5 GB', '700 MB' kabi stringlarni qaytaradi (agar raqam bo'lsa)."""
    return b_str or "?"

def _fmt_seeds(n: int) -> str:
    if n > 1000:
        return f"{n/1000:.1f}K"
    return str(n)

def _progress_bar(pct: int, length: int = 14) -> str:
    filled = int(length * pct / 100)
    return "▰" * filled + "▱" * (length - filled)

def _fmt_dur(seconds: int) -> str:
    if not seconds:
        return ""
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def _fmt_bytes(b: int) -> str:
    if not b:
        return "?"
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"

def _cleanup_sessions() -> None:
    now = time.monotonic()
    for uid in [k for k, v in _sessions.items() if now - v.created_at > SESSION_TTL]:
        del _sessions[uid]


# ── Qidiruv manbalari ─────────────────────────────────────────────────────────

async def _search_1337x(query: str, limit: int = 5) -> list[TorrentResult]:
    """1337x.to dan qidiradi."""
    results = []
    url = f"https://1337x.to/search/{quote_plus(query)}/1/"
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            html = r.text

        # Jadval qatorlarini parse qilamiz
        rows = re.findall(
            r'<tr>(.*?)</tr>', html, re.DOTALL
        )
        for row in rows[1:limit+1]:  # header o'tkazamiz
            name_m = re.search(r'class="name"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.DOTALL)
            seeds_m = re.search(r'class="seeds"[^>]*>(\d+)<', row)
            leeches_m = re.search(r'class="leeches"[^>]*>(\d+)<', row)
            size_m = re.search(r'class="size"[^>]*>([\d.,]+\s*[KMGT]?B)', row, re.I)

            if not name_m:
                continue

            info_path = name_m.group(1)
            title = re.sub(r'<[^>]+>', '', name_m.group(2)).strip()
            seeds = int(seeds_m.group(1)) if seeds_m else 0
            leeches = int(leeches_m.group(1)) if leeches_m else 0
            size = size_m.group(1) if size_m else "?"

            # Magnet linkni detail sahifadan olamiz
            info_url = f"https://1337x.to{info_path}"
            magnet = await _get_magnet_1337x(c if False else None, info_url)

            results.append(TorrentResult(
                title=title[:80],
                magnet=magnet,
                size=size,
                seeds=seeds,
                leeches=leeches,
                source="1337x",
                info_url=info_url,
            ))
    except Exception as e:
        logger.warning("1337x qidiruv xato: %s", e)
    return results


async def _get_magnet_1337x(_, info_url: str) -> str:
    """1337x detail sahifasidan magnet linkni oladi."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as c:
            r = await c.get(info_url)
            m = re.search(r'(magnet:\?[^"\'<\s]+)', r.text)
            return m.group(1) if m else ""
    except Exception:
        return ""


async def _search_yts(query: str, limit: int = 5) -> list[TorrentResult]:
    """YTS.mx API dan kino qidiradi."""
    results = []
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT) as c:
            r = await c.get(
                "https://yts.mx/api/v2/list_movies.json",
                params={"query_term": query, "limit": limit, "sort_by": "seeds"},
            )
            data = r.json()

        movies = data.get("data", {}).get("movies") or []
        for movie in movies:
            title = movie.get("title", "?")
            year = movie.get("year", "")
            for torrent in movie.get("torrents", [])[:2]:
                quality = torrent.get("quality", "?")
                codec = torrent.get("video_codec", "")
                size = torrent.get("size", "?")
                seeds = torrent.get("seeds", 0)
                leeches = torrent.get("peers", 0)
                magnet = _build_yts_magnet(torrent.get("hash", ""), f"{title} ({year}) [{quality}]")
                results.append(TorrentResult(
                    title=f"{title} ({year}) [{quality} {codec}]"[:80],
                    magnet=magnet,
                    size=size,
                    seeds=seeds,
                    leeches=leeches,
                    source="YTS",
                    category="Movie",
                    info_url=movie.get("url", ""),
                ))
    except Exception as e:
        logger.warning("YTS qidiruv xato: %s", e)
    return results


def _build_yts_magnet(info_hash: str, display_name: str) -> str:
    """YTS torrent hash dan magnet link yasaydi."""
    trackers = [
        "udp://open.demonii.com:1337/announce",
        "udp://tracker.openbittorrent.com:80",
        "udp://tracker.coppersurfer.tk:6969",
        "udp://glotorrents.pw:6969/announce",
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://torrent.gresille.org:80/announce",
        "udp://p4p.arenabg.com:1337",
        "udp://tracker.leechers-paradise.org:6969",
    ]
    dn = quote_plus(display_name)
    tr = "&".join(f"tr={quote_plus(t)}" for t in trackers)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={dn}&{tr}"


async def _search_rutor(query: str, limit: int = 5) -> list[TorrentResult]:
    """Rutor.info dan qidiradi (rus/uzbek kino uchun yaxshi)."""
    results = []
    seen_magnets: set[str] = set()
    url = f"https://rutor.info/search/0/0/100/0/{quote_plus(query)}"
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            html = r.text

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        for row in rows:
            if len(results) >= limit:
                break
            magnet_m = re.search(r'href="(magnet:\?[^"]+)"', row)
            name_m = re.search(r'<a[^>]+href="/torrent/[^"]*"[^>]*>(.*?)</a>', row, re.DOTALL)
            size_m = re.search(r'(\d+[\d.,]*\s*(?:KB|MB|GB|TB))', row, re.I)
            seeds_m = re.search(r'<span[^>]*green[^>]*>(\d+)</span>', row)
            leeches_m = re.search(r'<span[^>]*red[^>]*>(\d+)</span>', row)

            if not magnet_m or not name_m:
                continue

            magnet = magnet_m.group(1)
            # Infohash bo'yicha dublikat tekshiruv
            ih_m = re.search(r'btih:([a-fA-F0-9]{40})', magnet, re.I)
            ih = ih_m.group(1).lower() if ih_m else magnet[:60]
            if ih in seen_magnets:
                continue
            seen_magnets.add(ih)

            title = re.sub(r'<[^>]+>', '', name_m.group(1)).strip()
            if not title:
                continue

            results.append(TorrentResult(
                title=title[:80],
                magnet=magnet,
                size=size_m.group(1) if size_m else "?",
                seeds=int(seeds_m.group(1)) if seeds_m else 0,
                leeches=int(leeches_m.group(1)) if leeches_m else 0,
                source="Rutor",
            ))
    except Exception as e:
        logger.warning("Rutor qidiruv xato: %s", e)
    return results


async def _search_all(query: str) -> list[TorrentResult]:
    """Barcha manbalardan parallel qidiradi, dublikatlarni olib tashlaydi."""
    tasks = [
        _search_yts(query, 4),
        _search_1337x(query, 5),
        _search_rutor(query, 4),
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)
    merged = []
    seen_hashes: set[str] = set()

    for res in all_results:
        if not isinstance(res, list):
            continue
        for r in res:
            # Infohash bo'yicha global dedup
            ih_m = re.search(r'btih:([a-fA-F0-9]{40})', r.magnet or "", re.I)
            ih = ih_m.group(1).lower() if ih_m else None
            if ih and ih in seen_hashes:
                continue
            if ih:
                seen_hashes.add(ih)
            merged.append(r)

    # Seeds bo'yicha tartiblaymiz (seederli natijalar birinchi)
    merged.sort(key=lambda x: x.seeds, reverse=True)
    return merged[:10]


# ── aria2c orqali yuklab olish ────────────────────────────────────────────────

def _aria2c_available() -> bool:
    """aria2c o'rnatilganini tekshiradi."""
    try:
        r = subprocess.run(["aria2c", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


async def _download_torrent(
    magnet_or_url: str,
    status_msg,
    title: str,
) -> Optional[str]:
    """
    aria2c orqali magnet yoki .torrent URL ni yuklab oladi.
    Eng katta faylni qaytaradi (asosiy video fayl).
    """
    if not _aria2c_available():
        await status_msg.edit_text(
            "❌ `aria2c` o'rnatilmagan!\n\n"
            "Server adminiga `aria2c` o'rnatishni so'rang:\n"
            "`apt install aria2`",
            parse_mode="Markdown",
        )
        return None

    dl_dir = os.path.join(TEMP_DIR, f"torrent_{uuid.uuid4().hex[:8]}")
    os.makedirs(dl_dir, exist_ok=True)

    cmd = [
        "aria2c",
        "--dir", dl_dir,
        "--seed-time=0",                    # Seeding yo'q
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--file-allocation=none",
        "--console-log-level=warn",
        "--summary-interval=5",
        "--bt-stop-timeout=300",            # 5 daqiqa seeder kutish
        "--bt-tracker-connect-timeout=10",
        "--bt-tracker-timeout=20",
        "--follow-torrent=mem",             # .torrent URL bo'lsa xotiradan
        "--select-file=",                   # barcha fayllarni yuklaymiz
        magnet_or_url,
    ]

    start_time = time.monotonic()
    proc = None

    try:
        loop = asyncio.get_running_loop()

        def _start_proc():
            return subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

        proc = await loop.run_in_executor(None, _start_proc)

        last_edit = [0.0]
        downloaded_bytes = [0]
        total_bytes = [0]
        speed_str = ["?"]
        eta_str = [""]
        peers = [0]

        async def _read_output():
            """aria2c stdout ni okish va progress yangilash."""
            while True:
                line = await loop.run_in_executor(None, proc.stdout.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                # aria2c summary line: [#abc 10MiB/100MiB(10%) CN:5 DL:1.2MiB ETA:1m30s]
                m = re.search(
                    r'\[#\w+\s+([\d.]+\w+)/([\d.]+\w+)\((\d+)%\).*?CN:(\d+).*?DL:([\d.]+\w+).*?ETA:([^\]]+)\]',
                    line
                )
                if m:
                    pct = int(m.group(3))
                    speed_str[0] = m.group(5) + "/s"
                    eta_str[0] = m.group(6)
                    peers[0] = int(m.group(4))

                    now = time.monotonic()
                    if now - last_edit[0] < 4.0:
                        continue
                    last_edit[0] = now

                    bar = _progress_bar(pct)
                    elapsed = int(now - start_time)
                    try:
                        await status_msg.edit_text(
                            f"🧲 *{title[:50]}*\n\n"
                            f"`{bar}` `{pct}%`\n"
                            f"🚀 `{speed_str[0]}`  👥 `{peers[0]} peer`\n"
                            f"⏱ ETA: `{eta_str[0]}`  ⏳ `{_fmt_dur(elapsed)}`",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass

        read_task = asyncio.create_task(_read_output())

        # Timeout bilan kutamiz
        try:
            return_code = await asyncio.wait_for(
                loop.run_in_executor(None, proc.wait),
                timeout=DL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await status_msg.edit_text(
                "❌ Yuklab olish vaqti tugadi (1 soat)!\n"
                "Torrent seederlar yetarli emas bo'lishi mumkin.",
                parse_mode="Markdown",
            )
            return None
        finally:
            read_task.cancel()

        if return_code != 0:
            await status_msg.edit_text(
                "❌ Yuklab olish muvaffaqiyatsiz!\n"
                "Torrent seederlar yetarli emas yoki magnet link noto'g'ri.",
                parse_mode="Markdown",
            )
            return None

        # Eng katta faylni topamiz
        best_file = _find_best_file(dl_dir)
        if not best_file:
            await status_msg.edit_text(
                "❌ Torrentdan fayl topilmadi!",
                parse_mode="Markdown",
            )
            return None

        return best_file

    except Exception as e:
        logger.exception("_download_torrent xato: %s", e)
        await status_msg.edit_text(
            f"❌ Xato: `{e}`",
            parse_mode="Markdown",
        )
        return None
    finally:
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass


def _find_best_file(directory: str) -> Optional[str]:
    """
    Yuklab olingan papkadan eng katta media faylni topadi.
    """
    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts", ".wmv", ".m4v"}
    AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".opus"}
    ALL_EXTS = VIDEO_EXTS | AUDIO_EXTS | {".zip", ".rar", ".pdf"}

    best = None
    best_size = 0

    for root, dirs, files in os.walk(directory):
        # Mavjud bo'lmagan yoki sample fayllarni o'tkazamiz
        dirs[:] = [d for d in dirs if d.lower() not in {"sample", "extras", "bonus", "featurettes"}]
        for fname in files:
            if "sample" in fname.lower():
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ALL_EXTS:
                continue
            fpath = os.path.join(root, fname)
            size = os.path.getsize(fpath)
            if size > best_size:
                best_size = size
                best = fpath

    # Media fayl topilmasa har qanday katta faylni olamiz
    if not best:
        for root, dirs, files in os.walk(directory):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    size = os.path.getsize(fpath)
                    if size > best_size:
                        best_size = size
                        best = fpath
                except OSError:
                    pass

    return best


# ── Asosiy handler ────────────────────────────────────────────────────────────

async def torrent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /torrent <qidiruv so'zi>
    /torrent magnet:?xt=...
    /torrent https://...torrent
    """
    msg = update.effective_message
    args = context.args

    if not args:
        await msg.reply_text(
            "🧲 *Torrent Handler*\n\n"
            "*Qidirish:*\n"
            "`/torrent Inception 2010`\n"
            "`/torrent Breaking Bad S01`\n\n"
            "*To'g'ridan yuklab olish:*\n"
            "`/torrent magnet:?xt=urn:btih:...`\n"
            "`/torrent https://example.com/file.torrent`\n\n"
            "📦 Manba: YTS · 1337x · Rutor",
            parse_mode="Markdown",
        )
        return

    query = " ".join(args).strip()

    # Magnet link yoki .torrent URL bo'lsa to'g'ridan yuklaymiz
    if query.startswith("magnet:") or query.endswith(".torrent") or (
        query.startswith("http") and "torrent" in query.lower()
    ):
        title = _magnet_title(query)
        status = await msg.reply_text(
            f"🧲 Magnet yuklab olinmoqda...\n`{title[:60]}`",
            parse_mode="Markdown",
        )
        await _run_download(query, title, status, msg)
        return

    # Qidirish
    _cleanup_sessions()
    user_id = update.effective_user.id

    status = await msg.reply_text(
        f"🔍 Torrent qidirilmoqda...\n`{query}`",
        parse_mode="Markdown",
    )

    results = await _search_all(query)

    if not results:
        await status.edit_text(
            f"❌ `{query}` bo'yicha torrent topilmadi.\n\n"
            "Boshqa kalit so'zlar bilan urinib ko'ring.",
            parse_mode="Markdown",
        )
        return

    _sessions[user_id] = TorrentSession(results=results, query=query)
    keyboard = _build_results_keyboard(results, user_id)

    # Natijalar xabari
    lines = [f"🔍 *{query}* — {len(results)} natija\n"]
    for i, r in enumerate(results, 1):
        seed_icon = "🟢" if r.seeds > 50 else "🟡" if r.seeds > 10 else "🔴"
        lines.append(
            f"{i}. `{r.title[:45]}`\n"
            f"   📦 {r.size}  {seed_icon} {_fmt_seeds(r.seeds)} seed  [{r.source}]"
        )

    await status.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


def _magnet_title(magnet: str) -> str:
    """Magnet linkdan dn= qismini olib sarlavha yasaydi."""
    from urllib.parse import parse_qs, urlparse
    try:
        if magnet.startswith("magnet:"):
            qs = parse_qs(magnet[8:])
            dn = qs.get("dn", [""])[0]
            if dn:
                return dn[:80]
    except Exception:
        pass
    return "Torrent"


# ── Callback handler ──────────────────────────────────────────────────────────

async def torrent_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "tr_cancel":
        user_id = query.from_user.id
        _sessions.pop(user_id, None)
        await query.edit_message_text("❌ Bekor qilindi.")
        return

    # tr_force — 0 seed bo'lsa majburiy yuklab olish
    if data.startswith("tr_force|"):
        parts = data.split("|")
        if len(parts) >= 3:
            try:
                owner_id = int(parts[1])
                idx = int(parts[2])
            except ValueError:
                await query.edit_message_text("❌ Xato!")
                return
            session = _sessions.get(owner_id)
            if not session:
                await query.edit_message_text("❌ Session tugadi!")
                return
            result = session.results[idx]
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            status = await query.message.reply_text(
                f"🧲 *{result.title[:60]}*\n"
                f"📦 {result.size}  🔴 0 seed (majburiy)\n\n"
                f"Yuklab olinmoqda... (seeder kutilmoqda)",
                parse_mode="Markdown",
            )
            await _run_download(result.magnet, result.title, status, query.message)
        return

    if not data.startswith("tr_dl|"):
        return

    parts = data.split("|")
    if len(parts) < 3:
        return

    try:
        owner_id = int(parts[1])
        idx = int(parts[2])
    except ValueError:
        return

    session = _sessions.get(owner_id)
    if not session or time.monotonic() - session.created_at > SESSION_TTL:
        _sessions.pop(owner_id, None)
        await query.edit_message_text(
            "❌ Session muddati tugadi! Qaytadan `/torrent` yuboring.",
            parse_mode="Markdown",
        )
        return

    if idx >= len(session.results):
        await query.edit_message_text("❌ Natija topilmadi!")
        return

    result = session.results[idx]

    # Magnet yo'q bo'lsa xabar beramiz
    if not result.magnet:
        await query.edit_message_text(
            f"❌ Bu torrent uchun magnet link topilmadi.\n"
            f"Detail sahifaga o'ting: {result.info_url or 'mavjud emas'}",
            parse_mode="Markdown",
        )
        return


    # 0 seed — ogohlantirish
    if result.seeds == 0:
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚠️ Baribir yukla", callback_data=f"tr_force|{owner_id}|{idx}"),
                InlineKeyboardButton("❌ Bekor", callback_data="tr_cancel"),
            ]
        ])
        await query.edit_message_text(
            f"⚠️ *{result.title[:60]}*\n\n"
            f"🔴 Seeder: 0 — fayl yuklab olinmasligi mumkin!\n"
            f"Torrent 5 daqiqagacha kutadi, muvaffaqiyatsiz tugashi ehtimoli yuqori.\n\n"
            f"Baribir urinib ko'rasizmi?",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return

    # Klaviaturani olib tashlaymiz
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    seed_icon = "🟢" if result.seeds > 50 else "🟡" if result.seeds > 10 else "🔴"
    status = await query.message.reply_text(
        f"🧲 *{result.title[:60]}*\n"
        f"📦 {result.size}  {seed_icon} {result.seeds} seed\n\n"
        f"Yuklab olinmoqda...",
        parse_mode="Markdown",
    )

    await _run_download(result.magnet, result.title, status, query.message)


async def _run_download(magnet: str, title: str, status_msg, original_msg) -> None:
    """Yuklab olish va guruhga yuborish."""
    tmp_path = await _download_torrent(magnet, status_msg, title)

    if not tmp_path or not os.path.exists(tmp_path):
        return

    size = os.path.getsize(tmp_path)
    size_str = _fmt_bytes(size)
    ext = os.path.splitext(tmp_path)[1].lower()
    fname = os.path.basename(tmp_path)

    if size > MAX_FILE_SIZE:
        await status_msg.edit_text(
            f"❌ Fayl juda katta: `{size_str}`\n"
            f"Telegram limiti: `{_fmt_bytes(MAX_FILE_SIZE)}`",
            parse_mode="Markdown",
        )
        _cleanup_dir(tmp_path)
        return

    await status_msg.edit_text(
        f"✅ Yuklab olindi: `{size_str}`\n"
        f"📤 Guruhga yuborilmoqda...",
        parse_mode="Markdown",
    )

    client = await get_user_client()
    if not client:
        await status_msg.edit_text(
            "❌ Userbot ulangmagan! Admin bilan bog'laning.",
            parse_mode="Markdown",
        )
        _cleanup_dir(tmp_path)
        return

    caption = f"🧲 *{title[:60]}*\n📦 `{size_str}`\n📄 `{fname}`"

    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts"}
    AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav"}

    try:
        if ext in VIDEO_EXTS:
            meta = await _get_video_meta(tmp_path)
            thumb = None
            if meta.get("duration", 0) > 0:
                thumb = await _make_thumb(tmp_path, meta["duration"])
            extra = ""
            if meta.get("duration"):
                extra += f"\n⏱ `{_fmt_dur(meta['duration'])}`"
            if meta.get("width") and meta.get("height"):
                extra += f"\n📐 `{meta['width']}×{meta['height']}`"
            try:
                await client.send_video(
                    chat_id=ARCHIVE_GROUP_ID,
                    video=tmp_path,
                    caption=caption + extra,
                    supports_streaming=True,
                    duration=meta.get("duration") or None,
                    width=meta.get("width") or None,
                    height=meta.get("height") or None,
                    thumb=thumb or None,
                    parse_mode="Markdown",
                )
            finally:
                if thumb and os.path.exists(thumb):
                    try:
                        os.remove(thumb)
                    except Exception:
                        pass
        elif ext in AUDIO_EXTS:
            await client.send_audio(
                chat_id=ARCHIVE_GROUP_ID,
                audio=tmp_path,
                caption=caption,
                title=title,
                parse_mode="Markdown",
            )
        else:
            await client.send_document(
                chat_id=ARCHIVE_GROUP_ID,
                document=tmp_path,
                caption=caption,
                file_name=fname,
                parse_mode="Markdown",
            )

        await status_msg.edit_text(
            f"✅ *{title[:60]}*\n\n"
            f"📦 `{size_str}`\n"
            f"🗂 Guruhga yuborildi!",
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.exception("Guruhga yuborish xato: %s", e)
        await status_msg.edit_text(
            f"✅ Yuklab olindi, lekin yuborishda xato:\n`{e}`",
            parse_mode="Markdown",
        )
    finally:
        _cleanup_dir(tmp_path)


def _cleanup_dir(file_path: str) -> None:
    """Faylni va uning papkasini o'chiradi."""
    import shutil
    try:
        parent = os.path.dirname(file_path)
        if parent.startswith(TEMP_DIR) and parent != TEMP_DIR:
            shutil.rmtree(parent, ignore_errors=True)
        elif os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass


def _build_results_keyboard(results: list[TorrentResult], user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for i, r in enumerate(results):
        seed_icon = "🟢" if r.seeds > 50 else "🟡" if r.seeds > 10 else "🔴"
        label = f"{seed_icon} {r.title[:35]}... [{r.source}]" if len(r.title) > 35 else f"{seed_icon} {r.title} [{r.source}]"
        rows.append([InlineKeyboardButton(label, callback_data=f"tr_dl|{user_id}|{i}")])
    rows.append([InlineKeyboardButton("❌ Bekor", callback_data="tr_cancel")])
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
                if k.strip() == "duration":
                    try: meta["duration"] = int(float(v.strip()))
                    except ValueError: pass
                elif k.strip() == "width":
                    try: meta["width"] = int(v.strip())
                    except ValueError: pass
                elif k.strip() == "height":
                    try: meta["height"] = int(v.strip())
                    except ValueError: pass
    except Exception as e:
        logger.warning("ffprobe xato: %s", e)
    return meta

async def _get_video_meta(path: str) -> dict:
    return await asyncio.get_running_loop().run_in_executor(None, _get_video_meta_sync, path)

def _make_thumb_sync(path: str, duration: int) -> Optional[str]:
    try:
        thumb = os.path.join(TEMP_DIR, f"tr_thumb_{uuid.uuid4().hex[:8]}.jpg")
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
