"""
sender.py — Fayl yuborish logikasi.

Hajm bo'yicha yo'naltirish:
  <= 50 MB          → PTB (python-telegram-bot) to'g'ridan-to'g'ri
  50 MB – 2 GB      → Pyrogram MTProto (progress bilan)
  > 2 GB            → Cloudflare R2 (agar sozlangan), aks holda Gofile.io
"""

import os
import asyncio
import subprocess
import hashlib
import logging
import time
import aiohttp
from pyrogram.errors import PeerIdInvalid
from telegram import Message, InlineKeyboardButton, InlineKeyboardMarkup
from handlers.video_handler import get_pyrogram_client
from utils.r2_manager import upload_file as r2_upload, is_configured as r2_ok, R2_THRESHOLD, fmt_size as r2_fmt
from utils.ffmpeg_utils import sanitize_filename
from config import DATA_DIR

logger = logging.getLogger(__name__)

# Telegram callback_data 64 bayt bilan cheklangan.
# Fayl nomini to'g'ridan-to'g'ri ishlatish o'rniga qisqa hash saqlаymiz.
# {short_key: {"filename": ..., "url": ..., "file_path": ..., "ts": unix_time}}
#
# Diskka saqlanadi (_R2_PENDING_FILE) — bot qayta ishga tushganda (deploy/crash)
# ham yozuvlar (va R2 URL) yo'qolmaydi. `file_path` baribir vaqtinchalik bo'lgani
# uchun qayta ishga tushgandan keyin lokal fayl mavjud bo'lmaydi, lekin URL saqlanib
# qoladi va foydalanuvchi "ma'lumot topilmadi" emas, to'g'ri "fayl yo'q, link bu" javobini oladi.
_R2_PENDING_FILE = os.path.join(DATA_DIR, "r2_pending.json")
_R2_PENDING_TTL = 3600  # 1 soat


def _load_r2_pending() -> dict[str, dict]:
    from utils.atomic_json import load_json
    return load_json(_R2_PENDING_FILE, default={})


def _persist_r2_pending() -> None:
    from utils.atomic_json import save_json
    save_json(_R2_PENDING_FILE, _r2_pending)


_r2_pending: dict[str, dict] = _load_r2_pending()


def _cleanup_r2_pending():
    """1 soatdan eski yozuvlarni _r2_pending dan tozalaydi."""
    now = time.time()
    stale = [k for k, v in _r2_pending.items() if now - v.get("ts", 0) > _R2_PENDING_TTL]
    for k in stale:
        _r2_pending.pop(k, None)
    if stale:
        _persist_r2_pending()

TELEGRAM_LIMIT = 50 * 1024 * 1024        # 50 MB
PYROGRAM_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GB — oddiy akkaunt/bot limiti
PYROGRAM_PREMIUM_LIMIT = 4 * 1024 * 1024 * 1024  # 4 GB — Telegram Premium akkaunt limiti

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".wmv"}
AUDIO_EXTENSIONS = {".mp3", ".aac", ".ogg", ".wav", ".flac", ".m4a", ".opus", ".wma"}


def _progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


def _fmt_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


def _md_escape(text) -> str:
    """Telegram Markdown (legacy) uchun maxsus belgilarni escape qiladi.

    Fayl nomi, URL kabi tashqi manbadan keladigan matnlarni Markdown
    formatlangan xabar ichiga qo'yishdan oldin shundan o'tkazish kerak —
    aks holda `_`, `*`, `` ` ``, `[` kabi belgilar parse xatosiga olib
    kelishi mumkin.
    """
    s = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def _get_video_meta_sync(file_path: str) -> dict:
    """Sinxron versiya — faqat executor ichida ishlatiladi."""
    meta = {"duration": 0, "width": 0, "height": 0}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1",
             file_path],
            capture_output=True, text=True, timeout=30,
        )
        for line in r.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if key == "duration":
                    try:
                        meta["duration"] = int(float(val))
                    except Exception:
                        pass
                elif key == "width":
                    try:
                        meta["width"] = int(val)
                    except Exception:
                        pass
                elif key == "height":
                    try:
                        meta["height"] = int(val)
                    except Exception:
                        pass
    except Exception:
        pass
    return meta


