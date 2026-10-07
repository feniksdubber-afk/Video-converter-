"""
torrent_handler.py — Torrent qidirish va yuklab olish (v2).

Buyruqlar:
  /torrent <qidiruv>            — 5 ta manbadan qidiradi (YTS, 1337x, Rutor, TPB, Nyaa)
  /torrent magnet:?xt=...       — magnet link
  /torrent <40 xonali infohash> — infohash
  /torrent https://...torrent   — .torrent URL
  /torrent  (.torrent faylga reply) — yuborilgan .torrent fayl

Yangiliklar (v1 ga nisbatan):
  • Fayl tanlash: season-pack / ko'p fayli torrentlarda kerakli fayllarni
    belgilab olasiz (sahifalash bilan). Avval faqat eng katta fayl olinardi.
  • Fayllar KETMA-KET yuklanadi → yuboriladi → serverdan o'chiriladi
    (disk to'lib qolmaydi).
  • Yuklash paytida ❌ Bekor tugmasi (avval to'xtatib bo'lmasdi).
  • Yangi manbalar: TPB (apibay), Nyaa (anime). 1337x/YTS/Rutor uchun mirror fallback.
  • 1337x magnetlari parallel olinadi (avval ketma-ket, juda sekin edi).
  • Public tracker ro'yxati avtomatik qo'shiladi → ko'proq peer, tezroq start.
  • Xato bo'lganda vaqtinchalik papka tozalanadi (avval disk "oqardi").
  • Premium bo'lmasa 2 GB, Premium bo'lsa 4 GB limit.
  • Global parallel limit (TORRENT_MAX_PARALLEL, default 2) + foydalanuvchiga 1 ta faol job.
  • Tugmalarni faqat egasi (yoki admin) bosa oladi.
  • Yuborish progressi, qidiruv keshi, .torrent fayl / infohash qo'llab-quvvatlash.

v3 qo'shimchalari:
  • Katta fayllar (2/4 GB dan ortiq) ffmpeg bilan avtomatik QISMLARGA bo'linadi
    (qayta kodlashsiz, `-c copy`); video bo'lmasa bayt bo'yicha bo'linadi.
  • MKV/AVI (H.264) → MP4 (faststart) remux — Telegramda to'g'ridan oynatiladi.
  • Aqlli saralash: sifat (2160/1080/720), manba (BluRay/WEB-DL), CAM/TS jazolanadi;
    qidiruv natijalarida sifat filtri tugmalari.
  • Qo'shimcha manba: Torrents-CSV (jim, xato bersa ko'rsatilmaydi).
  • Uzilishda avtomatik qayta urinish (aria2 davom ettiradi), disk watchdog.
  • Telegram flood-wait himoyasi (progress edit vaqtincha to'xtaydi).
  • `/torrent status` va `/torrent cancel`.
  • Env: TORRENT_REMUX=0 (remux o'chirish), TORRENT_SPLIT=0 (bo'lish o'chirish).
"""

import asyncio
import glob
import html
import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, quote_plus

import httpx
from pyrogram.enums import ParseMode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, RetryAfter
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID, TEMP_DIR
from handlers.save_restricted import get_user_client, is_user_premium

try:  # pyrogram v2: callback ichida raise qilinsa uzatish to'xtaydi
    from pyrogram import StopTransmission
except Exception:  # pragma: no cover
    StopTransmission = None

logger = logging.getLogger(__name__)

# ── Konstantlar ───────────────────────────────────────────────────────────────

SESSION_TTL = 900            # qidiruv/tanlash sessiyasi: 15 daqiqa
SEARCH_TIMEOUT = 15
META_TIMEOUT = 150           # magnet → metadata kutish
STALL_TIMEOUT = 300          # tezlik 0 bo'lib tursa (soniya) aria2 to'xtaydi
FILE_TIMEOUT = 3 * 3600      # bitta fayl uchun maksimal vaqt
PAGE_SIZE = 8                # fayl tanlash sahifasida nechta fayl
SEARCH_CACHE_TTL = 600
MIN_FREE_BYTES = 300 * 1024 * 1024
MAX_RESULTS = 12
POOL_RESULTS = 20            # filtr tugmalari uchun kengroq havza
MAX_ATTEMPTS = 2             # bitta fayl uchun yuklash urinishlari (aria2 davom ettiradi)
LOW_DISK_BYTES = 120 * 1024 * 1024

REMUX = os.environ.get("TORRENT_REMUX", "1") != "0"
SPLIT = os.environ.get("TORRENT_SPLIT", "1") != "0"
ALLOW_IPV6 = os.environ.get("TORRENT_ALLOW_IPV6", "0") == "1"

MAX_PARALLEL = max(1, int(os.environ.get("TORRENT_MAX_PARALLEL", "2") or 2))
UP_LIMIT = os.environ.get("TORRENT_UP_LIMIT", "256K")  # seeding yo'q, lekin peer bilan almashuv uchun

LIMIT_FREE = 1990 * 1024 * 1024      # Telegram ~2000 MiB (zaxira bilan)
LIMIT_PREMIUM = 3990 * 1024 * 1024   # Premium ~4000 MiB

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".opus", ".ogg"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.6099.130 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_YTS_MIRRORS = ["https://yts.mx", "https://yts.lt", "https://yts.am"]
_1337X_MIRRORS = ["https://1337x.to", "https://1337x.st", "https://x1337x.ws", "https://1337xx.to"]
_RUTOR_MIRRORS = ["https://rutor.info", "https://rutor.is"]

_TRACKER_LIST_URLS = [
    "https://cdn.jsdelivr.net/gh/ngosang/trackerslist@master/trackers_best.txt",
    "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
]
_STATIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://opentracker.i2p.rocks:6969/announce",
    "https://tracker.tamersunion.org:443/announce",
]


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
    results: list
    query: str
    created_at: float = field(default_factory=time.monotonic)
    all: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    filt: str = "all"


@dataclass
class TFile:
    idx: int          # aria2 --select-file uchun 1-based indeks
    path: str         # torrent ichidagi nisbiy yo'l
    size: int
    pad: bool = False


@dataclass
class Job:
    id: str
    owner: int
    title: str
    dl_dir: str
    state: str = "meta"      # meta | select | queued | downloading | sending
    torrent_path: str = ""
    tname: str = ""
    files: list = field(default_factory=list)
    shown: list = field(default_factory=list)
    selected: set = field(default_factory=set)
    page: int = 0
    limit: int = LIMIT_FREE
    proc: Optional[asyncio.subprocess.Process] = None
    task: Optional[asyncio.Task] = None
    cancelled: bool = False
    timed_out: bool = False
    low_disk: bool = False
    last_line: str = ""
    created_at: float = field(default_factory=time.monotonic)


_sessions: dict = {}
_jobs: dict = {}
_search_cache: dict = {}
_tasks: set = set()
_sem: Optional[asyncio.Semaphore] = None
_trackers_cache: dict = {"ts": 0.0, "list": []}
_swept = False


# ── Yordamchilar ──────────────────────────────────────────────────────────────

def _fmt_seeds(n: int) -> str:
    return f"{n / 1000:.1f}K" if n > 1000 else str(n)


def _seed_icon(n: int) -> str:
    return "🟢" if n > 50 else "🟡" if n > 10 else "🔴"


def _progress_bar(pct: int, length: int = 14) -> str:
    pct = max(0, min(100, pct))
    filled = int(length * pct / 100)
    return "▰" * filled + "▱" * (length - filled)


def _fmt_dur(seconds: int) -> str:
    if not seconds:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt_bytes(b) -> str:
    try:
        b = float(b)
    except (TypeError, ValueError):
        return "?"
    if b <= 0:
        return "?"
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def _md_escape(text) -> str:
    """Telegram legacy Markdown uchun (code entity'dan TASHQARIDA)."""
    s = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def _code(text) -> str:
    """`code` entity ichiga xavfsiz qo'yish."""
    return "`" + str(text).replace("`", "'").replace("\\", "/") + "`"


