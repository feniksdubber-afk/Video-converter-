"""Kino posti: sarlavha (caption) yasash va video bilan yuborish.

Ko'rinish: matn videodan tepada, bitta xabarda, "YouTube" kartochkasiz.

Ikki yuborish yo'li:
  - send_post()      -> python-telegram-bot (50 MB gacha yoki file_id bilan)
  - send_post_pyro() -> Pyrogram/pyrofork userbot (katta fayllar, 2 GB gacha)
"""
from __future__ import annotations

import html
import re
from typing import Any, Mapping

CAPTION_LIMIT = 1024          # Telegram: video sarlavhasi (ko'rinadigan belgilar)
FOOTER = "🎬 @SaraFilmHD | Filmlar olami 🍿"
DIVIDER = "▬ ▬ ▬ ▬ ▬ ▬ ▬"

_TAG_RE = re.compile(r"<[^>]+>")


def _visible_len(text: str) -> int:
    """HTML teglarsiz, entity'lar (&amp;) 1 belgi sifatida."""
    return len(html.unescape(_TAG_RE.sub("", text)))


def _hashtags(genres: list[str]) -> str:
    tags = []
    for g in genres:
        clean = re.sub(r"[^\w]", "", g, flags=re.UNICODE)
        if clean:
            tags.append(f"#{clean}")
    return " ".join(tags)


def build_caption(movie: Mapping[str, Any], footer: str = FOOTER) -> str:
    """movie maydonlari (hammasi ixtiyoriy, bo'lmasa qator tushib qoladi):
    title, country, language, genres(list), quality, year, code, kind,
    views, rating, votes
    """
    def e(v: str) -> str:
        return html.escape(v, quote=False)   # faqat & < > (apostrof o'zgarmaydi)

    lines: list[str] = []

    raw_title = str(movie.get("title", "")).strip()
    if len(raw_title) > 200:                 # juda uzun sarlavha butun postni buzmasin
        raw_title = raw_title[:199].rstrip() + "…"
    title = e(raw_title)
    lines += [f"📺 <b>{title}</b>", DIVIDER, ""]

    def add(icon: str, label: str, key: str) -> None:
        val = movie.get(key)
        if val not in (None, "", []):
            lines.append(f"{icon} {label}: {e(str(val))}")

    add("🌐", "Davlat", "country")
    add("🚩", "Til", "language")
    genres = movie.get("genres") or []
    if genres:
        lines.append(f"🎭 Janr: {_hashtags(list(genres))}")
    add("💿", "Sifat", "quality")
    add("📆", "Yil", "year")

    # ixtiyoriy blok: cheklovga sig'masa shu qismlar birinchi bo'lib tushiriladi
    optional: list[list[str]] = []
    if movie.get("code") not in (None, ""):
        kind = f" ({e(str(movie['kind']))})" if movie.get("kind") else ""
        optional.append([f"🔢 Kod: {e(str(movie['code']))}{kind}"])
    if movie.get("views") not in (None, ""):
        optional.append([f"👁 Ko'rishlar: {e(str(movie['views']))}"])
    if movie.get("rating") not in (None, ""):
        votes = f" ({e(str(movie['votes']))})" if movie.get("votes") else ""
        optional.append([f"📱 Reyting: {e(str(movie['rating']))}/10{votes}"])

    def assemble(opt: list[list[str]]) -> str:
        parts = list(lines)
        if opt:
            parts.append("")
            for block in opt:
                parts += block
        parts += ["", footer]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(parts))

    text = assemble(optional)
    # sig'masa: reyting -> ko'rishlar -> kod tartibida tushiramiz
    while _visible_len(text) > CAPTION_LIMIT and optional:
        optional.pop()
        text = assemble(optional)
    return text


async def send_post(bot, chat_id, movie: Mapping[str, Any], video,
                    reply_markup=None, protect_content: bool = False,
                    **video_kwargs):
    """python-telegram-bot orqali. video: file_id, URL, Path yoki fayl obyekti."""
    from telegram.constants import ParseMode

    caption = build_caption(movie)
    try:
        return await bot.send_video(
            chat_id, video=video, caption=caption, parse_mode=ParseMode.HTML,
            show_caption_above_media=True, supports_streaming=True,
            reply_markup=reply_markup, protect_content=protect_content,
            **video_kwargs)
    except TypeError:
        # eski kutubxonada show_caption_above_media yo'q bo'lsa
        return await bot.send_video(
            chat_id, video=video, caption=caption, parse_mode=ParseMode.HTML,
            supports_streaming=True, reply_markup=reply_markup,
            protect_content=protect_content, **video_kwargs)


async def send_post_pyro(client, chat_id, movie: Mapping[str, Any], video,
                         reply_markup=None, **video_kwargs):
    """Pyrogram/pyrofork userbot orqali (katta fayllar). video: yo'l yoki file_id."""
    from pyrogram.enums import ParseMode

    caption = build_caption(movie)
    kwargs = dict(video=video, caption=caption, parse_mode=ParseMode.HTML,
                  supports_streaming=True, reply_markup=reply_markup,
                  **video_kwargs)
    try:
        return await client.send_video(chat_id, show_caption_above_media=True, **kwargs)
    except TypeError:
        # shu pyrofork versiyasida parametr yo'q bo'lsa — matn video tagida chiqadi
        return await client.send_video(chat_id, **kwargs)


if __name__ == "__main__":
    demo = {
        "title": "Sendan O'zga Muhabbat", "country": "Janubiy Koreya",
        "language": "O'zbek", "genres": ["Drama", "Romantika"],
        "quality": "1080p", "year": 2026, "code": 271, "kind": "serial",
        "views": 134, "rating": 6.6, "votes": 182,
    }
    cap = build_caption(demo)
    print(cap)
    print("\nbelgilar:", _visible_len(cap), "/", CAPTION_LIMIT)
