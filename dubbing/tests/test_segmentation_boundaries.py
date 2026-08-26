"""
dubbing.segmentation.boundaries uchun pure-function testlar. Fayl tizimi,
DB yoki subprocess kerak emas — tez, deterministik unit testlar.
"""

import pytest

from dubbing.segmentation.boundaries import (
    SegmentationError,
    build_raw_segments,
    merge_short_segments,
    segment_audio,
    split_long_segments,
)


def _assert_contiguous_full_coverage(segments, duration_sec):
    assert segments[0]["start_sec"] == 0.0
    assert segments[-1]["end_sec"] == round(duration_sec, 3)
    for i in range(len(segments) - 1):
        assert segments[i]["end_sec"] == segments[i + 1]["start_sec"]
    for i, seg in enumerate(segments):
        assert seg["index"] == i


def test_raw_segments_alternate_and_cover_full_timeline():
    raw = build_raw_segments(10.0, [(2.0, 4.0), (6.0, 7.0)])
    assert [s["kind"] for s in raw] == ["speech", "silence", "speech", "silence", "speech"]
    assert raw[0]["start"] == 0.0
    assert raw[-1]["end"] == 10.0


def test_raw_segments_no_silence_is_single_speech_segment():
    raw = build_raw_segments(5.0, [])
    assert raw == [{"start": 0.0, "end": 5.0, "kind": "speech"}]


def test_raw_segments_all_silence():
    raw = build_raw_segments(5.0, [(0.0, 5.0)])
    assert raw == [{"start": 0.0, "end": 5.0, "kind": "silence"}]


def test_short_segment_merges_into_same_kind_neighbor():
    # speech(0-2) silence(2-2.1) silence(2.1-5) -> the tiny 2-2.1 silence
    # should merge with the adjacent silence (same kind), not the speech.
    segs = [
        {"start": 0.0, "end": 2.0, "kind": "speech"},
        {"start": 2.0, "end": 2.1, "kind": "silence"},
        {"start": 2.1, "end": 5.0, "kind": "silence"},
    ]
    merged = merge_short_segments(segs, min_segment_sec=0.3)
    assert merged == [
        {"start": 0.0, "end": 2.0, "kind": "speech"},
        {"start": 2.0, "end": 5.0, "kind": "silence"},
    ]


def test_short_segment_merges_into_larger_neighbor_when_no_same_kind():
    # speech(0-5) silence(5-5.1) speech(5.1-6) -> tiny silence has no
    # same-kind neighbor; merges into the larger neighbor (left, 5.0 > 0.9).
    segs = [
        {"start": 0.0, "end": 5.0, "kind": "speech"},
        {"start": 5.0, "end": 5.1, "kind": "silence"},
        {"start": 5.1, "end": 6.0, "kind": "speech"},
    ]
    merged = merge_short_segments(segs, min_segment_sec=0.3)
    assert merged == [
        {"start": 0.0, "end": 5.1, "kind": "speech"},
        {"start": 5.1, "end": 6.0, "kind": "speech"},
    ]


def test_cascading_short_merges_resolve_to_stable_result():
    # Several tiny fragments in a row must all resolve without error.
    segs = [
        {"start": 0.0, "end": 0.1, "kind": "speech"},
        {"start": 0.1, "end": 0.15, "kind": "silence"},
        {"start": 0.15, "end": 10.0, "kind": "speech"},
    ]
    merged = merge_short_segments(segs, min_segment_sec=0.3)
    assert len(merged) == 1
    assert merged[0]["start"] == 0.0
    assert merged[0]["end"] == 10.0


def test_long_segment_is_force_split_at_midpoint_recursively():
    segs = [{"start": 0.0, "end": 50.0, "kind": "speech"}]
    split = split_long_segments(segs, max_segment_sec=20.0)
    assert len(split) == 4
    for s in split:
        assert (s["end"] - s["start"]) <= 20.0
    # Contiguous.
    assert split[0]["start"] == 0.0
    assert split[-1]["end"] == 50.0
    for i in range(len(split) - 1):
        assert split[i]["end"] == split[i + 1]["start"]


def test_short_segment_within_limit_is_not_split():
    segs = [{"start": 0.0, "end": 5.0, "kind": "speech"}]
    split = split_long_segments(segs, max_segment_sec=20.0)
    assert split == segs


def test_segment_audio_end_to_end_no_overlap_no_gap_full_coverage():
    segments = segment_audio(
        duration_sec=30.0,
        silence_intervals=[(5.0, 5.05), (10.0, 12.0)],
        min_segment_sec=0.3,
        max_segment_sec=20.0,
    )
    _assert_contiguous_full_coverage(segments, 30.0)
    # The tiny 5.0-5.05 silence should have been merged away (< min_segment_sec).
    assert all((s["end_sec"] - s["start_sec"]) >= 0.3 - 1e-9 for s in segments)


def test_segment_audio_is_deterministic():
    args = dict(
        duration_sec=17.234,
        silence_intervals=[(3.111, 4.222), (9.0, 9.5)],
        min_segment_sec=0.3,
        max_segment_sec=20.0,
    )
    first = segment_audio(**args)
    second = segment_audio(**args)
    assert first == second


def test_segment_audio_enforces_max_duration_even_after_merge():
    # A run of tiny silences inside a long speech run should not prevent
    # the max-duration split from applying to the merged result.
    segments = segment_audio(
        duration_sec=45.0,
        silence_intervals=[(10.0, 10.05), (20.0, 20.05)],
        min_segment_sec=0.3,
        max_segment_sec=20.0,
    )
    _assert_contiguous_full_coverage(segments, 45.0)
    for s in segments:
        assert (s["end_sec"] - s["start_sec"]) <= 20.0 + 1e-9


def test_segment_audio_rejects_non_positive_duration():
    with pytest.raises(SegmentationError):
        segment_audio(duration_sec=0.0, silence_intervals=[], min_segment_sec=0.3, max_segment_sec=20.0)
    with pytest.raises(SegmentationError):
        segment_audio(duration_sec=-1.0, silence_intervals=[], min_segment_sec=0.3, max_segment_sec=20.0)


def test_timestamps_rounded_to_three_decimals():
    segments = segment_audio(
        duration_sec=4.0,
        silence_intervals=[(1.0, 3.00006)],
        min_segment_sec=0.3,
        max_segment_sec=20.0,
    )
    for s in segments:
        assert s["start_sec"] == round(s["start_sec"], 3)
        assert s["end_sec"] == round(s["end_sec"], 3)
