import os

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Fall back to API_ID / API_HASH which are already set for the local bot-api server
API_ID    = int(os.environ.get("TELEGRAM_API_ID") or os.environ.get("API_ID", "0"))
API_HASH  = os.environ.get("TELEGRAM_API_HASH") or os.environ.get("API_HASH", "")

# Local Bot API — points to the telegram-bot-api server running on port 8080
LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL", "http://localhost:8080/bot")

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

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