async def _get_video_meta(file_path: str) -> dict:
    """Async: event loop ni bloklamasdan video meta ma'lumotlarini oladi."""
    return await asyncio.get_running_loop().run_in_executor(None, _get_video_meta_sync, file_path)


def _make_thumb_sync(file_path: str, duration: int) -> str | None:
    """Sinxron versiya — faqat executor ichida ishlatiladi."""
    try:
        from config import TEMP_DIR
        import uuid
        thumb_path = os.path.join(TEMP_DIR, f"thumb_{uuid.uuid4().hex}.jpg")
        seek = max(1, duration // 4) if duration > 4 else 1
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", file_path,
             "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "5", thumb_path],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
    except Exception:
        pass
    return None


async def _make_thumb(file_path: str, duration: int) -> str | None:
    """Async: event loop ni bloklamasdan thumbnail yaratadi."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _make_thumb_sync, file_path, duration
    )


async def _upload_to_gofile(file_path: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.gofile.io/servers") as r:
            data = await r.json()
            server = data["data"]["servers"][0]["name"]
        with open(file_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("file", f, filename=os.path.basename(file_path))
            async with session.post(
                f"https://{server}.gofile.io/contents/uploadfile", data=form
            ) as r:
                result = await r.json()
                if result.get("status") != "ok":
                    raise Exception(f"Gofile xato: {result}")
                return result["data"]["downloadPage"]


async def _upload_to_r2(
    message: Message, file_path: str, filename: str, file_size: int,
    user_id: int = 0, r2_object_key: str | None = None,
) -> str | None:
    """R2 ga yuklab, tugmali xabar yuboradi. URL qaytaradi yoki None."""
    status_msg = await message.reply_text(
        f"☁️ *R2 ga yuklanmoqda...*\n\n"
        f"`[░░░░░░░░░░░░]` `0%`\n"
        f"`0` / `{_fmt_size(file_size)}`",
        parse_mode="Markdown",
    )

    last_pct = [-1]

    async def progress_cb(uploaded, total, pct):
        if pct - last_pct[0] < 5:
            return
        last_pct[0] = pct
        bar = _progress_bar(pct)
        try:
            await status_msg.edit_text(
                f"☁️ *R2 ga yuklanmoqda...*\n\n"
                f"{bar} `{pct}%`\n"
                f"`{_fmt_size(uploaded)}` / `{_fmt_size(total)}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    try:
        # S3 object key
        if r2_object_key:
            safe_key = r2_object_key
        else:
            from config import R2_USER_PREFIX
            from utils.r2_manager import user_upload_key
            uid = context.user_data.get("_user_id", 0) if context else 0
            if uid:
                safe_key = user_upload_key(uid, filename, R2_USER_PREFIX)
            else:
                safe_key = sanitize_filename(filename)
        url = await r2_upload(file_path, safe_key, progress_cb=progress_cb)

        # Eski yozuvlarni tozalash (xotira sizintisini oldini olish)
        _cleanup_r2_pending()

        # callback_data Telegram da 64 bayt bilan cheklangan.
        # user_id + fayl nomi kombinatsiyasi — foydalanuvchilar orasida izolyatsiya.
        short_key = hashlib.md5(f"{user_id}:{filename}".encode()).hexdigest()[:8]
        _r2_pending[short_key] = {
            "filename": filename,
            "url": url,
            "file_path": file_path,
            "ts": time.time(),
        }
        _persist_r2_pending()

        # Telegram ga ham yuborish tugmasi chiqar
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Telegramga yuklash", callback_data=f"r2_send_tg__{short_key}")],
            [InlineKeyboardButton("🔗 Havolani nusxalash", url=url)],
        ])

        await status_msg.edit_text(
            f"✅ *R2 ga yuklandi!*\n\n"
            f"📁 Fayl: `{_md_escape(filename)}`\n"
            f"📦 Hajmi: `{_fmt_size(file_size)}`\n\n"
            f"🔗 Havola:\n`{_md_escape(url)}`",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return url
    except Exception as e:
        # Exception matni noma'lum belgilarni o'z ichiga olishi mumkin —
        # parse_mode'siz yuborib, ikkinchi Markdown xatosining oldini olamiz.
        await status_msg.edit_text(
            f"❌ R2 ga yuklashda xato:\n{e}",
        )
        return None


async def send_file(
    message: Message,
    file_path: str,
    filename: str,
    caption: str = "",
    context=None,
    force_r2: bool = False,
    force_document: bool = False,
    force_upload_mode: str | None = None,
    target_chat_id: int | None = None,
    message_thread_id: int | None = None,
    r2_object_key: str | None = None,
    pyro_client_override=None,
):
    """
    pyro_client_override: agar berilgan bo'lsa, 50MB-2GB Pyrogram MTProto
    yuborish bosqichida bot_session o'rniga shu client ishlatiladi.

    force_upload_mode: berilsa ("video"/"document"/"audio"), foydalanuvchi
    sozlamasidan (upload_mode) qat'iy nazar shu rejim ishlatiladi. Masalan
    studio backfill har doim "video" sifatida yuborishni xohlaydi —
    tetiklovchi shaxsning shaxsiy /settings holatiga bog'liq bo'lmasligi kerak.

    Nega kerak: ARCHIVE_GROUP_ID kabi guruhlar -100... (kanal/supergroup)
    bo'lsa, ularning MTProto darajasidagi update'lari alohida channel_pts
    orqali keladi va klient faqat avval o'zi resolve qilgan kanallarga shu
    oqimga "obuna" bo'ladi. Bot session GetDialogs ishlatolmagani (botlar
    uchun taqiqlangan — BOT_METHOD_INVALID) sababli bunday guruhni hech
    qachon o'zi resolve qila olmaydi — tuxum-tovuq holati. Userbot
    (user_session) esa GetDialogs orqali bemalol resolve qila oladi, shuning
    uchun chaqiruvchi kod (save_restricted.py) bu holatlarda userbot
    clientini shu yerga uzatadi.
    """
    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(filename)[1].lower()
    is_video = ext in VIDEO_EXTENSIONS
    is_audio = ext in AUDIO_EXTENSIONS

    if force_upload_mode is not None:
        upload_mode = force_upload_mode
    else:
        upload_mode = "document"
        if context is not None and not force_document:
            from utils.user_settings import get as get_setting
            upload_mode = get_setting(context, "upload_mode")
        elif force_document:
            upload_mode = "document"

    dest_chat = target_chat_id or message.chat_id

    # Thumbnail
    meta = {}
    thumb_path = None
    custom_thumb_tmp = None

    if is_video:
        meta = await _get_video_meta(file_path)
        if context is not None:
            from utils.user_settings import ensure_loaded as _ensure, get as get_setting
            await _ensure(context.user_data.get("_user_id", 0), context)
            custom_path = get_setting(context, "custom_thumbnail")
            if custom_path and isinstance(custom_path, str) and os.path.exists(custom_path):
                thumb_path = custom_path
        if not thumb_path and meta.get("duration", 0) > 0:
            thumb_path = await _make_thumb(file_path, meta["duration"])
            custom_thumb_tmp = thumb_path

    # ─── > 2 GB → sozlamaga qarab: Telegram (Premium) / R2 / Gofile ───────
    _user_id = context.user_data.get("_user_id", 0) if context else 0

    _large_file_dest = "auto"
    if context is not None:
        from utils.user_settings import get as _get_setting
        _large_file_dest = _get_setting(context, "large_file_dest") or "auto"

    # 2-4 GB oraliqdagi fayllar uchun: "auto" (standart) yoki aniq "telegram"
    # tanlangan bo'lsa — Premium userbot mavjudligini tekshirib, agar
    # mavjud bo'lsa, AVTOMATIK shu orqali yuboramiz (R2/Gofile'ga
    # o'tkazmasdan). Premium userbot ulanmagan/Premium emas bo'lsa —
    # pastdagi R2/Gofile yo'liga muqobil ravishda o'tamiz.
    _premium_client = None
    if (
        not force_r2
        and _large_file_dest in ("auto", "telegram")
        and file_size > PYROGRAM_LIMIT
        and file_size <= PYROGRAM_PREMIUM_LIMIT
    ):
        try:
            from handlers.save_restricted import get_user_client as _get_user_client, is_user_premium as _is_user_premium
            _candidate = pyro_client_override or await _get_user_client()
            if _candidate is not None and await _is_user_premium():
                _premium_client = _candidate
        except Exception as _pe:
            logger.warning("Premium userbot tekshiruvida xato: %s", _pe)

    _go_telegram_premium = _premium_client is not None

    if (file_size > PYROGRAM_LIMIT or force_r2) and not _go_telegram_premium:
        if _large_file_dest == "gofile" and not force_r2:
            # Foydalanuvchi aniq Gofile'ni tanlagan — R2 sozlangan bo'lsa ham
            # to'g'ridan-to'g'ri Gofile'ga yuboramiz.
            status_msg = await message.reply_text(
                "🌐 *Gofile.io ga yuklanmoqda...*",
                parse_mode="Markdown",
            )
            try:
                link = await _upload_to_gofile(file_path)
                await status_msg.edit_text(
                    f"✅ *Fayl tayyor!*\n\n"
                    f"📦 Hajmi: `{_fmt_size(file_size)}`\n"
                    f"📁 Nom: `{_md_escape(filename)}`\n\n"
                    f"🔗 {_md_escape(link)}\n\n_(Link 10 kun faol)_",
                    parse_mode="Markdown",
                )
            except Exception as e:
                # Exception matni noma'lum belgilarni o'z ichiga olishi mumkin —
                # parse_mode'siz yuborib, ikkinchi Markdown xatosining oldini olamiz.
                await status_msg.edit_text(
                    f"❌ Gofile.io ga yuklashda xato:\n{e}",
                )
        elif r2_ok():
            note = ""
            if _large_file_dest == "telegram" and file_size > PYROGRAM_PREMIUM_LIMIT:
                note = "ℹ️ Fayl Premium limitidan (4GB) ham katta — R2 ga yuklanadi.\n\n"
            elif _large_file_dest == "telegram":
                note = "⚠️ Telegram tanlangan, lekin Premium userbot mavjud emas — R2 ga yuklanadi.\n\n"
            if note:
                await message.reply_text(note, parse_mode="Markdown")
            await _upload_to_r2(message, file_path, filename, file_size, user_id=_user_id, r2_object_key=r2_object_key)
        else:
            # Fallback: Gofile
            status_msg = await message.reply_text(
                "☁️ *Fayl 2 GB dan katta!*\n\n"
                "`[░░░░░░░░░░░░]` Gofile.io ga yuklanmoqda...",
                parse_mode="Markdown",
            )
            try:
                link = await _upload_to_gofile(file_path)
                await status_msg.edit_text(
                    f"✅ *Fayl tayyor!*\n\n"
                    f"📦 Hajmi: `{_fmt_size(file_size)}`\n"
                    f"📁 Nom: `{_md_escape(filename)}`\n\n"
                    f"🔗 {_md_escape(link)}\n\n_(Link 10 kun faol)_",
                    parse_mode="Markdown",
                )
            except Exception as e:
                # Exception matni noma'lum belgilarni o'z ichiga olishi mumkin —
                # parse_mode'siz yuborib, ikkinchi Markdown xatosining oldini olamiz.
                await status_msg.edit_text(
                    f"❌ Gofile.io ga yuklashda xato:\n{e}",
                )
        if custom_thumb_tmp and os.path.exists(custom_thumb_tmp):
            os.remove(custom_thumb_tmp)
        return

    # ─── <= 50 MB → PTB ──────────────────────────────────────────────────
    if file_size <= TELEGRAM_LIMIT:
        sent_msg = None
        try:
            send_kw = {}
            if message_thread_id:
                send_kw["message_thread_id"] = message_thread_id
            with open(file_path, "rb") as f:
                if upload_mode == "video" and is_video:
                    thumb_file = open(thumb_path, "rb") if thumb_path else None
                    try:
                        if dest_chat == message.chat_id:
                            sent_msg = await message.reply_video(
                                video=f, filename=filename, caption=caption,
                                duration=meta.get("duration") or None,
                                width=meta.get("width") or None,
                                height=meta.get("height") or None,
                                thumbnail=thumb_file,
                                supports_streaming=True,
                                **send_kw,
                            )
                        else:
                            sent_msg = await message.get_bot().send_video(
                                chat_id=dest_chat, video=f, filename=filename, caption=caption,
                                duration=meta.get("duration") or None,
                                width=meta.get("width") or None,
                                height=meta.get("height") or None,
                                thumbnail=thumb_file,
                                supports_streaming=True,
                                **send_kw,
                            )
                    finally:
                        if thumb_file:
                            thumb_file.close()
                elif upload_mode == "audio" and is_audio:
                    if dest_chat == message.chat_id:
                        sent_msg = await message.reply_audio(audio=f, filename=filename, caption=caption, **send_kw)
                    else:
                        sent_msg = await message.get_bot().send_audio(
                            chat_id=dest_chat, audio=f, filename=filename, caption=caption, **send_kw,
                        )
                else:
                    if dest_chat == message.chat_id:
                        sent_msg = await message.reply_document(document=f, filename=filename, caption=caption, **send_kw)
                    else:
                        sent_msg = await message.get_bot().send_document(
                            chat_id=dest_chat, document=f, filename=filename, caption=caption, **send_kw,
                        )
        finally:
            if custom_thumb_tmp and os.path.exists(custom_thumb_tmp):
                os.remove(custom_thumb_tmp)
        return sent_msg.message_id if sent_msg else None

    # ─── 50 MB – 4 GB → Pyrogram MTProto ─────────────────────────────────
    status_msg = await message.reply_text("📤 Yuborilmoqda... 0%")

    if file_size > PYROGRAM_LIMIT:
        # Bot akkaunti (yoki Premium'siz oddiy akkaunt) hech qachon 2 GB dan
        # ortig'ini yubora olmaydi — bu Telegram'ning qattiq cheklovi, kod
        # bilan aylanib o'tib bo'lmaydi. Bu yerga faqat yuqorida Premium
        # userbot mavjudligi tasdiqlangan bo'lsagina (_premium_client)
        # yetib kelamiz — shuning uchun qayta tekshirmasdan o'shani ishlatamiz.
        client = _premium_client
        if client is None:
            await status_msg.edit_text(
                "⚠️ Fayl 2 GB dan katta — Telegram orqali yuborish uchun "
                "Premium userbot kerak, lekin u ulanmagan yoki Premium emas.\n\n"
                "Sozlamalardan «2GB+ fayllar» rejimini «R2» yoki «Gofile»ga "
                "o'zgartiring.",
                parse_mode="Markdown",
            )
            if custom_thumb_tmp and os.path.exists(custom_thumb_tmp):
                os.remove(custom_thumb_tmp)
            return
    else:
        client = pyro_client_override or await get_pyrogram_client()

    # Himoya: agar chaqiruvchi kod (masalan save_restricted.py) dest_chat
    # peer'ini oldindan cache'lamagan bo'lsa, shu yerda urinib ko'ramiz —
    # muvaffaqiyatsiz bo'lsa ham keyingi send chaqiruvi aniq xato bilan
    # qulaydi (silent emas), shuning uchun bu faqat qo'shimcha himoya.
    # MUHIM: pyro_client_override berilgan bo'lsa (userbot), bu tekshiruv
    # deyarli har doim muvaffaqiyatli o'tadi — chunki userbot bu chatni
    # GetDialogs orqali allaqachon biladi.
    if dest_chat != message.chat_id:
        try:
            await client.get_chat(dest_chat)
        except Exception as _pe:
            logger.warning(
                "Pyrogram client '%s' chatini cache'lay olmadi (%s) — "
                "get_dialogs orqali peer keshini majburan yangilashga urinamiz.",
                dest_chat, _pe,
            )
            try:
                # Deploydan keyin session'ning peer keshi bo'sh bo'ladi va
                # get_chat yolg'iz kifoya qilmasligi mumkin (ayniqsa guruh hali
                # userbot "dialog" ro'yxatida bo'lmasa). get_dialogs() barcha
                # dialoglarni MTProto orqali qayta olib, ularning peer'larini
                # sessiya keshiga yozadi — shundan keyin get_chat odatda o'tadi.
                async for _ in client.get_dialogs():
                    pass
                await client.get_chat(dest_chat)
                logger.info("Peer '%s' get_dialogs orqali muvaffaqiyatli keshlandi.", dest_chat)
            except Exception as _pe2:
                logger.warning(
                    "get_dialogs bilan ham '%s' peer'ini keshlab bo'lmadi: %s — "
                    "send chaqiruvi 'Peer id invalid' bilan qulashi mumkin.",
                    dest_chat, _pe2,
                )

    last_percent = [-1]
    total_mb = file_size / 1024 / 1024

    async def progress(current, total):
        if total == 0:
            return
        percent = min(int(current / total * 100), 99)
        if percent - last_percent[0] >= 5:
            last_percent[0] = percent
            cur_mb = current / 1024 / 1024
            bar = _progress_bar(percent)
            try:
                await status_msg.edit_text(
                    f"📤 *Yuborilmoqda...*\n\n"
                    f"{bar} `{percent}%`\n"
                    f"`{cur_mb:.1f}` / `{total_mb:.1f}` MB",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    # Hech qachon abadiy osilib qolmasin: hajmga qarab, lekin kamida 5 daqiqa.
    upload_timeout = max(300, int(total_mb * 4))

    pyro_kw = {}
    if message_thread_id:
        # MUHIM: bu o'rnatilgan Pyrogram versiyasida (2.0.106) send_video/
        # send_audio/send_document message_thread_id parametrini QABUL
        # QILMAYDI (TypeError: unexpected keyword argument). Forum topic'ga
        # MTProto darajasida reply_to_message_id orqali yo'naltiriladi.
        pyro_kw["reply_to_message_id"] = message_thread_id

    async def _do_send():
        if upload_mode == "video" and is_video:
            return await asyncio.wait_for(
                client.send_video(
                    chat_id=dest_chat, video=file_path,
                    file_name=filename, caption=caption,
                    duration=meta.get("duration") or None,
                    width=meta.get("width") or None,
                    height=meta.get("height") or None,
                    thumb=thumb_path, supports_streaming=True,
                    progress=progress,
                    **pyro_kw,
                ),
                timeout=upload_timeout,
            )
        elif upload_mode == "audio" and is_audio:
            return await asyncio.wait_for(
                client.send_audio(
                    chat_id=dest_chat, audio=file_path,
                    file_name=filename, caption=caption,
                    progress=progress,
                    **pyro_kw,
                ),
                timeout=upload_timeout,
            )
        else:
            return await asyncio.wait_for(
                client.send_document(
                    chat_id=dest_chat, document=file_path,
                    file_name=filename, caption=caption,
                    progress=progress,
                    **pyro_kw,
                ),
                timeout=upload_timeout,
            )

    pyro_sent = None
    try:
        try:
            pyro_sent = await _do_send()
        except PeerIdInvalid:
            logger.warning(
                "'%s' uchun PeerIdInvalid — get_dialogs bilan qayta urinilmoqda.",
                dest_chat,
            )
            async for _ in client.get_dialogs():
                pass
            pyro_sent = await _do_send()
    except asyncio.TimeoutError:
        try:
            await status_msg.edit_text(
                "❌ *Yuborish vaqti tugadi* — ulanish juda sekin yoki uzilib qoldi.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        raise
    finally:
        if custom_thumb_tmp and os.path.exists(custom_thumb_tmp):
            os.remove(custom_thumb_tmp)

    try:
        await status_msg.delete()
    except Exception:
        pass

    return pyro_sent.id if pyro_sent else None
