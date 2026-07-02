"""
fix_faststart_r2.py
────────────────────
Bir martalik / davriy tozalash skripti (Video-converter bot uchun).

Afsona MovieBot'dagi asl skriptdan farqi:
  - Bu botda `movies` / `episodes` kabi DB jadvallari YO'Q — foydalanuvchi
    fayllari faqat R2'ning o'zida (masalan `users/{id}/uploads/...`,
    `batch/{id}/...`) turadi, DB'da ularning ro'yxati saqlanmaydi.
  - Shu sabab bu versiya to'g'ridan-to'g'ri R2 bucket'ni skanerlaydi
    (ixtiyoriy `--prefix` bilan cheklash mumkin) va DB bilan solishtirish
    QILMAYDI.
  - Sync `boto3` ishlatadi (`aioboto3` bu loyihada yo'q), lekin bloklovchi
    chaqiruvlar `run_in_executor` orqali event loop'ni bo'g'maydi.

Nima qiladi:
  1. R2 bucket'dagi (yoki --prefix ostidagi) video fayllarni ro'yxatlaydi
     (kengaytmasi .mp4/.mov/.m4v bo'lganlar; .m3u8/HLS o'tkazib yuboriladi).
  2. Har biri uchun "moov atom" faylning boshida (faststart) yoki oxirida
     ekanini HTTP Range so'rovi bilan tekshiradi — butun faylni yuklab
     olmasdan.
  3. Agar faststart bo'lmasa:
       a) faylni to'liq yuklab oladi,
       b) `ffmpeg -c copy -movflags +faststart` bilan qayta paketlaydi
          (audio/video qayta encode QILINMAYDI — tez va sifatsiz yo'qotishsiz),
       c) yangi faylni R2'ga YANGI key bilan yuklaydi,
       d) eski (buzuq) faylni R2'dan o'chiradi,
       e) vaqtinchalik lokal fayllarni tozalaydi.

  DIQQAT: bu botda DB'da R2 key saqlanmagani uchun "bazani yangilash" bosqichi
  yo'q — shunchaki eski key o'rniga yangi key bilan fayl almashtiriladi (nomi
  o'zgaradi). Agar foydalanuvchiga avval yuborilgan havola henuz ishlatilmagan
  bo'lsa, eski havola endi ishlamaydi — shuning uchun bu skript ko'proq
  "yangi yuklanayotgan / hali hech kimga yuborilmagan" fayllar uchun emas,
  balki uzoq muddat R2'da turadigan (masalan --keep-both bilan ishlatilgan)
  fayllar uchun mo'ljallangan. Xavfsiz bo'lish uchun standart holatda eski
  fayl DARHOL o'chirilmaydi — buni --delete-old bilan yoqish kerak.

Ishlatish (loyiha papkasi ichidan):

    python -m scripts.fix_faststart_r2                       # hammasini tekshiradi/tuzatadi
    python -m scripts.fix_faststart_r2 --dry-run              # faqat tekshiradi, hech narsa o'zgartirmaydi
    python -m scripts.fix_faststart_r2 --prefix users/         # faqat shu prefiks ostida
    python -m scripts.fix_faststart_r2 --limit 20              # test uchun — faqat 20 ta fayl
    python -m scripts.fix_faststart_r2 --delete-old            # eski (buzuq) faylni ham o'chiradi
    python -m scripts.fix_faststart_r2 --concurrency 2          # bir vaqtda nechta fayl

Talablar:
    - Serverda `ffmpeg` o'rnatilgan bo'lishi kerak.
    - .env / muhit o'zgaruvchilarida R2_* to'liq bo'lishi kerak
      (config.py o'qiydigan barcha o'zgaruvchilar).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402

from config import TEMP_DIR  # noqa: E402
from utils import r2_manager  # noqa: E402
from utils.ffmpeg_utils import make_temp_path  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("fix_faststart_r2")

_PROBE_BYTES = 8 * 1024 * 1024      # moov/mdat qidirish uchun boshidan o'qiladigan hajm
_READ_CHUNK = 1 * 1024 * 1024
_VIDEO_EXTS = (".mp4", ".mov", ".m4v")


@dataclass
class R2VideoObject:
    key: str
    size: int
    url: str


# ==================== FASTSTART TEKSHIRUVI (Range so'rov bilan) ====================

async def probe_needs_faststart(session: aiohttp.ClientSession, url: str) -> Optional[bool]:
    """
    Faylning birinchi _PROBE_BYTES qismini o'qib, moov/mdat box tartibini
    tekshiradi.
      True  → faststart YO'Q, tuzatish kerak
      False → allaqachon faststart (moov mdat dan oldin)
      None  → aniqlab bo'lmadi (Range qo'llab-quvvatlanmadi va h.k.)
    """
    try:
        headers = {"Range": f"bytes=0-{_PROBE_BYTES - 1}"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status not in (200, 206):
                logger.warning("Probe: kutilmagan HTTP status %s — %s", resp.status, url[:100])
                return None
            data = await resp.read()
    except Exception as e:
        logger.warning("Probe xato (%s): %s", url[:100], e)
        return None

    pos = 0
    n = len(data)
    while pos + 8 <= n:
        size = int.from_bytes(data[pos:pos + 4], "big")
        boxtype = data[pos + 4:pos + 8]
        boxsize = size
        if size == 1:
            if pos + 16 > n:
                break
            boxsize = int.from_bytes(data[pos + 8:pos + 16], "big")
        elif size == 0:
            # box EOF gacha davom etadi — odatda oxirgi (mdat) box shunday bo'ladi
            if boxtype == b"moov":
                return False
            if boxtype == b"mdat":
                return True
            break

        if boxtype == b"moov":
            return False   # moov mdat dan oldin topildi → faststart YAXSHI
        if boxtype == b"mdat":
            return True    # mdat moov dan oldin → faststart KERAK

        if boxsize <= 0:
            break
        pos += boxsize

    # Prefiks ichida moov topilmadi → ehtimol fayl oxirida (odatiy muammo)
    return True


# ==================== FFMPEG REMUX ====================

async def remux_faststart(input_path: str) -> str:
    """`-c copy -movflags +faststart` bilan qayta paketlaydi.
    Muvaffaqiyatsiz bo'lsa, original yo'lni qaytaradi (chaqiruvchi buni
    solishtirib xatoni aniqlaydi)."""
    ext = os.path.splitext(input_path)[1].lstrip(".") or "mp4"
    out_path = make_temp_path(ext)
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-c", "copy", "-movflags", "+faststart", out_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=1800)
        if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            logger.error("ffmpeg remux xato: %s", stderr.decode(errors="replace")[-800:])
            return input_path
        return out_path
    except Exception as e:
        logger.error("ffmpeg remux istisno: %s", e)
        return input_path


def safe_delete_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def new_fixed_key(old_key: str) -> str:
    """Eski key asosida yangi (faststart) key yaratadi."""
    base, ext = os.path.splitext(old_key)
    ext = ext or ".mp4"
    return f"{base}_fs{int(time.time())}{ext}"


# ==================== R2'DAN VIDEO OBYEKTLARINI OLISH ====================

async def list_video_objects(prefix: str = "") -> list[R2VideoObject]:
    """R2 bucket'dagi (yoki prefix ostidagi) video fayllarni qaytaradi.
    HLS segmentlari (.m3u8, .ts) va boshqa video bo'lmagan fayllar
    o'tkazib yuboriladi."""
    all_files = await r2_manager.list_all_files(prefix=prefix, max_total=100000)
    videos: list[R2VideoObject] = []
    for f in all_files:
        key = f["key"]
        ext = os.path.splitext(key)[1].lower()
        if ext in _VIDEO_EXTS:
            videos.append(R2VideoObject(key=key, size=f["size"], url=f["url"]))
    return videos


