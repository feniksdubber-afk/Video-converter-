"""
Fon vazifasi: har bir bog'langan studiya guruhi uchun, hali topic'i
bo'lmagan film/seriallarga avtomatik topic ochib qo'yadi -- shunda manager
Afsona botidan yangi kontent qo'shishi bilan, guruhda darhol tegishli topic
paydo bo'ladi va u yerga video tashlashni boshlashi mumkin.

bot.py'da ishga tushiriladi:
    application.job_queue.run_repeating(sync_all_studio_topics, interval=120, first=20)
"""

import logging

from telegram.ext import ContextTypes

from utils.studio_group import list_groups, get_topic_id
from utils.studio_auth import list_tokens
from handlers.studio_group import ensure_topic
from handlers.studio_content import _fetch_list

logger = logging.getLogger(__name__)


async def _sync_one_kind(context: ContextTypes.DEFAULT_TYPE, studio: dict, kind: str) -> None:
    page = 1
    key = "movies" if kind == "m" else "series"
    while True:
        data = await _fetch_list(studio, kind, page, "")
        if not data:
            return
        items = data.get(key) or []
        if not items:
            return
        for item in items:
            content_id = item.get("id")
            if content_id is None:
                continue
            content_key = f"{kind}_{content_id}"
            if get_topic_id(studio["slug"], content_key):
                continue  # topic allaqachon bor
            title = item.get("title_uz") or item.get("title") or f"{key} #{content_id}"
            dest = await ensure_topic(context, studio, kind, content_id, title)
            if dest:
                logger.info("Avtomatik topic ochildi: %s / %s (#%s)", studio["slug"], title, content_id)
        total = data.get("total", 0)
        if page * 6 >= total:
            return
        page += 1


async def sync_all_studio_topics(context: ContextTypes.DEFAULT_TYPE) -> None:
    tokens = list_tokens()
    for slug, group in list_groups().items():
        token = tokens.get(slug)
        if not token:
            continue
        studio = {"slug": slug, "api_token": token}
        try:
            await _sync_one_kind(context, studio, "m")
            await _sync_one_kind(context, studio, "s")
        except Exception as e:
            logger.warning("Avtomatik topic sinxronizatsiyasida xato (%s): %s", slug, e)
