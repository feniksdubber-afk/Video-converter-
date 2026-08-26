"""
dubbing/segmentation/vad.py — ffmpeg `silencedetect` orqali sukunat
oraliqlarini aniqlash.

IZOLYATSIYA VA ATAYLAB QILINGAN DIZAYN QARORI:
Bu modul mavjud botning tizim `ffmpeg` binary'sini TO'G'RIDAN-TO'G'RI
subprocess orqali chaqiradi — `utils.ffmpeg_utils.run_ffmpeg`dan
FOYDALANMAYDI. Sababi:

    `run_ffmpeg` faqat ffmpeg muvaffaqiyatsiz chiqqanda (returncode != 0)
    stderr'ni qaytaradi; muvaffaqiyatli chiqishda ("True, ''") stderr
    butunlay tashlab yuboriladi. `silencedetect` filtri esa aynan
    stderr'ga yozadi VA ffmpeg odatiy holda 0 (muvaffaqiyat) bilan
    chiqadi — demak run_ffmpeg orqali chaqirilsa, kerakli ma'lumot
    (silence_start/silence_end vaqt belgilari) yo'qolib ketadi.

Bu — run_ffmpeg'dagi xato EMAS (u ingestion ehtiyojlariga to'liq javob
beradi), balki uning shartnomasi bu yerdagi ehtiyojga mos kelmasligi.
Shuning uchun `utils/ffmpeg_utils.py` O'ZGARTIRILMAYDI — bu modul shunchaki
o'zining mustaqil, izolyatsiya qilingan subprocess chaqiruviga ega, xuddi
shu `ffmpeg` binary'sidan foydalanadi. WAV qayta ekstraktsiyasi (VAD
kirishini tayyorlash) esa hamon tasdiqlangan `run_ffmpeg` orqali,
`dubbing.segmentation.segmenter` ichida amalga oshiriladi — bu modulda
EMAS.

Bu to'g'ridan-to'g'ri subprocess chaqiruvi FAQAT shu faylda mavjud —
dubbing'dagi boshqa hech qanday modul ffmpeg'ni to'g'ridan-to'g'ri
chaqirmaydi.

KUTILAYOTGAN FFMPEG CHIQISH FORMATI (stderr, "C" lokalida, ffmpeg 6.1.x
`silencedetect` audio filtridan):

    [silencedetect @ 0x...] silence_start: 12.345
    [silencedetect @ 0x...] silence_end: 14.360 | silence_duration: 2.015

Qatorlar xronologik tartibda chiqadi. Agar kirish fayli hali sukunatda
bo'lganida tugasa, ffmpeg oxirgi silence_end qatorini chiqarmasligi
mumkin — bunday holatda chaqiruvchi `silence_start`ni `duration_sec`da
yopilgan deb hisoblashi kerak (quyidagi `_parse_silence_intervals` shuni
qiladi).

Raqamlar OS lokalidan qat'iy nazar nuqta (`.`) o'nlik ajratgichi bilan
chiqadi (ffmpeg raqamli log chiqishini lokalizatsiya qilmaydi), lekin bu
modul baribir subprocess muhitini LC_ALL=C / LANG=C ga mahkamlaydi —
turli ffmpeg build'lari orasidagi har qanday lokalga bog'liq formatlash
farqlariga qarshi qo'shimcha himoya sifatida.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import List, Tuple

SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


class SilenceDetectionError(RuntimeError):
    """ffmpeg silencedetect chaqiruvi muvaffaqiyatsiz yoki chiqishni
    tahlil qilib bo'lmadi."""


def _build_env() -> dict:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def _run_silencedetect(
    input_path: str,
    threshold_db: float,
    min_silence_duration_sec: float,
    timeout_seconds: int,
) -> str:
    """
    ffmpeg'ni `silencedetect` audio filtri bilan ishga tushiradi va xom
    stderr matnini qaytaradi.

    Deterministik: bitta-thread'li dekodlash (-threads 1), fayl tizimiga
    hech qanday chiqish yozilmaydi (-f null -). Bir xil kirish + bir xil
    parametrlar → har doim bir xil stderr matni.
    """
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-threads", "1",
        "-i", input_path,
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_duration_sec}",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=_build_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"ffmpeg silencedetect timeout ({timeout_seconds}s): {input_path}"
        ) from exc
    except FileNotFoundError as exc:
        raise SilenceDetectionError("ffmpeg binary topilmadi") from exc

    if result.returncode != 0:
        tail = (result.stderr or "")[-2000:]
        raise SilenceDetectionError(
            f"ffmpeg silencedetect kod {result.returncode} bilan chiqdi: {tail}"
        )

    return result.stderr or ""


def _parse_silence_intervals(
    stderr_text: str,
    duration_sec: float,
) -> List[Tuple[float, float]]:
    """
    ffmpeg stderr matnidan silence_start/silence_end juftliklarini
    xronologik tartibda tahlil qiladi. Yopilmagan oxirgi silence_start
    (fayl sukunatda tugagan) `duration_sec`da yopiladi. [0, duration_sec]
    oralig'iga qisqartirilgan, saralangan (start, end) juftliklar
    ro'yxatini qaytaradi.
    """
    intervals: List[Tuple[float, float]] = []
    pending_start = None

    for line in stderr_text.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            if pending_start is not None:
                # Ikkita ketma-ket silence_start (silence_end'siz) —
                # bitta silencedetect filtri bilan sodir bo'lmasligi
                # kerak, lekin himoya sifatida avvalgisini shu nuqtada
                # yopamiz.
                intervals.append((pending_start, float(start_match.group(1))))
            pending_start = float(start_match.group(1))
            continue

        end_match = SILENCE_END_RE.search(line)
        if end_match:
            end_val = float(end_match.group(1))
            if pending_start is not None:
                intervals.append((pending_start, end_val))
                pending_start = None
            # aks holda: pending_start yo'q holda silence_end — e'tiborsiz
            # qoldiriladi (himoya, oddiy holatda yuz bermaydi).

    if pending_start is not None:
        intervals.append((pending_start, duration_sec))

    clipped: List[Tuple[float, float]] = []
    for start, end in intervals:
        start_c = max(0.0, min(start, duration_sec))
        end_c = max(0.0, min(end, duration_sec))
        if end_c > start_c:
            clipped.append((start_c, end_c))

    clipped.sort(key=lambda pair: pair[0])
    return clipped


def detect_silence_intervals(
    input_path: str,
    duration_sec: float,
    threshold_db: float,
    min_silence_duration_sec: float,
    timeout_seconds: int,
) -> List[Tuple[float, float]]:
    """
    `input_path` ustida ffmpeg silencedetect ishga tushiradi va aniqlangan
    sukunat oraliqlarini (start, end) juftliklari sifatida qaytaradi.

    SINXRON, BLOKLOVCHI chaqiruv — asyncio event loop ichida chaqiruvchi
    buni `loop.run_in_executor`ga o'rashi kerak, xuddi
    `dubbing.media.probe` tasdiqlangan `run_ffmpeg` sof funksiyalari
    uchun qiladigani kabi.
    """
    stderr_text = _run_silencedetect(
        input_path, threshold_db, min_silence_duration_sec, timeout_seconds
    )
    return _parse_silence_intervals(stderr_text, duration_sec)
