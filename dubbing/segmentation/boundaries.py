"""
dubbing/segmentation/boundaries.py — sukunat oraliqlaridan deterministik
segment ro'yxati qurish.

Pure functions: tashqi holatga (fayl tizimi, DB, subprocess) bog'liq emas —
faqat `duration_sec` va sukunat oraliqlari ro'yxatini oladi, segment
ro'yxatini qaytaradi. Shuning uchun sinov qilish oson va tezkor.

QOIDALAR (chaqiruvchi — dubbing.segmentation.segmenter — talab qiladi):
    - nol-asosli, ketma-ket indekslar (index 0, 1, 2, ...)
    - xronologik tartib
    - overlap yo'q, gap yo'q — [0, duration_sec] to'liq qoplanadi
    - vaqt belgilari 3 xonali aniqlikda (millisekund)
    - deterministik: bir xil kirish → bir xil chiqish
    - qisqa segmentlar (< min_segment_sec) qo'shni segmentga birlashtiriladi
      (avval bir xil turdagi qo'shniga, aks holda kattaroq qo'shniga)
    - uzun segmentlar (> max_segment_sec) rekursiv ravishda o'rtadan
      bo'linadi, har ikki bo'lak ham max_segment_sec dan oshmaguncha
"""

from __future__ import annotations

from typing import Iterable, List, Tuple, TypedDict


class SegmentDict(TypedDict):
    index: int
    start_sec: float
    end_sec: float
    kind: str


class SegmentationError(ValueError):
    """Segment ro'yxati talab qilingan invariantlarni buzdi (ichki xato)."""


def _round3(value: float) -> float:
    return round(value, 3)


def build_raw_segments(
    duration_sec: float,
    silence_intervals: Iterable[Tuple[float, float]],
) -> List[dict]:
    """
    Sukunat oraliqlaridan boshlab, [0, duration_sec] ni to'liq qoplaydigan,
    almashinuvchi speech/silence segmentlar ro'yxatini quradi.

    `silence_intervals` chaqiruvchi tomonidan allaqachon saralangan,
    bir-biriga ustma-ust tushmaydigan va [0, duration_sec] ichiga
    qisqartirilgan deb faraz qilinadi (dubbing.segmentation.vad shu
    kafolatni beradi).
    """
    segments: List[dict] = []
    cursor = 0.0

    for start, end in silence_intervals:
        if start > cursor:
            segments.append({"start": cursor, "end": start, "kind": "speech"})
        if end > start:
            segments.append({"start": start, "end": end, "kind": "silence"})
        cursor = max(cursor, end)

    if cursor < duration_sec:
        segments.append({"start": cursor, "end": duration_sec, "kind": "speech"})

    if not segments:
        # Sukunat topilmadi va duration_sec <= 0 emas — butun fayl bitta
        # speech segment sifatida qaraladi.
        segments.append({"start": 0.0, "end": duration_sec, "kind": "speech"})

    return segments


def merge_short_segments(segments: List[dict], min_segment_sec: float) -> List[dict]:
    """
    `min_segment_sec` dan qisqa segmentlarni qo'shni segmentga birlashtiradi:
    avval bir xil turdagi ('kind') qo'shniga, agar bunday qo'shni bo'lmasa —
    ikkala qo'shnidan kattarog'iga (teng bo'lsa — chapdagiga, deterministik
    tie-break). Yagona segment qolguncha yoki barcha segmentlar
    min_segment_sec dan uzun bo'lguncha davom etadi.
    """
    segs = [dict(s) for s in segments]

    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, seg in enumerate(segs):
            duration = seg["end"] - seg["start"]
            if duration >= min_segment_sec:
                continue

            left = segs[i - 1] if i > 0 else None
            right = segs[i + 1] if i < len(segs) - 1 else None

            if left is not None and left["kind"] == seg["kind"]:
                merge_into = "left"
            elif right is not None and right["kind"] == seg["kind"]:
                merge_into = "right"
            else:
                left_dur = (left["end"] - left["start"]) if left is not None else -1.0
                right_dur = (right["end"] - right["start"]) if right is not None else -1.0
                merge_into = "left" if left_dur >= right_dur else "right"

            if merge_into == "left":
                left["end"] = seg["end"]
            else:
                right["start"] = seg["start"]

            del segs[i]
            changed = True
            break  # ro'yxat o'zgardi — boshidan qayta skanerlash

    return segs


