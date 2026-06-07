import subprocess
import os
import uuid
import asyncio
import re
import time
import functools
from config import TEMP_DIR


def sanitize_filename(name: str) -> str:
    """
    Fayl nomidagi bo'shliq, qavs va boshqa URL/S3 uchun xavfli belgilarni
    pastki chiziqqa almashtiradi. Kengaytma (extension) saqlanadi.
    Misol: "Kung Fu Panda [HEVC].mkv" → "Kung_Fu_Panda_HEVC_.mkv"
    """
    base, ext = os.path.splitext(name)
    base = re.sub(r"[^\w\-]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return (base or "file") + ext


def run_ffmpeg(args: list[str], timeout: int = 1800) -> tuple[bool, str]:
    cmd = ["ffmpeg", "-y"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return False, result.stderr[-2000:] if result.stderr else "Noma'lum xato"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Vaqt tugadi (30 daqiqa)"
    except FileNotFoundError:
        return False, "FFmpeg topilmadi."
    except Exception as e:
        return False, str(e)


async def run_ffmpeg_async(
    args: list[str],
    status_msg,
    label: str = "Ishlanmoqda",
    input_path: str = None,
    timeout: int = 1800,
) -> tuple[bool, str]:
    """FFmpeg ni async + progress foizi bilan ishlatadi."""
    cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-nostats"] + args
    duration_sec = 0.0
    if input_path:
        duration_sec = get_video_duration(input_path)

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        last_percent = -1
        stderr_chunks = []

        async def read_stderr():
            async for line in proc.stderr:
                stderr_chunks.append(line.decode(errors="replace"))

        stderr_task = asyncio.ensure_future(read_stderr())

        current_time = 0.0

        async def _read_stdout():
            nonlocal current_time, last_percent
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()

                m = re.search(r"out_time_ms=(\d+)", line)
                if m:
                    current_time = int(m.group(1)) / 1_000_000
                elif re.search(r"out_time=(\d+):(\d+):([\d.]+)", line):
                    mt = re.search(r"out_time=(\d+):(\d+):([\d.]+)", line)
                    h, mn, s = mt.group(1), mt.group(2), mt.group(3)
                    current_time = int(h) * 3600 + int(mn) * 60 + float(s)

                if duration_sec > 0:
                    percent = min(int(current_time / duration_sec * 100), 99)
                    if percent - last_percent >= 5:
                        last_percent = percent
                        bar = _progress_bar(percent)
                        try:
                            await status_msg.edit_text(
                                f"⚙️ *{label}...*\n\n{bar} `{percent}%`",
                                parse_mode="Markdown",
                            )
                        except Exception:
                            pass

        try:
            await asyncio.wait_for(_read_stdout(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"Vaqt tugadi ({timeout // 60} daqiqa)"

        await asyncio.wait_for(proc.wait(), timeout=30)
        await stderr_task
        stderr_text = "".join(stderr_chunks)

        if proc.returncode != 0:
            return False, stderr_text[-2000:] if stderr_text else "Noma'lum xato"

        # 100% tugadi
        try:
            await status_msg.edit_text(
                f"⚙️ *{label}...*\n\n{_progress_bar(100)} `100%`",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True, ""

    except FileNotFoundError:
        return False, "FFmpeg topilmadi."
    except Exception as e:
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return False, str(e)


def _progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}]"


def get_video_duration(input_path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def get_video_info(input_path: str) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,codec_name,r_frame_rate",
             "-of", "default=noprint_wrappers=1", input_path],
            capture_output=True, text=True, timeout=30,
        )
        info = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                info[key.strip()] = val.strip()
        return info
    except Exception:
        return {}


def make_temp_path(ext: str) -> str:
    return os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}.{ext}")


def _thread_count() -> str:
    try:
        import multiprocessing
        return str(multiprocessing.cpu_count())
    except Exception:
        return "0"


def convert_video(input_path: str, output_format: str) -> tuple[bool, str, str]:
    output_path = make_temp_path(output_format)
    threads = _thread_count()
    codec_map = {
        "mp4":  ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
        "mkv":  ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
                 "-c:a", "aac"],
        "avi":  ["-c:v", "libxvid", "-q:v", "5",
                 "-c:a", "libmp3lame", "-q:a", "4"],
        "mov":  ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
                 "-c:a", "aac", "-movflags", "+faststart"],
        "webm": ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4",
                 "-c:a", "libopus"],
        "flv":  ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
                 "-c:a", "aac"],
    }
    extra = codec_map.get(output_format, ["-c:v", "libx264", "-preset", "medium",
                                           "-crf", "23", "-c:a", "aac"])
    args = ["-i", input_path, "-threads", threads] + extra + [output_path]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


