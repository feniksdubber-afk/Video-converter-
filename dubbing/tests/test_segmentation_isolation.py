"""
Step 3 segmentation — izolyatsiya va regressiya tekshiruvlari.

Umumiy tekshiruvlar (forbidden imports, DUBBING_ prefiks, va h.k.) allaqachon
test_isolation.py'da butun `dubbing/` daraxti bo'yicha amalga oshiriladi —
bu fayl Step 3'ga xos qo'shimcha kafolatlarni qo'shadi:

    1. `dubbing/segmentation/*.py` faqat `utils.ffmpeg_utils`dan tasdiqlangan
       nomlarni import qiladi (agar umuman import qilsa).
    2. `dubbing/segmentation/vad.py` `utils.ffmpeg_utils`dan HECH NARSA
       import qilmaydi — u to'g'ridan-to'g'ri subprocess ishlatadi
       (dizayn qarori, modul docstring'ida tushuntirilgan).
    3. Step 1/2'da taqiqlangan satrlar (bare TEMP_DIR, run_ffmpeg_async,
       make_temp_path, utils.task_queue, utils.task_manager,
       handlers.whisper_subtitle) Step 3 kodida ham yo'q.
    4. Step 3 yangi config o'zgaruvchilari (`DUBBING_MIN_SEGMENT_SEC`,
       `DUBBING_MAX_SEGMENT_SEC`, `DUBBING_SEGMENTATION_TIMEOUT_SECONDS`,
       `DUBBING_SILENCE_THRESHOLD_DB`, `DUBBING_SILENCE_MIN_DURATION_SEC`)
       dubbing/config.py'da mavjud va DUBBING_ prefiksli.
    5. Segmentation hech qanday yangi Python paketini import qilmaydi
       (torch, librosa, numpy, scipy, webrtcvad, silero, onnxruntime).
"""

import ast
import pathlib
import re

DUBBING_ROOT = pathlib.Path(__file__).resolve().parents[1]
SEGMENTATION_DIR = DUBBING_ROOT / "segmentation"

ALLOWED_UTILS_IMPORT = "utils.ffmpeg_utils"
ALLOWED_UTILS_NAMES = {"run_ffmpeg", "get_video_duration", "get_video_info", "get_audio_tracks"}

FORBIDDEN_STRINGS = (
    "/tmp/videobot",
    "utils.task_queue",
    "utils.task_manager",
    "handlers.whisper_subtitle",
    "make_temp_path",
    "run_ffmpeg_async",
)
FORBIDDEN_BARE_TEMP_DIR = re.compile(r"(?<!DUBBING_)\bTEMP_DIR\b")

FORBIDDEN_NEW_DEPENDENCIES = (
    "torch",
    "librosa",
    "numpy",
    "scipy",
    "webrtcvad",
    "silero",
    "onnxruntime",
)


def _segmentation_files():
    return sorted(SEGMENTATION_DIR.glob("*.py"))


def test_segmentation_files_exist():
    names = {p.name for p in _segmentation_files()}
    assert {"__init__.py", "vad.py", "boundaries.py", "segmenter.py"} <= names


def test_segmentation_only_imports_approved_ffmpeg_utils_functions():
    violations = []
    for path in _segmentation_files():
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


def test_vad_module_does_not_import_from_utils_at_all():
    """
    Design guarantee (see vad.py docstring): silence detection uses a
    direct, isolated ffmpeg subprocess call precisely because
    utils.ffmpeg_utils.run_ffmpeg's contract (stderr discarded on success)
    doesn't fit this need. vad.py must therefore import nothing from
    utils.* at all — WAV re-derivation (which does use run_ffmpeg) lives
    in segmenter.py instead.
    """
    vad_path = SEGMENTATION_DIR / "vad.py"
    tree = ast.parse(vad_path.read_text(), filename=str(vad_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("utils"), (
                    f"vad.py must not import from utils.*, found '{alias.name}'"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("utils"), (
                f"vad.py must not import from utils.*, found 'from {module} import ...'"
            )


def test_no_forbidden_string_references_in_segmentation():
    violations = []
    for path in _segmentation_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            code_part = line.split("#", 1)[0]
            for forbidden in FORBIDDEN_STRINGS:
                if forbidden in code_part:
                    violations.append(f"{path}:{lineno}: references '{forbidden}'")
            if FORBIDDEN_BARE_TEMP_DIR.search(code_part):
                violations.append(f"{path}:{lineno}: references bare 'TEMP_DIR'")
    assert not violations, "Forbidden references found:\n" + "\n".join(violations)


def test_no_new_heavy_dependencies_imported_anywhere_in_dubbing():
    """
    Step 3 was explicitly approved WITHOUT any new Python dependency
    (ffmpeg silencedetect only). This scans the whole dubbing/ tree, not
    just segmentation/, since a future stage accidentally adding one of
    these would be just as much a violation of this approval.
    """
    violations = []
    for path in DUBBING_ROOT.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".")[0]
                    if top_level in FORBIDDEN_NEW_DEPENDENCIES:
                        violations.append(f"{path}: imports forbidden dependency '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                top_level = module.split(".")[0]
                if top_level in FORBIDDEN_NEW_DEPENDENCIES:
                    violations.append(f"{path}: imports forbidden dependency '{module}'")
    assert not violations, "\n".join(violations)


def test_step3_config_vars_present_and_dubbing_prefixed():
    config_path = DUBBING_ROOT / "config.py"
    text = config_path.read_text()
    expected = (
        "DUBBING_MIN_SEGMENT_SEC",
        "DUBBING_MAX_SEGMENT_SEC",
        "DUBBING_SEGMENTATION_TIMEOUT_SECONDS",
        "DUBBING_SILENCE_THRESHOLD_DB",
        "DUBBING_SILENCE_MIN_DURATION_SEC",
    )
    for var in expected:
        assert var in text, f"expected {var} to be defined in dubbing/config.py"


def test_segmentation_module_does_not_import_media_ingestion():
    """
    Isolation guarantee from the approved plan: segmenter.py re-derives
    the working WAV independently rather than importing anything from
    dubbing.media.ingestion, so future changes to Step 2 cannot silently
    break Step 3 (and vice versa).
    """
    segmenter_path = SEGMENTATION_DIR / "segmenter.py"
    tree = ast.parse(segmenter_path.read_text(), filename=str(segmenter_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dubbing.media.ingestion":
            raise AssertionError("segmenter.py must not import from dubbing.media.ingestion")
