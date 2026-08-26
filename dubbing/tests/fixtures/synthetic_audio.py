"""
dubbing/tests/fixtures/synthetic_audio.py — segmentation testlari uchun
sintetik audio (WAV) fayllarni ffmpeg lavfi orqali ishlab chiqaradi.
Repozitoriyga hech qanday binary fayl saqlanmaydi — har safar testda
generatsiya qilinadi.
"""

from __future__ import annotations

import subprocess


def make_tone_then_silence_then_tone_wav(
    output_path: str,
    tone_sec: float = 1.0,
    silence_sec: float = 2.0,
    sample_rate: int = 16000,
) -> float:
    """
    <tone_sec> ohang + <silence_sec> chinakam raqamli sukunat + <tone_sec>
    ohang'dan iborat mono WAV yaratadi. Umumiy davomiylikni (soniyalarda)
    qaytaradi.
    """
    total_sec = tone_sec * 2 + silence_sec
    filter_complex = (
        f"sine=frequency=440:duration={tone_sec}[a];"
        f"anullsrc=r={sample_rate}:cl=mono:duration={silence_sec}[b];"
        f"sine=frequency=440:duration={tone_sec}[c];"
        f"[a][b][c]concat=n=3:v=0:a=1[out]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={tone_sec}:sample_rate={sample_rate}",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono:duration={silence_sec}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={tone_sec}:sample_rate={sample_rate}",
        "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
        "-map", "[out]",
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    return total_sec


def make_pure_silence_wav(output_path: str, duration_sec: float = 2.0, sample_rate: int = 16000) -> float:
    """Boshidan oxirigacha chinakam raqamli sukunat bo'lgan mono WAV yaratadi."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono:duration={duration_sec}",
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    return duration_sec


def make_pure_tone_wav(output_path: str, duration_sec: float = 2.0, sample_rate: int = 16000) -> float:
    """Boshidan oxirigacha uzluksiz ohang (sukunatsiz) bo'lgan mono WAV yaratadi."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_sec}:sample_rate={sample_rate}",
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    return duration_sec


def make_video_with_tone_then_silence(
    output_path: str,
    tone_sec: float = 1.0,
    silence_sec: float = 2.0,
) -> float:
    """
    Segmenter end-to-end testi uchun: video + (ohang, sukunat, ohang) audio
    bilan kichik mp4. `source_path` sifatida ishlatiladi — segmenter shu
    fayldan qayta WAV ekstraktsiya qilishi kerak (Option A).
    """
    total_sec = tone_sec * 2 + silence_sec
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={total_sec}:size=320x240:rate=15",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={tone_sec}",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:duration={silence_sec}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={tone_sec}",
        "-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    return total_sec
