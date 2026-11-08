"""Speech-to-text with word timestamps using faster-whisper (GPU when available)."""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path
from typing import Callable

from ..config import WHISPER_MODEL

_cuda_dirs_added = False


def _add_cuda_dll_dirs() -> None:
    """The pip packages nvidia-cublas-cu12 / nvidia-cudnn-cu12 ship the CUDA DLLs
    that CTranslate2 needs; make them findable on Windows."""
    global _cuda_dirs_added
    if _cuda_dirs_added or sys.platform != "win32":
        return
    _cuda_dirs_added = True
    for base in sys.path:
        nvidia = Path(base) / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def cuda_available() -> bool:
    _add_cuda_dll_dirs()
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _load_model():
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    if cuda_available():
        try:
            return WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16"), "cuda"
        except Exception as exc:  # e.g. missing cuDNN: fall back rather than fail
            print(f"[transcribe] GPU load failed, using CPU: {exc}")
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8"), "cpu"


def transcribe(
    audio: Path,
    duration: float,
    language: str | None,
    offset: float,
    on_progress: Callable[[float, str], None],
) -> dict:
    """Returns {"language", "device", "segments": [{start, end, text, words: [{start, end, word, p}]}]}.
    Timestamps are shifted by `offset` so they line up with the source video."""
    on_progress(0.0, "Loading speech model (first run downloads it)…")
    model, device = _load_model()
    try:
        segments_iter, info = model.transcribe(
            str(audio),
            language=language or None,
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        on_progress(0.02, f"Transcribing ({info.language}) on {device.upper()}…")
        segments = []
        for seg in segments_iter:
            words = [
                {
                    "start": round(w.start + offset, 3),
                    "end": round(w.end + offset, 3),
                    "word": w.word.strip(),
                    "p": round(w.probability, 3),
                }
                for w in (seg.words or [])
                if w.word.strip()
            ]
            if not words:
                continue
            segments.append({
                "start": round(seg.start + offset, 3),
                "end": round(seg.end + offset, 3),
                "text": seg.text.strip(),
                "words": words,
            })
            if duration > 0:
                pct = min(seg.end / duration, 1.0)
                on_progress(pct, f"Transcribing… {pct * 100:.0f}%")
        return {"language": info.language, "device": device, "segments": segments}
    finally:
        # Free GPU memory before the language model runs.
        del model
        gc.collect()
