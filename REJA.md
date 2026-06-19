# Video Converter Bot — To'liq Reja va Amalga Oshirish

## Maqsad
Shaxsiy video bot: faqat ruxsatli TG ID lar, kuchli R2 boshqaruvi, save restricted → arxiv guruhi, jarayon davomida bekor qilish.

---

## Bosqich 1 — Xavfsizlik (whitelist) ✅

| Vazifa | Holat |
|--------|-------|
| `ALLOWED_USER_IDS` + `ADMIN_USER_IDS` env | ✅ |
| `utils/auth.py` — JSON saqlash | ✅ |
| `/allow`, `/deny`, `/users` | ✅ |
| Barcha update lar oldidan `auth_gate` (group -1) | ✅ |

---

## Bosqich 2 — Save Restricted yaxshilash ✅

| Vazifa | Holat |
|--------|-------|
| `ARCHIVE_GROUP_ID` + avtomatik forum topic | ✅ |
| `force_document=True` — format saqlanadi | ✅ |
| Asl fayl nomi (caption/document) | ✅ |
| Album (media_group) qo'llab-quvvatlash | ✅ |
| To'g'ri `user_id` (guruh ID emas) | ✅ |
| Yuklash paytida ❌ Bekor (`sr_cancel_run`) | ✅ |
| Topik skan: 2000 xabar, `reply_to_top_message_id` | ✅ |

---

## Bosqich 3 — R2 Papka Menejeri ✅

| Vazifa | Holat |
|--------|-------|
| `list_prefix` — papka + fayl ajratish | ✅ |
| Papka navigatsiya (📁 ochish, 🔙 orqaga) | ✅ |
| Papka yaratish (`r2_mkdir`) | ✅ |
| Key-hash callback (indeks o'rniga) | ✅ |
| Rename bug tuzatildi (`state=r2_rename_input`) | ✅ |
| Windows `\` → `/` (`join_key`) | ✅ |
| Upload: `users/{id}/uploads/{uuid}_{nom}` | ✅ |
| Pagination (sahifa bo'yicha) | ✅ |

---

## Bosqich 4 — Bekor qilish tizimi ✅ (asosiy)

| Vazifa | Holat |
|--------|-------|
| `utils/task_manager.py` | ✅ |
| `run_ffmpeg_async` + `task_cancel` | ✅ |
| Save yuklash bekor qilish | ✅ |
| `task_cancel` callback bot.py da | ✅ |
| Barcha handlerlarda user_id uzatish | ✅ |
| Bekor qilish barcha FFmpeg vazifalarida | ✅ |

---

## Bosqich 5 — Sender yaxshilash ✅

| Vazifa | Holat |
|--------|-------|
| `target_chat_id` + `message_thread_id` | ✅ |
| `force_document` parametri | ✅ |
| R2 user papka prefiksi | ✅ |

---

## Keyingi bosqichlar (ixtiyoriy)

- [ ] Barcha FFmpeg handlerlarida `user_id` uzatish
- [ ] R2 papka o'chirish (bulk delete folder)
- [ ] R2 qidiruv
- [ ] `/status` — server holati
- [ ] README yangilash

---

## Sozlash

```env
ALLOWED_USER_IDS=SIZNING_ID
ADMIN_USER_IDS=SIZNING_ID
SESSION_STRING=...
ARCHIVE_GROUP_ID=-100...
AUTO_CREATE_TOPIC=true
```

Bot arxiv guruhida **admin** + **Manage Topics** huquqi bo'lishi kerak.

## Buyruqlar

| Buyruq | Vazifa |
|--------|--------|
| `/allow ID` | Foydalanuvchi qo'shish (admin) |
| `/deny ID` | Olib tashlash (admin) |
| `/users` | Ro'yxat (admin) |
| `/save URL` | Restricted media → arxiv |
| `/r2` | R2 papka brauzer |
