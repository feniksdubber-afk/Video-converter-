from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ═══════════════════════════════════════════════════════════
# Asosiy kategoriya menyusi (video qabul qilingandan keyin)
# ═══════════════════════════════════════════════════════════

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Kategoriyalar bo'yicha asosiy menyu."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Video Tahrirlash",    callback_data="cat_video"),
            InlineKeyboardButton("🎵 Audio & GIF",         callback_data="cat_audio"),
        ],
        [
            InlineKeyboardButton("📝 Subtitrlar",          callback_data="cat_subtitle"),
            InlineKeyboardButton("🎞 Stream Boshqaruv",    callback_data="cat_stream"),
        ],
        [
            InlineKeyboardButton("🛠 Qo'shimcha Vositalar", callback_data="cat_tools"),
            InlineKeyboardButton("📦 Batch Processor",     callback_data="cat_batch"),
        ],
        [
            InlineKeyboardButton("☁️ R2 Bulut",            callback_data="cat_r2"),
            InlineKeyboardButton("⚙️ Sozlamalar",          callback_data="settings"),
        ],
    ])


def start_keyboard() -> InlineKeyboardMarkup:
    """Start ekranida ko'rsatiladigan menyu (video yo'q)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Batch Processor",  callback_data="cat_batch"),
            InlineKeyboardButton("☁️ R2 Bulut",         callback_data="cat_r2"),
        ],
        [
            InlineKeyboardButton("⚙️ Sozlamalar",       callback_data="settings"),
            InlineKeyboardButton("❓ Yordam",            callback_data="help_cb"),
        ],
    ])


# ═══════════════════════════════════════════════════════════
# Kategoriya sub-menyulari
# ═══════════════════════════════════════════════════════════

def cat_video_keyboard() -> InlineKeyboardMarkup:
    """🎬 Video Tahrirlash kategoriyasi."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Format O'zgartirish", callback_data="convert"),
            InlineKeyboardButton("📐 O'lcham (Res)",       callback_data="resolution"),
        ],
        [
            InlineKeyboardButton("📉 Siqish",              callback_data="compress"),
            InlineKeyboardButton("✂️ Kesish (Trim)",       callback_data="trim"),
        ],
        [
            InlineKeyboardButton("🔄 Rotate / Flip",       callback_data="rotate"),
            InlineKeyboardButton("🚀 Tezlik",              callback_data="speed"),
        ],
        [
            InlineKeyboardButton("📐 Crop (Qirqish)",      callback_data="crop"),
            InlineKeyboardButton("✨ Fade Effekti",        callback_data="fade"),
        ],
        [
            InlineKeyboardButton("💧 Watermark",           callback_data="watermark"),
        ],
        [InlineKeyboardButton("🔙 Asosiy Menyu",           callback_data="back")],
    ])


def cat_audio_keyboard() -> InlineKeyboardMarkup:
    """🎵 Audio & GIF kategoriyasi."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔇 Ovozni O'chirish",   callback_data="remove_audio"),
            InlineKeyboardButton("🔊 Ovoz Balandligi",    callback_data="volume"),
        ],
        [
            InlineKeyboardButton("🎵 Videoni Audioga",    callback_data="video_to_audio"),
            InlineKeyboardButton("🎨 GIF Yaratish",       callback_data="gif_maker"),
        ],
        [InlineKeyboardButton("🔙 Asosiy Menyu",          callback_data="back")],
    ])


def cat_subtitle_keyboard() -> InlineKeyboardMarkup:
    """📝 Subtitrlar kategoriyasi."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Soft Sub Qo'shish",  callback_data="subtitle"),
            InlineKeyboardButton("🔥 Hardsub (Yoqish)",   callback_data="hardsub"),
        ],
        [
            InlineKeyboardButton("📤 Sub Chiqarib Olish", callback_data="subtitle_extractor"),
            InlineKeyboardButton("🔄 Sub Konvertor",      callback_data="sub_converter"),
        ],
        [
            InlineKeyboardButton("🌐 Sub Tarjimon",       callback_data="sub_translate"),
        ],
        [InlineKeyboardButton("🔙 Asosiy Menyu",          callback_data="back")],
    ])


def cat_stream_keyboard() -> InlineKeyboardMarkup:
    """🎞 Stream Boshqaruv kategoriyasi."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 Stream O'chirish",   callback_data="stream_remover"),
            InlineKeyboardButton("📦 Stream Olish",       callback_data="stream_extractor"),
        ],
        [InlineKeyboardButton("🔙 Asosiy Menyu",          callback_data="back")],
    ])


def cat_tools_keyboard() -> InlineKeyboardMarkup:
    """🛠 Qo'shimcha Vositalar kategoriyasi."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼 Thumbnail",          callback_data="thumbnail"),
            InlineKeyboardButton("📋 Media Info",         callback_data="media_info"),
        ],
        [
            InlineKeyboardButton("✏️ Rename",             callback_data="rename"),
            InlineKeyboardButton("🎬 Sample Yaratish",    callback_data="generate_sample"),
        ],
        [
            InlineKeyboardButton("✂️ Splitter",           callback_data="splitter"),
            InlineKeyboardButton("➕ Video Merger",       callback_data="merger"),
        ],
        [
            InlineKeyboardButton("📸 Skrinshotlar",       callback_data="screenshots"),
            InlineKeyboardButton("🖼 Qo'lda Skrinsot",   callback_data="manual_shot"),
        ],
        [
            InlineKeyboardButton("🎵 Video + Audio",      callback_data="vid_aud_merge"),
        ],
        [InlineKeyboardButton("🔙 Asosiy Menyu",          callback_data="back")],
    ])


# ═══════════════════════════════════════════════════════════
# Umumiy tugmalar
# ═══════════════════════════════════════════════════════════

