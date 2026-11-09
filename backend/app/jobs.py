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

from . import learning
from .pipeline import captions, countdown, curate, edit, ingest, media, posts, reframe, scoring, signals, transcribe
from .store import store

# Overall progress range for each stage.
STAGES = {
    "download": (0.00, 0.15),
    "transcribe": (0.15, 0.57),
    "curate": (0.57, 0.68),
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


def _part(report: Callable[[float, str], None], lo: float, hi: float) -> Callable[[float, str], None]:
    """A reporter for one part (lo..hi) of a stage."""
    return lambda pct, message: report(lo + (hi - lo) * pct, message)


def _run(project_id: str) -> None:
    project = store.get(project_id)
    if project is None:
        return
    folder = store.dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    opts = project["options"]
    store.update(project_id, status="processing", error=None, render_note=None)

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

    # 3. Pick and rank clips -----------------------------------------------------------
    report = _reporter(project_id, "curate")
    sig_path = folder / "signals.json"
    if sig_path.exists():
        sig_data = json.loads(sig_path.read_text(encoding="utf-8"))
    else:
        sig_data = signals.analyze(source, start, end, _part(report, 0.0, 0.3))
        sig_path.write_text(json.dumps(sig_data), encoding="utf-8")
    learned_weights = learning.factor_weights(exclude=project_id)
    pace = opts.get("pace", "natural")
    ranker = scoring.Ranker(segments, signals.Signals(sig_data), pace, info["fps"], learned_weights["weights"])

    curation_path = folder / "curation.json"
    if curation_path.exists():
        cur = json.loads(curation_path.read_text(encoding="utf-8"))
    else:
        report(0.3, "Finding the best moments…")
        learned, learned_from = learning.examples(exclude=project_id)
        hint = curate.ANIMATION_GUIDANCE if opts.get("content") == "animation" else None
        guidance = "\n\n".join(g for g in (hint, learned) if g) or None
        picked, mode, note = curate.curate(segments, opts.get("length", "under60"), int(opts.get("max_clips") or 0),
                                           ranker, _part(report, 0.3, 0.8), guidance)
        cur = {"clips": picked, "mode": mode, "note": note, "learned_from": learned_from if mode == "llm" else 0}
    if not cur["clips"]:
        raise RuntimeError(cur.get("note") or "No clips could be found in this video.")
    if not cur.get("ranked"):
        scoring.finalize(cur["clips"], ranker, source, info, _part(report, 0.8, 1.0))
        # Clip ids follow list order. New picks are sorted best-first; projects made before
        # ranking existed keep their order, so their clip ids (and logged results) still match.
        if not curation_path.exists():
            cur["clips"].sort(key=lambda c: c["score"], reverse=True)
        cur.update(ranked=True, weights=learned_weights["weights"], weights_from=learned_weights["learned_from"])
        curation_path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")

    stats = learning.load(project_id)
    by_score = sorted(range(len(cur["clips"])), key=lambda i: cur["clips"][i]["score"], reverse=True)
    clips = []
    for i, c in enumerate(cur["clips"]):
        clip_id = f"c{i + 1:02d}"
        clips.append({
            "id": clip_id, "index": i, "rank": by_score.index(i) + 1,
            "start": round(c["start"], 3), "end": round(c["end"], 3),
            "duration": round(c["end"] - c["start"], 2),
            "title": c["title"], "hook": c.get("hook", ""), "reason": c.get("reason", ""),
            "score": c["score"], "scores": c.get("scores"), "cues": c.get("cues", []), "status": "pending",
            "video": None, "thumb": None, "srt": None, "post": None,
            "stats": learning.public(stats.get(clip_id)),
        })
    store.update(project_id, clips=clips, curation_mode=cur["mode"], note=cur.get("note"),
                 learned_from=cur.get("learned_from", 0), weights_from=cur.get("weights_from", 0))

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
    track_faces = opts.get("reframe", "face") == "face" and reframe.needs_tracking(info)
    animation = opts.get("content") == "animation"
    render_note = None
    for i, clip in enumerate(clips):
        label = f"clip {i + 1} of {len(clips)}"
        clip_dir = folder / "clips" / clip["id"]
        clip_dir.mkdir(parents=True, exist_ok=True)
        rel = f"clips/{clip['id']}"
        src_dur = clip["end"] - clip["start"]

        # Jump cuts: the captions are re-timed onto the shortened clip.
        all_words = captions.clip_words(segments, clip["start"], clip["end"])
        keep = edit.plan_keep(all_words, src_dur, pace, info["fps"])
        timeline = edit.Timeline(keep)
        words = timeline.remap_words(all_words, drop_fillers=pace in edit.PACES)
        clip.update(duration=round(timeline.duration, 2), trimmed=round(src_dur - timeline.duration, 2))

        (clip_dir / "captions.srt").write_text(captions.build_srt(words), encoding="utf-8")
        ass_name = None
        hook = clip["hook"] if opts.get("hook_title", True) else None
        if template != "none" or hook:
            ass_words = words if template != "none" else []
            (clip_dir / "captions.ass").write_text(
                captions.build_ass(ass_words, clip["duration"], template, hook), encoding="utf-8")
            ass_name = "captions.ass"

        meta_path = clip_dir / "render.json"
        if not (clip_dir / "clip.mp4").exists():
            center_x = None
            if track_faces and render_note is None:
                report(i / len(clips), f"Finding {'characters' if animation else 'faces'} in {label}…")
                try:
                    center_x = reframe.plan(source, clip["start"], src_dur, info, timeline, animation)
                except reframe.ReframeUnavailable as exc:
                    render_note = f"{exc} Clips use a centre crop instead."
                    store.update(project_id, render_note=render_note)
                except Exception:  # never lose a clip over framing
                    traceback.print_exc()
            report((i + 0.5) / len(clips), f"Rendering {label} ({media.encoder_name()})…")
            media.render_clip(source, clip_dir, clip["start"], clip["end"], info, ass_name, keep, center_x)
            meta_path.write_text(json.dumps({"framing": "face" if center_x else "center"}), encoding="utf-8")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        media.thumbnail(clip_dir / "clip.mp4", clip_dir / "thumb.jpg", min(1.5, clip["duration"] / 2))
        clip.update(status="done", video=f"{rel}/clip.mp4", thumb=f"{rel}/thumb.jpg", srt=f"{rel}/captions.srt",
                    framing=meta.get("framing", "center"))
        store.update(project_id, clips=clips)

    store.update(project_id, status="done", stage="done", progress=1.0,
                 message=f"{len(clips)} clips ready")


COUNTDOWN = "countdown:"  # queue entries for countdown videos are prefixed with this


def enqueue_countdown(project_id: str) -> None:
    enqueue(COUNTDOWN + project_id)


def _set_countdown(project_id: str, **fields) -> None:
    project = store.get(project_id)
    if project is None:
        raise Cancelled()
    store.update(project_id, countdown={**(project.get("countdown") or {}), **fields})


def _run_countdown(project_id: str) -> None:
    project = store.get(project_id)
    job = (project or {}).get("countdown")
    if not job or job.get("status") not in ("queued", "processing"):
        return
    _set_countdown(project_id, status="processing", progress=0.0, message="Starting…", error=None)
    folder = store.dir(project_id)
    clips = {c["id"]: c for c in project["clips"]}
    by_rank = {}
    for n, entry in enumerate(job["items"]):  # best first: entry 0 is #1
        clip = clips.get(entry["id"])
        if clip is None or not clip.get("video") or not (folder / clip["video"]).exists():
            raise RuntimeError("A clip in the top list is missing. Re-render the project, then try again.")
        by_rank[n + 1] = {"video": folder / clip["video"], "duration": clip["duration"], "rank": n + 1,
                          "label": entry["title"] or clip["title"]}
    reveal = job.get("reveal")
    if sorted(reveal or []) != sorted(by_rank):
        reveal = countdown.shuffled_order(len(by_rank))
        _set_countdown(project_id, reveal=reveal)
    items = [by_rank[r] for r in reveal]  # playback order = reveal order
    last = [0.0]

    def report(pct: float, message: str) -> None:
        if store.get(project_id) is None:
            raise Cancelled()
        if pct - last[0] >= 0.02:
            last[0] = pct
            _set_countdown(project_id, progress=round(pct, 3), message=message)

    _, duration = countdown.render(items, job["title"], (project.get("media") or {}).get("fps", 30.0),
                                   folder / "countdown", report, job.get("clip_max"))
    _set_countdown(project_id, status="done", progress=1.0, message="Ready", video="countdown/countdown.mp4",
                   duration=round(duration, 2), rendered_at=time.time())


def _worker() -> None:
    while True:
        key = _queue.get()
        with _lock:
            _queued.discard(key)
        if key.startswith(COUNTDOWN):
            project_id = key[len(COUNTDOWN):]
            try:
                _run_countdown(project_id)
            except Cancelled:
                pass
            except Exception as exc:
                traceback.print_exc()
                if store.get(project_id) is not None:
                    _set_countdown(project_id, status="error", error=str(exc), message="Failed")
            finally:
                _queue.task_done()
            continue
        project_id = key
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
        if (project.get("countdown") or {}).get("status") in ("queued", "processing"):
            enqueue_countdown(project["id"])


def reset_for_retry(project_id: str, full: bool) -> None:
    """full=True also forgets the transcript and picked clips."""
    folder: Path = store.dir(project_id)
    shutil.rmtree(folder / "clips", ignore_errors=True)
    if full:
        for name in ("transcript.json", "signals.json", "curation.json", "posts.json"):
            (folder / name).unlink(missing_ok=True)
        learning.clear(project_id)  # new picks: old results no longer belong to these clip ids
        shutil.rmtree(folder / "countdown", ignore_errors=True)
        store.update(project_id, countdown=None)
    store.update(project_id, status="queued", stage="queued", progress=0.0, error=None,
                 message="Waiting in queue", clips=[])


def rewrite_posts(project_id: str) -> None:
    """Writes fresh post copy for a finished project. Already-rendered clips are kept,
    so the job only re-runs the copy step (and quickly re-checks the clips)."""
    (store.dir(project_id) / "posts.json").unlink(missing_ok=True)
    store.update(project_id, status="queued", stage="queued", progress=0.0, error=None,
                 message="Waiting in queue")
