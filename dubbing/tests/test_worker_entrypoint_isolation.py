"""
Step 4 — static isolation checks for dubbing/worker/entrypoint.py and
dubbing/media/r2_resolver.py.

General checks (no utils/handlers imports except the approved
utils.ffmpeg_utils names, no forbidden strings, no new heavy deps) are
already covered tree-wide by test_isolation.py and, for media/*, by
test_regression_isolation.py — this file adds Step-4-specific guarantees
that those don't check.
"""

import ast
import pathlib

DUBBING_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRYPOINT_PATH = DUBBING_ROOT / "worker" / "entrypoint.py"
R2_RESOLVER_PATH = DUBBING_ROOT / "media" / "r2_resolver.py"


def test_step4_files_exist():
    assert ENTRYPOINT_PATH.is_file()
    assert R2_RESOLVER_PATH.is_file()


def test_r2_resolver_does_not_import_bot_r2_manager():
    """The resolver must be self-contained: no dependency on the bot's
    utils.r2_manager (or anything else in utils/handlers) for building
    its own boto3 client — this is covered generically by
    test_regression_isolation.py's media/ scan, but pinned explicitly
    here since it's the core Step 4 design decision."""
    tree = ast.parse(R2_RESOLVER_PATH.read_text(), filename=str(R2_RESOLVER_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("utils"), f"r2_resolver.py must not import from '{module}'"
            assert not module.startswith("handlers"), f"r2_resolver.py must not import from '{module}'"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("utils"), f"r2_resolver.py must not import '{alias.name}'"
                assert not alias.name.startswith("handlers"), f"r2_resolver.py must not import '{alias.name}'"


def test_entrypoint_only_imports_dubbing_and_stdlib_or_asyncpg():
    """entrypoint.py must only import from dubbing.*, the stdlib, or
    asyncpg — no utils/handlers, no root config.py."""
    tree = ast.parse(ENTRYPOINT_PATH.read_text(), filename=str(ENTRYPOINT_PATH))
    allowed_top_level = {
        "__future__", "asyncio", "logging", "signal", "asyncpg", "dubbing",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            assert top in allowed_top_level, f"entrypoint.py imports unexpected module '{module}'"
            assert module != "config", "entrypoint.py must not import the bot's root config.py"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top in allowed_top_level, f"entrypoint.py imports unexpected module '{alias.name}'"


def test_entrypoint_checks_dubbing_enabled_before_creating_pool():
    """Static guarantee that DUBBING_ENABLED is referenced in main() before
    any asyncpg.create_pool call textually appears in _async_main — a
    behavioral version of this is covered by
    test_entrypoint_disabled_exits_cleanly_without_pool in
    test_worker_entrypoint.py; this just pins the source shape."""
    text = ENTRYPOINT_PATH.read_text()
    assert "DUBBING_ENABLED" in text
    assert "asyncpg.create_pool" in text
    # DUBBING_ENABLED check must appear in main(), textually before
    # asyncio.run(_async_main()) is reached.
    main_idx = text.index("def main()")
    enabled_idx = text.index("DUBBING_ENABLED", main_idx)
    run_idx = text.index("asyncio.run(_async_main())")
    assert enabled_idx < run_idx
