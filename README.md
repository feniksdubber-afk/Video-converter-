# 🎬 Video Converter Bot

O'zbekcha Telegram boti — video fayllarni qayta ishlash uchun.

## Funksiyalar

| Funksiya | Tavsif |
|----------|--------|
| 🎬 Video Konvertor | MKV→MP4, AVI, MOV, WEBM, FLV va boshqalar |
| 📐 O'lcham o'zgartirish | 4K, 1080p, 720p, 480p, 360p, 240p |
| 📐 Siqish / Optimallashtirish | Yuqori / O'rtacha / Past sifat |
| ✂️ Video Kesish | Boshlanish va tugash vaqti bo'yicha |
| 🔇 Ovozni O'chirish | Audio treklarni olib tashlash |
| 🎵 Videoni Audioga | MP3, AAC, OGG, WAV, FLAC |
| 📸 Avtomatik Skrinshotlar | 5, 10, 20 ta (teng oraliqda) |
| 🖼 Qo'lda Skrinsot | Muayyan vaqtda kadr olish |
| 📝 Subtitr Birlashtirish | SRT faylni videoga yozdirish |

## Railway'ga Deploy Qilish

### 1. Repository tayyorlash

```bash
# Faqat ushbu papkani alohida repo sifatida push qiling
cd artifacts/video-bot
git init
git add .
git commit -m "Video Converter Bot"
git remote add origin YOUR_GITHUB_REPO_URL
git push -u origin main
```

### 2. Railway sozlamalari

1. [railway.app](https://railway.app) ga kiring
2. **New Project** → **Deploy from GitHub repo**
3. Reponi tanlang
4. **Variables** bo'limiga o'ting va qo'shing:
   - `TELEGRAM_BOT_TOKEN` = BotFather dan olgan tokeningiz

### 3. Muhit o'zgaruvchilari

| O'zgaruvchi | Tavsif |
|-------------|--------|
| `TELEGRAM_BOT_TOKEN` | **Majburiy.** @BotFather dan olingan token |

### 4. Deploy tekshirish

Deploy bo'lgandan keyin Railway loglarida quyidagini ko'rishingiz kerak:
```
Bot ishga tushmoqda...
```

Telegram'da botingizga `/start` yuboring.

## Mahalliy ishga tushirish

```bash
# FFmpeg o'rnatilgan bo'lishi kerak
# Linux/Mac: sudo apt install ffmpeg / brew install ffmpeg

pip install -r requirements.txt
TELEGRAM_BOT_TOKEN=your_token python bot.py
```

## Stack

- Python 3.12
- python-telegram-bot 21.6
- FFmpeg (video qayta ishlash)
- Nixpacks (Railway build)
