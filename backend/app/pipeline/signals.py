"""Audio signals used to rank clips: loudness, plus laughter, applause, shouting and
music from Google's YAMNet sound classifier (16 MB ONNX, runs on the CPU at about
1000x real time).

The audio is streamed from ffmpeg in one-minute chunks, so a long video never has
to fit in memory. Everything is measured on a 0.48 s grid (YAMNet's hop): bin i
starts at `start + i * HOP`; its sound-event scores cover 0.96 s from there.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import ffmpeg_path
from . import models
from .media import _NO_WINDOW, MediaError

YAMNET = ("yamnet.onnx", 15_000_000, [  # Google YAMNet, Apache 2.0, converted with tf2onnx
    "https://huggingface.co/audiomagic/yamnet-onnx/resolve/main/yamnet.onnx",
    "https://huggingface.co/funground-hq/yamnet-onnx/resolve/main/yamnet.onnx",
])
RATE = 16000
HOP_SAMPLES = 7680                # 0.48 s
FRAME_SAMPLES = 15360             # 0.96 s
HOP = HOP_SAMPLES / RATE
CHUNK = HOP_SAMPLES * 125         # one minute of audio per model call

# AudioSet class indices (yamnet_class_map.csv).
EVENTS = {
    "laugh": [13, 14, 15, 16, 17, 18],   # laughter, baby laughter, giggle, snicker, belly laugh, chuckle
    "applause": [58, 61, 62, 64],         # clapping, cheering, applause, crowd
    "shout": [6, 7, 8, 9, 11],            # shout, bellow, whoop, yell, screaming
    "music": [132],
    "speech": [0],
}


def _session():
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    opts.log_severity_level = 3
    return ort.InferenceSession(str(models.ensure(YAMNET)), opts, providers=["CPUExecutionProvider"])


def analyze(src: Path, start: float, end: float, on_progress: Callable[[float, str], None]) -> dict:
    """Returns {"start", "hop", "db": [...], "laugh": [...], ...}. Sound events are left out
    (loudness only) when the classifier can't be loaded."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise MediaError("ffmpeg not found.")
    try:
        session = _session()
    except Exception as exc:  # download failed / onnxruntime missing: loudness still works
        print(f"[signals] sound classifier unavailable, using loudness only: {exc}")
        session = None

    proc = subprocess.Popen(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
         "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
    )
    db: list[np.ndarray] = []
    events: dict[str, list[np.ndarray]] = {k: [] for k in EVENTS}
    buf = np.zeros(0, np.float32)
    total = max(end - start, 1.0)
    done = 0.0

    def consume(final: bool) -> None:
        """Process every complete 0.48 s bin in `buf` (all of it when final)."""
        nonlocal buf
        if final:
            if len(buf) == 0:
                return
            pad = (-len(buf)) % HOP_SAMPLES
            buf = np.concatenate([buf, np.zeros(pad, np.float32)])
            n = len(buf) // HOP_SAMPLES
        else:
            n = (len(buf) - FRAME_SAMPLES) // HOP_SAMPLES + 1  # frames that fit completely
            if n <= 0:
                return
        bins = buf[: n * HOP_SAMPLES].reshape(n, HOP_SAMPLES)
        db.append(20 * np.log10(np.sqrt(np.mean(bins ** 2, axis=1)) + 1e-5))
        if session is not None:
            scores = session.run(["output_0"], {"waveform": buf})[0][:n]
            if len(scores) < n:  # the model may drop a trailing partial frame
                scores = np.vstack([scores, np.repeat(scores[-1:], n - len(scores), axis=0)])
            for name, idx in EVENTS.items():
                events[name].append(scores[:, idx].max(axis=1))
        buf = buf[n * HOP_SAMPLES:]

    try:
        while True:
            raw = proc.stdout.read(CHUNK * 2)
            if not raw:
                break
            buf = np.concatenate([buf, np.frombuffer(raw, np.int16).astype(np.float32) / 32768])
            consume(final=False)
            done += len(raw) / 2 / RATE
            on_progress(min(done / total, 0.99), "Listening for laughs, applause and energy…")
        consume(final=True)
    finally:
        proc.stdout.close()
        proc.wait()

    out: dict = {"start": round(start, 3), "hop": HOP,
                 "db": np.round(np.concatenate(db) if db else np.zeros(0), 1).tolist()}
    if session is not None and events["laugh"]:
        for name, parts in events.items():
            out[name] = np.round(np.concatenate(parts), 2).tolist()
    return out


class Signals:
    """Window queries over analyze()'s output. Times are absolute source seconds."""

    def __init__(self, data: dict | None):
        data = data or {}
        self.start = data.get("start", 0.0)
        self.hop = data.get("hop", HOP)
        self.db = np.asarray(data.get("db", []), np.float32)
        self.events = {k: np.asarray(data[k], np.float32) for k in EVENTS if k in data}
        self.ok = len(self.db) > 0
        if self.ok:
            voiced = self.db[self.db > np.percentile(self.db, 40)]  # skip pauses and silence
            self.ref = float(np.median(voiced))      # typical speaking loudness of this video
            self.loud = float(np.percentile(voiced, 90))

    @property
    def has_events(self) -> bool:
        return bool(self.events)

    def _span(self, a: float, b: float) -> slice:
        i = max(0, int((a - self.start) / self.hop))
        j = max(i + 1, int(np.ceil((b - self.start) / self.hop)))
        return slice(i, min(j, len(self.db)))

    def db_window(self, a: float, b: float) -> np.ndarray:
        return self.db[self._span(a, b)] if self.ok else np.zeros(0, np.float32)

    def event(self, name: str, a: float, b: float, top: int = 3) -> float:
        """Strength of a sound event in [a, b]: mean of its `top` strongest moments (0..1)."""
        arr = self.events.get(name)
        if arr is None:
            return 0.0
        win = arr[self._span(a, b)]
        if len(win) == 0:
            return 0.0
        return float(np.sort(win)[-top:].mean())

    def share(self, name: str, a: float, b: float, above: float) -> float:
        """Share of [a, b] where the event scores above `above`."""
        arr = self.events.get(name)
        if arr is None:
            return 0.0
        win = arr[self._span(a, b)]
        return float((win > above).mean()) if len(win) else 0.0
