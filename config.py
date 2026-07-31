import os

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Fall back to API_ID / API_HASH which are already set for the local bot-api server
API_ID    = int(os.environ.get("TELEGRAM_API_ID") or os.environ.get("API_ID", "0"))
API_HASH         = os.environ.get("TELEGRAM_API_HASH") or os.environ.get("API_HASH", "")

# ── Save Restricted (userbot session) ─────────────────────────────────────
SESSION_STRING   = os.environ.get("SESSION_STRING", "")

# Local Bot API — Railway da: http://local-bot-api.railway.internal:8081/bot
# Replit da: http://localhost:8080/bot
# Bo'sh qoldirilsa → standart Telegram API ishlatiladi (50 MB limit)
LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL", "").strip()

MAX_FILE_SIZE_MB = 2000
TEMP_DIR = "/tmp/videobot"

# ── Cloudflare R2 ─────────────────────────────────────────────────────────
R2_ACCOUNT_ID    = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_KEY    = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET        = os.environ.get("R2_BUCKET_NAME", "")
R2_PUBLIC_URL    = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")

# ── Persistent data directory ─────────────────────────────────────────────
_data_env = os.environ.get("DATA_DIR", "")
if _data_env and os.path.isdir(_data_env):
    DATA_DIR = _data_env
elif os.path.isdir("/data"):
    DATA_DIR = "/data"
else:
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DB_PATH = os.path.join(DATA_DIR, "settings.db")

# ── Ruxsat (whitelist) ───────────────────────────────────────────────────────
ALLOWED_USER_IDS_ENV = os.environ.get("ALLOWED_USER_IDS", "")
ADMIN_USER_IDS_ENV   = os.environ.get("ADMIN_USER_IDS", "")

# ── Save → arxiv guruhi (forum topic) ───────────────────────────────────────
# Bo'sh bo'lsa → buyruq yuborilgan chatga saqlanadi
ARCHIVE_GROUP_ID = os.environ.get("ARCHIVE_GROUP_ID", "").strip()
if ARCHIVE_GROUP_ID.lstrip("-").isdigit():
    ARCHIVE_GROUP_ID = int(ARCHIVE_GROUP_ID)
else:
    ARCHIVE_GROUP_ID = None

# Har save uchun yangi forum topic yaratish (ARCHIVE_GROUP_ID kerak)
AUTO_CREATE_TOPIC = os.environ.get("AUTO_CREATE_TOPIC", "true").lower() in ("1", "true", "yes")

# R2 upload default papka prefiksi (users/{user_id}/uploads/)
R2_USER_PREFIX = os.environ.get("R2_USER_PREFIX", "users")

# ── Studiya menejerlari uchun Afsona Studio API ────────────────────────────
# Studiya menejerlari konvertatsiya qilingan videoni to'g'ridan-to'g'ri
# o'z studiyasiga (Afsona platformasiga) shu manzil orqali yuklaydi.
STUDIO_API_BASE = os.environ.get("STUDIO_API_BASE", "https://app.afsonatv.uz/api")

# ── AfsonaMovieBot (asosiy platforma) bilan umumiy SQLite baza ─────────────
# Videokonverter bot shu fayldan (FAQAT O'QISH uchun) foydalanuvchining qaysi
# studiya(lar)ga "manager" sifatida biriktirilganini avtomatik aniqlaydi —
# alohida login/parol talab qilinmaydi. Hetzner'da bu odatda AfsonaMovieBot
# konteyneridagi kinobot.db fayliga (read-only volume orqali) ishora qiladi.
SHARED_DB_PATH = os.environ.get("SHARED_DB_PATH", "").strip()

# AfsonaMovieBot (asosiy platforma) SQLite bazasining videobot konteyneri
# ichidagi yo'li (read-only mount). Docker Compose'da shu faylni
# AfsonaMovieBot'ning /data/kinobot.db fayliga (read_only: true bilan)
# bog'lash kerak — shunda videobot mavjud studiyalar ro'yxatini jonli o'qiy oladi.
AFSONA_DB_PATH = os.environ.get("AFSONA_DB_PATH", "/afsona-data/kinobot.db")

# ── Asosiy AfsonaMovieBot platformasi bilan ulanish ────────────────────────
# Studiyalar va ularning HAQIQIY menejerlari (studio_members jadvali) asosiy
# platforma SQLite bazasida saqlanadi. Video-konvertor bot shu baza faylini
# FAQAT O'QISH uchun ko'radi (studiyalar ro'yxati + kim menejer ekanini
# tekshirish uchun) — hech qanday yozish amalga oshirilmaydi.
PLATFORM_DB_PATH = os.environ.get("PLATFORM_DB_PATH", "/platform-data/kinobot.db")

# Asosiy platforma bilan BIR XIL qiymat bo'lishi SHART (api-server'dagi
# SESSION_SECRET bilan aynan mos kelishi kerak) — aks holda bot yaratgan
# cli_upload tokenlarini platforma qabul qilmaydi.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
