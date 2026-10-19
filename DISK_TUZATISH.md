# Disk to'lishi va ffmpeg qotishi -- tuzatish

## Sabab
- `/status`: TEMP_DIR atigi 6 GB, lekin disk 72.4/74.8 GB (bo'sh 0). Qolgan ~66 GB
  `/data/tgbotapi/<token>/...` (local Bot API'ning ASL videolari -- hech qachon
  o'chirilmagan) va `/tmp/afsona_dubbing`da edi. Eski tozalash faqat TEMP_DIR'ni ko'rardi.
- Disk 0 bo'lganda ffmpeg yozishda osilib qoldi, `kill()+await proc.wait()` esa
  cheksiz kutdi -> "Formatga tayyorlash" 4 soat turdi, 2 soatlik taymaut ham o'z vaqtida
  ishlamadi.

## Nima o'zgardi
- `utils/disk_guard.py` (YANGI): hajm hisoblash, xavfsiz tozalash (bot-api baza
  fayllariga tegmaydi), `ensure_free_space`, `DiskFullError`.
- `utils/ffmpeg_utils.py`: stall-watchdog (5 daq. progress yo'q -> kill), cheklangan kill,
  taymautda ffmpeg yetim qolmaydi, "No space left" -> `DiskFullError` (qayta urinilmaydi),
  prepare'dan oldin joy tekshiruvi.
- `handlers/studio_topic_upload.py`: nusxadan keyin bot-api asl faylini o'chiradi,
  yuklashdan oldin joy tekshiradi, disk to'la xabari.
- `bot.py`: har 5 daqiqada disk tekshiruvi (3 GB dan kam -> shoshilinch tozalash),
  har soatda to'liq tozalash, startda tozalash, `/tozalash` buyrug'i.
- `handlers/status.py`: Local Bot API va Dubbing hajmi ko'rinadi.

Env (ixtiyoriy): `MIN_FREE_GB=3`, `FFMPEG_STALL_SECONDS=300`, `TGBOTAPI_DIR=/data/tgbotapi`.

## Hozirgi serverda darrov
1. Deploy qiling (bot qayta ishga tushganda startda avtomatik tozalaydi va qotgan ffmpeg o'ladi).
2. Deploy'gacha qo'lda: `du -h -d1 /data /tmp 2>/dev/null | sort -h | tail`, keyin
   `find /data/tgbotapi -mindepth 3 -type f -mmin +60 -delete` (baza fayllariga tegmaydi).
3. Deploy'dan keyin `/status` va kerak bo'lsa `/tozalash`.
