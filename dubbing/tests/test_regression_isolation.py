"""
Step 2 regression & isolation checks:

- dubbing/media/* imports nothing from utils/handlers except the specific
  pure functions from utils.ffmpeg_utils that were explicitly approved.
- No references to TEMP_DIR, make_temp_path, run_ffmpeg_async, task_queue,
  task_manager anywhere in dubbing/.
- A live smoke test proving utils.ffmpeg_utils itself still works
  unmodified (imported and called directly, outside of dubbing).
"""

import ast
import pathlib
import re
import subprocess

DUBBING_ROOT = pathlib.Path(__file__).resolve().parents[1]

FORBIDDEN_STRINGS = (
    "/tmp/videobot",
    "utils.task_queue",
    "handlers.whisper_subtitle",
    "make_temp_path",
    "run_ffmpeg_async",
    "task_manager",
)
# Matches the bot's bare TEMP_DIR (import or config.TEMP_DIR) without
# false-positiving on the legitimate DUBBING_TEMP_DIR.
FORBIDDEN_BARE_TEMP_DIR = re.compile(r"(?<!DUBBING_)\bTEMP_DIR\b")

ALLOWED_UTILS_IMPORT = "utils.ffmpeg_utils"
ALLOWED_UTILS_NAMES = {"run_ffmpeg", "get_video_duration", "get_video_info", "get_audio_tracks"}


def _python_files():
    return [
        p for p in DUBBING_ROOT.rglob("*.py")
        if "migrations" not in p.parts
    ]


def test_media_modules_only_import_approved_ffmpeg_utils_functions():
    media_dir = DUBBING_ROOT / "media"
    violations = []
    for path in media_dir.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == ALLOWED_UTILS_IMPORT:
                imported = {alias.name for alias in node.names}
                extra = imported - ALLOWED_UTILS_NAMES
                if extra:
                    violations.append(f"{path}: imports non-approved names {extra} from {ALLOWED_UTILS_IMPORT}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    if name and (name == "utils" or name.startswith("utils.")) and name != ALLOWED_UTILS_IMPORT:
                        violations.append(f"{path}: imports forbidden module '{name}'")
                    if name and (name == "handlers" or name.startswith("handlers.")):
                        violations.append(f"{path}: imports forbidden module '{name}'")
    assert not violations, "\n".join(violations)


def _step2_implementation_files():
    """Only the new Step 2 media/ implementation files — not tests (which
    legitimately name these strings in assertions/docs) and not Step 1
    files (already covered by test_isolation.py)."""
    return list((DUBBING_ROOT / "media").glob("*.py"))


def test_no_forbidden_string_references_step2():
    violations = []
    for path in _step2_implementation_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            code_part = line.split("#", 1)[0]
            for forbidden in FORBIDDEN_STRINGS:
                if forbidden in code_part:
                    violations.append(f"{path}:{lineno}: references '{forbidden}'")
            if FORBIDDEN_BARE_TEMP_DIR.search(code_part):
                violations.append(f"{path}:{lineno}: references bare 'TEMP_DIR'")
    assert not violations, "Forbidden references found:\n" + "\n".join(violations)


def test_existing_ffmpeg_utils_still_importable_and_functional(tmp_path):
    """Smoke test: the shared dependency utils.ffmpeg_utils must still work
    exactly as before, proving Step 2 didn't break it by import side-effects."""
    from dubbing.tests.fixtures.synthetic_media import make_valid_video_with_audio
    from utils.ffmpeg_utils import get_video_duration

    video_path = str(tmp_path / "smoke.mp4")
    make_valid_video_with_audio(video_path, duration_sec=1.0)

    duration = get_video_duration(video_path)
    assert duration > 0