def _natkey(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _short(path: str, n: int = 34) -> str:
    base = os.path.basename(path)
    return base if len(base) <= n else base[: n - 1] + "…"


def _free_bytes() -> int:
    try:
        return shutil.disk_usage(TEMP_DIR).free
    except Exception:
        return 1 << 60


def _blocked(job: "Job", tf: "TFile") -> bool:
    """Limitdan katta fayl faqat bo'lish o'chirilgan bo'lsa bloklanadi."""
    return tf.size > job.limit and not SPLIT


def _cleanup_sessions() -> None:
    now = time.monotonic()
    for uid in [k for k, v in _sessions.items() if now - v.created_at > SESSION_TTL]:
        _sessions.pop(uid, None)
    for k in [k for k, v in _search_cache.items() if now - v[0] > SEARCH_CACHE_TTL]:
        _search_cache.pop(k, None)
    # tanlash bosqichida qolib ketgan joblar
    for j in [j for j in _jobs.values() if j.state == "select" and now - j.created_at > SESSION_TTL]:
        _finish(j)


def _sweep_orphans() -> None:
    """Restartdan keyin qolib ketgan eski torrent papkalarini bir marta tozalaydi."""
    global _swept
    if _swept:
        return
    _swept = True
    try:
        active = {j.dl_dir for j in _jobs.values()}
        cutoff = time.time() - 2 * 3600
        for name in os.listdir(TEMP_DIR):
            if not (name.startswith("torrent_") or name.startswith("tr_thumb_") or name.startswith("tr_up_")):
                continue
            p = os.path.join(TEMP_DIR, name)
            if p in active:
                continue
            try:
                if os.path.getmtime(p) < cutoff:
                    shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
            except OSError:
                pass
    except Exception as e:
        logger.debug("sweep xato: %s", e)


def _spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(MAX_PARALLEL)
    return _sem


_edit_block_until = 0.0


async def _edit(msg, text: str, kb: Optional[InlineKeyboardMarkup] = None, progress: bool = False) -> None:
    """edit_text — 'not modified', Markdown xatolari va flood-waitni yutadi.
    progress=True bo'lsa flood-wait paytida jim o'tkazib yuboriladi."""
    global _edit_block_until
    now = time.monotonic()
    if now < _edit_block_until:
        if progress:
            return
        await asyncio.sleep(min(_edit_block_until - now, 30))
    try:
        await msg.edit_text(text[:4000], parse_mode="Markdown", reply_markup=kb)
    except RetryAfter as e:
        ra = getattr(e, "retry_after", 5)
        ra = ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)
        _edit_block_until = time.monotonic() + ra + 1
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        try:
            await msg.edit_text(re.sub(r"[*_`\\]", "", text)[:4000], reply_markup=kb)
        except Exception:
            pass
    except Exception:
        pass


def _cancel_kb(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data=f"tr_dl|x|{job.id}")]])


def _finish(job: Job) -> None:
    _jobs.pop(job.id, None)
    shutil.rmtree(job.dl_dir, ignore_errors=True)


# ── Bencode (faqat .torrent ichidan fayl ro'yxatini o'qish uchun) ─────────────

def _bdecode(data: bytes, i: int = 0):
    c = data[i:i + 1]
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = _bdecode(data, i)
            v, i = _bdecode(data, i)
            out[k] = v
        return out, i + 1
    if c.isdigit():
        colon = data.index(b":", i)
        n = int(data[i:colon])
        start = colon + 1
        return data[start:start + n], start + n
    raise ValueError("bencode xato")


def _dec(b) -> str:
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return str(b)


def _parse_torrent(path: str):
    """→ (torrent_nomi, [TFile, ...]). aria2 indeksi = fayllar tartibi (1-based)."""
    with open(path, "rb") as f:
        data = f.read()
    meta, _ = _bdecode(data)
    info = meta[b"info"]
    name = _dec(info.get(b"name.utf-8") or info.get(b"name") or b"torrent")
    files = []
    if b"files" in info:
        for n, f in enumerate(info[b"files"], 1):
            parts = f.get(b"path.utf-8") or f.get(b"path") or []
            rel = "/".join(_dec(p) for p in parts)
            attr = f.get(b"attr", b"")
            pad = (b"p" in attr) or rel.startswith(".pad") or "/.pad/" in rel
            files.append(TFile(n, rel, int(f[b"length"]), pad))
    else:
        files.append(TFile(1, name, int(info[b"length"]), False))
    return name, files


def _is_junk(path: str) -> bool:
    return bool(re.search(r"(^|[\W_])samples?([\W_]|$)", path.lower()))


def _locate(job: Job, tf: TFile) -> Optional[str]:
    """Yuklangan faylni diskdan topadi."""
    multi = len(job.files) > 1 or "/" in tf.path
    if multi:
        expected = os.path.join(job.dl_dir, job.tname, *tf.path.split("/"))
    else:
        expected = os.path.join(job.dl_dir, job.tname)
    if os.path.isfile(expected):
        return expected
    base = os.path.basename(tf.path)
    for root, _dirs, fnames in os.walk(job.dl_dir):
        for fn in fnames:
            if fn == base:
                p = os.path.join(root, fn)
                try:
                    if os.path.getsize(p) == tf.size:
                        return p
                except OSError:
                    pass
    return None


def _purge(job: Job, tf: TFile) -> None:
    p = _locate(job, tf)
    if p:
        for x in (p, p + ".aria2"):
            try:
                os.remove(x)
            except OSError:
                pass


# ── Tracker ro'yxati ──────────────────────────────────────────────────────────

async def _get_trackers() -> list:
    now = time.time()
    if _trackers_cache["list"] and now - _trackers_cache["ts"] < 12 * 3600:
        return _trackers_cache["list"]
    lst: list = []
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=8, follow_redirects=True) as c:
            for url in _TRACKER_LIST_URLS:
                try:
                    r = await c.get(url)
                    if r.status_code == 200:
                        lst = [ln.strip() for ln in r.text.splitlines() if ln.strip().startswith(("udp://", "http://", "https://"))]
                        if lst:
                            break
                except Exception:
                    continue
    except Exception:
        pass
    if not lst:
        lst = list(_STATIC_TRACKERS)
    lst = lst[:25]
    _trackers_cache.update(ts=now, list=lst)
    return lst


# ── Saralash / sifat ──────────────────────────────────────────────────────────

_QUAL_PATTERNS = [
    ("2160", re.compile(r"2160p|\b4k\b|\buhd\b", re.I)),
    ("1080", re.compile(r"1080[pi]", re.I)),
    ("720", re.compile(r"720p", re.I)),
    ("480", re.compile(r"480p|dvdrip", re.I)),
]
_QUAL_LABEL = {"2160": "4K", "1080": "1080p", "720": "720p", "480": "480p"}
_BAD_RE = re.compile(r"(?<![a-z0-9])(cam|hdcam|camrip|ts|hdts|telesync|tc|telecine|hdtc|scr|screener)(?![a-z0-9])", re.I)
_GOOD_SRC = re.compile(r"blu-?ray|web-?dl|webrip|remux|bdrip", re.I)


def _quality_of(title: str) -> str:
    for key, rx in _QUAL_PATTERNS:
        if rx.search(title or ""):
            return key
    return ""


def _score(r: "TorrentResult") -> float:
    sc = float(min(r.seeds, 3000))
    sc += {"2160": 100, "1080": 150, "720": 100}.get(_quality_of(r.title), 0)
    if _GOOD_SRC.search(r.title or ""):
        sc += 60
    if _BAD_RE.search(r.title or ""):
        sc -= 2000
    if r.seeds == 0:
        sc -= 500
    return sc


# ── Qidiruv manbalari ─────────────────────────────────────────────────────────

_BASE_TRACKERS_FOR_MAGNET = _STATIC_TRACKERS[:6]


def _build_magnet(info_hash: str, display_name: str) -> str:
    dn = quote_plus(display_name)
    tr = "&".join(f"tr={quote_plus(t)}" for t in _BASE_TRACKERS_FOR_MAGNET)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={dn}&{tr}"


def _ih(magnet: str) -> Optional[str]:
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet or "", re.I)
    return m.group(1).lower() if m else None


async def _search_yts(query: str, limit: int = 4) -> list:
    results = []
    data = None
    tried = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as c:
        for base in _YTS_MIRRORS:
            try:
                r = await c.get(
                    f"{base}/api/v2/list_movies.json",
                    params={"query_term": query, "limit": limit, "sort_by": "seeds"},
                )
                data = r.json()
                break
            except Exception as e:
                tried.append(f"{base.split('//')[1]}:{type(e).__name__}")
    if data is None:
        raise RuntimeError("YTS: " + ", ".join(tried))
    for movie in (data.get("data", {}).get("movies") or []):
        title = movie.get("title", "?")
        year = movie.get("year", "")
        for t in (movie.get("torrents") or [])[:2]:
            quality = t.get("quality", "?")
            codec = t.get("video_codec", "")
            results.append(TorrentResult(
                title=f"{title} ({year}) [{quality} {codec}]"[:80],
                magnet=_build_magnet(t.get("hash", ""), f"{title} ({year}) [{quality}]"),
                size=t.get("size", "?"),
                seeds=int(t.get("seeds", 0) or 0),
                leeches=int(t.get("peers", 0) or 0),
                source="YTS",
                category="Movie",
                info_url=movie.get("url", ""),
            ))
    return results


