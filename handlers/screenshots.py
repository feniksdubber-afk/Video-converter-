import os
import re
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes
from utils.keyboards import screenshots_count_keyboard, main_menu_keyboard, cancel_keyboard
from utils.ffmpeg_utils import take_screenshots_async, take_manual_shot_async


async def show_screenshots_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "screenshots"
    await query.edit_message_text(
        "📸 *Avtomatik Skrinshotlar*\n\n"
        "Nechta skrinsot olishni xohlaysiz?\n"
        "(Video bo'ylab teng oraliqda olinadi)",
        reply_markup=screenshots_count_keyboard(),
        parse_mode="Markdown",
    )


async def handle_screenshots_count(update: Update, context: ContextTypes.DEFAULT_TYPE, count: int):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await query.edit_message_text("❌ Video topilmadi. Iltimos qaytadan video yuboring.")
        return

    context.user_data["state"] = None
    await query.edit_message_text(
        f"⏳ *{count} ta skrinsot olinmoqda...*\n\nKuting...",
        parse_mode="Markdown",
    )

    ok, paths, err = await take_screenshots_async(video_path, count, status_msg=query.message)

    if ok and paths:
        await query.message.reply_text(f"✅ {len(paths)} ta skrinsot olindi! Yuborilmoqda...")

        batch_size = 10
        for i in range(0, len(paths), batch_size):
            batch = paths[i:i + batch_size]
            media = []
            for j, p in enumerate(batch):
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        caption = f"📸 Skrinsot {i + j + 1}/{len(paths)}" if j == 0 else None
                        media.append(InputMediaPhoto(media=f.read(), caption=caption))

            if media:
                await query.message.reply_media_group(media=media)

        for p in paths:
            if os.path.exists(p):
                os.remove(p)

        await query.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await query.message.reply_text(
            f"❌ Skrinsot olishda xato:\n`{err}`",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )


async def show_manual_shot_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "manual_shot"
    await query.edit_message_text(
        "🖼 *Qo'lda Skrinsot*\n\n"
        "Skrinsot olmoqchi bo'lgan vaqtni kiriting:\n\n"
        "Format: `HH:MM:SS` yoki `MM:SS` yoki soniyalar\n"
        "Misol: `00:02:15` yoki `2:15` yoki `135`",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_manual_shot_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    def parse_time(t):
        if re.match(r"^\d{1,2}:\d{2}:\d{2}$", t):
            return t
        if re.match(r"^\d{1,2}:\d{2}$", t):
            return "00:" + t
        if re.match(r"^\d+(\.\d+)?$", t):
            secs = float(t)
            h = int(secs // 3600)
            m = int((secs % 3600) // 60)
            s = secs % 60
            return f"{h:02d}:{m:02d}:{s:06.3f}"
        return None

    parsed = parse_time(text)
    if not parsed:
        await update.message.reply_text(
            "❌ Noto'g'ri format. Qaytadan kiriting:\n"
            "Misol: `00:02:15` yoki `2:15` yoki `135`",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    video_path = context.user_data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        await update.message.reply_text("❌ Video topilmadi. Qaytadan video yuboring.")
        context.user_data["state"] = None
        return

    context.user_data["state"] = None
    status = await update.message.reply_text(
        f"⏳ `{parsed}` vaqtida skrinsot olinmoqda...",
        parse_mode="Markdown",
    )

    ok, output_path, err = await take_manual_shot_async(video_path, parsed)

    if ok and os.path.exists(output_path):
        with open(output_path, "rb") as f:
            await update.message.reply_photo(
                photo=f,
                caption=f"📸 `{parsed}` vaqtidagi kadr",
                parse_mode="Markdown",
            )
        os.remove(output_path)
        await status.delete()
        await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
    else:
        await status.edit_text(
            f"❌ Skrinsot olishda xato:\n`{err}`",
            parse_mode="Markdown",
        )
        await update.message.reply_text("Qaytadan?", reply_markup=main_menu_keyboard())
