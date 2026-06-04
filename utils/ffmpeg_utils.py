import subprocess
import os
import uuid
from config import TEMP_DIR


def run_ffmpeg(args: list[str], timeout: int = 600) -> tuple[bool, str]:
    """Run ffmpeg command and return (success, error_message)."""
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
        return False, "Vaqt tugadi (10 daqiqa)"
    except FileNotFoundError:
        return False, "FFmpeg topilmadi. Iltimos server administratori bilan bog'laning."
    except Exception as e:
        return False, str(e)


def get_video_duration(input_path: str) -> float:
    """Get video duration in seconds."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def get_video_info(input_path: str) -> dict:
    """Get video resolution and other info."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name,r_frame_rate",
                "-of", "default=noprint_wrappers=1",
                input_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
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
    """Generate a unique temp file path."""
    return os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}.{ext}")


def convert_video(input_path: str, output_format: str) -> tuple[bool, str, str]:
    """Convert video to another format. Returns (success, output_path, error)."""
    output_path = make_temp_path(output_format)
    codec_map = {
        "mp4": ["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"],
        "mkv": ["-c:v", "libx264", "-c:a", "aac"],
        "avi": ["-c:v", "libxvid", "-c:a", "libmp3lame"],
        "mov": ["-c:v", "libx264", "-c:a", "aac"],
        "webm": ["-c:v", "libvpx-vp9", "-c:a", "libopus"],
        "flv": ["-c:v", "libx264", "-c:a", "aac"],
    }
    extra = codec_map.get(output_format, ["-c:v", "libx264", "-c:a", "aac"])
    args = ["-i", input_path] + extra + [output_path]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def change_resolution(input_path: str, height: int) -> tuple[bool, str, str]:
    """Change video resolution. Returns (success, output_path, error)."""
    output_path = make_temp_path("mp4")
    scale = f"scale=-2:{height}"
    args = [
        "-i", input_path,
        "-vf", scale,
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def compress_video(input_path: str, quality: str) -> tuple[bool, str, str]:
    """Compress video. quality: high/medium/low."""
    output_path = make_temp_path("mp4")
    crf_map = {"high": "23", "medium": "28", "low": "35"}
    crf = crf_map.get(quality, "28")
    args = [
        "-i", input_path,
        "-c:v", "libx264",
        "-crf", crf,
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def trim_video(input_path: str, start: str, end: str) -> tuple[bool, str, str]:
    """Trim video from start to end (HH:MM:SS or seconds)."""
    output_path = make_temp_path("mp4")
    args = [
        "-i", input_path,
        "-ss", start,
        "-to", end,
        "-c:v", "libx264",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def remove_audio(input_path: str) -> tuple[bool, str, str]:
    """Remove audio track from video."""
    output_path = make_temp_path("mp4")
    args = [
        "-i", input_path,
        "-c:v", "copy",
        "-an",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def video_to_audio(input_path: str, audio_format: str) -> tuple[bool, str, str]:
    """Extract audio from video."""
    output_path = make_temp_path(audio_format)
    codec_map = {
        "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        "aac": ["-c:a", "aac", "-b:a", "192k"],
        "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
        "wav": ["-c:a", "pcm_s16le"],
        "flac": ["-c:a", "flac"],
    }
    extra = codec_map.get(audio_format, ["-c:a", "libmp3lame"])
    args = ["-i", input_path, "-vn"] + extra + [output_path]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def take_screenshots(input_path: str, count: int) -> tuple[bool, list[str], str]:
    """Take evenly spaced screenshots."""
    duration = get_video_duration(input_path)
    if duration <= 0:
        return False, [], "Video davomiyligini aniqlab bo'lmadi"

    paths = []
    interval = duration / (count + 1)
    ok_all = True
    err_msg = ""

    for i in range(count):
        timestamp = interval * (i + 1)
        output_path = make_temp_path("jpg")
        args = [
            "-ss", str(timestamp),
            "-i", input_path,
            "-frames:v", "1",
            "-q:v", "2",
            output_path,
        ]
        ok, err = run_ffmpeg(args, timeout=60)
        if ok:
            paths.append(output_path)
        else:
            ok_all = False
            err_msg = err

    return len(paths) > 0, paths, err_msg


def take_manual_shot(input_path: str, timestamp: str) -> tuple[bool, str, str]:
    """Take a screenshot at specific timestamp."""
    output_path = make_temp_path("jpg")
    args = [
        "-ss", timestamp,
        "-i", input_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path,
    ]
    ok, err = run_ffmpeg(args, timeout=60)
    return ok, output_path, err


def merge_subtitle(video_path: str, subtitle_path: str) -> tuple[bool, str, str]:
    """Burn subtitle into video."""
    output_path = make_temp_path("mp4")
    escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    args = [
        "-i", video_path,
        "-vf", f"subtitles='{escaped}'",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err
