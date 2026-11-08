"""Ready-to-paste post copy (title, caption, description, hashtags) for each clip.

Primary: the local LLM through Ollama writes copy from the clip's own transcript.
Fallback: copy assembled from the clip title and transcript, so every clip still
gets something to paste when Ollama isn't running.

The model only writes four plain fields; the per-platform text (YouTube Shorts,
TikTok, Instagram Reels) is assembled here so lengths and hashtag rules are
always respected, even when a small model ignores the instructions.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Callable

import httpx

from ..config import OLLAMA_MODEL, OLLAMA_URL
from .captions import clip_words
from .curate import ollama_status

# Field limits of each platform's upload form.
LIMITS = {
    "youtube_title": 100,
    "youtube_description": 5000,
    "tiktok_caption": 4000,
    "instagram_caption": 2200,
}
MAX_HASHTAGS = 8
TRANSCRIPT_CHARS = 3500  # enough for a 3-minute clip; keeps the prompt small

SYSTEM_PROMPT = """You are a social media manager who writes the post copy for viral TikTok, Instagram Reels and YouTube Shorts clips.
You get the transcript of one short clip plus its working title. Write copy that makes people stop scrolling and watch to the end.
Return:
- title: a scroll-stopping video title, 40-70 characters, no hashtags, no quotes, at most one emoji,
- caption: one short punchy line for TikTok, max 100 characters, no hashtags,
- description: 2-3 short sentences that tease the payoff without spoiling it, ending with a question that invites comments, no hashtags,
- hashtags: 5-8 relevant hashtags without the # sign, mixing broad and niche topics from the clip.
Only use facts from the transcript; never invent names, numbers or claims.
Write everything in the same language as the transcript. Respond with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "caption", "description", "hashtags"],
}

# Common words that make useless hashtags (offline fallback only).
STOPWORDS = set("""
about above after again against because been before being below between could didn doesn doing don down during
each every from further going gonna have having here just know like little looking maybe more most much need never
only other really right should something still such than that their them then there these they thing things think
this those through very want wanna were what when where which while will with would yeah your yours actually
basically literally people pretty stuff kind sort okay
""".split())


# ---- shared helpers ---------------------------------------------------------------

def _clip_text(segments: list[dict], clip: dict) -> str:
    words = clip_words(segments, clip["start"], clip["end"])
    return re.sub(r"\s+", " ", " ".join(w["word"] for w in words)).strip()


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().strip('"“”').strip()


def _shorten(text: str, limit: int) -> str:
    """Cut at a word boundary so titles never end mid-word."""
    text = _one_line(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-–— ")
    return (cut or text[: limit - 1]) + "…"


def _clean_tags(raw: list[str]) -> list[str]:
    """"#Money Tips" -> "MoneyTips"; dedupes case-insensitively; keeps any script."""
    tags, seen = [], set()
    for item in raw:
        for part in re.split(r"[,\n]+|\s+#", str(item)):
            tag = re.sub(r"[^\w]", "", part.replace("#", ""), flags=re.UNICODE)
            if len(tag) < 2 or tag.isdigit() or tag.lower() in seen or tag.lower() == "shorts":
                continue
            seen.add(tag.lower())
            tags.append(tag)
    return tags[:MAX_HASHTAGS]


def _tag_line(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags)


def assemble(title: str, caption: str, description: str, tags: list[str]) -> dict:
    """Builds the per-platform fields from the four generated parts."""
    title = _shorten(title, 70)
    caption = _shorten(caption or title, 100)
    description = description.strip() or title
    yt_tags = _tag_line(["Shorts", *tags[:4]])
    # Lead the Instagram caption with the title, unless the description already opens with it.
    stem = title.rstrip("…").lower()[:30]
    insta_head = "" if description.lower().startswith(stem) else f"{title}\n\n"
    return {
        "hashtags": tags,
        "youtube": {
            "title": _shorten(title, LIMITS["youtube_title"]),
            "description": f"{description}\n\n{yt_tags}"[: LIMITS["youtube_description"]],
        },
        "tiktok": {
            "caption": f"{caption} {_tag_line(tags[:5])}".strip()[: LIMITS["tiktok_caption"]],
        },
        "instagram": {
            "caption": f"{insta_head}{description}\n\n{_tag_line(tags)}".strip()[: LIMITS["instagram_caption"]],
        },
    }


# ---- LLM path -----------------------------------------------------------------------

def _ask_llm(clip: dict, text: str, video_title: str | None, language: str | None) -> dict:
    parts = []
    if video_title:
        parts.append(f"Source video: {video_title}")
    if language:
        parts.append(f"Transcript language code: {language}")
    parts.append(f"Working title: {clip.get('title', '')}")
    if clip.get("hook"):
        parts.append(f"On-screen hook: {clip['hook']}")
    parts.append(f"Clip length: {int(clip['end'] - clip['start'])} seconds")
    parts.append(f"\nTranscript:\n{text[:TRANSCRIPT_CHARS]}")
    r = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n".join(parts)}],
            "stream": False,
            "format": SCHEMA,
            "options": {"temperature": 0.7, "num_ctx": 4096},
            "keep_alive": "5m",
        },
        timeout=300,
    )
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def _from_llm(clip: dict, text: str, video_title: str | None, language: str | None) -> dict | None:
    raw = _ask_llm(clip, text, video_title, language)
    title = _one_line(raw.get("title")) or clip.get("title", "")
    description = str(raw.get("description") or "").strip()
    tags = _clean_tags(raw.get("hashtags") or [])
    if not description or not tags:
        return None  # model returned junk; let the fallback fill this clip
    # Hashtags inside the prose would be duplicated by the hashtag line.
    description = re.sub(r"(?:\s*#\w+)+\s*$", "", description).strip()
    return assemble(title, _one_line(raw.get("caption")), description, tags)


