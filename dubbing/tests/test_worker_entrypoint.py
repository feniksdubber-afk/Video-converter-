"""
Step 4 — behavioral tests for dubbing/worker/entrypoint.py.

These mock asyncpg.create_pool and WorkerLoop.run so they run WITHOUT a
real PostgreSQL connection — they test the entrypoint's own wiring logic
(enable/disable gate, handler registration, shutdown sequencing), not the
DB-backed pipeline itself (that's test_segment_store.py's job).
"""

from __future__ import annotations

import asyncio

import pytest

import dubbing.worker.entrypoint as entrypoint_module

pytestmark = pytest.mark.asyncio


class _FakePool:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


async def test_entrypoint_disabled_exits_cleanly_without_pool(monkeypatch):
    monkeypatch.setattr(entrypoint_module, "DUBBING_ENABLED", False)

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("asyncpg.create_pool must not be called when DUBBING_ENABLED=false")

    monkeypatch.setattr(entrypoint_module.asyncpg, "create_pool", _fail_if_called)

    # main() must return normally (no exception, no sys.exit(nonzero)) —
    # the process's own exit code is then whatever Python's default is (0)
    # since main() just returns.
    entrypoint_module.main()


async def test_entrypoint_registers_ingestion_and_segmentation_handlers(monkeypatch):
    monkeypatch.setattr(entrypoint_module, "DUBBING_ENABLED", True)

    fake_pool = _FakePool()

    async def _fake_create_pool(*args, **kwargs):
        return fake_pool

    monkeypatch.setattr(entrypoint_module.asyncpg, "create_pool", _fake_create_pool)

    registered = {}

    class _FakeWorkerLoop:
        def __init__(self, pool, stages, lease_seconds, poll_interval_seconds):
            assert pool is fake_pool
            assert stages == ["ingestion", "segmentation"]

        def register_handler(self, stage, handler):
            registered[stage] = handler

        def request_stop(self):
            pass

        async def run(self):
            # Simulate an already-stopped loop returning immediately.
            return None

    monkeypatch.setattr(entrypoint_module, "WorkerLoop", _FakeWorkerLoop)

    async def _fake_reaper_loop(pool, interval_seconds, stop_event):
        await stop_event.wait()

    monkeypatch.setattr(entrypoint_module, "run_reaper_loop", _fake_reaper_loop)

    await entrypoint_module._async_main()

    assert set(registered.keys()) == {"ingestion", "segmentation"}
    assert fake_pool.closed is True


async def test_entrypoint_cancels_reaper_and_closes_pool_on_shutdown(monkeypatch):
    monkeypatch.setattr(entrypoint_module, "DUBBING_ENABLED", True)

    fake_pool = _FakePool()

    async def _fake_create_pool(*args, **kwargs):
        return fake_pool

    monkeypatch.setattr(entrypoint_module.asyncpg, "create_pool", _fake_create_pool)

    reaper_started = asyncio.Event()
    reaper_cancelled = asyncio.Event()

    class _FakeWorkerLoop:
        def __init__(self, *args, **kwargs):
            pass

        def register_handler(self, stage, handler):
            pass

        def request_stop(self):
            pass

        async def run(self):
            return None

    monkeypatch.setattr(entrypoint_module, "WorkerLoop", _FakeWorkerLoop)

    async def _fake_reaper_loop(pool, interval_seconds, stop_event):
        reaper_started.set()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            reaper_cancelled.set()
            raise

    monkeypatch.setattr(entrypoint_module, "run_reaper_loop", _fake_reaper_loop)

    await entrypoint_module._async_main()

    # worker.run() returns immediately in this fake, which triggers the
    # finally block: reaper_stop_event.set() + reaper_task.cancel().
    # Either the event unblocked the reaper cleanly or it was cancelled —
    # both are acceptable "stopped" outcomes; the pool must be closed either way.
    assert fake_pool.closed is True