async def _get_magnet_1337x(c: httpx.AsyncClient, info_url: str) -> str:
    try:
        r = await c.get(info_url)
        m = re.search(r'(magnet:\?[^"\'<\s]+)', r.text)
        return m.group(1) if m else ""
    except Exception:
        return ""


async def _search_1337x(query: str, limit: int = 5) -> list:
    async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as c:
        html_text, base, tried = None, None, []
        for b in _1337X_MIRRORS:
            try:
                r = await c.get(f"{b}/search/{quote_plus(query)}/1/")
            except Exception as e:
                tried.append(f"{b.split('//')[1]}:{type(e).__name__}")
                continue
            if r.status_code == 200 and 'class="name"' in r.text:
                html_text, base = r.text, b
                break
            if r.status_code == 200 and re.search(r"Just a moment|cf-chl|challenge-platform", r.text):
                tried.append(f"{b.split('//')[1]}:Cloudflare")
            elif r.status_code == 200:
                # sahifa ochildi, lekin natija yo'q — bu xato emas
                return []
            else:
                tried.append(f"{b.split('//')[1]}:{r.status_code}")
        if not html_text:
            raise RuntimeError("1337x: " + ", ".join(tried))

        rows = re.findall(r"<tr>(.*?)</tr>", html_text, re.DOTALL)
        parsed = []
        for row in rows[1:limit + 1]:
            name_m = re.search(r'class="name"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.DOTALL)
            if not name_m:
                continue
            seeds_m = re.search(r'class="seeds"[^>]*>(\d+)<', row)
            leeches_m = re.search(r'class="leeches"[^>]*>(\d+)<', row)
            size_m = re.search(r'class="size"[^>]*>([\d.,]+\s*[KMGT]?B)', row, re.I)
            parsed.append((
                name_m.group(1),
                re.sub(r"<[^>]+>", "", name_m.group(2)).strip(),
                int(seeds_m.group(1)) if seeds_m else 0,
                int(leeches_m.group(1)) if leeches_m else 0,
                size_m.group(1) if size_m else "?",
            ))

        sem = asyncio.Semaphore(4)

        async def one(info_path, title, seeds, leeches, size):
            info_url = f"{base}{info_path}"
            async with sem:
                magnet = await _get_magnet_1337x(c, info_url)
            if not magnet:
                return None
            return TorrentResult(title[:80], magnet, size, seeds, leeches, "1337x", info_url=info_url)

        got = await asyncio.gather(*(one(*p) for p in parsed), return_exceptions=True)
        return [g for g in got if isinstance(g, TorrentResult)]


_RUTOR_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(KB|MB|GB|TB|КБ|МБ|ГБ|ТБ)", re.I)


def _plain(fragment: str) -> str:
    """HTML bo'lagini oddiy matnga aylantiradi (&nbsp; → bo'shliq)."""
    t = html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def _parse_rutor_row(row: str):
    """→ (magnet, title, size, seeds, leeches) | None. Turli markup variantlariga bardoshli."""
    magnet_m = re.search(r'href="(magnet:\?[^"]+)"', row)
    name_m = re.search(r'<a[^>]+href="/torrent/[^"]*"[^>]*>(.*?)</a>', row, re.DOTALL)
    if not magnet_m or not name_m:
        return None
    title = _plain(name_m.group(1))
    if not title:
        return None
    after = _plain(row[name_m.end():])
    size_m = _RUTOR_SIZE_RE.search(after)
    size = f"{size_m.group(1)} {size_m.group(2)}" if size_m else "?"
    seeds = leeches = 0
    g = re.search(r'class="green"[^>]*>(.*?)</span>', row, re.DOTALL)
    r_ = re.search(r'class="red"[^>]*>(.*?)</span>', row, re.DOTALL)
    gn = re.findall(r"\d+", _plain(g.group(1))) if g else []
    rn = re.findall(r"\d+", _plain(r_.group(1))) if r_ else []
    if gn:
        seeds = int(gn[-1])
    if rn:
        leeches = int(rn[-1])
    if not gn and size_m:      # zaxira: hajmdan keyingi birinchi ikki son = seed, leech
        nums = re.findall(r"\b\d+\b", after[size_m.end():])
        if nums:
            seeds = int(nums[0])
        if len(nums) > 1:
            leeches = int(nums[1])
    return html.unescape(magnet_m.group(1)), title, size, seeds, leeches


async def _search_rutor(query: str, limit: int = 4) -> list:
    html_text = None
    tried = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as c:
        for base in _RUTOR_MIRRORS:
            try:
                r = await c.get(f"{base}/search/0/0/100/0/{quote_plus(query)}")
                tried.append(f"{base.split('//')[1]}:{r.status_code}")
                if r.status_code == 200:
                    html_text = r.text
                    break
            except Exception as e:
                tried.append(f"{base.split('//')[1]}:{type(e).__name__}")
    if html_text is None:
        raise RuntimeError("Rutor: " + ", ".join(tried))

    results, seen = [], set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.DOTALL):
        if len(results) >= limit:
            break
        parsed = _parse_rutor_row(row)
        if not parsed:
            continue
        magnet, title, size, seeds, leeches = parsed
        ih = _ih(magnet) or magnet[:60]
        if ih in seen:
            continue
        seen.add(ih)
        results.append(TorrentResult(
            title=title[:80], magnet=magnet, size=size,
            seeds=seeds, leeches=leeches, source="Rutor",
        ))
    return results


async def _search_tpb(query: str, limit: int = 5) -> list:
    """The Pirate Bay — apibay.org JSON API (HTML parse shart emas)."""
    async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as c:
        r = await c.get("https://apibay.org/q.php", params={"q": query, "cat": 0})
        if r.status_code != 200:
            raise RuntimeError(f"apibay HTTP {r.status_code}")
        data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("apibay noto'g'ri javob")
    items = []
    for it in data:
        ih = str(it.get("info_hash", "") or "")
        if not ih or set(ih) == {"0"}:
            continue
        if str(it.get("category", "")).startswith("5"):   # 5xx = XXX
            continue
        items.append(it)
    items.sort(key=lambda x: int(x.get("seeders", 0) or 0), reverse=True)
    out = []
    for it in items[:limit]:
        name = str(it.get("name", "?"))
        out.append(TorrentResult(
            title=name[:80],
            magnet=_build_magnet(it["info_hash"], name),
            size=_fmt_bytes(int(it.get("size", 0) or 0)),
            seeds=int(it.get("seeders", 0) or 0),
            leeches=int(it.get("leechers", 0) or 0),
            source="TPB",
        ))
    return out


async def _search_nyaa(query: str, limit: int = 5) -> list:
    """Nyaa.si — anime/dorama uchun (RSS)."""
    async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as c:
        r = await c.get(
            "https://nyaa.si/",
            params={"page": "rss", "q": query, "c": "0_0", "f": "0", "s": "seeders", "o": "desc"},
        )
        if r.status_code != 200:
            raise RuntimeError(f"nyaa {r.status_code}")
        root = ET.fromstring(r.content)
    ns = {"nyaa": "https://nyaa.si/xmlns/nyaa"}
    out = []
    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "?").strip()
        ih = (item.findtext("nyaa:infoHash", namespaces=ns) or "").strip()
        if not ih:
            continue
        out.append(TorrentResult(
            title=title[:80],
            magnet=_build_magnet(ih, title),
            size=(item.findtext("nyaa:size", namespaces=ns) or "?").strip(),
            seeds=int(item.findtext("nyaa:seeders", namespaces=ns) or 0),
            leeches=int(item.findtext("nyaa:leechers", namespaces=ns) or 0),
            source="Nyaa",
            category=(item.findtext("nyaa:category", namespaces=ns) or ""),
        ))
    return out


