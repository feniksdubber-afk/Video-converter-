"""
dubbing/media/probe.py — ffprobe orqali media metadata olish.

IZOLYATSIYA: bu modul utils.ffmpeg_utils'dan FAQAT quyidagi sof
(bot holatiga bog'liq bo'lmagan) funksiyalarni import qiladi:

    get_video_duration, get_video_info, get_audio_tracks

Bu funksiyalar sinxron (subprocess.run asosida) bo'lgani uchun, event
loop'ni bloklamaslik uchun executor'da ishga tushiriladi. utils.ffmpeg_utils
faylining o'zi HECH QANDAY o'zgartirilmaydi — faqat import qilinadi.

Botning async ffmpeg runneri, uning vaqtinchalik-fayl helperi va navbat/
task-boshqaruv modullari bu yerda ISHLATILMAYDI.
"""

from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass, field
from typing import Any

from utils.ffmpeg_utils import get_audio_tracks, get_video_duration, get_video_info


@dataclass(frozen=True)
class ProbeResult:
    duration_sec: float
    width: int
    height: int
    vcodec: str
    audio_tracks: list[dict] = field(default_factory=list)

    def to_params_dict(self) -> dict[str, Any]:
        """Artifact hashing/registratsiya uchun barqaror, JSON-mos dict."""
        return {
            "duration_sec": self.duration_sec,
            "width": self.width,
            "height": self.height,
            "vcodec": self.vcodec,
            "audio_track_count": len(self.audio_tracks),
            "audio_tracks": self.audio_tracks,
        }


async def probe_media(input_path: str) -> ProbeResult:
    loop = asyncio.get_running_loop()

    duration = await loop.run_in_executor(None, functools.partial(get_video_duration, input_path))
    info = await loop.run_in_executor(None, functools.partial(get_video_info, input_path))
    tracks = await loop.run_in_executor(None, functools.partial(get_audio_tracks, input_path))

    width = int(info.get("width", 0) or 0)
    height = int(info.get("height", 0) or 0)
    vcodec = info.get("codec_name", "")

    return ProbeResult(
        duration_sec=duration,
        width=width,
        height=height,
        vcodec=vcodec,
        audio_tracks=tracks,
    )