# ==================== ASOSIY QAYTA ISHLASH ====================

async def process_object(
    obj: R2VideoObject,
    session: aiohttp.ClientSession,
    dry_run: bool,
    delete_old: bool,
    stats: dict,
) -> None:
    stats["checked"] += 1
    logger.info("🔎 Tekshirilmoqda: %s (%.1f MB)", obj.key, obj.size / (1024 * 1024))

    needs_fix = await probe_needs_faststart(session, obj.url)
    if needs_fix is False:
        logger.info("✅ Allaqachon faststart, o'tkazib yuborildi: %s", obj.key)
        stats["already_ok"] += 1
        return
    if needs_fix is None:
        logger.warning("⚠️  Aniqlab bo'lmadi (Range qo'llab-quvvatlanmadi?) — xavfsizlik uchun tuzatishga urinamiz: %s", obj.key)

    stats["needs_fix"] += 1

    if dry_run:
        logger.info("🧪 [DRY RUN] Tuzatilishi kerak edi: %s — %s", obj.key, obj.url)
        return

    tmp_dir = tempfile.mkdtemp(prefix="faststart_", dir=TEMP_DIR if os.path.isdir(TEMP_DIR) else None)
    ext = os.path.splitext(obj.key)[1] or ".mp4"
    original_path = os.path.join(tmp_dir, f"original{ext}")
    remuxed_path = ""

    try:
        # 1) To'liq yuklab olish
        logger.info("⬇️  Yuklab olinmoqda: %s", obj.key)
        async with session.get(obj.url, timeout=aiohttp.ClientTimeout(total=3600, sock_read=600)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Yuklab olishda HTTP {resp.status}")
            with open(original_path, "wb") as fh:
                async for chunk in resp.content.iter_chunked(_READ_CHUNK):
                    fh.write(chunk)

        if not os.path.exists(original_path) or os.path.getsize(original_path) == 0:
            raise RuntimeError("Yuklab olingan fayl bo'sh")

        # 2) Faststart remux
        logger.info("🎬 Remux qilinmoqda (+faststart): %s", obj.key)
        remuxed_path = await remux_faststart(original_path)
        if remuxed_path == original_path:
            raise RuntimeError("ffmpeg remux muvaffaqiyatsiz bo'ldi (original o'zgarishsiz qaytdi)")

        # 3) Yangi key bilan R2'ga yuklash
        new_key = new_fixed_key(obj.key)
        logger.info("⬆️  R2 ga yuklanmoqda: %s", new_key)
        new_size = os.path.getsize(remuxed_path)
        new_url = await r2_manager.upload_file(remuxed_path, object_key=new_key)

        # 4) Eski (buzuq) faylni ixtiyoriy o'chirish
        if delete_old:
            ok = await r2_manager.delete_file(obj.key)
            if ok:
                logger.info("🗑️  Eski R2 fayl o'chirildi: %s", obj.key)
            else:
                logger.warning("⚠️  Eski faylni o'chirishda xato (davom etamiz): %s", obj.key)
        else:
            logger.info("↪️  Eski fayl saqlab qolindi (--delete-old berilmagan): %s", obj.key)

        stats["fixed"] += 1
        logger.info(
            "✅ Tuzatildi: %s (%.1f MB) → %s\n    Yangi havola: %s",
            obj.key, new_size / (1024 * 1024), new_key, new_url,
        )

    except Exception as e:
        stats["errors"] += 1
        logger.error("❌ Xato (%s): %s", obj.key, e)

    finally:
        safe_delete_file(original_path)
        if remuxed_path and remuxed_path != original_path:
            safe_delete_file(remuxed_path)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


# ==================== MAIN ====================

async def main() -> None:
    parser = argparse.ArgumentParser(description="R2 dagi faststart bo'lmagan videolarni tuzatish")
    parser.add_argument("--dry-run", action="store_true", help="Hech narsa o'zgartirmasdan, faqat qaysi fayllar buzuqligini ko'rsatadi")
    parser.add_argument("--prefix", type=str, default="", help="Faqat shu R2 prefiks ostidagi fayllarni tekshirish (masalan users/ yoki batch/)")
    parser.add_argument("--limit", type=int, default=None, help="Faqat birinchi N ta faylni qayta ishlash (test uchun)")
    parser.add_argument("--delete-old", action="store_true", help="Tuzatilgandan keyin eski (buzuq) faylni R2'dan o'chirish")
    parser.add_argument("--concurrency", type=int, default=1, help="Bir vaqtda nechta faylni qayta ishlash (default: 1 — ffmpeg/disk yukini cheklash uchun)")
    args = parser.parse_args()

    if not r2_manager.is_configured():
        logger.error("❌ R2 sozlanmagan (.env da R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET_NAME tekshiring)")
        sys.exit(1)

    from shutil import which
    if which("ffmpeg") is None:
        logger.error("❌ ffmpeg topilmadi. O'rnating: apt-get install -y ffmpeg")
        sys.exit(1)

    logger.info("☁️  R2 prefiks: %s", args.prefix or "(butun bucket)")
    if args.dry_run:
        logger.info("🧪 DRY-RUN rejimi — hech narsa o'zgartirilmaydi")
    if args.delete_old:
        logger.info("⚠️  --delete-old yoqilgan — eski (buzuq) fayllar o'chiriladi")

    stats = {"checked": 0, "already_ok": 0, "needs_fix": 0, "fixed": 0, "errors": 0}

    logger.info("🔎 R2'dagi video fayllar ro'yxatlanmoqda...")
    objects = await list_video_objects(prefix=args.prefix)
    if args.limit:
        objects = objects[: args.limit]

    logger.info("📋 Jami tekshiriladigan video fayllar: %d", len(objects))
    if not objects:
        logger.info("Hech narsa topilmadi — chiqilmoqda.")
        return

    http_timeout = aiohttp.ClientTimeout(total=3600, connect=30)
    session_conn = aiohttp.TCPConnector(limit=max(4, args.concurrency * 2))

    async with aiohttp.ClientSession(timeout=http_timeout, connector=session_conn) as session:
        sem = asyncio.Semaphore(max(1, args.concurrency))

        async def _bound(obj: R2VideoObject):
            async with sem:
                await process_object(obj, session, args.dry_run, args.delete_old, stats)

        await asyncio.gather(*(_bound(o) for o in objects))

    logger.info(
        "\n"
        "════════════════════════════════════\n"
        "  YAKUNIY HISOBOT\n"
        "════════════════════════════════════\n"
        "  Tekshirildi     : %d\n"
        "  Allaqachon OK   : %d\n"
        "  Tuzatish kerak  : %d\n"
        "  Tuzatildi       : %d\n"
        "  Xatolar         : %d\n"
        "════════════════════════════════════",
        stats["checked"], stats["already_ok"], stats["needs_fix"], stats["fixed"], stats["errors"],
    )


if __name__ == "__main__":
    asyncio.run(main())