def split_long_segments(segments: List[dict], max_segment_sec: float) -> List[dict]:
    """
    `max_segment_sec` dan uzun segmentlarni rekursiv ravishda o'rtadan
    (deterministik) bo'lib chiqadi, har bir bo'lak max_segment_sec dan
    oshmaguncha. `kind` har ikki bo'lakka ham meros bo'lib o'tadi.
    """
    result: List[dict] = []
    for seg in segments:
        result.extend(_split_one(seg, max_segment_sec))
    return result


def _split_one(segment: dict, max_segment_sec: float) -> List[dict]:
    duration = segment["end"] - segment["start"]
    if duration <= max_segment_sec:
        return [segment]

    midpoint = (segment["start"] + segment["end"]) / 2.0
    left = {"start": segment["start"], "end": midpoint, "kind": segment["kind"]}
    right = {"start": midpoint, "end": segment["end"], "kind": segment["kind"]}
    return _split_one(left, max_segment_sec) + _split_one(right, max_segment_sec)


def finalize_segments(segments: List[dict]) -> List[SegmentDict]:
    """
    Vaqt belgilarini 3 xonali aniqlikka yaxlitlaydi (barcha kesish
    nuqtalarini bitta ro'yxat sifatida yaxlitlab, keyin segmentlarni shu
    yaxlitlangan nuqtalardan qayta quradi — bu yaxlitlash tufayli gap/
    overlap paydo bo'lishining oldini oladi), indekslarni tayinlaydi va
    invariantlarni tekshiradi. Buzilish topilsa SegmentationError
    ko'taradi (ichki xato — chaqiruvchi kodni ko'rsatadi).
    """
    if not segments:
        raise SegmentationError("bo'sh segment ro'yxati")

    breakpoints = [segments[0]["start"]] + [s["end"] for s in segments]
    rounded = [_round3(b) for b in breakpoints]

    result: List[SegmentDict] = []
    for i, seg in enumerate(segments):
        result.append(
            {
                "index": i,
                "start_sec": rounded[i],
                "end_sec": rounded[i + 1],
                "kind": seg["kind"],
            }
        )

    if result[0]["start_sec"] != 0.0:
        raise SegmentationError(
            f"birinchi segment 0.0 dan boshlanmayapti: {result[0]['start_sec']}"
        )

    for i in range(len(result) - 1):
        if result[i]["end_sec"] != result[i + 1]["start_sec"]:
            raise SegmentationError(
                f"segment {i} va {i + 1} orasida gap yoki overlap: "
                f"{result[i]['end_sec']} != {result[i + 1]['start_sec']}"
            )
        if result[i]["end_sec"] < result[i]["start_sec"]:
            raise SegmentationError(f"segment {i} manfiy davomiylikka ega")

    return result


def segment_audio(
    duration_sec: float,
    silence_intervals: Iterable[Tuple[float, float]],
    min_segment_sec: float,
    max_segment_sec: float,
) -> List[SegmentDict]:
    """
    To'liq segmentatsiya quvuri: raw segmentlar → qisqalarni birlashtirish
    → uzunlarni bo'lish → yaxlitlash/indekslash/tekshirish.

    Deterministik: bir xil kirishlar uchun har doim bir xil chiqish
    (segment ro'yxati) qaytaradi — bu ArtifactStore content-hash bilan
    keshlash to'g'ri ishlashi uchun zarur.
    """
    if duration_sec <= 0:
        raise SegmentationError(f"yaroqsiz duration_sec: {duration_sec}")

    raw = build_raw_segments(duration_sec, silence_intervals)
    merged = merge_short_segments(raw, min_segment_sec)
    split = split_long_segments(merged, max_segment_sec)
    return finalize_segments(split)
