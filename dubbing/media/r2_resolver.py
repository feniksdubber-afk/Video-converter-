"""
dubbing/media/r2_resolver.py — R2 (Cloudflare, S3-compatible) dan ingestion
uchun original manba faylini mahalliy diskka yuklab beruvchi input-path
resolver (`dubbing.media.ingestion.InputPathResolver`).

IZOLYATSIYA: bu modul botning `utils.r2_manager` yoki boshqa hech qanday
utils/handlers modulini import qilmaydi. R2 hisob ma'lumotlari — mavjud
R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET_NAME
muhit o'zgaruvchilari (botning boshqa qismlari bilan bir xil Cloudflare R2
hisobi) — to'g'ridan-to'g'ri `os.environ` orqali mustaqil o'qiladi va bu
modul o'zining boto3 klientini yaratadi. Bu xuddi
`dubbing/segmentation/vad.py`ning ffmpeg subprocess chaqiruvini mustaqil
takrorlagani kabi ataylab qilingan izolyatsiya qarori — botning
`utils.r2_manager._get_config()`/`_client()`ga bog'liq bo'lish o'rniga,
o'z nusxasi saqlanadi (Step 2/3'da ffmpeg uchun qilingan naqsh bilan bir
xil sabab: kelajakda bot tomonidagi R2 kodi o'zgarsa, dubbing kutilmaganda
buzilmasin).

`episodes.original_r2_key` — R2'dagi original manba faylining kaliti
(bot tomonidan episode yaratilganda yozib qo'yiladi — bu modul faqat
o'qiydi, hech qachon yozmaydi).
"""

from __future__ import annotations

import asyncio
import logging
import os

import asyncpg
import boto3
from botocore.config import Config as BotoConfig

from dubbing.config import DUBBING_TEMP_DIR
from dubbing.models.types import JobRecord

logger = logging.getLogger("dubbing.media.r2_resolver")


class R2NotConfiguredError(RuntimeError):
    """R2 hisob ma'lumotlari (R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/
    R2_BUCKET_NAME) to'liq emas — yuklab bo'lmaydi."""


class EpisodeSourceMissingError(RuntimeError):
    """Berilgan episode_id uchun `episodes.original_r2_key` topilmadi yoki
    bo'sh — ingestion davom eta olmaydi."""


def _r2_config() -> dict:
    """Har safar env'dan yangi holda o'qiydi (deploy vaqtida env o'zgarishi
    mumkinligi uchun) — botning `utils.r2_manager._get_config()` bilan bir
    xil naqsh, lekin mustaqil nusxa."""
    account_id = os.environ.get("R2_ACCOUNT_ID", "").strip()
    return {
        "account_id": account_id,
        "access_key_id": os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
        "secret_key": os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
        "bucket": os.environ.get("R2_BUCKET_NAME", "").strip(),
        "endpoint": f"https://{account_id}.r2.cloudflarestorage.com" if account_id else "",
    }


def _r2_client_and_bucket():
    cfg = _r2_config()
    if not (cfg["account_id"] and cfg["access_key_id"] and cfg["secret_key"] and cfg["bucket"]):
        missing = [
            name for name, val in (
                ("R2_ACCOUNT_ID", cfg["account_id"]),
                ("R2_ACCESS_KEY_ID", cfg["access_key_id"]),
                ("R2_SECRET_ACCESS_KEY", cfg["secret_key"]),
                ("R2_BUCKET_NAME", cfg["bucket"]),
            ) if not val
        ]
        raise R2NotConfiguredError(f"R2 sozlanmagan. Yetishmayotgan: {missing}")

    client = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )
    return client, cfg["bucket"]


async def _fetch_original_r2_key(pool: asyncpg.Pool, episode_id: int) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT original_r2_key FROM episodes WHERE id = $1", episode_id,
        )
    if row is None or not row["original_r2_key"]:
        raise EpisodeSourceMissingError(
            f"episode_id={episode_id}: original_r2_key topilmadi yoki bo'sh"
        )
    return row["original_r2_key"]


def _download_sync(object_key: str, dest_path: str) -> None:
    """SINXRON, BLOKLOVCHI — chaqiruvchi buni executor orqali chaqirishi kerak."""
    client, bucket = _r2_client_and_bucket()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    client.download_file(bucket, object_key, dest_path)


def make_r2_input_path_resolver(pool: asyncpg.Pool):
    """
    `dubbing.media.ingestion.make_ingestion_handler(pool, resolver)` uchun
    mos `InputPathResolver` (`(JobRecord) -> Awaitable[str]`) qaytaradi:

        job.episode_id -> episodes.original_r2_key (Postgres, `pool` orqali)
                        -> R2'dan DUBBING_TEMP_DIR/r2_downloads/<job.id>/<nom>
                           ga yuklab olinadi
                        -> mahalliy yo'l qaytariladi

    Yuklash `loop.run_in_executor` orqali amalga oshiriladi — event loop'ni
    bloklamaydi.
    """

    async def resolve(job: JobRecord) -> str:
        object_key = await _fetch_original_r2_key(pool, job.episode_id)
        dest_path = os.path.join(
            DUBBING_TEMP_DIR, "r2_downloads", str(job.id), os.path.basename(object_key)
        )
        logger.info(
            "R2'dan yuklanmoqda: job=%s episode_id=%s key=%s -> %s",
            job.id, job.episode_id, object_key, dest_path,
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _download_sync, object_key, dest_path)
        return dest_path

    return resolve