async def convert_video_async(input_path: str, output_format: str, status_msg) -> tuple[bool, str, str]:
    output_path = make_temp_path(output_format)
    threads = _thread_count()
    codec_map = {
        "mp4":  ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
        "mkv":  ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-c:a", "aac"],
        "avi":  ["-c:v", "libxvid", "-q:v", "5", "-c:a", "libmp3lame", "-q:a", "4"],
        "mov":  ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
                 "-c:a", "aac", "-movflags", "+faststart"],
        "webm": ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4", "-c:a", "libopus"],
        "flv":  ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-c:a", "aac"],
    }
    extra = codec_map.get(output_format, ["-c:v", "libx264", "-preset", "medium",
                                           "-crf", "23", "-c:a", "aac"])
    args = ["-i", input_path, "-threads", threads] + extra + [output_path]
    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label=f"{output_format.upper()} formatiga o'tkazilmoqda",
        input_path=input_path,
    )
    return ok, output_path, err


async def change_resolution_async(input_path: str, height: int, status_msg) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    args = [
        "-i", input_path,
        "-threads", threads,
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label=f"{height}p o'lchamiga o'zgartirilmoqda",
        input_path=input_path,
    )
    return ok, output_path, err


def change_resolution(input_path: str, height: int) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    args = [
        "-i", input_path,
        "-threads", threads,
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


async def compress_video_async(input_path: str, quality: str, status_msg) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    crf_map = {"high": "23", "medium": "28", "low": "35"}
    crf = crf_map.get(quality, "28")
    labels = {"high": "Yuqori sifatda siqilmoqda", "medium": "O'rtacha sifatda siqilmoqda", "low": "Past sifatda siqilmoqda"}
    args = [
        "-i", input_path,
        "-threads", threads,
        "-c:v", "libx264", "-preset", "medium", "-crf", crf,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label=labels.get(quality, "Siqilmoqda"),
        input_path=input_path,
    )
    return ok, output_path, err


def compress_video(input_path: str, quality: str) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    crf_map = {"high": "23", "medium": "28", "low": "35"}
    crf = crf_map.get(quality, "28")
    args = [
        "-i", input_path,
        "-threads", threads,
        "-c:v", "libx264", "-preset", "medium", "-crf", crf,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def trim_video(input_path: str, start: str, end: str) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    args = [
        "-ss", start, "-to", end,
        "-i", input_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def remove_audio(input_path: str) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    args = ["-i", input_path, "-c:v", "copy", "-an", output_path]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def video_to_audio(input_path: str, audio_format: str) -> tuple[bool, str, str]:
    output_path = make_temp_path(audio_format)
    threads = _thread_count()
    codec_map = {
        "mp3":  ["-c:a", "libmp3lame", "-q:a", "2"],
        "aac":  ["-c:a", "aac", "-b:a", "192k"],
        "ogg":  ["-c:a", "libvorbis", "-q:a", "5"],
        "wav":  ["-c:a", "pcm_s16le"],
        "flac": ["-c:a", "flac"],
    }
    extra = codec_map.get(audio_format, ["-c:a", "libmp3lame"])
    args = ["-i", input_path, "-threads", threads, "-vn"] + extra + [output_path]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def take_screenshots(input_path: str, count: int) -> tuple[bool, list[str], str]:
    duration = get_video_duration(input_path)
    if duration <= 0:
        return False, [], "Video davomiyligini aniqlab bo'lmadi"
    paths = []
    interval = duration / (count + 1)
    err_msg = ""
    for i in range(count):
        timestamp = interval * (i + 1)
        output_path = make_temp_path("jpg")
        args = [
            "-ss", str(timestamp), "-i", input_path,
            "-frames:v", "1", "-q:v", "2",
            output_path,
        ]
        ok, err = run_ffmpeg(args, timeout=60)
        if ok:
            paths.append(output_path)
        else:
            err_msg = err
    return len(paths) > 0, paths, err_msg


def take_manual_shot(input_path: str, timestamp: str) -> tuple[bool, str, str]:
    output_path = make_temp_path("jpg")
    args = [
        "-ss", timestamp, "-i", input_path,
        "-frames:v", "1", "-q:v", "2",
        output_path,
    ]
    ok, err = run_ffmpeg(args, timeout=60)
    return ok, output_path, err


def softsub_video(video_path: str, subtitle_path: str) -> tuple[bool, str, str]:
    """Subtitle ni video stream sifatida birlashtiradi (qayta kodlash yo'q) → MKV."""
    output_path = make_temp_path("mkv")
    sub_ext = os.path.splitext(subtitle_path)[1].lower()
    sub_codec = "copy" if sub_ext in (".ass", ".ssa") else "srt"
    args = [
        "-i", video_path,
        "-i", subtitle_path,
        "-map", "0",
        "-map", "1",
        "-c", "copy",
        "-c:s", sub_codec,
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


# ── Yordamchi: video o'lchamini olish ────────────────────────────────────────

def get_video_resolution(input_path: str) -> tuple[int, int]:
    """(kenglik, balandlik) qaytaradi. Xato bo'lsa (0, 0)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                input_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        parts = result.stdout.strip().split(",")
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 0, 0


# ── Async executor wrappers (event loop bloklanmaydi) ────────────────────────

async def _run_in_executor(func, *args):
    """Sinxron funksiyani thread pool da ishlatadi — event loop bloklanmaydi."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args))


async def trim_video_async(
    input_path: str, start: str, end: str, status_msg=None
) -> tuple[bool, str, str]:
    """status_msg berilsa progress bar ko'rsatadi, aks holda thread pool ishlatadi."""
    if status_msg is None:
        return await _run_in_executor(trim_video, input_path, start, end)
    output_path = make_temp_path("mp4")
    args = [
        "-ss", start, "-to", end,
        "-i", input_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        args, status_msg, label="Video kesil moqda", input_path=input_path
    )
    return ok, output_path, err


async def remove_audio_async(input_path: str, status_msg=None) -> tuple[bool, str, str]:
    """status_msg berilsa progress bar ko'rsatadi."""
    if status_msg is None:
        return await _run_in_executor(remove_audio, input_path)
    output_path = make_temp_path("mp4")
    args = ["-i", input_path, "-c:v", "copy", "-an", output_path]
    ok, err = await run_ffmpeg_async(
        args, status_msg, label="Ovoz o'chirilmoqda", input_path=input_path
    )
    return ok, output_path, err


async def video_to_audio_async(
    input_path: str, audio_format: str, status_msg=None
) -> tuple[bool, str, str]:
    """status_msg berilsa progress bar ko'rsatadi."""
    if status_msg is None:
        return await _run_in_executor(video_to_audio, input_path, audio_format)
    output_path = make_temp_path(audio_format)
    threads = _thread_count()
    codec_map = {
        "mp3":  ["-c:a", "libmp3lame", "-q:a", "2"],
        "aac":  ["-c:a", "aac", "-b:a", "192k"],
        "ogg":  ["-c:a", "libvorbis", "-q:a", "5"],
        "wav":  ["-c:a", "pcm_s16le"],
        "flac": ["-c:a", "flac"],
    }
    extra = codec_map.get(audio_format, ["-c:a", "libmp3lame"])
    args = ["-i", input_path, "-threads", threads, "-vn"] + extra + [output_path]
    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label=f"{audio_format.upper()} ga o'tkazilmoqda",
        input_path=input_path,
    )
    return ok, output_path, err


async def take_screenshots_async(
    input_path: str, count: int, status_msg=None
) -> tuple[bool, list[str], str]:
    """status_msg berilsa har bir skrinsotdan keyin progress yangilanadi."""
    if status_msg is None:
        return await _run_in_executor(take_screenshots, input_path, count)
    duration = get_video_duration(input_path)
    if duration <= 0:
        return False, [], "Video davomiyligini aniqlab bo'lmadi"
    paths = []
    interval = duration / (count + 1)
    err_msg = ""
    loop = asyncio.get_event_loop()
    for i in range(count):
        timestamp = interval * (i + 1)
        pct = int(i / count * 100)
        bar = _progress_bar(pct)
        try:
            await status_msg.edit_text(
                f"⚙️ *Skrinsot olinmoqda...*\n\n{bar} `{i}/{count}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        output_path = make_temp_path("jpg")
        args = [
            "-ss", str(timestamp), "-i", input_path,
            "-frames:v", "1", "-q:v", "2", output_path,
        ]
        ok, err = await loop.run_in_executor(
            None, functools.partial(run_ffmpeg, args, 60)
        )
        if ok:
            paths.append(output_path)
        else:
            err_msg = err
    try:
        await status_msg.edit_text(
            f"⚙️ *Skrinsot olinmoqda...*\n\n{_progress_bar(100)} `{count}/{count}`",
            parse_mode="Markdown",
        )
    except Exception:
        pass
    return len(paths) > 0, paths, err_msg


async def take_manual_shot_async(
    input_path: str, timestamp: str, status_msg=None
) -> tuple[bool, str, str]:
    """status_msg berilsa holat xabari yangilanadi."""
    if status_msg is None:
        return await _run_in_executor(take_manual_shot, input_path, timestamp)
    output_path = make_temp_path("jpg")
    args = [
        "-ss", timestamp, "-i", input_path,
        "-frames:v", "1", "-q:v", "2", output_path,
    ]
    loop = asyncio.get_event_loop()
    ok, err = await loop.run_in_executor(
        None, functools.partial(run_ffmpeg, args, 60)
    )
    return ok, output_path, err


async def softsub_video_async(
    video_path: str, subtitle_path: str, status_msg=None
) -> tuple[bool, str, str]:
    """status_msg berilsa progress bar ko'rsatadi."""
    if status_msg is None:
        return await _run_in_executor(softsub_video, video_path, subtitle_path)
    output_path = make_temp_path("mkv")
    sub_ext = os.path.splitext(subtitle_path)[1].lower()
    sub_codec = "copy" if sub_ext in (".ass", ".ssa") else "srt"
    args = [
        "-i", video_path,
        "-i", subtitle_path,
        "-map", "0", "-map", "1",
        "-c", "copy",
        "-c:s", sub_codec,
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label="Subtitr birlashtirilmoqda",
        input_path=video_path,
    )
    return ok, output_path, err


async def downscale_for_telegram_async(
    input_path: str,
    target_height: int,
    status_msg,
) -> tuple[bool, str, str]:
    """Videoni target_height balandligiga tushiradi (Telegram uchun) → MP4.

    scale=-2:H — kengligi avtomatik, 2 ga bo'linadi (codec talabi).
    """
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    args = [
        "-i", input_path,
        "-threads", threads,
        "-vf", f"scale=-2:{target_height}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label=f"{target_height}p ga sifat tushirilmoqda",
        input_path=input_path,
    )
    return ok, output_path, err


async def hardsub_video_async(
    video_path: str,
    subtitle_path: str,
    font_size: int,
    status_msg,
    is_ass: bool = False,
) -> tuple[bool, str, str]:
    """Subtitle ni video kadrlariga yoqib qo'yadi (hardsub) → MP4."""
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    if is_ass:
        vf = f"subtitles='{escaped}'"
    else:
        force_style = (
            f"FontSize={font_size},"
            "Fontname=Arial,"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "Outline=2,"
            "Shadow=1,"
            "MarginV=20"
        )
        vf = f"subtitles='{escaped}':force_style='{force_style}'"

    args = [
        "-i", video_path,
        "-threads", threads,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = await run_ffmpeg_async(
        args, status_msg,
        label="Hardsub qo'shilmoqda",
        input_path=video_path,
    )
    return ok, output_path, err



async def convert_to_hls_async(
    input_path: str,
    output_dir: str,
    qualities: list[dict],
    status_msg=None,
) -> tuple[bool, str, str]:
    """
    Videoni HLS formatiga o'tkazadi (adaptive multi-quality).

    qualities misoli:
        [
            {"height": 360,  "bitrate": "800k",  "audio_bitrate": "96k"},
            {"height": 720,  "bitrate": "2800k", "audio_bitrate": "128k"},
            {"height": 1080, "bitrate": "5000k", "audio_bitrate": "192k"},
        ]

    Qaytaradi: (ok, master_m3u8_path, error_msg)
    Chiqish strukturasi:
        output_dir/
          master.m3u8
          stream_0/index.m3u8  stream_0/seg000.ts ...
          stream_1/index.m3u8  stream_1/seg000.ts ...
    """
    os.makedirs(output_dir, exist_ok=True)
    master_path = os.path.join(output_dir, "master.m3u8")

    # ── FFmpeg buyrug'ini quramiz ─────────────────────────────────────
    cmd = ["ffmpeg", "-i", input_path, "-y"]

    var_stream_map_parts: list[str] = []

    for i, q in enumerate(qualities):
        height = q["height"]
        vbitrate = q["bitrate"]
        abitrate = q.get("audio_bitrate", "96k")

        # Video va audio streamlarni map qilish
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]

        # Video filtr va codec
        cmd += [
            f"-vf:{i}",     f"scale=-2:{height}",
            f"-c:v:{i}",    "libx264",
            f"-b:v:{i}",    vbitrate,
            f"-maxrate:{i}", vbitrate,
            f"-bufsize:{i}", str(int(vbitrate.rstrip("k")) * 2) + "k",
            f"-preset:{i}", "veryfast",
            f"-profile:v:{i}", "main",
            f"-level:{i}",  "4.0",
        ]

        # Audio codec
        cmd += [
            f"-c:a:{i}",  "aac",
            f"-b:a:{i}",  abitrate,
            f"-ar:{i}",   "48000",
            f"-ac:{i}",   "2",
        ]

        var_stream_map_parts.append(f"v:{i},a:{i}")

    # HLS umumiy sozlamalar
    cmd += [
        "-f", "hls",
        "-hls_time", "6",
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_type", "mpegts",
        "-var_stream_map", " ".join(var_stream_map_parts),
        "-master_pl_name", "master.m3u8",
        "-hls_segment_filename", os.path.join(output_dir, "stream_%v", "seg%03d.ts"),
        os.path.join(output_dir, "stream_%v", "index.m3u8"),
    ]

    # Stream papkalarini oldindan yaratish
    for i in range(len(qualities)):
        os.makedirs(os.path.join(output_dir, f"stream_{i}"), exist_ok=True)

    # ── FFmpeg ni ishga tushirish ─────────────────────────────────────
    if status_msg:
        quality_names = " + ".join(f"{q['height']}p" for q in qualities)
        try:
            await status_msg.edit_text(
                f"⚙️ *FFmpeg ishlayapti...*\n"
                f"📊 Sifatlar: {quality_names}\n\n"
                f"`[░░░░░░░░░░░░░░]` Hisoblanyapti...\n"
                f"_(Bu bir necha daqiqa vaqt olishi mumkin)_",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # FFmpeg progress ni stderr dan o'qish (time=XX:XX:XX)
        last_update = 0.0
        stderr_lines: list[str] = []

        async def _read_stderr():
            nonlocal last_update
            assert proc.stderr is not None
            async for line in proc.stderr:
                text = line.decode(errors="replace").strip()
                stderr_lines.append(text)
                # "time=HH:MM:SS" ni qidirish
                if "time=" in text and status_msg:
                    import time
                    now = time.monotonic()
                    if now - last_update >= 5:   # har 5 soniyada yangilash
                        last_update = now
                        try:
                            await status_msg.edit_text(
                                f"⚙️ *FFmpeg ishlayapti...*\n\n"
                                f"`{text[-80:]}`",
                                parse_mode="Markdown",
                            )
                        except Exception:
                            pass

        await _read_stderr()
        await proc.wait()

        if proc.returncode != 0:
            err_text = "\n".join(stderr_lines[-20:])
            return False, "", err_text

        if not os.path.exists(master_path):
            return False, "", "master.m3u8 yaratilmadi"

        return True, master_path, ""

    except FileNotFoundError:
        return False, "", "FFmpeg topilmadi. Serverda FFmpeg o'rnatilganini tekshiring."
    except Exception as e:
        return False, "", str(e)
