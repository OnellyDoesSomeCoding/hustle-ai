"""Clip ranking: five sub-scores (0-100) combined into the overall score.

  hook        the first seconds grab attention    AI, or opening words + opening energy
  payoff      it ends on a punchline or insight    AI, or the ending + reactions after it
  standalone  makes sense without the full video   AI, or checks for dangling references
  energy      measured: loudness and expressiveness against the rest of the video,
              laughter / applause / shouting, on-screen motion and camera cuts
  pacing      measured: words per second after jump cuts, and dead air

The weights start at DEFAULT_WEIGHTS; once the creator has logged enough results,
learning.factor_weights() shifts them toward the factors that predict their views.
"""

from __future__ import annotations

import bisect
import re
import subprocess
from pathlib import Path

import numpy as np

from ..config import ffmpeg_path
from . import edit
from .media import _NO_WINDOW
from .signals import Signals

FACTORS = ("hook", "payoff", "standalone", "energy", "pacing")
AI_FACTORS = ("hook", "payoff", "standalone")
DEFAULT_WEIGHTS = {"hook": 0.30, "payoff": 0.20, "standalone": 0.15, "energy": 0.20, "pacing": 0.15}

HOOK_WORDS = re.compile(
    r"\b(how|why|what|secret|never|always|nobody|everyone|mistake|truth|actually|crazy|insane|"
    r"best|worst|biggest|stop|wrong|money|million|you|imagine|story|problem|craziest|weird|"
    r"hate|love|scared|died|fired|broke|first time|last time)\b|\d",
    re.IGNORECASE,
)
STRONG_OPENERS = {"you", "why", "how", "what", "never", "stop", "imagine", "nobody", "everyone", "the",
                  "i", "this", "if", "here's", "there's"}
WEAK_OPENERS = {"and", "but", "so", "or", "because", "then", "also", "plus", "um", "uh", "like", "yeah",
                "okay", "ok", "right", "well", "anyway", "mhm", "which"}
DANGLING_PRONOUNS = {"he", "she", "they", "it", "that", "those", "these", "him", "her", "them", "his", "their"}
BACK_REFERENCES = re.compile(
    r"\b(as i said|like i said|i mentioned|as we said|we talked about|going back to|the other thing|"
    r"that's why|which is why|earlier|before that|like you said|you just said)\b",
    re.IGNORECASE,
)
PAYOFF_PHRASES = re.compile(
    r"\b(that's how|that's why|the point is|the truth is|turns out|it turns out|the lesson|the answer|"
    r"the secret|so basically|and that's|and then|which means|the key|that's the)\b",
    re.IGNORECASE,
)
SENTENCE_END = (".", "?", "!", "…")


