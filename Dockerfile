# ─── videobot uchun local Telegram Bot API server bilan ─────────────────────
# telegram-bot-api binary'ni qaytadan compile qilmasdan, afsona-app image'da
# allaqachon tayyor bo'lganidan nusxa olamiz (bir necha daqiqalik build
# o'rniga bir necha soniya).
FROM afsona-app AS tgbotapi_src

FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    gcc \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

COPY --from=tgbotapi_src /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY supervisord.conf /etc/supervisor/conf.d/videobot.conf

RUN mkdir -p /data/tgbotapi/tmp

CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/videobot.conf"]
