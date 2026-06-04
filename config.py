import os

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

MAX_FILE_SIZE_MB = 2000
TEMP_DIR = "/tmp/videobot"

os.makedirs(TEMP_DIR, exist_ok=True)
