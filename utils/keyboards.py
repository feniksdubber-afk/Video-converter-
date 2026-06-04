from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🎬 Video Konvertor", callback_data="convert"),
            InlineKeyboardButton("📐 Siqish", callback_data="compress"),
        ],
        [
            InlineKeyboardButton("✂️ Video Kesish", callback_data="trim"),
            InlineKeyboardButton("🔇 Ovozni O'chirish", callback_data="remove_audio"),
        ],
        [
            InlineKeyboardButton("🎵 Videoni Audioga", callback_data="video_to_audio"),
            InlineKeyboardButton("📸 Skrinshotlar", callback_data="screenshots"),
        ],
        [
            InlineKeyboardButton("🖼 Qo'lda Skrinsot", callback_data="manual_shot"),
            InlineKeyboardButton("📝 Subtitr", callback_data="subtitle"),
        ],
        [
            InlineKeyboardButton("🎞 Stream Remover", callback_data="stream_remover"),
            InlineKeyboardButton("📦 Stream Extractor", callback_data="stream_extractor"),
        ],
        [
            InlineKeyboardButton("🖼 Thumbnail", callback_data="thumbnail"),
            InlineKeyboardButton("📋 Media Info", callback_data="media_info"),
        ],
        [
            InlineKeyboardButton("✏️ Rename", callback_data="rename"),
            InlineKeyboardButton("🎬 Sample", callback_data="generate_sample"),
        ],
        [
            InlineKeyboardButton("✂️ Splitter", callback_data="splitter"),
            InlineKeyboardButton("➕ Merger", callback_data="merger"),
        ],
        [
            InlineKeyboardButton("🎵 Video+Audio", callback_data="vid_aud_merge"),
        ],
        [
            InlineKeyboardButton("⚙️ Sozlamalar", callback_data="settings"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def format_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("MP4", callback_data="fmt_mp4"),
            InlineKeyboardButton("MKV", callback_data="fmt_mkv"),
            InlineKeyboardButton("AVI", callback_data="fmt_avi"),
        ],
        [
            InlineKeyboardButton("MOV", callback_data="fmt_mov"),
            InlineKeyboardButton("WEBM", callback_data="fmt_webm"),
            InlineKeyboardButton("FLV", callback_data="fmt_flv"),
        ],
        [InlineKeyboardButton("📐 O'lcham o'zgartirish", callback_data="resolution")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def resolution_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("4K (2160p)", callback_data="res_2160"),
            InlineKeyboardButton("1080p (Full HD)", callback_data="res_1080"),
        ],
        [
            InlineKeyboardButton("720p (HD)", callback_data="res_720"),
            InlineKeyboardButton("480p (SD)", callback_data="res_480"),
        ],
        [
            InlineKeyboardButton("360p", callback_data="res_360"),
            InlineKeyboardButton("240p", callback_data="res_240"),
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def compress_quality_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🟢 Yuqori sifat (kam siqish)", callback_data="cq_high"),
            InlineKeyboardButton("🟡 O'rtacha sifat", callback_data="cq_medium"),
        ],
        [
            InlineKeyboardButton("🔴 Past sifat (ko'p siqish)", callback_data="cq_low"),
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def audio_format_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("MP3", callback_data="aud_mp3"),
            InlineKeyboardButton("AAC", callback_data="aud_aac"),
            InlineKeyboardButton("OGG", callback_data="aud_ogg"),
        ],
        [
            InlineKeyboardButton("WAV", callback_data="aud_wav"),
            InlineKeyboardButton("FLAC", callback_data="aud_flac"),
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def screenshots_count_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("5 ta", callback_data="ss_5"),
            InlineKeyboardButton("10 ta", callback_data="ss_10"),
            InlineKeyboardButton("20 ta", callback_data="ss_20"),
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def cancel_keyboard():
    keyboard = [[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")]]
    return InlineKeyboardMarkup(keyboard)
