"""Paths and settings. Everything can be overridden with environment variables."""

from __future__ import annotations

import glob
import os
import shutil
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = Path(os.environ.get("HUSTLE_DATA_DIR", REPO_DIR / "data"))
PROJECTS_DIR = DATA_DIR / "projects"
FRONTEND_DIST = REPO_DIR / "frontend" / "dist"

WHISPER_MODEL = os.environ.get("HUSTLE_WHISPER_MODEL", "large-v3-turbo")
OLLAMA_URL = os.environ.get("HUSTLE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("HUSTLE_OLLAMA_MODEL", "qwen2.5:7b")

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _find_binary(name: str) -> str | None:
    """Find ffmpeg/ffprobe on PATH, or in the winget install folder (PATH isn't
    refreshed in shells that were open before winget installed it)."""
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(local, "Microsoft", "WinGet", "Links", f"{name}.exe"),
        os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "**", "bin", f"{name}.exe"),
        os.path.join("C:\\", "ProgramData", "chocolatey", "bin", f"{name}.exe"),
    ]
    for pattern in patterns:
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return hits[0]
    return None


@lru_cache
def ffmpeg_path() -> str | None:
    return os.environ.get("HUSTLE_FFMPEG") or _find_binary("ffmpeg")


@lru_cache
def ffprobe_path() -> str | None:
    return os.environ.get("HUSTLE_FFPROBE") or _find_binary("ffprobe")