def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")]
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Asosiy Menyu", callback_data="back")]
    ])


# ═══════════════════════════════════════════════════════════
# Eski funksiyalar (ichki handlerlarda ishlatiladi)
# ═══════════════════════════════════════════════════════════

def format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Video Format Tanlang", callback_data="convert_header")],
        [
            InlineKeyboardButton("MP4",  callback_data="fmt_mp4"),
            InlineKeyboardButton("MKV",  callback_data="fmt_mkv"),
            InlineKeyboardButton("AVI",  callback_data="fmt_avi"),
        ],
        [
            InlineKeyboardButton("MOV",  callback_data="fmt_mov"),
            InlineKeyboardButton("WEBM", callback_data="fmt_webm"),
            InlineKeyboardButton("FLV",  callback_data="fmt_flv"),
        ],
        [
            InlineKeyboardButton("📹 Video sifatida yuborish", callback_data="fmt_as_video"),
            InlineKeyboardButton("📄 Fayl sifatida yuborish",  callback_data="fmt_as_file"),
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="cat_video")],
    ])


def resolution_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("4K (2160p)",     callback_data="res_2160"),
            InlineKeyboardButton("1080p (Full HD)", callback_data="res_1080"),
        ],
        [
            InlineKeyboardButton("720p (HD)",      callback_data="res_720"),
            InlineKeyboardButton("480p (SD)",      callback_data="res_480"),
        ],
        [
            InlineKeyboardButton("360p",           callback_data="res_360"),
            InlineKeyboardButton("240p",           callback_data="res_240"),
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="cat_video")],
    ])


def compress_quality_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Yuqori sifat (kam siqish)", callback_data="cq_high")],
        [InlineKeyboardButton("🟡 O'rtacha sifat",            callback_data="cq_medium")],
        [InlineKeyboardButton("🔴 Kichik hajm (ko'p siqish)", callback_data="cq_low")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="cat_video")],
    ])


def audio_format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("MP3",  callback_data="aud_mp3"),
            InlineKeyboardButton("AAC",  callback_data="aud_aac"),
            InlineKeyboardButton("OGG",  callback_data="aud_ogg"),
        ],
        [
            InlineKeyboardButton("WAV",  callback_data="aud_wav"),
            InlineKeyboardButton("FLAC", callback_data="aud_flac"),
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="cat_audio")],
    ])


def screenshots_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("5 ta",  callback_data="ss_5"),
            InlineKeyboardButton("10 ta", callback_data="ss_10"),
            InlineKeyboardButton("20 ta", callback_data="ss_20"),
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="cat_tools")],
    ])


def stream_remover_keyboard(streams: list[dict], selected: set) -> InlineKeyboardMarkup:
    keyboard = []
    for s in streams:
        idx = s.get("index")
        stype = s.get("codec_type", "?").upper()
        codec = s.get("codec_name", "?")
        tags = s.get("tags", {})
        lang = tags.get("language", "")
        title = tags.get("title", "")

        extra = ""
        if stype == "VIDEO":
            w, h = s.get("width", ""), s.get("height", "")
            extra = f" {w}x{h}" if w and h else ""
        elif stype == "AUDIO":
            ch = s.get("channels", "")
            extra = f" {ch}ch" if ch else ""

        label = f"#{idx} {stype} [{codec}{extra}]"
        if lang:
            label += f" {lang}"
        if title:
            label += f" — {title}"

        check = "✅ " if idx in selected else "⬜ "
        keyboard.append([InlineKeyboardButton(check + label, callback_data=f"rmtoggle_{idx}")])

    keyboard.append([InlineKeyboardButton("🎵 Barcha audiolarni belgilash",    callback_data="rmall_audio")])
    keyboard.append([InlineKeyboardButton("📝 Barcha subtitrlarni belgilash",  callback_data="rmall_subs")])
    keyboard.append([InlineKeyboardButton("🗑 Barcha streamlarni belgilash",   callback_data="rmall_streams")])

    selected_count = len(selected)
    confirm_label = f"✅ Tayyor ({selected_count} ta tanlandi)" if selected_count else "✅ Tayyor"
    keyboard.append([InlineKeyboardButton(confirm_label, callback_data="rm_confirm")])
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="cat_stream")])
    return InlineKeyboardMarkup(keyboard)


def stream_extractor_keyboard(streams: list[dict]) -> InlineKeyboardMarkup:
    keyboard = []
    keyboard.append([InlineKeyboardButton("🎵 Barcha audiolarni chiqarib olish",    callback_data="extract_all_audio")])
    keyboard.append([InlineKeyboardButton("📝 Barcha subtitrlarni chiqarib olish",  callback_data="extract_all_subs")])
    keyboard.append([InlineKeyboardButton("📦 Barcha streamlarni chiqarib olish",   callback_data="extract_all_streams")])

    for s in streams:
        idx = s.get("index")
        stype = s.get("codec_type", "?").upper()
        codec = s.get("codec_name", "?")
        tags = s.get("tags", {})
        lang = tags.get("language", "")
        title = tags.get("title", "")

        extra = ""
        if stype == "VIDEO":
            w, h = s.get("width", ""), s.get("height", "")
            extra = f" {w}x{h}" if w and h else ""
        elif stype == "AUDIO":
            ch = s.get("channels", "")
            extra = f" {ch}ch" if ch else ""

        label = f"#{idx} {stype} [{codec}{extra}]"
        if lang:
            label += f" {lang}"
        if title:
            label += f" — {title}"

        keyboard.append([InlineKeyboardButton(label, callback_data=f"extract_stream_{idx}")])

    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="cat_stream")])
    return InlineKeyboardMarkup(keyboard)