def _c(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at `lo`, 1 at `hi`, linear in between."""
    return _c((x - lo) / (hi - lo))


def _bare(word: str) -> str:
    return re.sub(r"[^\w']+", "", word.lower())


def clip_words(segments: list[dict], a: float, b: float) -> list[dict]:
    """Words inside [a, b] with absolute times."""
    return [w for s in segments if s["end"] >= a and s["start"] <= b
            for w in s["words"] if w["end"] > a and w["start"] < b]


# ---- measured factors -----------------------------------------------------------------

def reactions(sig: Signals, a: float, b: float) -> dict[str, float]:
    """Audience/host reactions in the clip, including the second and a half after it ends
    (the laugh lands just after the punchline)."""
    return {
        "laugh": _ramp(sig.event("laugh", a, b + 1.5), 0.06, 0.30),
        "applause": _ramp(sig.event("applause", a, b + 1.5), 0.12, 0.45),
        "shout": _ramp(sig.event("shout", a, b), 0.12, 0.45),
    }


def cues(sig: Signals, a: float, b: float) -> list[str]:
    """Labels for the UI: sounds that clearly happen in the clip."""
    r = reactions(sig, a, b)
    out = [name for name, key in (("laughter", "laugh"), ("applause", "applause"), ("shouting", "shout"))
           if r[key] >= 0.65]
    if sig.share("music", a, b, 0.5) > 0.5 and sig.share("speech", a, b, 0.5) < 0.4:
        out.append("music")
    return out


def energy(sig: Signals, a: float, b: float, visual: dict | None = None) -> float | None:
    if not sig.ok:
        return None
    win = sig.db_window(a, b)
    voiced = win[win > np.percentile(win, 40)] if len(win) > 4 else win
    loud = _ramp(float(np.mean(voiced)) - sig.ref, -3.0, 4.0) if len(voiced) else 0.0
    dynamics = _ramp(float(np.std(voiced)), 1.5, 6.0) if len(voiced) > 4 else 0.0
    peaks = _ramp(float((win > sig.loud).mean()) if len(win) else 0.0, 0.05, 0.35)
    r = reactions(sig, a, b)
    reaction = max(r["laugh"], r["applause"], r["shout"] * 0.8)
    audio = 0.30 * loud + 0.20 * dynamics + 0.15 * peaks + 0.35 * reaction
    # A wall of music (theme song, montage) is not the kind of energy that makes a clip.
    if sig.has_events and sig.share("music", a, b, 0.5) > 0.5 and sig.share("speech", a, b, 0.5) < 0.4:
        audio *= 0.5
    if visual is None:
        return audio
    moving = max(_ramp(visual["motion"], 0.01, 0.10), _ramp(visual["cuts_per_10s"], 0.3, 2.5))
    return 0.8 * audio + 0.2 * moving


def pacing(words: list[dict], a: float, b: float, pace: str, fps: float) -> float | None:
    rel = [{"start": w["start"] - a, "end": w["end"] - a, "word": w["word"]} for w in words]
    if len(rel) < 3:
        return None
    timeline = edit.Timeline(edit.plan_keep(rel, b - a, pace, fps))
    kept = timeline.remap_words(rel, drop_fillers=pace in edit.PACES)
    if not kept or timeline.duration <= 0:
        return None
    wps = len(kept) / timeline.duration
    # Podcasts land around 2.7-3.7 words/s after cuts; ~4 feels punchy, past ~5 is hard to follow.
    speed = _ramp(wps, 1.8, 4.0) if wps < 4.4 else 1.0 - 0.4 * _ramp(wps, 4.4, 5.8)
    gaps = [kept[i + 1]["start"] - kept[i]["end"] for i in range(len(kept) - 1)]
    dead = sum(g for g in gaps if g > 0.6) + max(0.0, kept[0]["start"] - 0.6)
    air = 1.0 - _ramp(dead / timeline.duration, 0.05, 0.30)
    return 0.65 * speed + 0.35 * air


def visual_activity(src: Path, start: float, dur: float, media: dict, fps: float = 4.0) -> dict | None:
    """Share of pixels that change between samples, and camera/scene cuts per 10 s."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg or dur <= 0:
        return None
    w = 160
    h = max(2, round(w * media["height"] / media["width"] / 2) * 2)
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
         "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, creationflags=_NO_WINDOW,
    )
    frames = np.frombuffer(proc.stdout, np.uint8)
    n = len(frames) // (w * h)
    if proc.returncode != 0 or n < 3:
        return None
    frames = frames[: n * w * h].reshape(n, h, w).astype(np.int16)
    diff = np.abs(np.diff(frames, axis=0))
    changed = (diff > 25).mean(axis=(1, 2))   # share of pixels that changed, per step
    cuts = int(((diff.mean(axis=(1, 2)) > 30) & (changed > 0.5)).sum())
    moving = changed[changed <= 0.5]           # cuts aren't motion
    return {"motion": float(np.median(moving)) if len(moving) else 0.0,
            "cuts_per_10s": cuts / dur * 10}


# ---- text factors (used when there's no AI) ----------------------------------------

def _sentences(words: list[dict]) -> list[list[dict]]:
    out, cur = [], []
    for w in words:
        cur.append(w)
        if w["word"].endswith(SENTENCE_END) and len(cur) >= 3:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def hook_text(words: list[dict], a: float, sig: Signals) -> float:
    if not words:
        return 0.0
    first = _sentences(words)[0][:25]
    text = " ".join(w["word"] for w in first)
    opener = _bare(first[0]["word"])
    early = [w for w in words if w["start"] < a + 3.0]
    score = 0.15
    score += 0.30 * min(len(HOOK_WORDS.findall(text)), 3) / 3
    score += 0.15 if text.rstrip().endswith(("?", "!")) else 0.0
    score += 0.10 if opener in STRONG_OPENERS else 0.0
    score -= 0.25 if opener in WEAK_OPENERS else 0.0
    score += 0.15 * _ramp(len(early) / 3.0, 1.0, 3.0)  # gets talking right away
    if sig.ok:
        opening = sig.db_window(a, a + 3.0)
        if len(opening):
            score += 0.15 * _ramp(float(np.max(opening)) - sig.ref, -2.0, 4.0)
    return _c(score)


