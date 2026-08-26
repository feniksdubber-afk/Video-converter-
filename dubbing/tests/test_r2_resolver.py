"""
Step 4 — tests for dubbing/media/r2_resolver.py.

No real network / R2 credentials are used: boto3's client construction
and download_file are monkeypatched, so these run without any external
service. The Postgres-backed lookup (`episodes.original_r2_key`) is
exercised against the real test DB in test_segment_store.py's style
end-to-end tests once the full pipeline is wired — here we test the
resolver's own logic in isolation with a fake pool.
"""

from __future__ import annotations

import os

import pytest

import dubbing.media.r2_resolver as r2_resolver_module
from dubbing.media.r2_resolver import (
    EpisodeSourceMissingError,
    R2NotConfiguredError,
    make_r2_input_path_resolver,
)
from dubbing.models.enums import JobStatus
from dubbing.models.types import JobRecord

pytestmark = pytest.mark.asyncio


def _job(episode_id=1, job_id=1):
    return JobRecord(
        id=job_id, episode_id=episode_id, stage="ingestion", status=JobStatus.PROCESSING,
        depends_on_job_id=None, input_hash=None, priority=100, attempts=1, max_attempts=3,
        leased_by="w", lease_expires_at=None, error=None, error_kind=None,
        created_at=None, started_at=None, finished_at=None,
    )


class _FakeConn:
    def __init__(self, row):
        self._row = row

    async def fetchrow(self, query, *args):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, row):
        self._row = row

    def acquire(self):
        return _FakeConn(self._row)


def test_r2_not_configured_raises(monkeypatch):
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(R2NotConfiguredError):
        r2_resolver_module._r2_client_and_bucket()


def test_r2_configured_builds_client(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")

    captured = {}

    class _FakeClient:
        pass

    def _fake_boto_client(service, **kwargs):
        captured["service"] = service
        captured.update(kwargs)
        return _FakeClient()

    monkeypatch.setattr(r2_resolver_module.boto3, "client", _fake_boto_client)

    client, bucket = r2_resolver_module._r2_client_and_bucket()
    assert bucket == "bucket"
    assert captured["endpoint_url"] == "https://acct.r2.cloudflarestorage.com"
    assert captured["aws_access_key_id"] == "key"
    assert captured["aws_secret_access_key"] == "secret"


async def test_missing_episode_source_raises(monkeypatch):
    fake_pool = _FakePool(row=None)
    resolver = make_r2_input_path_resolver(fake_pool)
    with pytest.raises(EpisodeSourceMissingError):
        await resolver(_job(episode_id=42))


async def test_resolver_downloads_via_executor_and_returns_local_path(monkeypatch, tmp_path):
    monkeypatch.setattr(r2_resolver_module, "DUBBING_TEMP_DIR", str(tmp_path))

    fake_pool = _FakePool(row={"original_r2_key": "episodes/42/source.mkv"})

    download_calls = []

    def _fake_download_sync(object_key, dest_path):
        download_calls.append((object_key, dest_path))
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(b"fake video bytes")

    monkeypatch.setattr(r2_resolver_module, "_download_sync", _fake_download_sync)

    resolver = make_r2_input_path_resolver(fake_pool)
    result_path = await resolver(_job(episode_id=42, job_id=7))

    assert len(download_calls) == 1
    assert download_calls[0][0] == "episodes/42/source.mkv"
    assert result_path.endswith(os.path.join("7", "source.mkv"))
    assert os.path.isfile(result_path)