async def _search_tcsv(query: str, limit: int = 5) -> list:
    """Torrents-CSV (DHT'dan yig'ilgan indeks) — qo'shimcha, jim manba."""
    async with httpx.AsyncClient(headers=HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as c:
        r = await c.get("https://torrents-csv.com/service/search", params={"q": query, "size": 25})
        data = r.json()
    items = data.get("torrents") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuntimeError("torrents-csv noto'g'ri javob")
    items = [i for i in items if isinstance(i, dict) and i.get("infohash")]
    items.sort(key=lambda x: int(x.get("seeders", 0) or 0), reverse=True)
    out = []
    for it in items[:limit]:
        name = str(it.get("name", "?"))
        out.append(TorrentResult(
            title=name[:80],
            magnet=_build_magnet(str(it["infohash"]), name),
            size=_fmt_bytes(int(it.get("size_bytes", 0) or 0)),
            seeds=int(it.get("seeders", 0) or 0),
            leeches=int(it.get("leechers", 0) or 0),
            source="TCSV",
        ))
    return out


def _source_list(query: str) -> list:
    """(nom, coro, asosiy_manba)"""
    return [
        ("YTS", _search_yts(query, 4), True),
        ("1337x", _search_1337x(query, 5), True),
        ("Rutor", _search_rutor(query, 4), True),
        ("TPB", _search_tpb(query, 5), True),
        ("Nyaa", _search_nyaa(query, 5), True),
        ("TCSV", _search_tcsv(query, 5), False),
    ]


async def _debug_sources(query: str) -> str:
    """Admin uchun: har bir manba nima qaytardi (son, vaqt, xato matni)."""
    async def run(name, coro):
        t0 = time.monotonic()
        try:
            res = await coro
            seeds = ",".join(str(r.seeds) for r in res[:4])
            sizes = ",".join(r.size for r in res[:3])
            return f"✅ {name}: {len(res)} ta  ⏱{time.monotonic() - t0:.1f}s" + (f"\n    seed[{seeds}] hajm[{sizes}]" if res else "")
        except Exception as e:
            return f"❌ {name}: {type(e).__name__}: {str(e)[:110]}  ⏱{time.monotonic() - t0:.1f}s"
    lines = await asyncio.gather(*(run(n, c) for n, c, _ in _source_list(query)))
    return "\n".join(lines)


async def _search_all(query: str):
    """→ (natijalar_havzasi, javob_bermagan_asosiy_manbalar)"""
    key = query.lower().strip()
    cached = _search_cache.get(key)
    if cached and time.monotonic() - cached[0] < SEARCH_CACHE_TTL:
        return cached[1], cached[2]

    sources = _source_list(query)
    raw = await asyncio.gather(*(s_[1] for s_ in sources), return_exceptions=True)

    merged, failed, seen = [], [], set()
    for (name, _, critical), res in zip(sources, raw):
        if isinstance(res, Exception):
            logger.warning("%s qidiruv xato: %s", name, res)
            if critical:
                failed.append(name)
            continue
        for r in res:
            if not r.magnet:
                continue
            ih = _ih(r.magnet)
            if ih and ih in seen:
                continue
            if ih:
                seen.add(ih)
            merged.append(r)

    merged.sort(key=_score, reverse=True)
    merged = merged[:POOL_RESULTS]
    _search_cache[key] = (time.monotonic(), merged, failed)
    return merged, failed


# ── aria2c ────────────────────────────────────────────────────────────────────

_aria_ok: Optional[bool] = None


def _aria2c_available() -> bool:
    global _aria_ok
    if _aria_ok is None:
        try:
            r = subprocess.run(["aria2c", "--version"], capture_output=True, timeout=5)
            _aria_ok = r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            _aria_ok = False
    return _aria_ok


def _aria_base(trackers: list) -> list:
    cmd = [
        "aria2c",
        "--console-log-level=warn",
        "--summary-interval=3",
        "--seed-time=0",
        "--file-allocation=none",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--bt-tracker-connect-timeout=10",
        "--bt-tracker-timeout=20",
        "--bt-max-peers=150",
        "--bt-request-peer-speed-limit=10M",
        "--bt-enable-lpd=false",
        "--enable-dht=true",
        "--enable-peer-exchange=true",
        "--disk-cache=64M",
        f"--max-overall-upload-limit={UP_LIMIT}",
        "--peer-id-prefix=-qB4650-",
        "--peer-agent=qBittorrent/4.6.5",
    ]
    if not ALLOW_IPV6:
        cmd.append("--disable-ipv6=true")   # IPv6 peer/trackerlarda osilib qolishning oldini oladi
    if trackers:
        cmd.append("--bt-tracker=" + ",".join(trackers))
    return cmd


_PROG_RE = re.compile(r"\[#\w+\s+([\d.]+\s?\w*)/([\d.]+\s?\w*)\((\d+)%\)(.*?)\]")


def _parse_progress(line: str) -> Optional[dict]:
    m = _PROG_RE.search(line)
    if not m:
        return None
    rest = m.group(4)
    cn = re.search(r"CN:(\d+)", rest)
    sd = re.search(r"SD:(\d+)", rest)
    dl = re.search(r"DL:([\d.]+\w*)", rest)
    eta = re.search(r"ETA:(\S+)", rest)
    return {
        "done": m.group(1), "total": m.group(2), "pct": int(m.group(3)),
        "peers": int(cn.group(1)) if cn else 0,
        "seeds": int(sd.group(1)) if sd else 0,
        "speed": (dl.group(1) + "/s") if dl else "0B/s",
        "eta": eta.group(1) if eta else "",
    }


async def _run_aria(job: Job, cmd: list, on_line, timeout: int) -> int:
    """aria2c ni ishga tushiradi. Qaytaradi: returncode | -2 bekor | -3 vaqt tugadi."""
    job.timed_out = False
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    job.proc = proc

    async def reader():
        buf = b""
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]+", buf)
            buf = parts.pop()
            for p in parts:
                line = p.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if not line.startswith(("[#", "***", "===", "FILE:", "---", "Status Legend", "(OK)", "Download Results", "gid", "Download Progress")):
                    job.last_line = line[:160]
                if on_line:
                    try:
                        await on_line(line)
                    except Exception:
                        pass

    rt = asyncio.create_task(reader())
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        job.timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
    except asyncio.CancelledError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise
    finally:
        try:
            await asyncio.wait_for(rt, timeout=5)
        except Exception:
            rt.cancel()
        job.proc = None

    if job.cancelled:
        return -2
    if job.timed_out:
        return -3
    return proc.returncode if proc.returncode is not None else -1


def _rc_reason(rc: int, job: Job) -> str:
    if job.low_disk:
        return "diskda joy qolmadi"
    if rc == -3:
        return "vaqt tugadi"
    if rc == 9:
        return "diskda joy yo'q"
    tail = f" ({job.last_line[:80]})" if job.last_line else ""
    return f"seederlar yetarli emas yoki aloqa uzildi{tail}"


# ── Manba turini aniqlash ─────────────────────────────────────────────────────

def _normalize_source(q: str):
    """→ ('magnet'|'url'|None, qiymat)"""
    q = q.strip()
    if q.startswith("magnet:"):
        return "magnet", q
    if re.fullmatch(r"[0-9a-fA-F]{40}", q):
        return "magnet", f"magnet:?xt=urn:btih:{q.lower()}"
    if re.fullmatch(r"[A-Za-z2-7]{32}", q):
        return "magnet", f"magnet:?xt=urn:btih:{q.upper()}"
    if re.match(r"https?://", q, re.I) and (q.lower().split("?")[0].endswith(".torrent") or "torrent" in q.lower()):
        return "url", q
    return None, q


def _magnet_title(magnet: str) -> str:
    try:
        if magnet.startswith("magnet:"):
            dn = parse_qs(magnet[8:]).get("dn", [""])[0]
            if dn:
                return dn[:80]
    except Exception:
        pass
    return "Torrent"


# ── Job: metadata → tanlash → yuklash → yuborish ──────────────────────────────

def _user_active_count(uid: int) -> int:
    return sum(1 for j in _jobs.values() if j.owner == uid and j.state != "select")


async def _start_job(kind: str, source: str, title: str, msg, uid: int) -> None:
    if not _aria2c_available():
        await msg.reply_text(
            "❌ `aria2c` o'rnatilmagan!\n\nServer adminiga `apt install aria2` so'rang.",
            parse_mode="Markdown",
        )
        return
    if _user_active_count(uid) >= 1:
        await msg.reply_text(
            "⏳ Sizda allaqachon faol torrent bor. Tugashini kuting yoki ❌ Bekor bosing."
        )
        return

    os.makedirs(TEMP_DIR, exist_ok=True)
    _sweep_orphans()
    job_id = uuid.uuid4().hex[:8]
    dl_dir = os.path.join(TEMP_DIR, f"torrent_{job_id}")
    os.makedirs(dl_dir, exist_ok=True)
    job = Job(id=job_id, owner=uid, title=title, dl_dir=dl_dir)
    _jobs[job_id] = job

    status = await msg.reply_text(
        f"🧲 *{_md_escape(title[:50])}*\n\n🔎 Metadata olinmoqda...",
        parse_mode="Markdown",
        reply_markup=_cancel_kb(job),
    )
    job.task = _spawn(_prepare_job(job, kind, source, status))


async def _abort(job: Job, status, text: str) -> None:
    await _edit(status, text, None)
    _finish(job)


