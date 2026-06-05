"""
r2_manager.py — Cloudflare R2 (S3-compatible) fayl boshqarish moduli.

Funksiyalar:
  upload_file(local_path, object_key)  → public URL
  delete_file(object_key)
  list_files(prefix, max_keys)         → [{"key", "size", "last_modified", "url"}]
  generate_presigned_url(object_key, expires)
  get_public_url(object_key)
  rename_file(old_key, new_key)        → yangi URL
"""

import os
import math
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

R2_ACCOUNT_ID      = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID   = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_KEY      = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET          = os.environ.get("R2_BUCKET_NAME", "")
R2_PUBLIC_URL      = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")

R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

# 2 GB chegarasi — bu dan kattalar R2 ga ketadi
R2_THRESHOLD = 2 * 1024 * 1024 * 1024


def _client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def is_configured() -> bool:
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_KEY and R2_BUCKET)


def get_public_url(object_key: str) -> str:
    if R2_PUBLIC_URL:
        return f"{R2_PUBLIC_URL}/{object_key}"
    return f"{R2_ENDPOINT}/{R2_BUCKET}/{object_key}"


def fmt_size(b: int) -> str:
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(b, 1024)))
    i = min(i, len(units) - 1)
    p = math.pow(1024, i)
    return f"{b / p:.1f} {units[i]}"


async def upload_file(local_path: str, object_key: str | None = None, progress_cb=None) -> str:
    """
    Faylni R2 ga yuklaydi.
    object_key ko'rsatilmasa — fayl nomidan olinadi.
    Qaytaradi: public URL (str)
    """
    if object_key is None:
        object_key = os.path.basename(local_path)

    file_size = os.path.getsize(local_path)
    uploaded = [0]

    def _callback(bytes_transferred):
        uploaded[0] += bytes_transferred
        if progress_cb:
            import asyncio
            pct = min(int(uploaded[0] / file_size * 100), 99) if file_size else 0
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(progress_cb(uploaded[0], file_size, pct))
            except Exception:
                pass

    c = _client()
    c.upload_file(
        local_path,
        R2_BUCKET,
        object_key,
        Callback=_callback if progress_cb else None,
    )
    return get_public_url(object_key)


async def delete_file(object_key: str) -> bool:
    try:
        _client().delete_object(Bucket=R2_BUCKET, Key=object_key)
        return True
    except ClientError:
        return False


async def list_files(prefix: str = "", max_keys: int = 50) -> list[dict]:
    try:
        c = _client()
        kwargs = {"Bucket": R2_BUCKET, "MaxKeys": max_keys}
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
    except ClientError:
        return []


async def rename_file(old_key: str, new_key: str) -> str | None:
    """Faylni nusxalab, eskisini o'chiradi. Yangi URL qaytaradi."""
    try:
        c = _client()
        c.copy_object(
            Bucket=R2_BUCKET,
            CopySource={"Bucket": R2_BUCKET, "Key": old_key},
            Key=new_key,
        )
        c.delete_object(Bucket=R2_BUCKET, Key=old_key)
        return get_public_url(new_key)
    except ClientError:
        return None


async def generate_presigned_url(object_key: str, expires: int = 3600) -> str | None:
    try:
        url = _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET, "Key": object_key},
            ExpiresIn=expires,
        )
        return url
    except ClientError:
        return None
