"""
dubbing.segmentation.vad uchun testlar — real ffmpeg'ga qarshi ishlaydi
(chunki bu modulning butun maqsadi ffmpeg silencedetect'ni to'g'ri
chaqirish va uning stderr chiqishini tahlil qilish).
"""

import subprocess

import pytest

from dubbing.segmentation.vad import (
    SilenceDetectionError,
    _parse_silence_intervals,
    detect_silence_intervals,
)
from dubbing.tests.fixtures.synthetic_audio import (
    make_pure_silence_wav,
    make_pure_tone_wav,
    make_tone_then_silence_then_tone_wav,
)


def test_detects_silence_in_middle_of_tone(tmp_path):
    path = str(tmp_path / "tone_silence_tone.wav")
    duration = make_tone_then_silence_then_tone_wav(path, tone_sec=1.0, silence_sec=2.0)

    intervals = detect_silence_intervals(
        path, duration_sec=duration, threshold_db=-35, min_silence_duration_sec=0.5, timeout_seconds=30,
    )

    assert len(intervals) == 1
    start, end = intervals[0]
    assert 0.9 <= start <= 1.1
    assert 2.9 <= end <= 3.1


def test_pure_silence_detected_as_one_interval_spanning_whole_file(tmp_path):
    path = str(tmp_path / "silence.wav")
    duration = make_pure_silence_wav(path, duration_sec=2.0)

    intervals = detect_silence_intervals(
        path, duration_sec=duration, threshold_db=-35, min_silence_duration_sec=0.5, timeout_seconds=30,
    )

    assert len(intervals) == 1
    start, end = intervals[0]
    assert start == 0.0
    assert end == duration


def test_pure_tone_has_no_silence_intervals(tmp_path):
    path = str(tmp_path / "tone.wav")
    duration = make_pure_tone_wav(path, duration_sec=2.0)

    intervals = detect_silence_intervals(
        path, duration_sec=duration, threshold_db=-35, min_silence_duration_sec=0.5, timeout_seconds=30,
    )

    assert intervals == []


def test_detection_is_deterministic_across_runs(tmp_path):
    path = str(tmp_path / "tone_silence_tone.wav")
    duration = make_tone_then_silence_then_tone_wav(path, tone_sec=1.0, silence_sec=2.0)

    first = detect_silence_intervals(
        path, duration_sec=duration, threshold_db=-35, min_silence_duration_sec=0.5, timeout_seconds=30,
    )
    second = detect_silence_intervals(
        path, duration_sec=duration, threshold_db=-35, min_silence_duration_sec=0.5, timeout_seconds=30,
    )
    assert first == second


def test_missing_input_file_raises_silence_detection_error(tmp_path):
    missing = str(tmp_path / "does_not_exist.wav")
    with pytest.raises(SilenceDetectionError):
        detect_silence_intervals(
            missing, duration_sec=1.0, threshold_db=-35, min_silence_duration_sec=0.5, timeout_seconds=30,
        )


def test_intervals_are_clipped_to_duration_and_sorted():
    # Trailing unmatched silence_start closed at duration_sec; out-of-range
    # values clipped.
    stderr_text = (
        "[silencedetect @ 0x1] silence_start: -0.5\n"
        "[silencedetect @ 0x1] silence_end: 1.0 | silence_duration: 1.5\n"
        "[silencedetect @ 0x1] silence_start: 8.0\n"
    )
    intervals = _parse_silence_intervals(stderr_text, duration_sec=10.0)
    assert intervals == [(0.0, 1.0), (8.0, 10.0)]


def test_parses_realistic_multiline_stderr_with_extra_log_noise():
    stderr_text = (
        "ffmpeg version 6.1.1 Copyright (c) 2000-2023\n"
        "Input #0, wav, from 'x.wav':\n"
        "  Duration: 00:00:04.00, bitrate: 256 kb/s\n"
        "[silencedetect @ 0x55f] silence_start: 1.000021\n"
        "[silencedetect @ 0x55f] silence_end: 3.00006 | silence_duration: 2.000039\n"
        "size=N/A time=00:00:04.00 bitrate=N/A speed=...\n"
    )
    intervals = _parse_silence_intervals(stderr_text, duration_sec=4.0)
    assert len(intervals) == 1
    start, end = intervals[0]
    assert abs(start - 1.000021) < 1e-6
    assert abs(end - 3.00006) < 1e-6
