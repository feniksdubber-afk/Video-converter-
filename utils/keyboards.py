from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard():
    keyboard = [
        # ── Video editing ─────────────────────────────────────────
        [
            InlineKeyboardButton("🎬 Video Konvertor",   callback_data="convert"),
            InlineKeyboardButton("📐 Siqish",            callback_data="compress"),
        ],
        [
            InlineKeyboardButton("✂️ Video Kesish",      callback_data="trim"),
            InlineKeyboardButton("🔄 Rotate / Flip",     callback_data="rotate"),
        ],
        [
            InlineKeyboardButton("🚀 Tezlik",            callback_data="speed"),
            InlineKeyboardButton("📐 Crop (Qirqish)",    callback_data="crop"),
        ],
        [
            InlineKeyboardButton("✨ Fade Effekti",      callback_data="fade"),
            InlineKeyboardButton("💧 Watermark",         callback_data="watermark"),
        ],
        # ── Audio ─────────────────────────────────────────────────
        [
            InlineKeyboardButton("🔇 Ovozni O'chirish",  callback_data="remove_audio"),
            InlineKeyboardButton("🔊 Ovoz Balandligi",   callback_data="volume"),
        ],
        [
            InlineKeyboardButton("🎵 Videoni Audioga",   callback_data="video_to_audio"),
            InlineKeyboardButton("🎨 GIF Yaratish",      callback_data="gif_maker"),
        ],
        # ── Screenshots ───────────────────────────────────────────
        [
            InlineKeyboardButton("📸 Skrinshotlar",      callback_data="screenshots"),
            InlineKeyboardButton("🖼 Qo'lda Skrinsot",  callback_data="manual_shot"),
        ],
        # ── Subtitles ─────────────────────────────────────────────
        [
            InlineKeyboardButton("📝 Soft Sub",           callback_data="subtitle"),
            InlineKeyboardButton("🔥 Hardsub",            callback_data="hardsub"),
        ],
        [
            InlineKeyboardButton("📤 Subtitr Extractor", callback_data="subtitle_extractor"),
            InlineKeyboardButton("🔄 Sub Konvertor",     callback_data="sub_converter"),
        ],
        [
            InlineKeyboardButton("🌐 Sub Tarjimon",      callback_data="sub_translate"),
        ],
        # ── Streams ───────────────────────────────────────────────
        [
            InlineKeyboardButton("🎞 Stream Remover",    callback_data="stream_remover"),
            InlineKeyboardButton("📦 Stream Extractor",  callback_data="stream_extractor"),
        ],
        # ── Tools ─────────────────────────────────────────────────
        [
            InlineKeyboardButton("🖼 Thumbnail",         callback_data="thumbnail"),
            InlineKeyboardButton("📋 Media Info",        callback_data="media_info"),
        ],
        [
            InlineKeyboardButton("✏️ Rename",            callback_data="rename"),
            InlineKeyboardButton("🎬 Sample",            callback_data="generate_sample"),
        ],
        [
            InlineKeyboardButton("✂️ Splitter",          callback_data="splitter"),
            InlineKeyboardButton("➕ Merger",            callback_data="merger"),
        ],
        [
            InlineKeyboardButton("🎵 Video+Audio",       callback_data="vid_aud_merge"),
        ],
        # ── System ────────────────────────────────────────────────
        [
            InlineKeyboardButton("📦 Batch Processor",   callback_data="batch"),
        ],
        [
            InlineKeyboardButton("☁️ R2 Fayl Menejer",  callback_data="r2_list_0"),
        ],
        [
            InlineKeyboardButton("⚙️ Sozlamalar",        callback_data="settings"),
            InlineKeyboardButton("❌ Bekor qilish",      callback_data="cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def format_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎬 Video Converter", callback_data="convert_header")],
        [
            InlineKeyboardButton("Convert To MP4", callback_data="fmt_mp4"),
            InlineKeyboardButton("Convert To MKV", callback_data="fmt_mkv"),
        ],
        [
            InlineKeyboardButton("Convert To AVI", callback_data="fmt_avi"),
            InlineKeyboardButton("Convert To M4V", callback_data="fmt_m4v"),
        ],
        [
            InlineKeyboardButton("Convert as Video", callback_data="fmt_as_video"),
            InlineKeyboardButton("Convert as File", callback_data="fmt_as_file"),
        ],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back")],
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


def stream_remover_keyboard(streams: list[dict], selected: set) -> InlineKeyboardMarkup:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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

    keyboard.append([
        InlineKeyboardButton("🎵 Barcha audiolarni belgilash", callback_data="rmall_audio"),
    ])
    keyboard.append([
        InlineKeyboardButton("📝 Barcha subtitrlarni belgilash", callback_data="rmall_subs"),
    ])
    keyboard.append([
        InlineKeyboardButton("🗑 Barcha streamlarni belgilash", callback_data="rmall_streams"),
    ])

    selected_count = len(selected)
    confirm_label = f"✅ Tayyor ({selected_count} ta tanlandi)" if selected_count else "✅ Tayyor"
    keyboard.append([InlineKeyboardButton(confirm_label, callback_data="rm_confirm")])
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)


def stream_extractor_keyboard(streams: list[dict]) -> InlineKeyboardMarkup:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = []

    keyboard.append([InlineKeyboardButton("🎵 Barcha audiolarni chiqarib olish", callback_data="extract_all_audio")])
    keyboard.append([InlineKeyboardButton("📝 Barcha subtitrlarni chiqarib olish", callback_data="extract_all_subs")])
    keyboard.append([InlineKeyboardButton("📦 Barcha streamlarni chiqarib olish", callback_data="extract_all_streams")])

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

    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)
