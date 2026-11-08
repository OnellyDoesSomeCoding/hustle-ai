"""Download a video from a link with yt-dlp (YouTube, Twitch, Vimeo, Kick, ...)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..config import ffmpeg_path

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv"}


def find_source(folder: Path) -> Path | None:
    for path in sorted(folder.glob("source.*")):
        if path.suffix.lower() in VIDEO_EXTS:
            return path
    return None


def download(url: str, folder: Path, on_progress: Callable[[float, str], None]) -> tuple[Path, str]:
    """Returns (path to downloaded file, video title)."""
    import yt_dlp  # imported lazily: slow import

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                pct = done / total
                on_progress(pct, f"Downloading… {pct * 100:.0f}%")
        elif d.get("status") == "finished":
            on_progress(1.0, "Merging audio and video…")

    ffmpeg = ffmpeg_path()
    opts = {
        "format": (
            "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
            "bv*[height<=1080]+ba/b[height<=1080]/b"
        ),
        "outtmpl": str(folder / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        # YouTube needs a JavaScript runtime; Node is installed on this machine.
        "js_runtimes": {"deno": {}, "node": {}},
    }
    if ffmpeg:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    path = find_source(folder)
    if path is None:
        raise RuntimeError("Download finished but no video file was produced.")
    return path, (info or {}).get("title") or "Untitled video"
