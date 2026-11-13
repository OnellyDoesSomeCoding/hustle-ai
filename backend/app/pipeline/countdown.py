"""Top-N list video: the chosen clips play back to back with no cards between them. A
title stays at the top, and a numbered list ("1." to "N.") stays on the left. As each
clip starts, its slot in the list is filled in and highlighted. Earlier slots stay
filled and later ones stay empty, so the list completes as the video plays. The ranks
are revealed in a shuffled order, so viewers watch to the end to see where everything
landed.

The clips are the rendered 9:16 clips, captions included, so this is one re-encode of
footage that's already edited. Text is drawn with an ASS subtitle file (libass), the
same way captions are, because ffmpeg's drawtext needs fontconfig, which isn't set up
on a stock Windows ffmpeg.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import Callable

from ..config import ffmpeg_path
from .captions import _clean, _ts, _wrap
from .media import _NO_WINDOW, MediaError, OUT_H, OUT_W, video_encoder

ACCENT = "&H0032F4C6"  # #c6f432 as ASS &HAABBGGRR
WHITE = "&H00FFFFFF"
LABEL_MAX = 26  # characters per list label before it's cut with "…"

# Layout (1080x1920). The title sits above the clips' own hook title (top-centre, from
# y≈280 for the first 3.5 s of each clip), and the list sits above the captions (y≈1300+).
TITLE_Y = 80
LIST_Y = 520
LIST_X = 60


def shuffled_order(n: int, rng: random.Random | None = None) -> list[int]:
    """A random reveal order of ranks 1..n. For three or more ranks it never comes out
    as a plain 1→N or N→1, because those don't look shuffled."""
    rng = rng or random.Random()
    order = list(range(1, n + 1))
    if n < 2:
        return order
    plain = (order[:], order[::-1]) if n >= 3 else ()
    while True:
        rng.shuffle(order)
        if order not in plain:
            return order


def short_label(text: str) -> str:
    text = " ".join(_clean(text).split())
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1].rstrip(" ,.;:-") + "…"


def build_ass(title: str, labels: dict[int, str], segments: list[tuple[float, float, int]]) -> str:
    """`labels` = {rank: label}; `segments` = (start, end, rank revealed) in playback order."""
    n = len(labels)
    big = n <= 5
    size, step = (54, 74) if big else (42, 58)
    events = []

    def add(a: float, b: float, style: str, text: str, layer: int = 0) -> None:
        events.append(f"Dialogue: {layer},{_ts(a)},{_ts(b)},{style},,0,0,0,,{text}")

    total = segments[-1][1] if segments else 0.0
    lines = _wrap(_clean(title), 22).split(r"\N")
    head = lines[0] + "".join(r"\N{\c" + ACCENT + "&}" + line for line in lines[1:])
    add(0, total, "Title", r"{\an8\pos(540," + str(TITLE_Y) + ")}" + head, layer=2)

    shown: set[int] = set()
    for start, end, current in segments:
        for rank in range(1, n + 1):
            pos = r"{\an7\pos(" + f"{LIST_X},{LIST_Y + (rank - 1) * step}" + ")}"
            num = r"{\c" + ACCENT + "&}" + f"{rank}."
            if rank == current:
                pop = r"{\fscx70\fscy70\t(0,160,\fscx112\fscy112)\t(160,280,\fscx100\fscy100)}"
                text = pos + pop + r"{\c" + ACCENT + "&}" + f"{rank}. {labels[rank]}"
            elif rank in shown:
                text = pos + num + r"{\c" + WHITE + "&}" + f" {labels[rank]}"
            else:
                text = pos + num
            add(start, end, "List", text, layer=1)
        shown.add(current)

    styles = [
        f"Style: Title,Arial Black,76,{WHITE},{WHITE},&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,5,3,8,60,60,0,1",
        f"Style: List,Arial Black,{size},{WHITE},{WHITE},&H00000000,&H78000000,1,0,0,0,100,100,0,0,1,4,3,7,60,60,0,1",
    ]
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {OUT_W}", f"PlayResY: {OUT_H}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, "",
    ])


def render(items: list[dict], title: str, fps: float, out_dir: Path,
           on_progress: Callable[[float, str], None]) -> tuple[Path, float]:
    """`items` = [{"video": Path, "duration": s, "rank": n, "label": str, "clip_max": s|None}]
    in playback order (the reveal order). An item's `clip_max` cuts that clip to its first
    N seconds (the hook is at the start); None plays it in full. Returns (video path, duration)."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise MediaError("ffmpeg not found.")
    fps = fps if fps and fps > 1 else 30.0

    parts, lengths = [], []
    for i, item in enumerate(items):
        dur, clip_max = item["duration"], item.get("clip_max")
        cut = clip_max is not None and dur > clip_max + 0.5
        if cut:
            dur = float(clip_max)
        lengths.append(dur)
        vtrim = f"trim=duration={dur:.3f}," if cut else ""
        atrim = f"atrim=duration={dur:.3f}," if cut else ""
        fade = f",afade=t=out:st={dur - 0.15:.3f}:d=0.15" if cut else ""  # no click at the cut
        parts.append(f"[{i}:v]{vtrim}setpts=PTS-STARTPTS,fps={fps},scale={OUT_W}:{OUT_H},"
                     f"format=yuv420p,setsar=1[v{i}]")
        parts.append(f"[{i}:a]{atrim}aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"asetpts=PTS-STARTPTS{fade}[a{i}]")
    parts.append("".join(f"[v{i}][a{i}]" for i in range(len(items))) + f"concat=n={len(items)}:v=1:a=1[cv][ca]")
    parts.append("[cv]subtitles=countdown.ass[vout]")

    t, segments = 0.0, []
    for item, dur in zip(items, lengths):
        segments.append((t, t + dur, item["rank"]))
        t += dur
    total = t
    labels = {item["rank"]: short_label(item["label"]) for item in items}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "countdown.ass").write_text(build_ass(title, labels, segments), encoding="utf-8")
    tmp, out = out_dir / "countdown.part.mp4", out_dir / "countdown.mp4"
    args = [ffmpeg, "-y", "-v", "error", "-nostats", "-progress", "pipe:1"]
    for item in items:
        args += ["-i", str(item["video"])]
    args += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[ca]",
             *video_encoder(), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-movflags", "+faststart", tmp.name]
    proc = subprocess.Popen(args, cwd=out_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    for line in proc.stdout:
        if line.startswith("out_time_us="):
            try:
                done = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            on_progress(min(done / total, 0.99), f"Rendering top {len(items)}… {int(done)}s of {int(total)}s")
    err = proc.stderr.read()
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        tail = "\n".join(err.strip().splitlines()[-12:])
        raise MediaError(f"ffmpeg failed:\n{tail}")
    tmp.replace(out)
    return out, total
