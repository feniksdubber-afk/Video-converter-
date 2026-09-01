"""
orphan_uploads.py — bekor qilingan `/joylash` jarayonlarida R2'ga ulgurib
yuklangan, lekin bazaga HECH QACHON yozilmagan "yetim" (orphan) fayllarni
qayd etish.

DIQQAT (tarix): bot ilgari bunday fayllarni studiya backend API orqali
avtomatik o'chirishga urinar edi (`DELETE /studios/:slug/uploads`).
Backend kodini (AfsonaMovieBot-main/artifacts/api-server) tekshirganda
ma'lum bo'ldi:

  1) bunday DELETE endpoint umuman mavjud emas (faqat
     `POST /studios/:slug/uploads/presign` bor);
  2) backenddagi `deleteFromR2()` funksiyasi ataylab FAQAT `social/`
     prefiksli R2 kalitlarini o'chiradi -- studiya film/serial fayllari
     (`{slug}/movies/...`, `{slug}/series/...`) tasodifan o'chib
     ketmasligi uchun bu ataylab qo'yilgan xavfsizlik chegarasi
     (r2.ts'dagi izohda aniq yozilgan).

Demak, bu yo'l bilan o'chirish HECH QACHON ishlamas edi -- na endpoint
yo'qligi, na backend'ning ataylab bloklashi tufayli. Shuning uchun bot
endi bunday faylni o'chirishga urinmaydi (keraksiz tarmoq so'rovi +
har doim 404/muvaffaqiyatsizlik), buning o'rniga uni shu yerda,
mahalliy JSON ro'yxatga yozib qo'yadi -- admin xohlasa buni ko'rib,
R2 konsolidan yoki alohida skript orqali qo'lda tozalashi mumkin.
"""

import logging
import os
import time
import uuid

from config import DATA_DIR
from utils.atomic_json import load_json, save_json

logger = logging.getLogger(__name__)

_FILE = os.path.join(DATA_DIR, "orphan_r2_uploads.json")


def record_orphan_upload(*, studio_slug: str, public_url: str, label: str = "") -> None:
    """R2'ga yuklangan, lekin bekor qilingani sababli bazaga yozilmay
    qolgan faylni ro'yxatga oladi. O'zi xato bersa ham (masalan disk
    muammosi) faqat log qiladi -- bu funksiya hech qachon chaqiruvchi
    jarayonni to'xtatmasligi kerak, chunki u faqat "yaxshi bo'lsin"
    (best-effort) yozuv."""
    try:
        data = load_json(_FILE, default=[])
        if not isinstance(data, list):
            data = []
        data.append({
            "id": uuid.uuid4().hex[:8],
            "studio_slug": studio_slug,
            "public_url": public_url,
            "label": label,
            "recorded_at": time.time(),
        })
        save_json(_FILE, data)
        logger.info(
            "Yetim R2 fayl ro'yxatga olindi (bekor qilingan joylashdan qoldi): %s (studio=%s)",
            public_url, studio_slug,
        )
    except Exception:
        logger.warning(
            "Yetim R2 faylni ro'yxatga olib bo'lmadi (fayl o'zi R2'da qolgan bo'lishi mumkin): %s",
            public_url, exc_info=True,
        )


def list_orphan_uploads() -> list[dict]:
    """Hozircha ro'yxatga olingan barcha yetim fayllarni qaytaradi
    (masalan admin buyrug'i orqali ko'rsatish uchun)."""
    data = load_json(_FILE, default=[])
    return data if isinstance(data, list) else []


def remove_orphan_upload(entry_id: str) -> dict | None:
    """Bitta yozuvni `id` bo'yicha ro'yxatdan olib tashlaydi (masalan R2'dan
    muvaffaqiyatli o'chirilgandan keyin). Topilgan (va olib tashlangan)
    yozuvni qaytaradi, topilmasa `None`."""
    data = list_orphan_uploads()
    for i, entry in enumerate(data):
        if entry.get("id") == entry_id:
            removed = data.pop(i)
            save_json(_FILE, data)
            return removed
    return None


def clear_orphan_uploads() -> None:
    """Ro'yxatni tozalaydi -- masalan admin qo'lda R2'dan tozalab
    bo'lgach, ro'yxatni ham bo'shatish uchun."""
    save_json(_FILE, [])