# ---- offline fallback -------------------------------------------------------------

def _keywords(text: str, limit: int) -> list[str]:
    words = re.findall(r"[^\W\d_]{5,}", text.lower(), flags=re.UNICODE)
    counts = Counter(w for w in words if w not in STOPWORDS)
    return [w.capitalize() for w, n in counts.most_common(limit * 2) if n >= 2][:limit]


def _dedupe_repeats(text: str) -> str:
    """Speech-to-text often doubles phrases ("you can't you can't do it"); keep one."""
    return re.sub(r"\b(.{4,60}?)(?:\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)


def _from_text(clip: dict, text: str, video_title: str | None) -> dict:
    text = _dedupe_repeats(text)
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text) if s]
    description, total = [], 0
    for s in sentences:
        if total + len(s) > 220 and description:
            break
        description.append(s)
        total += len(s)
    teaser = _shorten(" ".join(description), 240) or clip.get("title", "")
    # An AI-picked clip (it has a hook) already has a written title; the offline
    # picker's title is just the first 60 characters of speech, so rebuild it.
    title = clip.get("title") if clip.get("hook") else _shorten(sentences[0] if sentences else text, 60)
    topic = _keywords(video_title or "", 2) if video_title else []
    tags = _clean_tags(_keywords(text, 4) + topic + ["viral", "fyp"])
    return assemble(title or clip.get("title", ""), clip.get("hook") or title, teaser, tags)


# ---- entry point -----------------------------------------------------------------------

def write_posts(clips: list[dict], segments: list[dict], video_title: str | None, language: str | None,
                on_progress: Callable[[float, str], None]) -> tuple[dict[str, dict], str, str | None]:
    """Returns ({clip_id: post}, mode, note). mode is "llm" or "basic"."""
    status = ollama_status()
    use_llm = status["running"] and status["model_ready"]
    posts: dict[str, dict] = {}
    failed = 0
    for i, clip in enumerate(clips):
        on_progress(i / max(len(clips), 1), f"Writing titles & descriptions… clip {i + 1} of {len(clips)}")
        text = _clip_text(segments, clip)
        post = None
        if use_llm:
            try:
                post = _from_llm(clip, text, video_title, language)
            except Exception as exc:
                print(f"[posts] LLM failed for {clip['id']}: {exc}")
            if post is None:
                failed += 1
        posts[clip["id"]] = post or _from_text(clip, text, video_title)

    if not use_llm:
        return posts, "basic", "Post copy was written offline. Start Ollama for AI-written titles and descriptions."
    if failed == len(clips):
        return posts, "basic", "AI post copy failed, so basic copy was written instead. Check that Ollama is running."
    if failed:
        return posts, "llm", f"AI copy failed for {failed} clip(s); those got basic copy instead."
    return posts, "llm", None