def payoff_text(words: list[dict], a: float, b: float, sig: Signals) -> float:
    if not words:
        return 0.0
    tail_start = a + (b - a) * 0.7
    tail = [w for w in words if w["start"] >= tail_start] or words[-5:]
    text = " ".join(w["word"] for w in tail)
    score = 0.15
    score += 0.25 if words[-1]["word"].endswith(SENTENCE_END) else 0.0
    score += 0.15 if PAYOFF_PHRASES.search(text) else 0.0
    score += 0.10 if ("!" in text or "?" in text) else 0.0
    if sig.ok:
        r = reactions(sig, tail_start, b)
        score += 0.35 * max(r["laugh"], r["applause"])
        end_db, all_db = sig.db_window(tail_start, b), sig.db_window(a, b)
        if len(end_db) and len(all_db):
            score += 0.10 * _ramp(float(np.max(end_db)) - float(np.median(all_db)), 2.0, 8.0)
    return _c(score)


def standalone_text(words: list[dict]) -> float:
    if not words:
        return 0.0
    sentences = _sentences(words)
    opening = " ".join(w["word"] for s in sentences[:2] for w in s)
    opener = _bare(words[0]["word"])
    score = 0.85
    score -= 0.30 if opener in WEAK_OPENERS else 0.0
    score -= 0.20 if opener in DANGLING_PRONOUNS else 0.0
    score -= 0.25 if BACK_REFERENCES.search(opening) else 0.0
    if not words[-1]["word"].endswith(SENTENCE_END):
        score -= 0.20
    if _bare(words[-1]["word"]) in WEAK_OPENERS:
        score -= 0.15
    return _c(score)


# ---- combining -----------------------------------------------------------------------

def overall(scores: dict[str, int | None], weights: dict[str, float]) -> int:
    parts = [(scores[f], weights.get(f, 0.0)) for f in FACTORS if scores.get(f) is not None]
    total = sum(w for _, w in parts)
    return int(round(sum(s * w for s, w in parts) / total)) if total else 0


def breakdown(segments: list[dict], a: float, b: float, sig: Signals, pace: str, fps: float,
              ai: dict | None = None, visual: dict | None = None,
              words: list[dict] | None = None) -> dict[str, int | None]:
    """Sub-scores 0-100. `ai` holds the AI's hook/payoff/standalone when it picked the clip."""
    words = clip_words(segments, a, b) if words is None else words
    raw: dict[str, float | None] = {
        "energy": energy(sig, a, b, visual),
        "pacing": pacing(words, a, b, pace, fps),
    }
    if ai and all(isinstance(ai.get(f), (int, float)) for f in AI_FACTORS):
        raw.update({f: _c(ai[f] / 100) for f in AI_FACTORS})
    else:
        raw.update(hook=hook_text(words, a, sig), payoff=payoff_text(words, a, b, sig),
                   standalone=standalone_text(words))
    return {f: None if raw.get(f) is None else int(round(raw[f] * 100)) for f in FACTORS}


class Ranker:
    """Scores candidate ranges of one video with its transcript, audio signals and settings."""

    def __init__(self, segments: list[dict], sig: Signals, pace: str, fps: float,
                 weights: dict[str, float] | None = None):
        self.segments, self.sig, self.pace, self.fps = segments, sig, pace, fps
        self.weights = weights or DEFAULT_WEIGHTS
        self._words = [w for s in segments for w in s["words"]]
        self._starts = [w["start"] for w in self._words]

    def words(self, a: float, b: float) -> list[dict]:
        i = max(0, bisect.bisect_left(self._starts, a) - 1)
        out = []
        for w in self._words[i:]:
            if w["start"] >= b:
                break
            if w["end"] > a:
                out.append(w)
        return out

    def score(self, a: float, b: float, ai: dict | None = None,
              visual: dict | None = None) -> tuple[int, dict[str, int | None]]:
        scores = breakdown(self.segments, a, b, self.sig, self.pace, self.fps, ai, visual, self.words(a, b))
        return overall(scores, self.weights), scores


def finalize(clips: list[dict], ranker: Ranker, src: Path, media: dict, on_progress) -> None:
    """Adds on-screen activity to each picked clip and computes its final scores and cues."""
    for i, c in enumerate(clips):
        on_progress(i / max(len(clips), 1), f"Ranking clips… {i + 1}/{len(clips)}")
        try:
            visual = visual_activity(src, c["start"], c["end"] - c["start"], media)
        except Exception as exc:  # never lose a project over the visual pass
            print(f"[scoring] visual activity failed: {exc}")
            visual = None
        c["score"], c["scores"] = ranker.score(c["start"], c["end"], c.get("ai"), visual)
        c["cues"] = cues(ranker.sig, c["start"], c["end"])
