"""
dubbing/artifacts/store.py — Artifact ro'yxatga olish/qidirish/lineage.

Step 1 doirasi: bu modul faqat Postgres'dagi `artifacts` va
`artifact_lineage` jadvallari bilan ishlaydi. R2/haqiqiy storage integratsiyasi
keyingi bosqichda qo'shiladi — `storage_key` hozircha ixtiyoriy (NULL bo'lishi
mumkin).

IZOLYATSIYA: bu modul faqat `dubbing.*` importlaridan foydalanadi.
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

import asyncpg

from dubbing.artifacts.hashing import compute_content_hash
from dubbing.models.types import ArtifactRecord


class ArtifactStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @staticmethod
    def compute_hash(parent_hashes: Iterable[str], engine_name: str, engine_version: str, params: dict) -> str:
        return compute_content_hash(parent_hashes, engine_name, engine_version, params)

    async def lookup(self, episode_id: int, stage: str, content_hash: str) -> Optional[ArtifactRecord]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM artifacts
                WHERE episode_id = $1 AND stage = $2 AND content_hash = $3
                """,
                episode_id, stage, content_hash,
            )
            return ArtifactRecord.from_row(row) if row else None

    async def register(
        self,
        episode_id: int,
        stage: str,
        content_hash: str,
        engine_name: str,
        engine_version: str,
        params: dict,
        producing_job_id: Optional[int] = None,
        parent_artifact_ids: Optional[Iterable[int]] = None,
        storage_key: Optional[str] = None,
    ) -> ArtifactRecord:
        """
        Idempotent ro'yxatga olish: bir xil (episode_id, stage, content_hash)
        uchun ikkinchi chaqiruv xato bermaydi va yangi qator yaratmaydi —
        ON CONFLICT DO NOTHING, so'ng mavjud qatorni qaytaradi.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO artifacts
                        (episode_id, stage, content_hash, storage_key,
                         engine_name, engine_version, params, producing_job_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                    ON CONFLICT (episode_id, stage, content_hash) DO NOTHING
                    RETURNING *
                    """,
                    episode_id, stage, content_hash, storage_key,
                    engine_name, engine_version, json.dumps(params), producing_job_id,
                )
                if row is None:
                    # Allaqachon mavjud — mavjud qatorni o'qib qaytaramiz.
                    row = await conn.fetchrow(
                        """
                        SELECT * FROM artifacts
                        WHERE episode_id = $1 AND stage = $2 AND content_hash = $3
                        """,
                        episode_id, stage, content_hash,
                    )
                artifact = ArtifactRecord.from_row(row)

                if parent_artifact_ids:
                    for parent_id in parent_artifact_ids:
                        await conn.execute(
                            """
                            INSERT INTO artifact_lineage (artifact_id, parent_artifact_id)
                            VALUES ($1, $2)
                            ON CONFLICT DO NOTHING
                            """,
                            artifact.id, parent_id,
                        )
                return artifact

    async def get_lineage(self, artifact_id: int) -> list[ArtifactRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT a.* FROM artifacts a
                JOIN artifact_lineage l ON l.parent_artifact_id = a.id
                WHERE l.artifact_id = $1
                """,
                artifact_id,
            )
            return [ArtifactRecord.from_row(row) for row in rows]
