"""
dubbing/tests/conftest.py — Step 1 test fixtures.

IZOLYATSIYA: bu testlar FAQAT DUBBING_TEST_DATABASE_URL ga qarshi ishlaydi,
hech qachon production afsona_dubbing bazasiga tegmaydi. Har bir test
funksiyasidan oldin/keyin jobs/artifacts/artifact_lineage/episodes
tozalanadi (TRUNCATE ... CASCADE), shuning uchun testlar bir-biridan
mustaqil.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from dubbing.config import DUBBING_TEST_DATABASE_URL


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(DUBBING_TEST_DATABASE_URL, min_size=1, max_size=5)
    try:
        async with p.acquire() as conn:
            await conn.execute(
                "TRUNCATE artifact_lineage, artifacts, jobs, episodes RESTART IDENTITY CASCADE"
            )
        yield p
    finally:
        async with p.acquire() as conn:
            await conn.execute(
                "TRUNCATE artifact_lineage, artifacts, jobs, episodes RESTART IDENTITY CASCADE"
            )
        await p.close()


@pytest_asyncio.fixture
async def episode_id(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO episodes (project_name, original_r2_key)
            VALUES ('Test Project', 'r2/test/original.mkv')
            RETURNING id
            """
        )
        return row["id"]
