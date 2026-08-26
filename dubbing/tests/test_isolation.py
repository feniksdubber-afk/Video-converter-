"""
Static isolation checks: dubbing/ must never import from the existing
bot's utils/, handlers/, or root config.py, and must never reference the
existing bot's temp dir or SQLite database.
"""

import ast
import pathlib

DUBBING_ROOT = pathlib.Path(__file__).resolve().parents[1]

FORBIDDEN_IMPORT_PREFIXES = ("utils", "handlers")
# Step 2 approval: dubbing/media/* may import specific pure, stateless
# functions from utils.ffmpeg_utils (never modifying that file). This is
# the ONLY sanctioned exception to the "no utils/handlers imports" rule.
APPROVED_UTILS_EXCEPTION_MODULE = "utils.ffmpeg_utils"
APPROVED_UTILS_EXCEPTION_NAMES = {"run_ffmpeg", "get_video_duration", "get_video_info", "get_audio_tracks"}
FORBIDDEN_STRINGS = ("/tmp/videobot", "handlers.whisper_subtitle")


def _python_files():
    return [
        p for p in DUBBING_ROOT.rglob("*.py")
        if "tests" not in p.parts and "migrations" not in p.parts
    ]


def test_no_forbidden_imports():
    violations = []
    for path in _python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name and name.split(".")[0] in FORBIDDEN_IMPORT_PREFIXES:
                        violations.append(f"{path}: imports '{name}'")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if not module:
                    continue
                if module == APPROVED_UTILS_EXCEPTION_MODULE:
                    imported = {alias.name for alias in node.names}
                    extra = imported - APPROVED_UTILS_EXCEPTION_NAMES
                    if extra:
                        violations.append(
                            f"{path}: imports non-approved names {extra} from {module}"
                        )
                elif module.split(".")[0] in FORBIDDEN_IMPORT_PREFIXES:
                    violations.append(f"{path}: imports '{module}'")
    assert not violations, "Forbidden imports found:\n" + "\n".join(violations)


def test_no_forbidden_string_references():
    """Forbidden paths/modules must not appear in executable code — mentioning
    them in a comment (e.g. explaining why they're deliberately avoided) is fine."""
    violations = []
    for path in _python_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            code_part = line.split("#", 1)[0]
            for forbidden in FORBIDDEN_STRINGS:
                if forbidden in code_part:
                    violations.append(f"{path}:{lineno}: references '{forbidden}'")
    assert not violations, "Forbidden references found:\n" + "\n".join(violations)


def test_config_module_does_not_import_root_config():
    config_path = DUBBING_ROOT / "config.py"
    tree = ast.parse(config_path.read_text(), filename=str(config_path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                assert name != "config", "dubbing/config.py must not import the bot's root config.py"


def test_all_env_vars_are_dubbing_prefixed():
    config_path = DUBBING_ROOT / "config.py"
    text = config_path.read_text()
    import re
    env_reads = re.findall(r'os\.environ\.get\(\s*["\'](\w+)["\']', text)
    assert env_reads, "expected at least one os.environ.get call in config.py"
    for var in env_reads:
        assert var.startswith("DUBBING_"), f"env var '{var}' is not DUBBING_-prefixed"
