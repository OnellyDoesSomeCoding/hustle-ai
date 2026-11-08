"""Find the best short-form moments in a transcript.

Primary: a local LLM through Ollama reads numbered transcript lines and returns
clip ranges by line number (small models are far more reliable with line
numbers than with raw timestamps). Fallback: a text heuristic, so the app still
works when Ollama isn't running.
"""

from __future__ import annotations

import json
import math
import re
from typing import Callable

import httpx

from ..config import OLLAMA_MODEL, OLLAMA_URL

LENGTHS: dict[str, tuple[float, float]] = {
    "under30": (12, 30),
    "under60": (25, 60),
    "under90": (40, 90),
    "under3min": (60, 180),
}
WINDOW_SECONDS = 600  # transcript chunk the LLM sees at once
WINDOW_OVERLAP = 60

SYSTEM_PROMPT = """You are a senior short-form video editor who cuts viral TikTok, Reels and YouTube Shorts from long videos.
You get a transcript as numbered lines: [line] mm:ss text.
Pick the moments most likely to go viral as standalone clips. A great clip:
- opens with a strong hook in the first sentence (a bold claim, question, surprising fact, conflict or emotion),
- is one complete, self-contained thought that makes sense without the rest of the video,
- has a payoff (insight, punchline, story resolution, useful tip),
- starts at the beginning of a sentence and ends at the end of one.
Avoid intros, outros, sponsor reads, small talk and moments that depend on earlier context.
For each clip return:
- start_line, end_line: inclusive line numbers from the transcript,
- title: catchy post title, max 60 characters,
- hook: punchy on-screen text for the first seconds, max 8 words,
- score: 0-100 virality estimate (be critical; most moments are 40-75),
- reason: one short sentence on why it works.
Write title and hook in the same language as the transcript. Respond with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["start_line", "end_line", "title", "hook", "score", "reason"],
            },
        }
    },
    "required": ["clips"],
}


# ---- transcript -> sentence units -----------------------------------------

def build_units(segments: list[dict]) -> list[dict]:
    """Split the word stream into sentence-like units with timings."""
    words = [w for seg in segments for w in seg["words"]]
    units: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if current:
            units.append({
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": " ".join(w["word"] for w in current),
                "words": len(current),
                "p": sum(w["p"] for w in current) / len(current),
            })
            current.clear()

    for i, word in enumerate(words):
        current.append(word)
        nxt = words[i + 1] if i + 1 < len(words) else None
        sentence_end = word["word"].endswith((".", "?", "!", "…", "。", "？", "！")) and len(current) >= 3
        long_pause = nxt is not None and nxt["start"] - word["end"] > 1.2
        if sentence_end or long_pause or len(current) >= 30 or nxt is None:
            flush()
    return units


def _fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def auto_clip_count(units: list[dict]) -> int:
    if not units:
        return 0
    span = units[-1]["end"] - units[0]["start"]
    return max(3, min(10, int(span / 150) + 1))


# ---- shaping candidates -----------------------------------------------------

def _fit_length(units: list[dict], s: int, e: int, lo: float, hi: float) -> tuple[int, int] | None:
    s, e = max(0, min(s, e)), min(len(units) - 1, max(s, e))
    dur = lambda: units[e]["end"] - units[s]["start"]  # noqa: E731
    while dur() > hi and e > s:
        e -= 1
    while dur() < lo and e + 1 < len(units) and units[e + 1]["end"] - units[s]["start"] <= hi:
        e += 1
    if dur() < lo * 0.6:
        return None
    return s, e


def _dedupe(cands: list[dict], limit: int) -> list[dict]:
    picked: list[dict] = []
    for c in sorted(cands, key=lambda c: c["score"], reverse=True):
        overlap = False
        for p in picked:
            inter = min(c["end"], p["end"]) - max(c["start"], p["start"])
            if inter > 0.3 * min(c["end"] - c["start"], p["end"] - p["start"]):
                overlap = True
                break
        if not overlap:
            picked.append(c)
        if len(picked) >= limit:
            break
    return picked


# ---- LLM path --------------------------------------------------------------

def ollama_status() -> dict:
    """{"running": bool, "model_ready": bool, "model": str}"""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        want = OLLAMA_MODEL if ":" in OLLAMA_MODEL else f"{OLLAMA_MODEL}:latest"
        return {"running": True, "model_ready": want in names, "model": OLLAMA_MODEL}
    except Exception:
        return {"running": False, "model_ready": False, "model": OLLAMA_MODEL}


def _windows(units: list[dict]) -> list[tuple[int, int]]:
    windows, i = [], 0
    while i < len(units):
        j = i
        while j + 1 < len(units) and units[j + 1]["end"] - units[i]["start"] <= WINDOW_SECONDS:
            j += 1
        windows.append((i, j))
        if j >= len(units) - 1:
            break
        nxt = j
        while nxt > i and units[j]["end"] - units[nxt]["start"] < WINDOW_OVERLAP:
            nxt -= 1
        i = max(nxt, i + 1)
    return windows


def _ask_llm(lines: str, wanted: int, lo: float, hi: float) -> list[dict]:
    user = (
        f"Find the {wanted} best clips. Each clip must be between {int(lo)} and {int(hi)} seconds long "
        f"(use the mm:ss timestamps to judge length).\n\nTranscript:\n{lines}"
    )
    r = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
            "stream": False,
            "format": SCHEMA,
            "options": {"temperature": 0.3, "num_ctx": 8192},
            "keep_alive": "5m",
        },
        timeout=600,
    )
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"]).get("clips", [])


def unload_llm() -> None:
    """Free the model's GPU memory. Called by the job once all LLM steps are done."""
    try:
        httpx.post(f"{OLLAMA_URL}/api/generate", json={"model": OLLAMA_MODEL, "keep_alive": 0}, timeout=10)
    except Exception:
        pass


