from telegram import Update
from telegram.ext import ContextTypes


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Converter Botga xush kelibsiz!*\n\n"
        "🎬 Bu bot sizga video fayllar bilan ishlashga yordam beradi:\n\n"
        "• 🔄 Formatlarni o'zgartirish (MKV→MP4 va boshqalar)\n"
        "• 📐 Siqish va optimallashtirish\n"
        "• ✂️ Video kesish\n"
        "• 🔇 Ovozni o'chirish\n"
        "• 🎵 Videoni audioga aylantirish\n"
        "• 📸 Avtomatik skrinshotlar\n"
        "• 🖼 Muayyan vaqtda skrinsot\n"
        "• 📝 Subtitr birlashtirish\n\n"
        "📤 *Foydalanish uchun video yuboring!*\n\n"
        "⚠️ Maksimal fayl hajmi: 2 GB",
        parse_mode="Markdown",
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Yordam*\n\n"
        "1️⃣ Botga video fayl yuboring\n"
        "2️⃣ Kerakli amalni tanlang\n"
        "3️⃣ Qo'shimcha ma'lumot so'ralsa kiriting\n"
        "4️⃣ Tayyor faylni oling\n\n"
        "🔧 *Qo'llab-quvvatlanadigan formatlar:*\n"
        "MP4, MKV, AVI, MOV, WEBM, FLV\n\n"
        "🎯 *Qo'llab-quvvatlanadigan audio:*\n"
        "MP3, AAC, OGG, WAV, FLAC\n\n"
        "💡 Muammo bo'lsa /start buyrug'ini bosing",
        parse_mode="Markdown",
    )