async def _fetch_torrent_file(job: Job, kind: str, source: str, status, trackers: list) -> Optional[str]:
    dest = os.path.join(job.dl_dir, "source.torrent")

    if kind == "file":
        shutil.copy(source, dest)
        try:
            os.remove(source)
        except OSError:
            pass
        return dest

    if kind == "url":
        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as c:
            r = await c.get(source)
        body = r.content
        if body[:1] == b"d":
            with open(dest, "wb") as f:
                f.write(body)
            return dest
        # HTML sahifa bo'lsa — ichidan magnet qidiramiz
        m = re.search(r'(magnet:\?[^"\'<\s]+)', r.text)
        if not m:
            job.last_line = "URL .torrent fayl emas"
            return None
        kind, source = "magnet", html.unescape(m.group(1))

    # magnet → metadata-only
    t0 = time.monotonic()
    last_edit = [0.0]

    async def on_line(line: str):
        now = time.monotonic()
        if now - last_edit[0] < 4:
            return
        cn = re.search(r"CN:(\d+)", line)
        if not cn:
            return
        last_edit[0] = now
        await _edit(
            status,
            f"🧲 *{_md_escape(job.title[:50])}*\n\n🔎 Metadata olinmoqda...\n"
            f"👥 `{cn.group(1)} peer`  ⏳ `{_fmt_dur(now - t0)}`",
            _cancel_kb(job),
        )

    cmd = _aria_base(trackers) + [
        "--dir", job.dl_dir,
        "--bt-metadata-only=true",
        "--bt-save-metadata=true",
        source,
    ]
    rc = await _run_aria(job, cmd, on_line, META_TIMEOUT)
    if rc != 0:
        job.last_line = "metadata olinmadi" if rc != -2 else ""
        return None
    for fn in os.listdir(job.dl_dir):
        if fn.endswith(".torrent"):
            return os.path.join(job.dl_dir, fn)
    return None


async def _prepare_job(job: Job, kind: str, source: str, status) -> None:
    try:
        trackers = await _get_trackers()
        tpath = await _fetch_torrent_file(job, kind, source, status, trackers)

        if job.cancelled:
            await _abort(job, status, "❌ Bekor qilindi.")
            return
        if not tpath:
            await _abort(
                job, status,
                "❌ Metadata olinmadi!\n"
                "Seederlar yo'q, magnet noto'g'ri yoki tarmoq muammosi bo'lishi mumkin."
                + (f"\n{_code(job.last_line[:100])}" if job.last_line else ""),
            )
            return

        try:
            tname, files = _parse_torrent(tpath)
        except Exception as e:
            logger.warning("torrent parse xato: %s", e)
            await _abort(job, status, "❌ .torrent faylni o'qib bo'lmadi.")
            return

        job.torrent_path = tpath
        job.tname = tname
        job.files = files
        if job.title in ("Torrent", ""):
            job.title = tname[:80]

        premium = False
        try:
            premium = await is_user_premium()
        except Exception:
            pass
        job.limit = LIMIT_PREMIUM if premium else LIMIT_FREE

        real = [f for f in files if not f.pad]
        media = [f for f in real if os.path.splitext(f.path)[1].lower() in MEDIA_EXTS and not _is_junk(f.path)]
        shown = media or real
        job.shown = sorted(shown, key=lambda f: _natkey(f.path))

        if not job.shown:
            await _abort(job, status, "❌ Torrentda fayl topilmadi!")
            return

        if len(job.shown) == 1:
            tf = job.shown[0]
            if _blocked(job, tf):
                await _abort(
                    job, status,
                    f"❌ Fayl juda katta: {_code(_fmt_bytes(tf.size))}\n"
                    f"Telegram limiti: {_code(_fmt_bytes(job.limit))} (bo'lish o'chirilgan)",
                )
                return
            job.selected = {tf.idx}
            await _download_and_send(job, status)
            return

        # Ko'p fayl — tanlash oynasi
        job.selected = {f.idx for f in job.shown if not _blocked(job, f)} if media else set()
        job.state = "select"
        job.created_at = time.monotonic()
        await _edit(status, _picker_text(job), _picker_kb(job))

    except asyncio.CancelledError:
        _finish(job)
        raise
    except Exception as e:
        logger.exception("_prepare_job xato: %s", e)
        await _abort(job, status, f"❌ Xato: {_code(str(e)[:150])}")


# ── Fayl tanlash oynasi ───────────────────────────────────────────────────────

def _picker_text(job: Job) -> str:
    sel = [f for f in job.shown if f.idx in job.selected]
    total = sum(f.size for f in sel)
    return (
        f"🧲 *{_md_escape(job.title[:60])}*\n\n"
        f"📁 Fayllar: `{len(job.shown)}`  ✅ Tanlandi: `{len(sel)}` ({_fmt_bytes(total)})\n\n"
        "Kerakli fayllarni belgilang va «Yuklash»ni bosing.\n"
        "Fayllar ketma-ket yuklanadi; har biri guruhga yuborilgach serverdan o'chiriladi.\n"
        + ("✂️ — Telegram limitidan katta: avtomatik qismlarga bo'linadi." if SPLIT else "🚫 — Telegram limitidan katta.")
    )


def _picker_kb(job: Job) -> InlineKeyboardMarkup:
    pages = max(1, (len(job.shown) + PAGE_SIZE - 1) // PAGE_SIZE)
    job.page = max(0, min(job.page, pages - 1))
    chunk = job.shown[job.page * PAGE_SIZE:(job.page + 1) * PAGE_SIZE]

    rows = []
    for tf in chunk:
        if _blocked(job, tf):
            mark = "🚫"
        else:
            mark = ("✅" if tf.idx in job.selected else "⬜") + ("✂️" if tf.size > job.limit else "")
        rows.append([InlineKeyboardButton(
            f"{mark} {_short(tf.path)} · {_fmt_bytes(tf.size)}",
            callback_data=f"tr_dl|f|{job.id}|{tf.idx}",
        )])

    if pages > 1:
        nav = []
        if job.page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"tr_dl|pg|{job.id}|{job.page - 1}"))
        nav.append(InlineKeyboardButton(f"{job.page + 1}/{pages}", callback_data=f"tr_dl|pg|{job.id}|{job.page}"))
        if job.page < pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"tr_dl|pg|{job.id}|{job.page + 1}"))
        rows.append(nav)

    rows.append([
        InlineKeyboardButton("☑️ Barchasi", callback_data=f"tr_dl|all|{job.id}"),
        InlineKeyboardButton("⬜ Tozalash", callback_data=f"tr_dl|none|{job.id}"),
        InlineKeyboardButton("🎬 Eng katta", callback_data=f"tr_dl|b|{job.id}"),
    ])
    rows.append([
        InlineKeyboardButton(f"⬇️ Yuklash ({len(job.selected)})", callback_data=f"tr_dl|go|{job.id}"),
        InlineKeyboardButton("❌ Bekor", callback_data=f"tr_dl|x|{job.id}"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Yuklab olish va guruhga yuborish ──────────────────────────────────────────

def _make_dl_cb(job: Job, status, header: str):
    t0 = time.monotonic()
    last = [0.0]

    async def cb(line: str):
        # disk watchdog: joy tugab qolsa aria2 to'xtatiladi
        if not job.low_disk and _free_bytes() < LOW_DISK_BYTES:
            job.low_disk = True
            if job.proc and job.proc.returncode is None:
                try:
                    job.proc.kill()
                except ProcessLookupError:
                    pass
            return
        p = _parse_progress(line)
        if not p:
            return
        now = time.monotonic()
        if now - last[0] < 4.0:
            return
        last[0] = now
        await _edit(
            status,
            f"{header}\n\n"
            f"`{_progress_bar(p['pct'])}` `{p['pct']}%`  ({p['done']}/{p['total']})\n"
            f"🚀 `{p['speed']}`  👥 `{p['peers']}`  🌱 `{p['seeds']}`\n"
            f"⏱ ETA: `{p['eta'] or '?'}`  ⏳ `{_fmt_dur(now - t0)}`",
            _cancel_kb(job),
            progress=True,
        )
    return cb


def _make_up_cb(job: Job, status, header: str):
    last = [0.0]

    async def cb(current, total):
        if job.cancelled and StopTransmission is not None:
            raise StopTransmission
        now = time.monotonic()
        if now - last[0] < 4.0 and current < total:
            return
        last[0] = now
        pct = int(current * 100 / total) if total else 0
        await _edit(
            status,
            f"{header}\n\n📤 Guruhga yuborilmoqda\n"
            f"`{_progress_bar(pct)}` `{pct}%`  ({_fmt_bytes(current)}/{_fmt_bytes(total)})",
            _cancel_kb(job),
            progress=True,
        )
    return cb


async def _download_and_send(job: Job, status) -> None:
    try:
        sem = _get_sem()
        if sem.locked():
            job.state = "queued"
            await _edit(
                status,
                f"⏳ *{_md_escape(job.title[:50])}*\n\n"
                f"Navbatda... (hozir {MAX_PARALLEL} ta torrent yuklanmoqda)",
                _cancel_kb(job),
            )
        async with sem:
            if job.cancelled:
                await _edit(status, "❌ Bekor qilindi.")
                return
            await _process_files(job, status)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("_download_and_send xato: %s", e)
        await _edit(status, f"❌ Xato: {_code(str(e)[:150])}")
    finally:
        _finish(job)


# ── Post-processing: remux / bo'lish ──────────────────────────────────────────

def _probe_sync(path: str) -> dict:
    info = {"duration": 0.0, "vcodec": "", "acodecs": []}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            capture_output=True, text=True, timeout=90,
        )
        d = json.loads(r.stdout or "{}")
        for st in d.get("streams", []):
            ct = st.get("codec_type")
            if ct == "video" and not info["vcodec"] and not st.get("disposition", {}).get("attached_pic"):
                info["vcodec"] = st.get("codec_name", "")
            elif ct == "audio":
                info["acodecs"].append(st.get("codec_name", ""))
        info["duration"] = float(d.get("format", {}).get("duration", 0) or 0)
    except Exception as e:
        logger.warning("ffprobe(json) xato: %s", e)
    return info


def _rm(*paths) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


async def _remux_mp4(job: Job, src: str, probe: dict) -> Optional[str]:
    """H.264 videoni qayta kodlamasdan MP4 (faststart) ga o'tkazadi."""
    dst = os.path.splitext(src)[0] + ".mp4"
    if os.path.abspath(dst) == os.path.abspath(src):
        return None
    audio_ok = all(a in ("aac", "mp3") for a in probe["acodecs"])
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", src,
           "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy"]
    cmd += ["-c:a", "copy"] if audio_ok else ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    cmd += ["-sn", "-dn", "-map_chapters", "-1", "-movflags", "+faststart", dst]
    rc = await _run_aria(job, cmd, None, 3600)
    if rc == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return dst
    _rm(dst)
    return None


async def _split_video(job: Job, src: str, limit: int, probe: dict) -> Optional[list]:
    """Videoni `-c copy` bilan limitdan kichik qismlarga bo'ladi (keyframe chegarasida)."""
    dur = probe["duration"]
    if dur <= 0:
        return None
    size = os.path.getsize(src)
    stem, ext = os.path.splitext(src)
    n = max(2, math.ceil(size * 1.06 / (limit * 0.92)))
    for _ in range(4):
        if n > 60:
            return None
        pattern = stem.replace("%", "%%") + ".part%02d" + ext
        cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", src,
               "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
               "-f", "segment", "-segment_time", f"{dur / n:.3f}",
               "-reset_timestamps", "1", "-segment_start_number", "1"]
        if ext.lower() == ".mp4":
            cmd += ["-segment_format_options", "movflags=+faststart"]
        cmd.append(pattern)
        rc = await _run_aria(job, cmd, None, 3600)
        parts = sorted(glob.glob(glob.escape(stem) + ".part*" + glob.escape(ext)))
        if job.cancelled:
            _rm(*parts)
            return None
        if rc == 0 and parts and all(0 < os.path.getsize(x) <= limit for x in parts):
            return parts
        _rm(*parts)
        n = int(n * 1.4) + 1
    return None


