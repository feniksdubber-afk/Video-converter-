"""
r2_manager.py — Cloudflare R2 (S3-compatible) fayl boshqarish moduli.

Funksiyalar:
  upload_file(local_path, object_key)  → public URL
  delete_file(object_key)
  list_files(prefix, max_keys)         → [{"key", "size", "last_modified", "url"}]
  generate_presigned_url(object_key, expires)
  get_public_url(object_key)
  rename_file(old_key, new_key)        → yangi URL

TUZATISH: Barcha env o'zgaruvchilar endi runtime da o'qiladi (modul yuklanish
vaqtida emas). Bu Railway Variables bilan ishlaganda to'g'ri ishlaydi.
"""

import os
import math
import asyncio
import logging
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# 2 GB chegarasi
R2_THRESHOLD = 2 * 1024 * 1024 * 1024


def _get_config() -> dict:
    """Har safar env dan yangi holda o'qiydi — Railway Variables uchun muhim."""
    account_id = os.environ.get("R2_ACCOUNT_ID", "").strip()
    return {
        "account_id":    account_id,
        "access_key_id": os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
        "secret_key":    os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
        "bucket":        os.environ.get("R2_BUCKET_NAME", "").strip(),
        "public_url":    os.environ.get("R2_PUBLIC_URL", "").strip().rstrip("/"),
        "endpoint":      f"https://{account_id}.r2.cloudflarestorage.com",
    }


def is_configured() -> bool:
    cfg = _get_config()
    ok = bool(cfg["account_id"] and cfg["access_key_id"] and cfg["secret_key"] and cfg["bucket"])
    if not ok:
        missing = [k for k in ("account_id", "access_key_id", "secret_key", "bucket") if not cfg[k]]
        logger.warning(f"R2 sozlanmagan. Yetishmayotgan: {missing}")
    return ok


def get_public_url(object_key: str) -> str:
    cfg = _get_config()
    if cfg["public_url"]:
        return f"{cfg['public_url']}/{object_key}"
    return f"{cfg['endpoint']}/{cfg['bucket']}/{object_key}"


def fmt_size(b: int) -> str:
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(b, 1024)))
    i = min(i, len(units) - 1)
    p = math.pow(1024, i)
    return f"{b / p:.1f} {units[i]}"


def _client():
    cfg = _get_config()
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_key"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


async def upload_file(local_path: str, object_key: str | None = None, progress_cb=None) -> str:
    """
    Faylni R2 ga yuklaydi (async, event loop bloklanmaydi).
    object_key ko'rsatilmasa — fayl nomidan olinadi.
    Qaytaradi: public URL (str)
    """
    if object_key is None:
        object_key = os.path.basename(local_path)

    cfg = _get_config()
    file_size = os.path.getsize(local_path)
    uploaded = [0]
    loop = asyncio.get_event_loop()

    def _callback(bytes_transferred):
        uploaded[0] += bytes_transferred
        if progress_cb:
            pct = min(int(uploaded[0] / file_size * 100), 99) if file_size else 0
            asyncio.run_coroutine_threadsafe(
                progress_cb(uploaded[0], file_size, pct), loop
            )

    def _do_upload():
        c = _client()
        c.upload_file(
            local_path,
            cfg["bucket"],
            object_key,
            Callback=_callback if progress_cb else None,
        )

    await asyncio.get_event_loop().run_in_executor(None, _do_upload)
    return get_public_url(object_key)


async def delete_file(object_key: str) -> bool:
    cfg = _get_config()

    def _do():
        try:
            _client().delete_object(Bucket=cfg["bucket"], Key=object_key)
            return True
        except ClientError as e:
            logger.warning(f"R2 delete xato: {e}")
            return False

    return await asyncio.get_event_loop().run_in_executor(None, _do)


async def list_files(prefix: str = "", max_keys: int = 50) -> list[dict]:
    cfg = _get_config()

    def _do():
        try:
            c = _client()
            kwargs = {"Bucket": cfg["bucket"], "MaxKeys": max_keys}
            if prefix:
                kwargs["Prefix"] = prefix
            resp = c.list_objects_v2(**kwargs)
            items = []
            for obj in resp.get("Contents", []):
                items.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "size_str": fmt_size(obj["Size"]),
                    "last_modified": obj["LastModified"],
                    "url": get_public_url(obj["Key"]),
                })
            return items
        except ClientError as e:
            logger.warning(f"R2 list xato: {e}")
            return []

    return await asyncio.get_event_loop().run_in_executor(None, _do)


async def rename_file(old_key: str, new_key: str) -> str | None:
    """Faylni nusxalab, eskisini o'chiradi. Yangi URL qaytaradi."""
    cfg = _get_config()

    def _do():
        try:
            c = _client()
            c.copy_object(
                Bucket=cfg["bucket"],
                CopySource={"Bucket": cfg["bucket"], "Key": old_key},
                Key=new_key,
            )
            c.delete_object(Bucket=cfg["bucket"], Key=old_key)
            return get_public_url(new_key)
        except ClientError as e:
            logger.warning(f"R2 rename xato: {e}")
            return None

    return await asyncio.get_event_loop().run_in_executor(None, _do)


async def generate_presigned_url(object_key: str, expires: int = 3600) -> str | None:
    def _do():
        try:
            url = _client().generate_presigned_url(
                "get_object",
                Params={"Bucket": _get_config()["bucket"], "Key": object_key},
                ExpiresIn=expires,
            )
            return url
        except ClientError as e:
            logger.warning(f"R2 presigned URL xato: {e}")
            return None

    return await asyncio.get_event_loop().run_in_executor(None, _do)
