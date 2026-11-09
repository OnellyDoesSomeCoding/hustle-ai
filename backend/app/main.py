"""HustleAI local API. No accounts, no login: it only listens on 127.0.0.1."""

from __future__ import annotations

import json
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import jobs, learning
from .config import FRONTEND_DIST, OLLAMA_MODEL, PROJECTS_DIR, WHISPER_MODEL, ffmpeg_path
from .pipeline import captions, countdown, curate, ingest, media, transcribe
from .store import store


class Options(BaseModel):
    length: Literal["under30", "under60", "under90", "under3min"] = "under60"
    max_clips: int = Field(0, ge=0, le=20)  # 0 = auto
    language: Optional[str] = None  # None = auto-detect
    caption_template: str = "karaoke"
    hook_title: bool = True
    pace: Literal["off", "natural", "fast"] = "natural"  # jump cuts: remove pauses and filler words
    reframe: Literal["face", "center"] = "face"  # face-tracked or centred 9:16 crop
    content: Literal["live", "animation"] = "live"  # animation: anime faces + action tracking, cartoon-aware picks
    start: Optional[float] = Field(None, ge=0)
    end: Optional[float] = Field(None, ge=0)

    @field_validator("caption_template")
    @classmethod
    def _template(cls, v: str) -> str:
        if v != "none" and v not in captions.TEMPLATES:
            raise ValueError(f"unknown caption template '{v}'")
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip().lower()
        return v if v and v != "auto" else None


class Stats(BaseModel):
    """Results of a posted clip, typed in from the platform's analytics."""
    platform: Optional[Literal["youtube", "tiktok", "instagram"]] = None
    views: int = Field(ge=0)
    likes: Optional[int] = Field(None, ge=0)
    comments: Optional[int] = Field(None, ge=0)
    shares: Optional[int] = Field(None, ge=0)
    retention: Optional[float] = Field(None, ge=0, le=100)  # average % of the clip viewed


class CountdownItem(BaseModel):
    id: str
    title: str = Field("", max_length=100)  # the list label; empty = the clip's title


class Countdown(BaseModel):
    """A Top-N list video. `items` are best first: the first one is #1. `reveal` is the
    order the ranks play in (e.g. [3, 1, 5, 2, 4]); left out, it's shuffled."""
    items: list[CountdownItem] = Field(min_length=2, max_length=10)
    title: str = Field("", max_length=80)
    reveal: Optional[list[int]] = None
    clip_max: Optional[float] = Field(None, ge=5, le=120)  # seconds per clip; None = full clips


def _countdown_busy(project: dict) -> bool:
    return (project.get("countdown") or {}).get("status") in ("queued", "processing")


@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.start_worker()
    yield


app = FastAPI(title="HustleAI", lifespan=lifespan)


def _parse_options(raw: str) -> dict:
    try:
        opts = Options(**json.loads(raw or "{}"))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"Invalid options: {exc}") from exc
    if opts.start is not None and opts.end is not None and opts.end <= opts.start:
        raise HTTPException(422, "End time must be after start time.")
    return opts.model_dump()


def _get(project_id: str) -> dict:
    project = store.get(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


# ---- system --------------------------------------------------------------------

@app.get("/api/system")
def system() -> dict:
    ff = ffmpeg_path()
    return {
        "ffmpeg": bool(ff),
        "encoder": media.encoder_name() if ff else None,
        "cuda": transcribe.cuda_available(),
        "whisper_model": WHISPER_MODEL,
        "ollama": curate.ollama_status(),
        "ollama_model": OLLAMA_MODEL,
        "templates": [{"id": t.id, "name": t.name} for t in captions.TEMPLATES.values()],
    }


# ---- projects ------------------------------------------------------------------

@app.get("/api/projects")
def list_projects() -> list[dict]:
    return store.list()


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    return _get(project_id)


@app.post("/api/projects", status_code=201)
def create_project(
    url: str = Form(""),
    options: str = Form("{}"),
    file: Optional[UploadFile] = File(None),
) -> dict:
    opts = _parse_options(options)
    url = url.strip()
    if bool(url) == bool(file and file.filename):
        raise HTTPException(422, "Provide either a video link or a file, not both.")

    if url:
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise HTTPException(422, "The link must start with http:// or https://")
        project = store.create(title=url, source={"type": "url", "url": url}, options=opts)
    else:
        ext = Path(file.filename).suffix.lower()
        if ext not in ingest.VIDEO_EXTS:
            raise HTTPException(422, f"Unsupported file type '{ext}'. Use MP4, MOV, MKV or WEBM.")
        project = store.create(title=Path(file.filename).stem,
                               source={"type": "upload", "filename": file.filename}, options=opts)
        folder = store.dir(project["id"])
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / f"source{ext}", "wb") as out:
            shutil.copyfileobj(file.file, out, length=8 * 1024 * 1024)

    jobs.enqueue(project["id"])
    return project


@app.post("/api/projects/{project_id}/retry")
def retry_project(project_id: str, full: bool = False) -> dict:
    project = _get(project_id)
    if project["status"] in ("queued", "processing"):
        raise HTTPException(409, "Project is already running.")
    if _countdown_busy(project):
        raise HTTPException(409, "Wait until the top list video has finished rendering.")
    jobs.reset_for_retry(project_id, full)
    jobs.enqueue(project_id)
    return _get(project_id)


