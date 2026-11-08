"""Background worker: runs one project at a time (the GPU can only fit one job).

Each step saves its output, so a retry or a server restart resumes where it
stopped instead of starting over.
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from .pipeline import captions, curate, ingest, media, posts, transcribe
from .store import store

# Overall progress range for each stage.
STAGES = {
    "download": (0.00, 0.15),
    "transcribe": (0.15, 0.60),
    "curate": (0.60, 0.68),
    "posts": (0.68, 0.72),
    "render": (0.72, 1.00),
}


class Cancelled(Exception):
    pass


_queue: "queue.Queue[str]" = queue.Queue()
_queued: set[str] = set()
_lock = threading.Lock()


def enqueue(project_id: str) -> None:
    with _lock:
        if project_id in _queued:
            return
        _queued.add(project_id)
    _queue.put(project_id)


def _reporter(project_id: str, stage: str) -> Callable[[float, str], None]:
    lo, hi = STAGES[stage]
    last = [0.0]

    def report(pct: float, message: str) -> None:
        if store.get(project_id) is None:
            raise Cancelled()
        now = time.monotonic()
        if pct < 1.0 and now - last[0] < 0.5:  # throttle disk writes
            return
        last[0] = now
        store.update(project_id, stage=stage, progress=round(lo + (hi - lo) * max(0.0, min(pct, 1.0)), 4),
                     message=message)

    return report


def _run(project_id: str) -> None:
    project = store.get(project_id)
    if project is None:
        return
    folder = store.dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    opts = project["options"]
    store.update(project_id, status="processing", error=None)

    # 1. Source video ----------------------------------------------------------
    report = _reporter(project_id, "download")
    source = ingest.find_source(folder)
    if source is None:
        if project["source"]["type"] != "url":
            raise RuntimeError("The uploaded file is missing. Delete this project and upload again.")
        report(0.0, "Starting download…")
        source, title = ingest.download(project["source"]["url"], folder, report)
        store.update(project_id, title=title)
    report(1.0, "Reading video info…")
    info = media.probe(source)
    store.update(project_id, media=info)

    start = float(opts.get("start") or 0)
    end = float(opts.get("end") or 0) or info["duration"]
    end = min(end, info["duration"])
    if end - start < 5:
        raise RuntimeError("The selected time range is shorter than 5 seconds.")

    # 2. Transcript --------------------------------------------------------------
    report = _reporter(project_id, "transcribe")
    transcript_path = folder / "transcript.json"
    if transcript_path.exists():
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    else:
        report(0.0, "Extracting audio…")
        audio = folder / "audio.wav"
        media.extract_audio(source, audio, start if start > 0 else None, end if end < info["duration"] else None)
        transcript = transcribe.transcribe(audio, end - start, opts.get("language"), start, report)
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        audio.unlink(missing_ok=True)
    store.update(project_id, language=transcript["language"])
    segments = transcript["segments"]
    if not segments:
        raise RuntimeError("No speech was detected in the video.")

    # 3. Pick clips ----------------------------------------------------------------
    report = _reporter(project_id, "curate")
    curation_path = folder / "curation.json"
    if curation_path.exists():
        cur = json.loads(curation_path.read_text(encoding="utf-8"))
    else:
        report(0.0, "Finding the best moments…")
        picked, mode, note = curate.curate(segments, opts.get("length", "under60"), int(opts.get("max_clips") or 0), report)
        cur = {"clips": picked, "mode": mode, "note": note}
        curation_path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    if not cur["clips"]:
        raise RuntimeError(cur.get("note") or "No clips could be found in this video.")

    clips = []
    for i, c in enumerate(sorted(cur["clips"], key=lambda c: c["score"], reverse=True)):
        clips.append({
            "id": f"c{i + 1:02d}", "index": i,
            "start": round(c["start"], 3), "end": round(c["end"], 3),
            "duration": round(c["end"] - c["start"], 2),
            "title": c["title"], "hook": c.get("hook", ""), "reason": c.get("reason", ""),
            "score": c["score"], "status": "pending",
            "video": None, "thumb": None, "srt": None, "post": None,
        })
    store.update(project_id, clips=clips, curation_mode=cur["mode"], note=cur.get("note"))

    # 4. Post copy (titles, descriptions, hashtags) ----------------------------------------
    report = _reporter(project_id, "posts")
    posts_path = folder / "posts.json"
    written = json.loads(posts_path.read_text(encoding="utf-8")) if posts_path.exists() else None
    if not written or set(written["posts"]) != {c["id"] for c in clips}:
        report(0.0, "Writing titles & descriptions…")
        title = store.get(project_id)["title"]  # the download step may have renamed it
        video_title = None if re.match(r"^https?://", title) else title
        try:
            by_id, mode, note = posts.write_posts(clips, segments, video_title, transcript["language"], report)
        finally:
            curate.unload_llm()  # free the GPU before rendering
        written = {"posts": by_id, "mode": mode, "note": note}
        posts_path.write_text(json.dumps(written, ensure_ascii=False, indent=2), encoding="utf-8")
    for clip in clips:
        clip["post"] = written["posts"].get(clip["id"])
    store.update(project_id, clips=clips, posts_mode=written["mode"], posts_note=written.get("note"))

    # 5. Render ------------------------------------------------------------------------
    report = _reporter(project_id, "render")
    template = opts.get("caption_template", "karaoke")
    for i, clip in enumerate(clips):
        report(i / len(clips), f"Rendering clip {i + 1} of {len(clips)} ({media.encoder_name()})…")
        clip_dir = folder / "clips" / clip["id"]
        clip_dir.mkdir(parents=True, exist_ok=True)
        rel = f"clips/{clip['id']}"
        words = captions.clip_words(segments, clip["start"], clip["end"])
        (clip_dir / "captions.srt").write_text(captions.build_srt(words), encoding="utf-8")
        ass_name = None
        hook = clip["hook"] if opts.get("hook_title", True) else None
        if template != "none" or hook:
            ass_words = words if template != "none" else []
            (clip_dir / "captions.ass").write_text(
                captions.build_ass(ass_words, clip["duration"], template, hook), encoding="utf-8")
            ass_name = "captions.ass"
        if not (clip_dir / "clip.mp4").exists():
            media.render_clip(source, clip_dir, clip["start"], clip["end"], info, ass_name)
        media.thumbnail(clip_dir / "clip.mp4", clip_dir / "thumb.jpg", min(1.5, clip["duration"] / 2))
        clip.update(status="done", video=f"{rel}/clip.mp4", thumb=f"{rel}/thumb.jpg", srt=f"{rel}/captions.srt")
        store.update(project_id, clips=clips)

    store.update(project_id, status="done", stage="done", progress=1.0,
                 message=f"{len(clips)} clips ready")


def _worker() -> None:
    while True:
        project_id = _queue.get()
        with _lock:
            _queued.discard(project_id)
        try:
            _run(project_id)
        except Cancelled:
            shutil.rmtree(store.dir(project_id), ignore_errors=True)
        except Exception as exc:
            traceback.print_exc()
            if store.get(project_id) is not None:
                store.update(project_id, status="error", error=str(exc), message="Failed")
        finally:
            _queue.task_done()


def start_worker() -> None:
    threading.Thread(target=_worker, name="hustle-worker", daemon=True).start()
    # Resume anything that was interrupted by a restart.
    for project in sorted(store.list(), key=lambda p: p["created_at"]):
        if project["status"] in ("queued", "processing"):
            enqueue(project["id"])


def reset_for_retry(project_id: str, full: bool) -> None:
    """full=True also forgets the transcript and picked clips."""
    folder: Path = store.dir(project_id)
    shutil.rmtree(folder / "clips", ignore_errors=True)
    if full:
        for name in ("transcript.json", "curation.json", "posts.json"):
            (folder / name).unlink(missing_ok=True)
    store.update(project_id, status="queued", stage="queued", progress=0.0, error=None,
                 message="Waiting in queue", clips=[])


def rewrite_posts(project_id: str) -> None:
    """Writes fresh post copy for a finished project. Already-rendered clips are kept,
    so the job only re-runs the copy step (and quickly re-checks the clips)."""
    (store.dir(project_id) / "posts.json").unlink(missing_ok=True)
    store.update(project_id, status="queued", stage="queued", progress=0.0, error=None,
                 message="Waiting in queue")
