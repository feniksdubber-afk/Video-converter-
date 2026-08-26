"""
dubbing/artifacts/hashing.py — Artifact content-hash hisoblash.

Pure function: tashqi holatga (DB, fayl tizimi) bog'liq emas, shuning uchun
unit test qilish oson.

hash = f(parent_hashes, engine_name, engine_version, params)

Bir xil kirishlar → bir xil hash, `params` dict kalitlari tartibidan
qat'iy nazar (barqaror JSON serialization orqali).
"""

import hashlib
import json
from typing import Iterable, Mapping


def compute_content_hash(
    parent_hashes: Iterable[str],
    engine_name: str,
    engine_version: str,
    params: Mapping,
) -> str:
    """
    Deterministik SHA-256 content-hash qaytaradi (hex string).

    - `parent_hashes` tartibidan qat'iy nazar bir xil natija berishi uchun
      saralanadi (bir xil to'plam, boshqa tartibda berilgan bo'lsa ham bir
      xil hash chiqishi kerak).
    - `params` `sort_keys=True` bilan JSON serialize qilinadi — dict
      kalitlari tartibi hash'ga ta'sir qilmaydi.
    """
    normalized = {
        "parent_hashes": sorted(parent_hashes),
        "engine_name": engine_name,
        "engine_version": engine_version,
        "params": params,
    }
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
