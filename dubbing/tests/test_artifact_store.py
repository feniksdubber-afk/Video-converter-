import asyncio

import asyncpg
import pytest

from dubbing.artifacts.store import ArtifactStore

pytestmark = pytest.mark.asyncio


async def test_register_is_idempotent(pool: asyncpg.Pool, episode_id: int):
    store = ArtifactStore(pool)
    h = store.compute_hash([], "whisper", "1.0", {"lang": "uz"})

    a1 = await store.register(episode_id, "transcription", h, "whisper", "1.0", {"lang": "uz"})
    a2 = await store.register(episode_id, "transcription", h, "whisper", "1.0", {"lang": "uz"})
    assert a1.id == a2.id


async def test_concurrent_registration_no_duplicates(pool: asyncpg.Pool, episode_id: int):
    store = ArtifactStore(pool)
    h = store.compute_hash([], "whisper", "1.0", {"lang": "uz"})

    results = await asyncio.gather(*[
        store.register(episode_id, "transcription", h, "whisper", "1.0", {"lang": "uz"})
        for _ in range(5)
    ])
    ids = {r.id for r in results}
    assert len(ids) == 1


async def test_lineage_recorded_explicitly(pool: asyncpg.Pool, episode_id: int):
    store = ArtifactStore(pool)
    parent_hash = store.compute_hash([], "ingest", "1.0", {})
    parent = await store.register(episode_id, "ingestion", parent_hash, "ingest", "1.0", {})

    child_hash = store.compute_hash([parent_hash], "transcribe", "1.0", {})
    child = await store.register(
        episode_id, "transcription", child_hash, "transcribe", "1.0", {},
        parent_artifact_ids=[parent.id],
    )

    lineage = await store.get_lineage(child.id)
    assert len(lineage) == 1
    assert lineage[0].id == parent.id


async def test_lookup_returns_none_when_missing(pool: asyncpg.Pool, episode_id: int):
    store = ArtifactStore(pool)
    result = await store.lookup(episode_id, "transcription", "nonexistent-hash")
    assert result is None
