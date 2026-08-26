# Qo'shilgan/o'zgargan fayllar (Step 4 to'liqlash)

## Yangi fayllar (repo'da yo'q edi):
- dubbing/worker/entrypoint.py   — worker process kirish nuqtasi
- dubbing/media/r2_resolver.py   — R2'dan original fayl yuklovchi
- dubbing/tests/test_worker_entrypoint.py
- dubbing/tests/test_worker_entrypoint_isolation.py
- dubbing/tests/test_r2_resolver.py

## O'zgargan fayllar (repo'dagi mavjud faylga QO'SHIMCHA qilindi, TO'LIQ ALMASHTIRILMAYDI):
- requirements.txt      — oxiriga `asyncpg==0.29.0` qo'shildi
- supervisord.conf       — oxiriga `[program:dubbing-worker]` bloki qo'shildi

## Muhim: bular TO'LIQ fayl emas, faqat QO'SHILGAN qism
requirements.txt va supervisord.conf uchun repo'dagi eski qatorlarni
o'chirmang — shu ikkita faylni pastdagi ko'rsatma bo'yicha qo'lda
birlashtiring (yoki men bergan to'liq versiyani ustiga yozing —
ular allaqachon eski qatorlar + yangi qo'shimchani o'z ichiga oladi).