def _split_raw_sync(src: str, limit: int) -> list:
    """Video bo'lmagan fayllar uchun bayt bo'yicha bo'lish: name.part001 ..."""
    chunk = int(limit * 0.95)
    parts, i = [], 1
    with open(src, "rb") as f:
        while True:
            first = f.read(1)
            if not first:
                break
            out = f"{src}.part{i:03d}"
            written = 1
            with open(out, "wb") as o:
                o.write(first)
                while written < chunk:
                    buf = f.read(min(8 * 1024 * 1024, chunk - written))
                    if not buf:
                        break
                    o.write(buf)
                    written += len(buf)
            parts.append(out)
            i += 1
    return parts


async def _prepare_outputs(job: Job, status, header: str, path: str) -> list:
    """Yuklangan fayldan yuboriladigan fayl(lar) ro'yxatini tayyorlaydi."""
    cur = path
    ext = os.path.splitext(cur)[1].lower()
    size = os.path.getsize(cur)

    if ext in VIDEO_EXTS:
        probe = await asyncio.to_thread(_probe_sync, cur)
        if REMUX and ext != ".mp4" and probe["vcodec"] == "h264" and _free_bytes() > size * 1.1 + MIN_FREE_BYTES:
            await _edit(status, f"{header}\n\n🛠 MP4 ga o'tkazilmoqda (qayta kodlashsiz)...", _cancel_kb(job))
            dst = await _remux_mp4(job, cur, probe)
            if job.cancelled:
                if dst:
                    _rm(dst)
                return []
            if dst:
                _rm(cur)
                cur, ext, size = dst, ".mp4", os.path.getsize(dst)
        if size > job.limit:
            if not SPLIT:
                raise RuntimeError("fayl Telegram limitidan katta")
            if _free_bytes() < size * 1.1 + MIN_FREE_BYTES:
                raise RuntimeError("bo'lish uchun diskda joy yetarli emas")
            await _edit(status, f"{header}\n\n✂️ Limitdan katta ({_fmt_bytes(size)}) — qismlarga bo'linmoqda...", _cancel_kb(job))
            parts = await _split_video(job, cur, job.limit, probe)
            if job.cancelled:
                return []
            if not parts:
                raise RuntimeError("videoni bo'lib bo'lmadi")
            _rm(cur)
            return parts
        return [cur]

    if size > job.limit:
        if not SPLIT:
            raise RuntimeError("fayl Telegram limitidan katta")
        if _free_bytes() < size * 1.1 + MIN_FREE_BYTES:
            raise RuntimeError("bo'lish uchun diskda joy yetarli emas")
        await _edit(status, f"{header}\n\n✂️ Limitdan katta ({_fmt_bytes(size)}) — qismlarga bo'linmoqda...", _cancel_kb(job))
        parts = await asyncio.to_thread(_split_raw_sync, cur, job.limit)
        _rm(cur)
        return parts
    return [cur]


async def _process_files(job: Job, status) -> None:
    job.state = "downloading"

    if not ARCHIVE_GROUP_ID:
        await _edit(status, "❌ ARCHIVE_GROUP_ID sozlanmagan!")
        return
    client = await get_user_client()
    if not client:
        await _edit(status, "❌ Userbot ulanmagan! Admin bilan bog'laning.")
        return

    trackers = await _get_trackers()
    sel = [f for f in job.shown if f.idx in job.selected]
    total = len(sel)
    sent = 0
    failed: list = []
    title_md = _md_escape(job.title[:50])

    for n, tf in enumerate(sel, 1):
        if job.cancelled:
            break
        name = os.path.basename(tf.path)
        counter = f"Fayl {n}/{total}: " if total > 1 else ""
        header = f"🧲 *{title_md}*\n📄 {counter}{_code(name[:60])}"

        if _free_bytes() < tf.size + MIN_FREE_BYTES:
            failed.append((name, "diskda joy yetarli emas"))
            continue

        job.state = "downloading"
        job.low_disk = False
        cmd = _aria_base(trackers) + [
            "--dir", job.dl_dir,
            f"--select-file={tf.idx}",
            f"--bt-stop-timeout={STALL_TIMEOUT}",
            job.torrent_path,
        ]
        rc = -1
        for attempt in range(1, MAX_ATTEMPTS + 1):
            rc = await _run_aria(job, cmd, _make_dl_cb(job, status, header), FILE_TIMEOUT)
            if job.cancelled or rc in (0, -3, 9) or job.low_disk:
                break
            if attempt < MAX_ATTEMPTS:
                await _edit(status, f"{header}\n\n🔁 Aloqa uzildi — davom ettirilmoqda...", _cancel_kb(job))
        if job.cancelled:
            break
        if rc != 0:
            failed.append((name, _rc_reason(rc, job)))
            _purge(job, tf)
            continue

        path = _locate(job, tf)
        if not path:
            failed.append((name, "fayl diskdan topilmadi"))
            continue

        job.state = "sending"
        outs: list = []
        ok = False
        try:
            outs = await _prepare_outputs(job, status, header, path)
            if job.cancelled:
                break
            ok = bool(outs)
            for pi, outp in enumerate(outs, 1):
                part = (pi, len(outs)) if len(outs) > 1 else None
                hdr = header + (f"\n✂️ Qism {pi}/{len(outs)}" if part else "")
                if not await _send_file(client, outp, job.title, _make_up_cb(job, status, hdr), part):
                    ok = False
                    break
                if job.cancelled:
                    break
        except Exception as e:
            logger.exception("Qayta ishlash/yuborish xato: %s", e)
            failed.append((name, f"xato: {str(e)[:80]}"))
            ok = False
        finally:
            _rm(*outs)
            _purge(job, tf)
            # bo'lish/remux yarim yo'lda qolgan qoldiqlar
            for leftover in glob.glob(glob.escape(os.path.splitext(path)[0]) + ".*"):
                if leftover != path and (".part" in leftover or leftover.endswith(".mp4")):
                    _rm(leftover)
        if job.cancelled:
            break
        if ok:
            sent += 1

    # ── Yakuniy xabar ──
    if job.cancelled:
        await _edit(status, f"❌ Bekor qilindi.\nYuborilgan: `{sent}/{total}`")
        return

    if sent == total:
        text = (
            f"✅ *{title_md}*\n\n"
            f"🗂 Guruhga yuborildi: `{sent}` ta fayl"
        )
    else:
        lines = [f"{'⚠️' if sent else '❌'} *{title_md}*\n", f"Yuborildi: `{sent}/{total}`"]
        for name, why in failed[:6]:
            lines.append(f"• {_code(name[:40])} — {_md_escape(why)}")
        if len(failed) > 6:
            lines.append(f"… va yana {len(failed) - 6} ta")
        text = "\n".join(lines)
    await _edit(status, text)


