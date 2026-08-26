"""
dubbing/tests/fixtures/synthetic_media.py — sinov uchun sintetik media
fayllarni ffmpeg lavfi orqali ishlab chiqaradi. Repozitoriyga hech qanday
binary fayl saqlanmaydi — har safar testda generatsiya qilinadi.
"""

from __future__ import annotations

import subprocess


def make_valid_video_with_audio(output_path: str, duration_sec: float = 2.0) -> None:
    """2 soniyalik rangli video + sinusoidal audio bilan kichik mp4 yaratadi."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration_sec}:size=320x240:rate=15",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_sec}",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60, check=True)


def make_video_without_audio(output_path: str, duration_sec: float = 2.0) -> None:
    """Faqat video, audio track'siz mp4 yaratadi (hard-fail testi uchun)."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration_sec}:size=320x240:rate=15",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-an",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60, check=True)


def make_corrupt_file(output_path: str) -> None:
    """Yaroqsiz/buzilgan 'video' fayl — validatsiya bosqichida ushlanishi kerak."""
    with open(output_path, "wb") as f:
        f.write(b"this is not a real video file, just garbage bytes")
