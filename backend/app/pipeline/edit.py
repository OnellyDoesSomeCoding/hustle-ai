"""Jump cuts: remove dead air and filler words from a clip using the word timestamps.

`plan_keep` decides which parts of the clip to keep (clip-relative seconds), and
`Timeline` maps source times onto the shortened clip so captions stay in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Only sounds that never carry meaning. Words like "like" or "you know" are left alone
# because cutting them would often change what the speaker said.
FILLERS = {"um", "umm", "uhm", "uh", "uhh", "er", "erm", "hmm", "hm", "mm", "mmm", "ah"}


@dataclass(frozen=True)
class Pace:
    pad_before: float  # kept before each word (Whisper word starts are often a bit late)
    pad_after: float   # kept after each word
    min_cut: float     # shorter pauses (after padding) are kept, so speech doesn't stutter


PACES: dict[str, Pace] = {
    "natural": Pace(0.12, 0.18, 0.30),  # removes pauses longer than ~0.6 s
    "fast": Pace(0.06, 0.10, 0.12),     # removes pauses longer than ~0.3 s
}
TAIL = 0.35  # kept after the last word so the clip doesn't end abruptly


def is_filler(word: str) -> bool:
    return re.sub(r"[^\w']", "", word.lower()) in FILLERS


def plan_keep(words: list[dict], clip_dur: float, pace: str, fps: float) -> list[tuple[float, float]]:
    """Ranges of the clip to keep. `words` are clip-relative and include fillers."""
    p = PACES.get(pace)
    spoken = [w for w in words if not is_filler(w["word"])]
    if p is None or not spoken:
        return [(0.0, clip_dur)]

    ranges: list[list[float]] = []
    for i, w in enumerate(spoken):
        a = max(0.0, w["start"] - p.pad_before)
        b = min(clip_dur, w["end"] + (TAIL if i == len(spoken) - 1 else p.pad_after))
        if ranges and a - ranges[-1][1] <= p.min_cut:
            ranges[-1][1] = max(ranges[-1][1], b)
        else:
            ranges.append([a, b])

    # Cut on frame boundaries so the video and audio pieces have the same length.
    fps = fps if fps and fps > 1 else 30.0
    out: list[tuple[float, float]] = []
    for a, b in ranges:
        a, b = round(a * fps) / fps, min(clip_dur, round(b * fps) / fps)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        elif b - a >= 2 / fps:
            out.append((a, b))
    return out or [(0.0, clip_dur)]


class Timeline:
    """Maps clip-relative source times onto the cut clip."""

    def __init__(self, keep: list[tuple[float, float]]):
        self.keep = keep
        self.offsets: list[float] = []
        total = 0.0
        for a, b in keep:
            self.offsets.append(total)
            total += b - a
        self.duration = total

    @property
    def cuts(self) -> int:
        return len(self.keep) - 1

    def _index(self, t: float) -> int | None:
        for i, (a, b) in enumerate(self.keep):
            if a <= t <= b:
                return i
        return None

    def to_output(self, t: float, snap_forward: bool = False) -> float | None:
        """Output time for source time `t`, or None if `t` was cut. With `snap_forward`,
        a cut time maps to where the clip resumes (the start of the next kept range)."""
        i = self._index(t)
        if i is not None:
            return self.offsets[i] + t - self.keep[i][0]
        if snap_forward:
            for j, (a, _) in enumerate(self.keep):
                if a > t:
                    return self.offsets[j]
        return None

    def remap_words(self, words: list[dict], drop_fillers: bool) -> list[dict]:
        out = []
        for w in words:
            if drop_fillers and is_filler(w["word"]):
                continue
            i = self._index((w["start"] + w["end"]) / 2)
            if i is None:
                continue
            a, b = self.keep[i]
            o = self.offsets[i]
            out.append({**w, "start": o + max(w["start"], a) - a, "end": o + min(w["end"], b) - a})
        return out
