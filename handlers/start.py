from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, start_keyboard, cat_video_keyboard, cat_audio_keyboard, cat_subtitle_keyboard, cat_stream_keyboard, cat_tools_keyboard


START_TEXT = (
    "👋 *Video Converter Botga xush kelibsiz!*\n\n"
    "🎬 Bu bot video fayllar bilan ishlashning barcha imkoniyatlarini beradi:\n\n"
    "  • Format o'zgartirish (MKV, MP4, AVI, WEBM...)\n"
    "  • Siqish, kesish, o'lcham o'zgartirish\n"
    "  • Subtitrlar qo'shish, tarjima qilish\n"
    "  • Audio ajratish, ovoz boshqarish\n"
    "  • Stream remover/extractor\n"
    "  • GIF, Watermark, Fade, Crop...\n"
    "  • Batch Processor (10 ta fayl bir vaqtda)\n"
    "  • Cloudflare R2 bulut saqlash\n\n"
    "📤 *Foydalanish uchun video yuboring!*\n"
    "_(Maksimal fayl: 2 GB | Local Bot API bilan: cheksiz)_"
)

HELP_TEXT = (
    "📖 *Foydalanish Qo'llanmasi*\n\n"
    "*1️⃣ Video yuborish:*\n"
    "  Botga video fayl yuboring (video yoki document sifatida)\n\n"
    "*2️⃣ Kategoriya tanlash:*\n"
    "  🎬 Video Tahrirlash — format, o'lcham, siqish...\n"
    "  🎵 Audio & GIF — ovoz, GIF yaratish...\n"
    "  📝 Subtitrlar — qo'shish, tarjima, konvert...\n"
    "  🎞 Stream Boshqaruv — stream o'chirish/ajratish\n"
    "  🛠 Vositalar — thumbnail, rename, splitter...\n"
    "  📦 Batch — bir necha faylni avtomatik ishlash\n\n"
    "*3️⃣ Amalni bajarish:*\n"
    "  Kategoriyadan kerakli amalni tanlang va ko'rsatmalarga amal qiling\n\n"
    "*🔧 Qo'llab-quvvatlanadigan video formatlari:*\n"
    "  MP4, MKV, AVI, MOV, WEBM, FLV, M4V, TS va boshqalar\n\n"
    "*🎵 Audio formatlari:*\n"
    "  MP3, AAC, OGG, WAV, FLAC\n\n"
    "*📝 Subtitr formatlari:*\n"
    "  SRT, ASS, SSA, VTT\n\n"
    "💡 Muammo bo'lsa /start bosing"
)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        START_TEXT,
        parse_mode="Markdown",
        reply_markup=start_keyboard(),
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        HELP_TEXT,
        parse_mode="Markdown",
        reply_markup=start_keyboard(),
    )


# ── Kategoriya callback handlerlari ───────────────────────────────────────────

async def show_cat_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path:
        await query.edit_message_text(
            "⚠️ Avval video yuboring!",
            reply_markup=start_keyboard(),
        )
        return
    await query.edit_message_text(
        "🎬 *Video Tahrirlash*\n\nKerakli amalni tanlang:",
        reply_markup=cat_video_keyboard(),
        parse_mode="Markdown",
    )


async def show_cat_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path:
        await query.edit_message_text(
            "⚠️ Avval video yuboring!",
            reply_markup=start_keyboard(),
        )
        return
    await query.edit_message_text(
        "🎵 *Audio & GIF*\n\nKerakli amalni tanlang:",
        reply_markup=cat_audio_keyboard(),
        parse_mode="Markdown",
    )


async def show_cat_subtitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path:
        await query.edit_message_text(
            "⚠️ Avval video yuboring!",
            reply_markup=start_keyboard(),
        )
        return
    await query.edit_message_text(
        "📝 *Subtitrlar*\n\nKerakli amalni tanlang:",
        reply_markup=cat_subtitle_keyboard(),
        parse_mode="Markdown",
    )


async def show_cat_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path:
        await query.edit_message_text(
            "⚠️ Avval video yuboring!",
            reply_markup=start_keyboard(),
        )
        return
    await query.edit_message_text(
        "🎞 *Stream Boshqaruv*\n\nKerakli amalni tanlang:",
        reply_markup=cat_stream_keyboard(),
        parse_mode="Markdown",
    )


async def show_cat_tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_path = context.user_data.get("video_path")
    if not video_path:
        await query.edit_message_text(
            "⚠️ Avval video yuboring!",
            reply_markup=start_keyboard(),
        )
        return
    await query.edit_message_text(
        "🛠 *Qo'shimcha Vositalar*\n\nKerakli amalni tanlang:",
        reply_markup=cat_tools_keyboard(),
        parse_mode="Markdown",
    )


async def show_help_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        HELP_TEXT,
        parse_mode="Markdown",
        reply_markup=start_keyboard(),
    )
