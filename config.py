import os

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_ID    = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH  = os.environ.get("TELEGRAM_API_HASH", "")

MAX_FILE_SIZE_MB = 2000
TEMP_DIR = "/tmp/videobot"

# ── Persistent data directory ─────────────────────────────────────────────
# Railway: /data volume mount qilingan bo'lsa ishlatiladi
# Local / Replit: loyiha papkasida ./data/ papkasi
_data_env = os.environ.get("DATA_DIR", "")
if _data_env and os.path.isdir(_data_env):
    DATA_DIR = _data_env
elif os.path.isdir("/data"):
    DATA_DIR = "/data"
else:
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DB_PATH = os.path.join(DATA_DIR, "settings.db")

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
