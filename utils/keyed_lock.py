"""
keyed_lock.py — turli kalitlar (masalan (slug, topic_id)) bo'yicha alohida
asyncio.Lock beruvchi, lekin foydalanilmay qolgan lock'larni AVTOMATIK
xotiradan tozalaydigan yordamchi.

Muammo: oddiy `dict[key] = asyncio.Lock()` yondashuvida har bir yangi kalit
uchun lock yaratiladi, lekin hech qachon o'chirilmaydi. Uzoq vaqt (oylab)
ishlaydigan botda minglab turli (studiya, topic) juftligi paydo bo'lgani
sayin bu lug'at cheksiz o'sib boradi — sekin, lekin doimiy xotira sizib
chiqishi (memory leak).

Yechim: har bir lock uchun REFERENCE COUNT saqlaymiz. Lock so'ralganda
hisob +1, ishlatib bo'lingach -1. Hisob 0 ga tushganda (ya'ni HECH KIM bu
aniq lock obyektini na ushlab turibdi, na navbatda kutmoqda) — lug'atdan
xavfsiz o'chiramiz.

Nega bu xavfsiz: refcount oshirilishi bilan lock navbatiga turish orasida
`await` yo'q (ikkalasi ham bitta sinxron bloqda) — shuning uchun boshqa
asyncio task orada "kirib ulgurib", eskirgan lock obyektini dict'dan
o'chirib, uning o'rniga yangisini yaratib qo'yishi mumkin emas. Refcount
noldan katta bo'lgan ekan, demak kimdir hali ham aynan shu lock obyektiga
murojaat qilmoqda (yo ushlab turibdi, yo navbatda) — shu payt o'chirish esa
ikkita mustaqil lock obyekti bir xil kalit uchun parallel ishlatilib,
bloklashning o'zi ma'nosiz bo'lib qolishiga olib kelardi.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Hashable


class KeyedLockMap:
    """Kalit bo'yicha asyncio.Lock beradi va bo'shagach o'zini tozalaydi."""

    def __init__(self) -> None:
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._refcounts: dict[Hashable, int] = {}

    @asynccontextmanager
    async def acquire(self, key: Hashable):
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        self._refcounts[key] = self._refcounts.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._refcounts[key] -= 1
            if self._refcounts[key] <= 0:
                self._refcounts.pop(key, None)
                # Faqat hali ham AYNAN shu lock obyekti joriy bo'lsa o'chiramiz
                # (boshqa birov allaqachon almashtirib ulgurmagan bo'lsin).
                if self._locks.get(key) is lock:
                    self._locks.pop(key, None)

    def locked(self, key: Hashable) -> bool:
        lock = self._locks.get(key)
        return bool(lock and lock.locked())

    def active_count(self) -> int:
        """Hozir band (band qilingan) lock'lar soni -- diagnostika uchun."""
        return sum(1 for lock in self._locks.values() if lock.locked())

    def tracked_count(self) -> int:
        """Lug'atda hozir saqlanayotgan (band yoki navbatdagi) lock'lar
        soni -- xotira sizib chiqishi bo'lmayotganini tekshirish uchun."""
        return len(self._locks)
