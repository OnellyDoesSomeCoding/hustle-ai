"""Learn from real results: the creator logs how posted clips did, and new projects use the
best and worst of them as examples when the AI picks and scores moments.

Results live in each project's stats.json, keyed by clip id. Re-rendering or rewriting
posts keeps clip ids, so results survive; "Retry from scratch" re-picks clips and clears them.
Each entry stores a snapshot of the clip (title, hook, opening lines), so learning never
needs to re-read transcripts.
"""

from __future__ import annotations

import json
import math
import threading
import time
from typing import Any

from .store import store

MIN_LOGGED = 4      # clips with results needed before the AI starts learning from them
MIN_FOR_WEIGHTS = 8  # clips with results (and a score breakdown) before the ranking re-weights itself
PRIOR = 10          # the default weights count as this many clips of evidence
EXAMPLE_WORDS = 45  # opening words of each example clip shown to the AI
METRICS = ("views", "likes", "comments", "shares", "retention", "platform")

_lock = threading.Lock()


def _path(project_id: str):
    return store.dir(project_id) / "stats.json"


def load(project_id: str) -> dict[str, dict]:
    path = _path(project_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def public(entry: dict | None) -> dict | None:
    """Only the numbers the user typed, for the UI."""
    return {k: entry.get(k) for k in (*METRICS, "updated_at")} if entry else None


def _clip_text(project_id: str, clip: dict) -> str:
    path = store.dir(project_id) / "transcript.json"
    try:
        segments = json.loads(path.read_text(encoding="utf-8"))["segments"]
    except (OSError, json.JSONDecodeError, KeyError):
        return ""
    words = [w["word"] for s in segments for w in s["words"] if clip["start"] <= w["start"] < clip["end"]]
    return " ".join(words[:EXAMPLE_WORDS * 2])


def save(project_id: str, clip: dict, stats: dict) -> dict:
    with _lock:
        data = load(project_id)
        old = data.get(clip["id"], {})
        data[clip["id"]] = {
            **{k: stats.get(k) for k in METRICS},
            "updated_at": time.time(),
            "title": clip["title"], "hook": clip.get("hook", ""), "score": clip["score"],
            "duration": clip["duration"], "scores": clip.get("scores"),
            "text": old.get("text") or _clip_text(project_id, clip),
        }
        _path(project_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data[clip["id"]]


def remove(project_id: str, clip_id: str) -> None:
    with _lock:
        data = load(project_id)
        if data.pop(clip_id, None) is not None:
            _path(project_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear(project_id: str) -> None:
    _path(project_id).unlink(missing_ok=True)


# ---- scoring ------------------------------------------------------------------------

def _percentiles(values: list[float | None]) -> list[float | None]:
    """0..1 rank among the values that are present (ties share the average rank)."""
    present = sorted(v for v in values if v is not None)
    if len(present) < 2:
        return [0.5 if v is not None else None for v in values]
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        below = sum(1 for p in present if p < v)
        equal = sum(1 for p in present if p == v)
        out.append((below + (equal - 1) / 2) / (len(present) - 1))
    return out


def engagement(entry: dict) -> float | None:
    parts = [entry.get(k) for k in ("likes", "comments", "shares")]
    if not entry.get("views") or all(p is None for p in parts):
        return None
    return sum(p or 0 for p in parts) / entry["views"]


def rank(entries: list[dict]) -> list[float]:
    """Performance 0..1 for each entry, relative to the creator's other clips:
    views 60 %, engagement rate 25 %, average % viewed 15 % (missing metrics are skipped)."""
    views = _percentiles([math.log1p(e["views"]) for e in entries])
    eng = _percentiles([engagement(e) for e in entries])
    ret = _percentiles([e.get("retention") for e in entries])
    out = []
    for v, g, r in zip(views, eng, ret):
        parts = [(v, 0.60), (g, 0.25), (r, 0.15)]
        total = sum(w for x, w in parts if x is not None)
        out.append(sum(x * w for x, w in parts if x is not None) / total)
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None
    rx, ry = _percentiles(xs), _percentiles(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (sx * sy) if sx and sy else None


def all_logged(exclude: str | None = None) -> list[dict]:
    """Every clip with results, newest projects first, each with its performance."""
    rows = []
    for project in store.list():
        if project["id"] == exclude:
            continue
        clips = {c["id"]: c for c in project.get("clips", [])}
        for clip_id, entry in load(project["id"]).items():
            if not entry.get("views") and entry.get("views") != 0:
                continue
            clip = clips.get(clip_id, {})
            rows.append({**entry, "project_id": project["id"], "project_title": project["title"],
                         "clip_id": clip_id, "thumb": clip.get("thumb"), "video": clip.get("video")})
    for row, perf in zip(rows, rank(rows)):
        row["perf"] = round(perf, 3)
    return rows


# ---- prompt -------------------------------------------------------------------------

def _fmt_n(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 1e4 else str(n)


def _describe(e: dict) -> str:
    bits = [f"{_fmt_n(e['views'])} views"]
    if (g := engagement(e)) is not None:
        bits.append(f"{g * 100:.1f}% engagement")
    if e.get("retention") is not None:
        bits.append(f"{e['retention']:.0f}% average viewed")
    opening = " ".join((e.get("text") or "").split()[:EXAMPLE_WORDS])
    line = f'- "{e["title"]}"' + (f' (hook: "{e["hook"]}")' if e.get("hook") else "") + " | " + ", ".join(bits)
    return line + (f'\n  Opens with: "{opening}…"' if opening else "")


def examples(exclude: str | None = None) -> tuple[str | None, int]:
    """(prompt text, clips learned from). None until enough clips have results."""
    rows = all_logged(exclude)
    if len(rows) < MIN_LOGGED:
        return None, 0
    rows.sort(key=lambda r: r["perf"], reverse=True)
    winners = [r for r in rows[:3] if r["perf"] >= 0.5]
    losers = [r for r in rows[::-1][:2] if r["perf"] < 0.5 and r not in winners]
    if not winners:
        return None, 0
    text = [
        "This creator has posted clips before. Here is how some of them actually did with their audience:",
        "TOP PERFORMERS:", *map(_describe, winners),
    ]
    if losers:
        text += ["WEAK PERFORMERS:", *map(_describe, losers)]
    text.append(
        "Learn from this: favour moments with the topics, hook style and energy of the top performers, "
        "avoid what the weak performers have in common, and let it shape your scores. "
        "Still judge every moment on its own merits."
    )
    return "\n".join(text), len(rows)


# ---- ranking weights ----------------------------------------------------------------

def factor_weights(exclude: str | None = None) -> dict:
    """How much each ranking factor (hook, payoff, ...) predicted the creator's real results.

    Returns {"weights", "learned_from", "factors": [{"id", "corr", "weight", "default"}]}.
    Each factor's weight follows its rank correlation with performance (factors that don't
    help get none, and correlations within chance level are ignored), blended with the defaults so a handful of clips can't swing it wildly.
    """
    from .pipeline.scoring import DEFAULT_WEIGHTS, FACTORS

    rows = [r for r in all_logged(exclude) if isinstance(r.get("scores"), dict)]
    corr: dict[str, float | None] = {}
    for f in FACTORS:
        pairs = [(r["scores"][f], r["perf"]) for r in rows if r["scores"].get(f) is not None]
        corr[f] = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None

    weights, n = dict(DEFAULT_WEIGHTS), len(rows)
    if n >= MIN_FOR_WEIGHTS:
        # With few clips a correlation of ~1/sqrt(n) happens by chance, so only the part beyond
        # that counts, and it's trusted more as clips add up. A factor that predicts views well
        # can reach ~3x its default weight; one that works against them drops to as low as 1/4.
        noise, trust = 1 / math.sqrt(n - 1), n / (n + PRIOR)
        for f in FACTORS:
            r = corr[f] or 0.0
            effect = math.copysign(max(0.0, abs(r) - noise), r) * trust
            weights[f] = DEFAULT_WEIGHTS[f] * min(3.0, max(0.25, 1 + 2.5 * effect))
        total = sum(weights.values())
        weights = {f: w / total for f, w in weights.items()}
    return {
        "weights": {f: round(w, 3) for f, w in weights.items()},
        "learned_from": n if n >= MIN_FOR_WEIGHTS else 0,
        "with_scores": n,
        "min_needed": MIN_FOR_WEIGHTS,
        "factors": [{"id": f, "corr": None if corr[f] is None else round(corr[f], 2),
                     "weight": round(weights[f], 3), "default": DEFAULT_WEIGHTS[f]} for f in FACTORS],
    }
