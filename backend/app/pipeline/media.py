"""ffmpeg / ffprobe helpers."""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from ..config import ffmpeg_path, ffprobe_path

OUT_W, OUT_H = 1080, 1920
# Hide console windows spawned by ffmpeg on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaError(RuntimeError):
    pass


def _require(path: str | None, name: str) -> str:
    if not path:
        raise MediaError(f"{name} not found. Install it (winget install Gyan.FFmpeg) and restart the server.")
    return path


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise MediaError(f"{Path(args[0]).name} failed:\n{tail}")
    return proc


def probe(path: Path) -> dict:
    ffprobe = _require(ffprobe_path(), "ffprobe")
    out = run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]).stdout
    info = json.loads(out)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError("The file has no video stream.")
    if audio is None:
        raise MediaError("The file has no audio stream, so there is nothing to transcribe.")
    width, height = int(video["width"]), int(video["height"])
    # Respect rotation metadata from phone recordings.
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = abs(int(side["rotation"]))
    if rotation in (90, 270):
        width, height = height, width
    num, _, den = (video.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    return {"duration": duration, "width": width, "height": height, "fps": round(fps, 3)}


def extract_audio(src: Path, dst: Path, start: float | None = None, end: float | None = None) -> None:
    ffmpeg = _require(ffmpeg_path(), "ffmpeg")
    args = [ffmpeg, "-y", "-v", "error"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    if end:
        args += ["-to", f"{end:.3f}"]
    args += ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    run(args)


@lru_cache
def video_encoder() -> list[str]:
    """Use the NVIDIA hardware encoder when it works, otherwise libx264."""
    ffmpeg = _require(ffmpeg_path(), "ffmpeg")
    test = subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
         "-c:v", "h264_nvenc", "-f", "null", "-"],
        capture_output=True, creationflags=_NO_WINDOW,
    )
    if test.returncode == 0:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def encoder_name() -> str:
    return video_encoder()[1]


def crop_filter(width: int, height: int) -> str:
    """Centre-crop any source to 9:16, then scale to 1080x1920."""
    if width / height > OUT_W / OUT_H:  # wider than 9:16 (normal landscape video)
        crop = "crop=trunc(ih*9/16/2)*2:ih:(iw-trunc(ih*9/16/2)*2)/2:0"
    else:  # taller/narrower than 9:16
        crop = "crop=iw:trunc(iw*16/9/2)*2:0:(ih-trunc(iw*16/9/2)*2)/2"
    return f"{crop},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1"


def render_clip(src: Path, clip_dir: Path, start: float, end: float, media: dict, ass_name: str | None) -> Path:
    ffmpeg = _require(ffmpeg_path(), "ffmpeg")
    vf = crop_filter(media["width"], media["height"])
    if ass_name:
        # ffmpeg runs inside clip_dir, so a bare filename avoids Windows path escaping.
        vf += f",subtitles={ass_name}"
    out = clip_dir / "clip.mp4"
    tmp = clip_dir / "clip.part.mp4"  # renamed when finished, so a crash never leaves a "done" file
    args = [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
        "-vf", vf, *video_encoder(), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", tmp.name,
    ]
    run(args, cwd=clip_dir)
    tmp.replace(out)
    return out


def thumbnail(video: Path, dst: Path, at: float = 1.0) -> None:
    ffmpeg = _require(ffmpeg_path(), "ffmpeg")
    run([ffmpeg, "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(dst)])