# ── Guruhga yuborish ──────────────────────────────────────────────────────────

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
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            try:
                if k == "duration":
                    meta["duration"] = int(float(v))
                elif k in ("width", "height"):
                    meta[k] = int(v)
            except ValueError:
                pass
    except Exception as e:
        logger.warning("ffprobe xato: %s", e)
    return meta


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


async def _send_file(client, path: str, title: str, up_cb, part: Optional[tuple] = None) -> bool:
    """Faylni ARCHIVE_GROUP_ID ga yuboradi. 2 urinish. True — muvaffaqiyat."""
    size = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lower()
    fname = os.path.basename(path)
    cap = (
        f"🧲 <b>{html.escape(title[:120])}</b>\n"
        f"📦 <code>{_fmt_bytes(size)}</code>\n"
        f"📄 <code>{html.escape(fname[:120])}</code>"
        + (f"\n✂️ Qism {part[0]}/{part[1]}" if part else "")
    )

    last_exc: Optional[Exception] = None
    for attempt in (1, 2):
        thumb = None
        try:
            if ext in VIDEO_EXTS:
                meta = await asyncio.to_thread(_get_video_meta_sync, path)
                if meta["duration"] > 0:
                    thumb = await asyncio.to_thread(_make_thumb_sync, path, meta["duration"])
                extra = ""
                if meta["duration"]:
                    extra += f"\n⏱ <code>{_fmt_dur(meta['duration'])}</code>"
                if meta["width"] and meta["height"]:
                    extra += f"\n📐 <code>{meta['width']}×{meta['height']}</code>"
                await client.send_video(
                    chat_id=ARCHIVE_GROUP_ID, video=path, caption=cap + extra,
                    supports_streaming=True,
                    duration=meta["duration"] or None,
                    width=meta["width"] or None, height=meta["height"] or None,
                    thumb=thumb or None, parse_mode=ParseMode.HTML, progress=up_cb,
                )
            elif ext in AUDIO_EXTS:
                await client.send_audio(
                    chat_id=ARCHIVE_GROUP_ID, audio=path, caption=cap,
                    title=os.path.splitext(fname)[0][:60],
                    parse_mode=ParseMode.HTML, progress=up_cb,
                )
            else:
                await client.send_document(
                    chat_id=ARCHIVE_GROUP_ID, document=path, caption=cap,
                    file_name=fname, parse_mode=ParseMode.HTML, progress=up_cb,
                )
            return True
        except Exception as e:
            last_exc = e
            logger.warning("send urinish %d xato: %s", attempt, e)
            await asyncio.sleep(3)
        finally:
            if thumb and os.path.exists(thumb):
                try:
                    os.remove(thumb)
                except OSError:
                    pass
    if last_exc:
        raise last_exc
    return False


# ── Asosiy handler ────────────────────────────────────────────────────────────

_HELP = (
    "🧲 *Torrent*\n\n"
    "*Qidirish:*\n"
    "`/torrent Inception 2010`\n"
    "`/torrent Breaking Bad S01`\n\n"
    "*To'g'ridan:*\n"
    "`/torrent magnet:?xt=urn:btih:...`\n"
    "`/torrent <infohash>`\n"
    "`/torrent https://example.com/file.torrent`\n"
    "yoki .torrent faylga *reply* qilib `/torrent`\n\n"
    "📦 Manba: YTS · 1337x · Rutor · TPB · Nyaa · TCSV\n"
    "📁 Ko'p fayli torrentda kerakli fayllarni tanlaysiz.\n"
    "✂️ Katta fayllar avtomatik qismlarga bo'linadi, MKV → MP4.\n"
    "📊 `/torrent status` · `/torrent cancel`"
)


_STATE_ICON = {"meta": "🔎", "select": "📋", "queued": "⏳", "downloading": "⬇️", "sending": "📤"}


def _cancel_user_jobs(uid: int) -> int:
    n = 0
    for j in list(_jobs.values()):
        if j.owner != uid:
            continue
        n += 1
        if j.state == "select":
            _finish(j)
            continue
        j.cancelled = True
        if j.proc and j.proc.returncode is None:
            try:
                j.proc.kill()
            except ProcessLookupError:
                pass
        if j.state == "queued" and j.task:
            j.task.cancel()
    return n


async def _show_status(msg, uid: int) -> None:
    mine = [j for j in _jobs.values() if _can_control(uid, j.owner) and (j.owner == uid or _is_admin(uid))]
    lines = [f"📊 *Torrent holati*  (parallel: `{MAX_PARALLEL}`, bo'sh joy: `{_fmt_bytes(_free_bytes())}`)\n"]
    if not mine:
        lines.append("Faol torrent yo'q.")
    for j in mine[:10]:
        lines.append(f"{_STATE_ICON.get(j.state, '•')} {_code(j.title[:40])} — `{j.state}`, tanlangan: `{len(j.selected)}`")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


def _is_admin(uid: int) -> bool:
    try:
        from utils.auth import is_admin
        return bool(is_admin(uid))
    except Exception:
        return False


async def torrent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    _cleanup_sessions()
    query = " ".join(context.args or []).strip()

    # .torrent faylga reply
    reply = msg.reply_to_message
    doc = getattr(reply, "document", None) if reply else None
    if not query and doc and (doc.file_name or "").lower().endswith(".torrent"):
        try:
            os.makedirs(TEMP_DIR, exist_ok=True)
            path = os.path.join(TEMP_DIR, f"tr_up_{uuid.uuid4().hex[:8]}.torrent")
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(path)
        except Exception as e:
            await msg.reply_text(f"❌ Faylni olib bo'lmadi: {e}")
            return
        await _start_job("file", path, doc.file_name[:-8][:80] or "Torrent", msg, user.id)
        return

    if not query:
        await msg.reply_text(_HELP, parse_mode="Markdown")
        return

    if query.lower().startswith("debug ") and _is_admin(user.id):
        q = query[6:].strip()
        wait = await msg.reply_text("🔧 Manbalar tekshirilmoqda...")
        report = await _debug_sources(q)
        try:
            await wait.edit_text(f"🔧 Debug: {q}\n\n{report}"[:4000])
        except Exception:
            pass
        return

    if query.lower() in ("status", "holat"):
        await _show_status(msg, user.id)
        return
    if query.lower() in ("cancel", "stop", "bekor"):
        n = _cancel_user_jobs(user.id)
        await msg.reply_text(f"❌ Bekor qilindi: {n} ta torrent." if n else "Faol torrent yo'q.")
        return

    kind, value = _normalize_source(query)
    if kind:
        await _start_job(kind, value, _magnet_title(value), msg, user.id)
        return

    # Qidirish
    status = await msg.reply_text(
        f"🔍 Torrent qidirilmoqda...\n{_code(query[:100])}",
        parse_mode="Markdown",
    )
    results, failed = await _search_all(query)

    if not results:
        extra = f"\n⚠️ Javob bermadi: {', '.join(failed)}" if failed else ""
        await _edit(
            status,
            f"❌ {_code(query[:100])} bo'yicha torrent topilmadi.\n\n"
            f"Boshqa kalit so'zlar bilan urinib ko'ring.{extra}",
        )
        return

    session = TorrentSession(results=results[:MAX_RESULTS], query=query, all=results, failed=failed)
    _sessions[user.id] = session
    text, kb = _render_results(session, user.id)
    await _edit(status, text, kb)


