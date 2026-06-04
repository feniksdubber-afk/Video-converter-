import subprocess
import os
import uuid
from config import TEMP_DIR


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
    """CPU core sonini aniqlash."""
    try:
        import multiprocessing
        return str(multiprocessing.cpu_count())
    except Exception:
        return "0"  # 0 = FFmpeg o'zi tanlaydi


def convert_video(input_path: str, output_format: str) -> tuple[bool, str, str]:
    output_path = make_temp_path(output_format)
    threads = _thread_count()

    codec_map = {
        "mp4":  ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
        "mkv":  ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                 "-c:a", "aac"],
        "avi":  ["-c:v", "libxvid", "-q:v", "5",
                 "-c:a", "libmp3lame", "-q:a", "4"],
        "mov":  ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                 "-c:a", "aac", "-movflags", "+faststart"],
        "webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                 "-c:a", "libopus"],
        "flv":  ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                 "-c:a", "aac"],
    }
    extra = codec_map.get(output_format, ["-c:v", "libx264", "-preset", "ultrafast",
                                           "-crf", "23", "-c:a", "aac"])
    args = ["-i", input_path, "-threads", threads] + extra + [output_path]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def change_resolution(input_path: str, height: int) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    args = [
        "-i", input_path,
        "-threads", threads,
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def compress_video(input_path: str, quality: str) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    # ultrafast bilan tezlik, crf bilan sifat balansi
    crf_map = {"high": "23", "medium": "28", "low": "35"}
    crf = crf_map.get(quality, "28")
    args = [
        "-i", input_path,
        "-threads", threads,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", crf,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err


def trim_video(input_path: str, start: str, end: str) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    # -ss oldida qo'yish = keyframe dan kesish (juda tez, copy codec)
    args = [
        "-ss", start, "-to", end,
        "-i", input_path,
        "-c", "copy",  # re-encode qilmaydi — millisaniyada tugaydi
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


def merge_subtitle(video_path: str, subtitle_path: str) -> tuple[bool, str, str]:
    output_path = make_temp_path("mp4")
    threads = _thread_count()
    escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    args = [
        "-i", video_path,
        "-threads", threads,
        "-vf", f"subtitles='{escaped}'",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    ok, err = run_ffmpeg(args)
    return ok, output_path, err
