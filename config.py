import os

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

MAX_FILE_SIZE_MB = 2000
TEMP_DIR = "/tmp/videobot"

os.makedirs(TEMP_DIR, exist_ok=True)
