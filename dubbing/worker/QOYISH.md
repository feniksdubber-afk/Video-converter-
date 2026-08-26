# Step 6 — qayerga qo'yish kerak (repo ildizidan)

- bot.py                              -> bot.py (ustiga yozing)
- dubbing_bridge.py                   -> dubbing_bridge.py (ustiga yozing)
- dubbing/config.py                   -> dubbing/config.py (ustiga yozing)
- dubbing_worker_entrypoint.py        -> dubbing/worker/entrypoint.py (ustiga yozing, nom o'zgardi!)
- dubbing/transcription/__init__.py   -> dubbing/transcription/__init__.py (YANGI papka)
- dubbing/transcription/transcriber.py-> dubbing/transcription/transcriber.py (YANGI)

## Diqqat: `dubbing_worker_entrypoint.py` fayl nomi ataylab o'zgartirilgan
(zip ichida ikkita "entrypoint.py" bo'lib qolmasligi uchun) — serverga
qo'yishda uni `dubbing/worker/entrypoint.py`ga albatta qayta nomlang.

## .env ga hech narsa qo'shish shart emas (standart qiymatlar yetarli):
DUBBING_WHISPER_MODEL_SIZE=small   (config.py'da standart, xohlasangiz .env'da override qiling)
DUBBING_WHISPER_COMPUTE_TYPE=int8  (CPU uchun tez)

## Push va deploy (Step 4/5 bilan bir xil tartib):
git add bot.py dubbing_bridge.py dubbing/config.py dubbing/worker/entrypoint.py dubbing/transcription/
git commit -m "Step 6: Whisper transcription (small model, CPU)"
git push origin main

# serverda:
cd /opt/videobot/app && git pull origin main
cd /opt/videobot && sudo docker compose up -d --build videobot