def curate_llm(units: list[dict], count: int, lo: float, hi: float, on_progress: Callable[[float, str], None]) -> list[dict]:
    windows = _windows(units)
    total_span = max(units[-1]["end"] - units[0]["start"], 1)
    cands: list[dict] = []
    errors = 0
    for n, (a, b) in enumerate(windows):
        on_progress(n / len(windows), f"AI is reading the transcript… part {n + 1}/{len(windows)}")
        span = units[b]["end"] - units[a]["start"]
        wanted = max(3, math.ceil(count * span / total_span) + 2)
        lines = "\n".join(f"[{i}] {_fmt(units[i]['start'])} {units[i]['text']}" for i in range(a, b + 1))
        try:
            raw = _ask_llm(lines, wanted, lo, hi)
        except Exception as exc:
            print(f"[curate] LLM window {n} failed: {exc}")
            errors += 1
            continue
        for c in raw:
            try:
                s, e = int(c["start_line"]), int(c["end_line"])
            except (KeyError, TypeError, ValueError):
                continue
            s, e = max(a, min(s, b)), max(a, min(e, b))
            fitted = _fit_length(units, s, e, lo, hi)
            if not fitted:
                continue
            s, e = fitted
            cands.append({
                "start": units[s]["start"],
                "end": units[e]["end"],
                "title": str(c.get("title", "")).strip()[:80] or units[s]["text"][:60],
                "hook": str(c.get("hook", "")).strip()[:60],
                "score": int(max(0, min(100, c.get("score", 50)))),
                "reason": str(c.get("reason", "")).strip()[:200],
            })
    if errors == len(windows):
        raise RuntimeError("Every request to Ollama failed.")
    return _dedupe(cands, count)


# ---- heuristic path ----------------------------------------------------------

HOOK_WORDS = re.compile(
    r"\b(how|why|what|secret|never|always|nobody|everyone|mistake|truth|actually|crazy|insane|"
    r"best|worst|biggest|stop|wrong|money|million|you|imagine|story|problem)\b|\d",
    re.IGNORECASE,
)


def curate_heuristic(units: list[dict], count: int, lo: float, hi: float) -> list[dict]:
    target = (lo + hi) / 2
    cands = []
    for s in range(len(units)):
        e = s
        while e + 1 < len(units) and units[e + 1]["end"] - units[s]["start"] <= target:
            e += 1
        fitted = _fit_length(units, s, e, lo, hi)
        if not fitted:
            continue
        s2, e2 = fitted
        chunk = units[s2:e2 + 1]
        dur = chunk[-1]["end"] - chunk[0]["start"]
        text = " ".join(u["text"] for u in chunk)
        density = sum(u["words"] for u in chunk) / max(dur, 1)          # words per second
        punch = text.count("?") + text.count("!")
        hook = len(HOOK_WORDS.findall(chunk[0]["text"]))
        clarity = sum(u["p"] for u in chunk) / len(chunk)
        raw = density * 0.8 + min(punch, 4) * 0.5 + min(hook, 4) * 0.6 + clarity * 1.5
        cands.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "raw": raw,
                      "title": chunk[0]["text"][:60], "hook": "", "reason": "Picked by the offline heuristic."})
    if not cands:
        return []
    lo_raw, hi_raw = min(c["raw"] for c in cands), max(c["raw"] for c in cands)
    for c in cands:
        c["score"] = int(40 + 50 * (c.pop("raw") - lo_raw) / max(hi_raw - lo_raw, 1e-6))
    return _dedupe(cands, count)


def curate(segments: list[dict], length_key: str, max_clips: int,
           on_progress: Callable[[float, str], None]) -> tuple[list[dict], str, str | None]:
    """Returns (clips, mode, note). mode is "llm" or "heuristic"."""
    units = build_units(segments)
    if not units:
        return [], "heuristic", "No speech was found in the video."
    lo, hi = LENGTHS.get(length_key, LENGTHS["under60"])
    span = units[-1]["end"] - units[0]["start"]
    if span < lo:
        lo = max(5.0, span * 0.5)
    count = max_clips or auto_clip_count(units)

    status = ollama_status()
    note = None
    if status["running"] and status["model_ready"]:
        try:
            return curate_llm(units, count, lo, hi, on_progress), "llm", None
        except Exception as exc:
            note = f"AI curation failed ({exc}); used the offline heuristic instead."
    elif status["running"]:
        note = f"Ollama model '{OLLAMA_MODEL}' isn't installed (run: ollama pull {OLLAMA_MODEL}); used the offline heuristic."
    else:
        note = "Ollama isn't running; used the offline heuristic. Start Ollama for AI-picked clips."
    on_progress(0.5, "Scoring moments (offline heuristic)…")
    return curate_heuristic(units, count, lo, hi), "heuristic", note