@app.post("/api/projects/{project_id}/posts")
def rewrite_posts(project_id: str) -> dict:
    """Write fresh titles/descriptions/hashtags for every clip (keeps the rendered videos)."""
    project = _get(project_id)
    if project["status"] != "done":
        raise HTTPException(409, "Post copy can be rewritten once the project has finished.")
    jobs.rewrite_posts(project_id)
    jobs.enqueue(project_id)
    return _get(project_id)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    if not store.delete(project_id):
        raise HTTPException(404, "Project not found")


@app.get("/api/projects/{project_id}/clips/{clip_id}/download")
def download_clip(project_id: str, clip_id: str, kind: Literal["mp4", "srt"] = "mp4") -> FileResponse:
    project = _get(project_id)
    clip = next((c for c in project["clips"] if c["id"] == clip_id), None)
    if clip is None or clip["status"] != "done":
        raise HTTPException(404, "Clip not ready")
    rel = clip["video"] if kind == "mp4" else clip["srt"]
    safe_title = re.sub(r"[^\w\- ]+", "", clip["title"]).strip()[:60] or clip_id
    return FileResponse(store.dir(project_id) / rel, filename=f"{safe_title}.{kind}")


# ---- results & learning ------------------------------------------------------------

def _done_clip(project: dict, clip_id: str) -> dict:
    if project["status"] in ("queued", "processing"):
        raise HTTPException(409, "Wait until the project has finished processing.")
    clip = next((c for c in project["clips"] if c["id"] == clip_id), None)
    if clip is None or clip["status"] != "done":
        raise HTTPException(404, "Clip not found")
    return clip


@app.put("/api/projects/{project_id}/clips/{clip_id}/stats")
def save_stats(project_id: str, clip_id: str, stats: Stats) -> dict:
    project = _get(project_id)
    clip = _done_clip(project, clip_id)
    clip["stats"] = learning.public(learning.save(project_id, clip, stats.model_dump()))
    return store.update(project_id, clips=project["clips"])


@app.delete("/api/projects/{project_id}/clips/{clip_id}/stats")
def delete_stats(project_id: str, clip_id: str) -> dict:
    project = _get(project_id)
    clip = _done_clip(project, clip_id)
    learning.remove(project_id, clip_id)
    clip["stats"] = None
    return store.update(project_id, clips=project["clips"])


@app.get("/api/insights")
def insights() -> dict:
    rows = sorted(learning.all_logged(), key=lambda r: r["perf"], reverse=True)
    for r in rows:
        r["engagement"] = learning.engagement(r)
        r.pop("text", None)
    return {
        "logged": len(rows),
        "min_needed": learning.MIN_LOGGED,
        "learning": len(rows) >= learning.MIN_LOGGED,
        "accuracy": learning.spearman([r["score"] for r in rows], [r["perf"] for r in rows]),
        "ranking": learning.factor_weights(),
        "clips": rows,
    }


# ---- top list video ----------------------------------------------------------------------

@app.post("/api/projects/{project_id}/countdown")
def make_countdown(project_id: str, body: Countdown) -> dict:
    project = _get(project_id)
    if project["status"] != "done":
        raise HTTPException(409, "The top list video can be made once the project has finished.")
    if _countdown_busy(project):
        raise HTTPException(409, "A top list video is already rendering.")
    ids = [item.id for item in body.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each clip can only be in the list once.")
    done = {c["id"] for c in project["clips"] if c["status"] == "done"}
    if missing := [i for i in ids if i not in done]:
        raise HTTPException(404, f"Clip not found: {', '.join(missing)}")
    ranks = list(range(1, len(ids) + 1))
    if body.reveal is not None and sorted(body.reveal) != ranks:
        raise HTTPException(422, f"The reveal order must use each rank from 1 to {len(ids)} once.")
    reveal = body.reveal or countdown.shuffled_order(len(ids))
    title = body.title.strip() or f"Top {len(ids)} moments"
    store.update(project_id, countdown={
        "status": "queued", "progress": 0.0, "message": "Waiting in queue", "error": None,
        "items": [{"id": i.id, "title": i.title.strip()} for i in body.items], "title": title,
        "reveal": reveal, "clip_max": body.clip_max, "video": None, "duration": None,
    })
    jobs.enqueue_countdown(project_id)
    return _get(project_id)


@app.delete("/api/projects/{project_id}/countdown")
def delete_countdown(project_id: str) -> dict:
    project = _get(project_id)
    if _countdown_busy(project):
        raise HTTPException(409, "Wait until the top list video has finished rendering.")
    shutil.rmtree(store.dir(project_id) / "countdown", ignore_errors=True)
    return store.update(project_id, countdown=None)


@app.get("/api/projects/{project_id}/countdown/download")
def download_countdown(project_id: str) -> FileResponse:
    project = _get(project_id)
    job = project.get("countdown") or {}
    if job.get("status") != "done" or not job.get("video"):
        raise HTTPException(404, "Top list video not ready")
    safe_title = re.sub(r"[^\w\- ]+", "", job["title"]).strip()[:60] or "countdown"
    return FileResponse(store.dir(project_id) / job["video"], filename=f"{safe_title}.mp4")


# ---- static files ----------------------------------------------------------------

# Rendered clips, thumbnails, captions (supports range requests for video seeking).
app.mount("/media", StaticFiles(directory=PROJECTS_DIR), name="media")

# Serve the built frontend when it exists (npm run build), so one server is enough.
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
