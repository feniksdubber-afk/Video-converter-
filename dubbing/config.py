"""
dubbing/config.py — Dubbing Engine uchun butunlay alohida konfiguratsiya.

MUHIM IZOLYATSIYA QOIDASI:
Bu fayl mavjud `config.py` (bot ildizida) bilan HECH QANDAY bog'liqligi yo'q —
undan import qilmaydi va uni import qilmaydi. Barcha muhit o'zgaruvchilari
DUBBING_ prefiksi bilan boshlanadi, shuning uchun mavjud botning .env
qiymatlari bilan hech qachon to'qnashmaydi.

Bu modul asosiy botning `TEMP_DIR`, `DB_PATH` yoki boshqa har qanday
sozlamasiga hech qanday tarzda murojaat qilmaydi.
"""

import os

# ── Feature flag ────────────────────────────────────────────────────────────
# False bo'lsa: dubbing worker ishga tushmaydi (darhol chiqadi), dubbing
# database'ga ulanish urinilmaydi, va (keyingi bosqichlarda) bot handlerlari
# ro'yxatdan o'tkazilmaydi. Bu Step 1'da hali bot integratsiyasi yo'qligi
# uchun to'g'ridan-to'g'ri ta'sir qilmaydi, lekin worker entrypoint shu
# flagni tekshiradi.
DUBBING_ENABLED = os.environ.get("DUBBING_ENABLED", "false").strip().lower() in (
    "1", "true", "yes",
)

# ── PostgreSQL (dubbing'ga tegishli, alohida database) ──────────────────────
# Mavjud botning SQLite bazasiga yoki AfsonaMovieBot'ning kinobot.db fayliga
# HECH QANDAY aloqasi yo'q. Bu butunlay yangi, alohida PostgreSQL instansiyasi
# uchun ulanish satri.
DUBBING_DATABASE_URL = os.environ.get(
    "DUBBING_DATABASE_URL",
    "postgresql://dubbing_user:dubbing_pass@127.0.0.1:5432/afsona_dubbing",
)

# Test uchun alohida baza (test suite shu qiymatni ishlatadi, hech qachon
# yuqoridagi production URL'ga tegmaydi).
DUBBING_TEST_DATABASE_URL = os.environ.get(
    "DUBBING_TEST_DATABASE_URL",
    "postgresql://dubbing_user:dubbing_pass@127.0.0.1:5432/afsona_dubbing_test",
)

# ── Vaqtinchalik fayllar ─────────────────────────────────────────────────────
# Mavjud botning TEMP_DIR (/tmp/videobot) ga HECH QACHON yozilmaydi — u yerda
# soatlik cleanup loop ishlaydi va bu dubbing ishlarini kutilmaganda o'chirib
# yuborishi mumkin. Dubbing o'zining mustaqil papkasidan foydalanadi.
DUBBING_TEMP_DIR = os.environ.get("DUBBING_TEMP_DIR", "/tmp/afsona_dubbing")

# ── Worker sozlamalari ────────────────────────────────────────────────────────
DUBBING_WORKER_CONCURRENCY = int(os.environ.get("DUBBING_WORKER_CONCURRENCY", "1"))
DUBBING_LEASE_SECONDS = int(os.environ.get("DUBBING_LEASE_SECONDS", "1800"))
DUBBING_REAPER_INTERVAL_SECONDS = int(os.environ.get("DUBBING_REAPER_INTERVAL_SECONDS", "60"))
DUBBING_CLAIM_POLL_INTERVAL_SECONDS = float(os.environ.get("DUBBING_CLAIM_POLL_INTERVAL_SECONDS", "2.0"))

# ── Log ────────────────────────────────────────────────────────────────────
DUBBING_LOG_LEVEL = os.environ.get("DUBBING_LOG_LEVEL", "INFO")

# ── Media ingestion (Step 2) ─────────────────────────────────────────────────
# Kirish faylining maksimal ruxsat etilgan hajmi (baytlarda). Standart: 20GB.
DUBBING_MAX_INPUT_BYTES = int(os.environ.get("DUBBING_MAX_INPUT_BYTES", str(20 * 1024 * 1024 * 1024)))

# Ingestion bosqichida ffmpeg audio ekstraktsiyasi uchun timeout (soniyalarda).
DUBBING_INGEST_TIMEOUT_SECONDS = int(os.environ.get("DUBBING_INGEST_TIMEOUT_SECONDS", "1800"))

# ── Segmentation (Step 3) ─────────────────────────────────────────────────────
# Bu segmentdan qisqa bo'lgan speech/silence segmentlar qo'shni segmentga
# birlashtiriladi (soniyalarda).
DUBBING_MIN_SEGMENT_SEC = float(os.environ.get("DUBBING_MIN_SEGMENT_SEC", "0.3"))

# Bu segmentdan uzun bo'lgan segmentlar rekursiv o'rtadan bo'linadi
# (soniyalarda).
DUBBING_MAX_SEGMENT_SEC = float(os.environ.get("DUBBING_MAX_SEGMENT_SEC", "20.0"))

# Segmentatsiya bosqichi (audio qayta ekstraktsiya + silencedetect) uchun
# umumiy timeout (soniyalarda).
DUBBING_SEGMENTATION_TIMEOUT_SECONDS = int(
    os.environ.get("DUBBING_SEGMENTATION_TIMEOUT_SECONDS", "900")
)

# ffmpeg `silencedetect` audio filtri uchun shovqin chegarasi (dBFS). Bundan
# pastroq (jimroq) audio sukunat sifatida hisoblanadi.
DUBBING_SILENCE_THRESHOLD_DB = float(os.environ.get("DUBBING_SILENCE_THRESHOLD_DB", "-35"))

# `silencedetect` sukunat sifatida hisoblash uchun talab qilinadigan
# minimal davomiylik (soniyalarda).
DUBBING_SILENCE_MIN_DURATION_SEC = float(
    os.environ.get("DUBBING_SILENCE_MIN_DURATION_SEC", "0.5")
)

os.makedirs(DUBBING_TEMP_DIR, exist_ok=True)
