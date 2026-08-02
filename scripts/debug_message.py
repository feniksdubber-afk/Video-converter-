"""
Diagnostika skripti: bitta xabarning TO'LIQ (xom) ma'lumotini chiqarib beradi.

Ishlatish (loyiha papkasida, .env fayl mavjud joyda):
    python3 scripts/debug_message.py <chat_id_yoki_username> <message_id>

Misol:
    python3 scripts/debug_message.py Minxotv_Arxiv 9260

Bu skript hech narsani o'zgartirmaydi/yubormaydi — faqat konsolga
xabar haqida bor ma'lumotni chop etadi, shu orqali nega .media bo'sh
chiqayotganini aniqlaymiz.
"""
import asyncio
import sys

from pyrogram import Client
from config import API_ID, API_HASH, SESSION_STRING


async def main():
    if len(sys.argv) < 3:
        print("Foydalanish: python3 scripts/debug_message.py <chat> <message_id>")
        return

    chat = sys.argv[1]
    if chat.lstrip("-").isdigit():
        chat = int(chat)
    msg_id = int(sys.argv[2])

    if not SESSION_STRING:
        print("❌ SESSION_STRING topilmadi (.env tekshiring).")
        return

    app = Client("debug_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
    async with app:
        m = await app.get_messages(chat, msg_id)
        print("=" * 60)
        print(f"message.id        = {m.id}")
        print(f"message.empty     = {m.empty}")
        print(f"message.media     = {m.media}")
        print(f"message.text      = {m.text!r}")
        print(f"message.caption   = {m.caption!r}")
        print(f"message.video     = {m.video}")
        print(f"message.document  = {m.document}")
        print(f"message.photo     = {m.photo}")
        print(f"message.web_page  = {m.web_page}")
        print(f"message.animation = {getattr(m, 'animation', None)}")
        print(f"message.reply_markup = {m.reply_markup}")
        print(f"message.service   = {getattr(m, 'service', None)}")
        print(f"message.views     = {getattr(m, 'views', None)}")
        print("=" * 60)
        print("TO'LIQ (raw) obyekt:")
        print(m)


if __name__ == "__main__":
    asyncio.run(main())