def _render_results(s: TorrentSession, user_id: int):
    lines = [f"🔍 *{_md_escape(s.query[:60])}* — {len(s.results)} natija\n"]
    for i, r in enumerate(s.results, 1):
        q = _QUAL_LABEL.get(_quality_of(r.title), "")
        lines.append(
            f"{i}. {_code(r.title[:45])}\n"
            f"   📦 {_md_escape(r.size)}  {_seed_icon(r.seeds)} {_fmt_seeds(r.seeds)} seed  [{r.source}]"
            + (f"  🎞 {q}" if q else "")
        )
    if s.failed:
        lines.append(f"\n⚠️ Javob bermadi: {', '.join(s.failed)}")
    return "\n".join(lines), _build_results_keyboard(s, user_id)


def _build_results_keyboard(s: TorrentSession, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    quals = [k for k, _ in _QUAL_PATTERNS if any(_quality_of(r.title) == k for r in s.all)]
    if quals:
        chips = [InlineKeyboardButton(("• " if s.filt == "all" else "") + "Hammasi", callback_data=f"tr_dl|q|{user_id}|all")]
        for k in quals:
            chips.append(InlineKeyboardButton(("• " if s.filt == k else "") + _QUAL_LABEL[k], callback_data=f"tr_dl|q|{user_id}|{k}"))
        rows.append(chips)
    for i, r in enumerate(s.results):
        t = r.title if len(r.title) <= 32 else r.title[:31] + "…"
        rows.append([InlineKeyboardButton(
            f"{_seed_icon(r.seeds)} {i + 1}. {t} [{r.source}]",
            callback_data=f"tr_dl|{user_id}|{i}",
        )])
    rows.append([InlineKeyboardButton("❌ Bekor", callback_data="tr_cancel")])
    return InlineKeyboardMarkup(rows)


# ── Callback handler ──────────────────────────────────────────────────────────
# Callback formatlari (bot.py dagi "tr_dl|", "tr_cancel", "tr_force|" prefikslari
# bilan mos — bot.py ga TEGISH SHART EMAS):
#   tr_dl|<user_id>|<idx>          — qidiruv natijasini tanlash
#   tr_force|<user_id>|<idx>       — 0 seedli natijani majburiy yuklash
#   tr_cancel                      — qidiruvni bekor qilish
#   tr_dl|f|<job>|<file_idx>       — faylni belgilash/olib tashlash
#   tr_dl|pg|<job>|<page>          — sahifa
#   tr_dl|all|<job> / none / b     — barchasi / tozalash / eng katta
#   tr_dl|go|<job>                 — yuklashni boshlash
#   tr_dl|x|<job>                  — bekor qilish (istalgan bosqichda)

def _can_control(uid: int, owner: int) -> bool:
    if uid == owner:
        return True
    try:
        from utils.auth import is_admin
        return bool(is_admin(uid))
    except Exception:
        return False


async def torrent_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    uid = query.from_user.id
    parts = data.split("|")

    # ── Qidiruvni bekor qilish ──
    if data == "tr_cancel":
        await query.answer()
        _sessions.pop(uid, None)
        await query.edit_message_text("❌ Bekor qilindi.")
        return

    # ── 0 seed — majburiy yuklash ──
    if parts[0] == "tr_force" and len(parts) >= 3:
        try:
            owner_id, idx = int(parts[1]), int(parts[2])
        except ValueError:
            await query.answer("Xato!", show_alert=True)
            return
        if not _can_control(uid, owner_id):
            await query.answer("Bu sizning qidiruvingiz emas.", show_alert=True)
            return
        await query.answer()
        session = _sessions.get(owner_id)
        if not session or idx >= len(session.results):
            await query.edit_message_text("❌ Session tugadi! Qaytadan /torrent yuboring.")
            return
        result = session.results[idx]
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _start_job("magnet", result.magnet, result.title, query.message, owner_id)
        return

    if parts[0] != "tr_dl" or len(parts) < 3:
        await query.answer()
        return

    # ── Qidiruv natijasini tanlash: tr_dl|<uid>|<idx> ──
    if parts[1].isdigit():
        try:
            owner_id, idx = int(parts[1]), int(parts[2])
        except ValueError:
            await query.answer()
            return
        if not _can_control(uid, owner_id):
            await query.answer("Bu sizning qidiruvingiz emas.", show_alert=True)
            return
        await query.answer()

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
        if not result.magnet:
            await query.edit_message_text(
                f"❌ Bu torrent uchun magnet link topilmadi.\n{result.info_url or ''}"
            )
            return

        if result.seeds == 0:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("⚠️ Baribir yukla", callback_data=f"tr_force|{owner_id}|{idx}"),
                InlineKeyboardButton("❌ Bekor", callback_data="tr_cancel"),
            ]])
            await query.edit_message_text(
                f"⚠️ *{_md_escape(result.title[:60])}*\n\n"
                "🔴 Seeder: 0 — fayl yuklanmasligi mumkin!\n"
                "Metadata uchun 2.5 daqiqagacha kutiladi.\n\n"
                "Baribir urinib ko'rasizmi?",
                parse_mode="Markdown",
                reply_markup=kb,
            )
            return

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _start_job("magnet", result.magnet, result.title, query.message, owner_id)
        return

    # ── Sifat filtri: tr_dl|q|<uid>|<qual> ──
    if parts[1] == "q" and len(parts) >= 4 and parts[2].isdigit():
        owner_id = int(parts[2])
        if not _can_control(uid, owner_id):
            await query.answer("Bu sizning qidiruvingiz emas.", show_alert=True)
            return
        sess = _sessions.get(owner_id)
        if not sess:
            await query.answer("Session tugadi. Qaytadan /torrent yuboring.", show_alert=True)
            return
        await query.answer()
        qual = parts[3]
        pool = sess.all if qual == "all" else [r for r in sess.all if _quality_of(r.title) == qual]
        if not pool:
            return
        sess.filt, sess.results = qual, pool[:MAX_RESULTS]
        text, kb = _render_results(sess, owner_id)
        await _edit(query.message, text, kb)
        return

    # ── Job amallari: tr_dl|<act>|<job>|<arg> ──
    act, jid = parts[1], parts[2]
    arg = parts[3] if len(parts) > 3 else ""
    job = _jobs.get(jid)

    if not job:
        await query.answer("Bu job tugagan yoki topilmadi.", show_alert=True)
        return
    if not _can_control(uid, job.owner):
        await query.answer("Bu sizning torrentingiz emas.", show_alert=True)
        return

    # Bekor qilish (istalgan bosqich)
    if act == "x":
        await query.answer("Bekor qilinmoqda...")
        if job.state in ("meta", "downloading", "sending"):
            job.cancelled = True
            if job.proc and job.proc.returncode is None:
                try:
                    job.proc.kill()
                except ProcessLookupError:
                    pass
            # xabarni ishlayotgan task o'zi yangilaydi
            return
        if job.state == "queued":
            job.cancelled = True
            if job.task:
                job.task.cancel()
        await _edit(query.message, "❌ Bekor qilindi.")
        _finish(job)
        return

    # Qolganlari faqat tanlash bosqichida
    if job.state != "select":
        await query.answer()
        return

    if act == "f":
        try:
            fidx = int(arg)
        except ValueError:
            await query.answer()
            return
        tf = next((f for f in job.shown if f.idx == fidx), None)
        if not tf:
            await query.answer()
            return
        if _blocked(job, tf):
            await query.answer(
                f"Fayl {_fmt_bytes(tf.size)} — Telegram limitidan ({_fmt_bytes(job.limit)}) katta.",
                show_alert=True,
            )
            return
        await query.answer()
        job.selected.symmetric_difference_update({fidx})
    elif act == "pg":
        await query.answer()
        try:
            job.page = int(arg)
        except ValueError:
            pass
    elif act == "all":
        await query.answer()
        job.selected = {f.idx for f in job.shown if not _blocked(job, f)}
    elif act == "none":
        await query.answer()
        job.selected = set()
    elif act == "b":
        await query.answer()
        ok = [f for f in job.shown if not _blocked(job, f)]
        job.selected = {max(ok, key=lambda f: f.size).idx} if ok else set()
    elif act == "go":
        if not job.selected:
            await query.answer("Hech narsa tanlanmagan!", show_alert=True)
            return
        await query.answer("Boshlanmoqda...")
        job.state = "queued"
        job.task = _spawn(_download_and_send(job, query.message))
        return
    else:
        await query.answer()
        return

    await _edit(query.message, _picker_text(job), _picker_kb(job))
