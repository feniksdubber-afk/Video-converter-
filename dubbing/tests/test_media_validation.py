import os

import pytest

from dubbing.media.validation import InvalidInputMediaError, validate_input_file


def test_missing_file_rejected(tmp_path):
    with pytest.raises(InvalidInputMediaError, match="topilmadi"):
        validate_input_file(str(tmp_path / "does_not_exist.mp4"))


def test_empty_file_rejected(tmp_path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    with pytest.raises(InvalidInputMediaError, match="bo'sh"):
        validate_input_file(str(p))


def test_disallowed_extension_rejected(tmp_path):
    p = tmp_path / "video.xyz"
    p.write_bytes(b"some bytes")
    with pytest.raises(InvalidInputMediaError, match="kengaytmasi"):
        validate_input_file(str(p))


def test_oversized_file_rejected(tmp_path, monkeypatch):
    import dubbing.media.validation as validation_mod
    monkeypatch.setattr(validation_mod, "DUBBING_MAX_INPUT_BYTES", 10)
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    with pytest.raises(InvalidInputMediaError, match="chegaradan katta"):
        validate_input_file(str(p))


def test_valid_file_passes(tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    validate_input_file(str(p))  # should not raise
