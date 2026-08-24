"""
kino_sender.py — Istalgan botdan xabarni mirror qilish + video yuborish.

Foydalanish:
  /kino @Kinofilmnewbot 923        — bot javobini mirror qiladi
  /kino @Kinofilmnewbot 923 2      — to'g'ridan 2-tugmani bosadi
  /kino @FilmBot 12345             — istalgan bot ishlaydi
"""

import asyncio
import logging
import os
import time
import uuid

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ARCHIVE_GROUP_ID, TEMP_DIR
from handlers.save_restricted import get_user_client

logger = logging.getLogger(__name__)

WAIT_TIMEOUT = 45          # botdan javob kutish (soniya)
COLLECT_DELAY = 3.0        # barcha xabarlar kelishini kutish (soniya)

_kino_lock = asyncio.Lock()


# ── Progress / format yordamchilari ──────────────────────────────────────────

def _progress_bar(percent: int, length: int = 14) -> str:
    filled = int(length * percent / 100)
    return "▰" * filled + "▱" * (length - filled)


def _fmt_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


def _fmt_dur(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _md_escape(text) -> str:
    """Telegram Markdown (legacy) uchun maxsus belgilarni escape qiladi.

    Foydalanuvchi/bot tomonidan keladigan har qanday matnni (xato matni,
    bot_username, tugma matni, fayl nomi, sarlavha va h.k.) parse_mode='Markdown'
    bo'lgan xabar ichiga qo'yishdan oldin shu funksiyadan o'tkazish kerak,
    aks holda `_`, `*`, `` ` ``, `[` kabi belgilar parse xatosiga olib kelishi mumkin.
    """
    s = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


# ── Asosiy handler ────────────────────────────────────────────────────────────

async def kino_sender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    args = context.args

    if not args or len(args) < 2:
        await msg.reply_text(
            "❗ *Foydalanish:*\n"
            "`/kino @BotNomi payload` — mirror qiladi\n"
            "`/kino @BotNomi payload 2` — 2-tugmani bosadi",
            parse_mode="Markdown",
        )
        return

    bot_username = args[0].lstrip("@")
    payload = args[1].strip()
    button_index = None
    if len(args) >= 3 and args[2].isdigit():
        button_index = int(args[2]) - 1  # 0-indexed

    if not ARCHIVE_GROUP_ID:
        await msg.reply_text("❌ `ARCHIVE_GROUP_ID` `.env`da sozlanmagan!", parse_mode="Markdown")
        return

    if _kino_lock.locked():
        await msg.reply_text("⏳ Oldingi so'rov bajarilmoqda, kuting...")
        return

    status_msg = await msg.reply_text(
        f"🔍 *@{_md_escape(bot_username)}* ga so'rov yuborilmoqda...\n`{_md_escape(payload)}`",
        parse_mode="Markdown",
    )

    client = await get_user_client()
    if not client:
        await status_msg.edit_text("❌ Userbot (`SESSION_STRING`) ulangmagan!", parse_mode="Markdown")
        return

    async with _kino_lock:
        try:
            await _run_flow(
                client=client,
                bot_username=bot_username,
                payload=payload,
                button_index=button_index,
                dest_chat=ARCHIVE_GROUP_ID,
                status_msg=status_msg,
                context=context,
                user_msg=msg,
            )
        except Exception as e:
            logger.exception("kino_sender xato: %s", e)
            # Exception matni oldindan bilinmaydi va Markdown belgilarini o'z ichiga
            # olishi mumkin — parse_mode=None bilan yuborib, bot o'zi yiqilib
            # qolishining oldini olamiz.
            await status_msg.edit_text(f"❌ Xato:\n{e}")


# ── Asosiy flow ───────────────────────────────────────────────────────────────

async def _run_flow(client, bot_username, payload, button_index,
                    dest_chat, status_msg, context, user_msg):
    from pyrogram.raw import functions

    # 1. Bot peer ni resolve qilamiz
    try:
        bot_peer = await client.resolve_peer(bot_username)
    except Exception as e:
        await status_msg.edit_text(f"❌ @{bot_username} topilmadi!\n{e}")
        return

    # 2. Oxirgi xabar ID ni eslab qolamiz
    last_msg_id = 0
    try:
        async for h in client.get_chat_history(bot_username, limit=1):
            last_msg_id = h.id
            break
    except Exception:
        pass

    # 3. /start <payload> yuboramiz
    await status_msg.edit_text(
        f"📨 *@{_md_escape(bot_username)}* ga `/start {_md_escape(payload)}` yuborildi\n"
        f"⏳ Javob kutilmoqda...",
        parse_mode="Markdown",
    )

    # MUHIM: event handler StartBot yuborishdan OLDIN o'rnatilishi kerak.
    # Aks holda bot juda tez javob bersa (StartBot -> javob -> handler hali yo'q),
    # birinchi xabar(lar) o'tkazib yuborilib, keraksiz timeout yuzaga kelishi mumkin.
    async def _send_start():
        await client.invoke(
            functions.messages.StartBot(
                bot=bot_peer,
                peer=bot_peer,
                random_id=client.rnd_id(),
                start_param=payload,
            )
        )

    # 4. Handlerni oldindan o'rnatib, keyin StartBot yuborib, javoblarni yig'amiz
    new_messages = await _collect_new_messages(
        client=client,
        from_username=bot_username,
        after_id=last_msg_id,
        timeout=WAIT_TIMEOUT,
        collect_delay=COLLECT_DELAY,
        status_msg=status_msg,
        trigger_fn=_send_start,
    )

    if not new_messages:
        await status_msg.edit_text(
            f"❌ *@{_md_escape(bot_username)}* javob bermadi (timeout {WAIT_TIMEOUT}s)\n\n"
            f"Qaytadan urinib ko'ring.",
            parse_mode="Markdown",
        )
        return

    await status_msg.edit_text(
        f"✅ *@{_md_escape(bot_username)}* dan {len(new_messages)} ta xabar keldi\n"
        f"⏳ Qayta ishlanmoqda...",
        parse_mode="Markdown",
    )

    # 5. Agar button_index berilgan bo'lsa — to'g'ridan o'sha tugmani bosamiz
    if button_index is not None:
        btn_msg = next((m for m in new_messages if _flatten_buttons(m)), None)
        if not btn_msg:
            await status_msg.edit_text("❌ Tugmali xabar topilmadi!")
            return

        buttons = _flatten_buttons(btn_msg)
        if button_index >= len(buttons):
            btn_list = "\n".join(f"  {i+1}. {b.text}" for i, b in enumerate(buttons))
            await status_msg.edit_text(
                f"❌ {button_index+1}-tugma mavjud emas\n\n"
                f"📋 Tugmalar ({len(buttons)} ta):\n{btn_list}\n\n"
                f"Masalan: /kino @{bot_username} {payload} 1"
            )
            return

        chosen = buttons[button_index]
        await status_msg.edit_text(
            f"🔘 `{_md_escape(chosen.text)}` tanlandi\n⏳ Video kutilmoqda...",
            parse_mode="Markdown",
        )
        await _press_button_and_download(
            client, bot_username, bot_peer, btn_msg, chosen,
            dest_chat, status_msg, context
        )
        return

    # 6. button_index berilmagan — barcha xabarlarni mirror qilamiz
    await status_msg.delete()

    for pyro_msg in new_messages:
        await _mirror_message(
            client=client,
            pyro_msg=pyro_msg,
            bot_username=bot_username,
            bot_peer=bot_peer,
            dest_chat=dest_chat,
            user_msg=user_msg,
            context=context,
        )


# ── Xabarni mirror qilish ─────────────────────────────────────────────────────

async def _mirror_message(client, pyro_msg, bot_username, bot_peer,
                           dest_chat, user_msg, context):
    """Pyrogram xabarni PTB orqali foydalanuvchiga mirror qiladi."""

    text = pyro_msg.text or pyro_msg.caption or ""
    buttons = _flatten_buttons(pyro_msg)

    # Inline keyboard yasaymiz (agar bor bo'lsa)
    tg_keyboard = None
    if buttons:
        kb_rows = []
        # Asl row strukturasini saqlashga harakat qilamiz
        if hasattr(pyro_msg.reply_markup, "inline_keyboard"):
            for row in pyro_msg.reply_markup.inline_keyboard:
                kb_row = []
                for btn in row:
                    if hasattr(btn, "callback_data") and btn.callback_data is not None:
                        cb = btn.callback_data
                        if isinstance(cb, bytes):
                            cb = cb.decode("utf-8", errors="replace")
                        # callback_data ni encode qilib saqlaymiz
                        # format: "kino|bot_username|msg_id|cb_data"
                        safe_cb = f"kino|{bot_username}|{pyro_msg.id}|{cb}"
                        # Telegram callback_data limiti BYTE bo'yicha (UTF-8), character emas —
                        # emoji/kirill kabi belgilar bir nechta baytga teng bo'lishi mumkin.
                        if len(safe_cb.encode("utf-8")) <= 64:
                            kb_row.append(InlineKeyboardButton(btn.text, callback_data=safe_cb))
                        else:
                            # Juda uzun bo'lsa index ishlatamiz
                            idx = buttons.index(btn) if btn in buttons else 0
                            kb_row.append(InlineKeyboardButton(
                                btn.text,
                                callback_data=f"kinoi|{bot_username}|{pyro_msg.id}|{idx}"
                            ))
                if kb_row:
                    kb_rows.append(kb_row)
        tg_keyboard = InlineKeyboardMarkup(kb_rows) if kb_rows else None

    # Media bor bo'lsa — yuklab olish tugmasini qo'shamiz
    has_media = bool(pyro_msg.video or pyro_msg.document or pyro_msg.audio)
    if has_media:
        dl_cb = f"kinodl|{bot_username}|{pyro_msg.id}"
        if pyro_msg.video:
            dl_label = "📥 Videoni yuborish"
        elif pyro_msg.audio:
            dl_label = "📥 Audioni yuborish"
        else:
            dl_label = "📥 Faylni yuborish"
        dl_btn = [InlineKeyboardButton(dl_label, callback_data=dl_cb)]
        if tg_keyboard:
            # inline_keyboard Pyrogram/PTB obyektlarida tuple bo'lishi mumkin —
            # list() bilan tuple+list TypeError'idan qochamiz
            tg_keyboard = InlineKeyboardMarkup(list(tg_keyboard.inline_keyboard) + [dl_btn])
        else:
            tg_keyboard = InlineKeyboardMarkup([dl_btn])

    # Xabarni yuboramiz
    if has_media:
        # Avval matnni (caption) yuboramiz
        media_obj = pyro_msg.video or pyro_msg.document or pyro_msg.audio
        file_size = getattr(media_obj, "file_size", 0) or 0
        size_str = _fmt_size(file_size) if file_size else "?"
        fname = getattr(media_obj, "file_name", "") or ""
        mime = getattr(media_obj, "mime_type", "") or ""

        info_text = (
            f"{text}\n\n" if text else ""
        ) + (
            f"📎 `{_md_escape(fname)}`\n" if fname else ""
        ) + (
            f"📦 Hajmi: `{size_str}`\n"
            f"🎞 MIME: `{_md_escape(mime)}`"
        )

        try:
            await user_msg.reply_text(
                info_text.strip(),
                parse_mode="Markdown",
                reply_markup=tg_keyboard,
            )
        except Exception:
            # Markdown parse xatosi bo'lsa ham xabar borsin
            await user_msg.reply_text(info_text.strip(), reply_markup=tg_keyboard)
    else:
        # Matnli yoki boshqa xabar
        if text:
            await user_msg.reply_text(
                text,
                parse_mode=None,  # asl formatni buzmaslik uchun
                reply_markup=tg_keyboard,
            )
        else:
            await user_msg.reply_text(
                "📨 (Bo'sh xabar)",
                reply_markup=tg_keyboard,
            )


# ── Callback handler (tugma bosilganda) ──────────────────────────────────────

async def kino_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    # kinodl — video yuklab guruhga yuborish
    if data.startswith("kinodl|"):
        parts = data.split("|", 3)
        if len(parts) < 3:
            return
        _, bot_username, msg_id_str = parts
        msg_id = int(msg_id_str)

        if _kino_lock.locked():
            await query.message.reply_text("⏳ Oldingi so'rov bajarilmoqda, birozdan so'ng qayta urinib ko'ring.")
            return

        status = await query.message.reply_text(
            f"⏳ *@{_md_escape(bot_username)}* dan video yuklab olinmoqda...",
            parse_mode="Markdown",
        )
        client = await get_user_client()
        if not client:
            await status.edit_text("❌ Userbot ulangmagan!")
            return

        # Userbot bitta Telegram sessiyasi orqali ishlaydi — /kino komandasi bilan
        # bir vaqtda ishlab ketmasligi uchun xuddi shu lock ishlatiladi.
        async with _kino_lock:
            try:
                # Xabarni Pyrogram orqali olamiz
                pyro_msg = await client.get_messages(bot_username, msg_id)
                await _download_and_send(
                    client=client,
                    media_msg=pyro_msg,
                    dest_chat=ARCHIVE_GROUP_ID,
                    title=bot_username,
                    status_msg=status,
                )
            except Exception as e:
                logger.exception("kinodl xato: %s", e)
                await status.edit_text(f"❌ Xato:\n{e}")
        return

    # kino| — inline tugmani userbot orqali bosish
    if data.startswith("kino|") or data.startswith("kinoi|"):
        is_indexed = data.startswith("kinoi|")
        parts = data.split("|", 4)

        if is_indexed:
            # kinoi|bot_username|msg_id|index
            if len(parts) < 4:
                return
            _, bot_username, msg_id_str, idx_str = parts
            msg_id = int(msg_id_str)
            btn_index = int(idx_str)
            cb_data_str = None
        else:
            # kino|bot_username|msg_id|cb_data
            if len(parts) < 4:
                return
            _, bot_username, msg_id_str, cb_data_str = parts
            msg_id = int(msg_id_str)
            btn_index = None

        if _kino_lock.locked():
            await query.message.reply_text("⏳ Oldingi so'rov bajarilmoqda, birozdan so'ng qayta urinib ko'ring.")
            return

        status = await query.message.reply_text("⏳ Tugma bosilmoqda...")
        client = await get_user_client()
        if not client:
            await status.edit_text("❌ Userbot ulangmagan!")
            return

        # Userbot bitta Telegram sessiyasi orqali ishlaydi — /kino komandasi bilan
        # bir vaqtda ishlab ketmasligi uchun xuddi shu lock ishlatiladi.
        async with _kino_lock:
            try:
                from pyrogram.raw import functions

                bot_peer = await client.resolve_peer(bot_username)
                pyro_msg = await client.get_messages(bot_username, msg_id)

                if cb_data_str is None:
                    # index orqali topamiz
                    buttons = _flatten_buttons(pyro_msg)
                    if btn_index >= len(buttons):
                        await status.edit_text("❌ Tugma topilmadi!")
                        return
                    chosen = buttons[btn_index]
                else:
                    # to'g'ridan callback_data
                    class _FakeBtn:
                        def __init__(self, text, cb):
                            self.text = text
                            self.callback_data = cb
                    chosen = _FakeBtn(cb_data_str, cb_data_str)

                await status.edit_text(
                    f"🔘 `{_md_escape(chosen.text)}` tanlandi\n⏳ Javob kutilmoqda...",
                    parse_mode="Markdown",
                )

                await _press_button_and_download(
                    client, bot_username, bot_peer, pyro_msg, chosen,
                    ARCHIVE_GROUP_ID, status, context
                )
            except Exception as e:
                logger.exception("kino callback xato: %s", e)
                await status.edit_text(f"❌ Xato:\n{e}")


# ── Tugmani bosib video kutish ────────────────────────────────────────────────

async def _press_button_and_download(client, bot_username, bot_peer,
                                      btn_msg, chosen_btn,
                                      dest_chat, status_msg, context):
    from pyrogram.raw import functions

    cb_data = chosen_btn.callback_data
    if isinstance(cb_data, str):
        cb_data = cb_data.encode("utf-8")

    # Oxirgi xabar ID
    last_id = btn_msg.id

    async def _press():
        try:
            await asyncio.wait_for(
                client.invoke(
                    functions.messages.GetBotCallbackAnswer(
                        peer=bot_peer,
                        msg_id=btn_msg.id,
                        data=cb_data,
                    )
                ),
                timeout=10,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.info("GetBotCallbackAnswer xato (normal): %s", e)

    # Media xabarini event handler orqali kutamiz
    await status_msg.edit_text(
        f"🔘 `{_md_escape(chosen_btn.text)}` tanlandi\n⏳ Video yuborilishini kutilmoqda...",
        parse_mode="Markdown",
    )

    # Listener tugma bosishdan OLDIN o'rnatiladi (race condition oldini olish)
    media_msg = await _wait_for_media_event(
        client=client,
        from_username=bot_username,
        after_id=last_id,
        timeout=WAIT_TIMEOUT,
        trigger_fn=_press,
    )

    if media_msg is None:
        await status_msg.edit_text(
            f"❌ Bot video yuboramadi (timeout {WAIT_TIMEOUT}s)\n\n"
            f"Qaytadan urinib ko'ring.",
            parse_mode="Markdown",
        )
        return

    await _download_and_send(
        client=client,
        media_msg=media_msg,
        dest_chat=dest_chat,
        title=chosen_btn.text or bot_username,
        status_msg=status_msg,
    )


# ── Yuklab olish va yuborish ──────────────────────────────────────────────────

async def _download_and_send(client, media_msg, dest_chat, title, status_msg):
    media_obj = media_msg.video or media_msg.document or media_msg.audio
    file_size = getattr(media_obj, "file_size", 0) or 0
    size_str = _fmt_size(file_size) if file_size else "?"
    # title bot/tugma matnidan keladi — Markdown ichida ishlatilishidan oldin escape qilamiz
    title = _md_escape(title)

    await status_msg.edit_text(
        f"🎬 *{title}*\n\n"
        f"⬇️ Yuklab olinmoqda...\n"
        f"`{'▱' * 14}` `0%`\n"
        f"`0.0 MB` / `{size_str}`",
        parse_mode="Markdown",
    )

    ext = _guess_ext(media_msg)
    tmp_path = os.path.join(TEMP_DIR, f"kino_{media_msg.id}_{uuid.uuid4().hex[:6]}.{ext}")
    thumb_path = None

    try:
        last_edit = [0.0]
        last_pct = [-1]

        async def _dl_progress(current, total):
            now = time.monotonic()
            if now - last_edit[0] < 2.5 or not total:
                return
            pct = min(int(current / total * 100), 99)
            if pct == last_pct[0]:
                return
            last_pct[0] = pct
            last_edit[0] = now
            bar = _progress_bar(pct)
            cur_mb = current / 1024 / 1024
            tot_mb = total / 1024 / 1024
            try:
                await status_msg.edit_text(
                    f"🎬 *{title}*\n\n"
                    f"⬇️ *Yuklab olinmoqda...*\n"
                    f"`{bar}` `{pct}%`\n"
                    f"`{cur_mb:.1f} MB` / `{tot_mb:.1f} MB`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        await client.download_media(media_msg, file_name=tmp_path, progress=_dl_progress)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            await status_msg.edit_text("❌ Fayl yuklanmadi (bo'sh yoki yo'q)!")
            return

        actual_size = os.path.getsize(tmp_path)
        size_str = _fmt_size(actual_size)

        # Metadata
        await status_msg.edit_text(
            f"🎬 *{title}*\n\n"
            f"✅ Yuklab olindi: `{size_str}`\n"
            f"🔄 Metadata tayyorlanmoqda...",
            parse_mode="Markdown",
        )

        meta = {}
        is_video_file = ext in {"mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "ts", "wmv"}

        if is_video_file:
            meta = await _get_video_meta(tmp_path)
            if meta.get("duration", 0) > 0:
                thumb_path = await _make_thumb(tmp_path, meta["duration"])

        # Yuborish
        await status_msg.edit_text(
            f"🎬 *{title}*\n\n"
            f"📤 *Guruhga yuborilmoqda...*\n"
            f"`{'▱' * 14}` `0%`\n"
            f"`0.0 MB` / `{size_str}`",
            parse_mode="Markdown",
        )

        last_edit[0] = 0.0
        last_pct[0] = -1

        async def _ul_progress(current, total):
            now = time.monotonic()
            if now - last_edit[0] < 2.5 or not total:
                return
            pct = min(int(current / total * 100), 99)
            if pct == last_pct[0]:
                return
            last_pct[0] = pct
            last_edit[0] = now
            bar = _progress_bar(pct)
            try:
                await status_msg.edit_text(
                    f"🎬 *{title}*\n\n"
                    f"📤 *Guruhga yuborilmoqda...*\n"
                    f"`{bar}` `{pct}%`\n"
                    f"`{current/1024/1024:.1f} MB` / `{total/1024/1024:.1f} MB`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        caption = (media_msg.caption or "").strip() or f"🎬 {title}"

        # dest_chat ni Pyrogram peer cache ga kiritamiz
        # (-100xxxx supergroup ID lar resolve_peer da "Peer id invalid" berishi mumkin)
        try:
            await client.get_chat(dest_chat)
        except Exception as e:
            logger.warning("dest_chat get_chat xato (normal): %s", e)

        try:
            if is_video_file:
                await client.send_video(
                    chat_id=dest_chat,
                    video=tmp_path,
                    caption=caption,
                    supports_streaming=True,
                    duration=meta.get("duration") or None,
                    width=meta.get("width") or None,
                    height=meta.get("height") or None,
                    thumb=thumb_path or None,
                    progress=_ul_progress,
                )
            else:
                await client.send_document(
                    chat_id=dest_chat,
                    document=tmp_path,
                    caption=caption,
                    thumb=thumb_path or None,
                    progress=_ul_progress,
                )
        finally:
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass

        # Yakuniy
        dur_str = f"\n⏱ Davomiyligi: `{_fmt_dur(meta['duration'])}`" if meta.get("duration") else ""
        res_str = f"\n📐 O'lchami: `{meta['width']}×{meta['height']}`" if meta.get("width") and meta.get("height") else ""

        await status_msg.edit_text(
            f"✅ *{title}*\n\n"
            f"📦 Hajmi: `{size_str}`"
            f"{dur_str}"
            f"{res_str}\n\n"
            f"🗂 Guruhga muvaffaqiyatli yuborildi!",
            parse_mode="Markdown",
        )

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ── Event handler orqali xabar yig'ish ───────────────────────────────────────

async def _collect_new_messages(client, from_username, after_id,
                                  timeout, collect_delay, status_msg,
                                  trigger_fn=None):
    """
    Botdan kelgan barcha yangi xabarlarni yig'adi.
    Birinchi xabar kelgandan keyin collect_delay soniya kutib, qolganlarni ham oladi.

    trigger_fn: agar berilsa, event handler ALLAQACHON o'rnatilgandan keyin
    chaqiriladigan async funksiya (masalan StartBot yuborish). Bu race condition'ni
    oldini oladi — chunki botning javobi handler yo'qligi sababli o'tkazib
    yuborilishi mumkin emas.
    """
    from pyrogram import filters
    from pyrogram.handlers import MessageHandler

    collected = []
    first_arrived = asyncio.Event()

    async def _on_message(c, m):
        if m.chat.username and m.chat.username.lower() == from_username.lower():
            if m.id > after_id:
                collected.append(m)
                first_arrived.set()

    handler = client.add_handler(MessageHandler(_on_message, filters.private))

    try:
        # Handler o'rnatilgach, so'rovni (masalan StartBot) yuboramiz
        if trigger_fn is not None:
            await trigger_fn()

        # Birinchi xabar kelishini kutamiz
        dots = 0
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                await asyncio.wait_for(first_arrived.wait(), timeout=1.5)
                break
            except asyncio.TimeoutError:
                dots = (dots % 3) + 1
                waited = int(timeout - (deadline - asyncio.get_event_loop().time()))
                try:
                    await status_msg.edit_text(
                        f"⏳ Javob kutilmoqda{'.' * dots}\n"
                        f"`{waited}s` / `{timeout}s`",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

        if not collected:
            return []

        # Qolgan xabarlar kelishini kutamiz
        await status_msg.edit_text(
            f"📨 Xabar keldi, to'liq yuklanmoqda...",
            parse_mode="Markdown",
        )
        await asyncio.sleep(collect_delay)

        # Yana polling bilan tekshiramiz (event handler o'tkazib yuborgan bo'lishi mumkin)
        try:
            seen_ids = {m.id for m in collected}
            async for m in client.get_chat_history(from_username, limit=10):
                if m.id <= after_id:
                    break
                if m.id not in seen_ids:
                    collected.append(m)
                    seen_ids.add(m.id)
        except Exception as e:
            logger.warning("Polling fallback xato: %s", e)

        # ID bo'yicha tartiblash (eski → yangi)
        collected.sort(key=lambda m: m.id)
        return collected

    finally:
        try:
            client.remove_handler(*handler)
        except Exception:
            pass


async def _wait_for_media_event(client, from_username, after_id, timeout, trigger_fn=None):
    """Media xabarini kutadi. trigger_fn berilsa, handler o'rnatilgandan KEYIN
    chaqiriladi (masalan tugmani bosish so'rovi) — race condition oldini olish uchun."""
    from pyrogram import filters
    from pyrogram.handlers import MessageHandler

    result = []
    arrived = asyncio.Event()

    async def _on_media(c, m):
        if m.chat.username and m.chat.username.lower() == from_username.lower():
            if m.id > after_id and (m.video or m.document or m.audio):
                result.append(m)
                arrived.set()

    handler = client.add_handler(MessageHandler(_on_media, filters.private))

    try:
        if trigger_fn is not None:
            await trigger_fn()

        try:
            await asyncio.wait_for(arrived.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

        if result:
            return result[0]

        # Fallback: polling
        try:
            async for m in client.get_chat_history(from_username, limit=5):
                if m.id <= after_id:
                    break
                if m.video or m.document or m.audio:
                    return m
        except Exception as e:
            logger.warning("Media polling fallback xato: %s", e)

        return None
    finally:
        try:
            client.remove_handler(*handler)
        except Exception:
            pass


# ── Video metadata ────────────────────────────────────────────────────────────

import subprocess


def _get_video_meta_sync(file_path: str) -> dict:
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
        for line in r.stdout.splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                val = val.strip()
                if key == "duration":
                    try:
                        meta["duration"] = int(float(val))
                    except ValueError:
                        pass
                elif key == "width":
                    try:
                        meta["width"] = int(val)
                    except ValueError:
                        pass
                elif key == "height":
                    try:
                        meta["height"] = int(val)
                    except ValueError:
                        pass
    except Exception as e:
        logger.warning("ffprobe xato: %s", e)
    return meta


async def _get_video_meta(file_path: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_video_meta_sync, file_path)


def _make_thumb_sync(file_path: str, duration: int) -> str | None:
    try:
        thumb_path = os.path.join(TEMP_DIR, f"kthumb_{uuid.uuid4().hex}.jpg")
        seek = max(1, duration // 4) if duration > 4 else 1
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", file_path,
             "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "5", thumb_path],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        logger.warning("Thumbnail xato: %s", e)
    return None


async def _make_thumb(file_path: str, duration: int) -> str | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _make_thumb_sync, file_path, duration)


# ── Yordamchi ─────────────────────────────────────────────────────────────────

def _guess_ext(msg) -> str:
    if msg.video:
        mime = getattr(msg.video, "mime_type", "") or ""
        fname = getattr(msg.video, "file_name", "") or ""
        if "." in fname:
            return fname.rsplit(".", 1)[-1].lower()
        if "mkv" in mime or "matroska" in mime:
            return "mkv"
        return "mp4"
    if msg.document:
        mime = getattr(msg.document, "mime_type", "") or ""
        fname = getattr(msg.document, "file_name", "") or ""
        if "." in fname:
            return fname.rsplit(".", 1)[-1].lower()
        if "mkv" in mime:
            return "mkv"
        if "mp4" in mime:
            return "mp4"
        return "bin"
    return "mp4"


def _flatten_buttons(msg) -> list:
    buttons = []
    if not msg.reply_markup:
        return buttons
    if hasattr(msg.reply_markup, "inline_keyboard"):
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                if hasattr(btn, "callback_data") and btn.callback_data is not None:
                    buttons.append(btn)
    return buttons
