FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway environment variables (set in Railway dashboard):
#   TELEGRAM_BOT_TOKEN   — bot token (@BotFather)
#   API_ID               — my.telegram.org
#   API_HASH             — my.telegram.org
#   LOCAL_BOT_API_URL    — http://local-bot-api.railway.internal:8081/bot
#   R2_ACCOUNT_ID        — Cloudflare account ID
#   R2_ACCESS_KEY_ID     — R2 API token key
#   R2_SECRET_ACCESS_KEY — R2 API token secret
#   R2_BUCKET_NAME       — bucket name
#   R2_PUBLIC_URL        — public URL (optional)

CMD ["python", "bot.py"]
