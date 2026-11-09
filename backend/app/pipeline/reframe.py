"""Face-tracked 9:16 framing.

The clip is sampled a few times per second and faces are found with OpenCV's
YuNet detector. Faces are grouped into tracks (one per person), and mouth
movement picks the person who is talking. The result is a few steady "shots":
the crop holds still while the speaker stays put and cuts to a new position when
the speaker changes or the scene cuts. That looks like an edited multi-cam shot
instead of a camera that wobbles after every head movement.

Animation mode adds an anime-face detector, keeps characters who hold still (in
limited animation that's normal, not a poster on the wall), and where no face is
found (most Western cartoons) follows the on-screen action instead: the centre of
the pixels that change between frames, which is usually the character moving or
talking.

Everything here runs on the CPU (a 60 s clip takes a few seconds), so it doesn't
compete with Whisper or Ollama for GPU memory.
"""

from __future__ import annotations

import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ffmpeg_path
from . import models
from .edit import Timeline
from .media import _NO_WINDOW, OUT_H, OUT_W, MediaError

# Each model has a mirror, so one site being down doesn't turn face tracking off.
YUNET = ("face_detection_yunet_2023mar.onnx", 100_000, [
    "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
])
ANIME = ("lbpcascade_animeface.xml", 100_000, [  # nagadomi/lbpcascade_animeface, MIT licence
    "https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml",
    "https://github.com/nagadomi/lbpcascade_animeface/raw/master/lbpcascade_animeface.xml",
])

SAMPLE_FPS = 5        # face samples per second
DETECT_W = 960        # frames are scaled to this width (small faces need the pixels for mouth movement)
MIN_FACE_AREA = 0.002  # ignore tiny faces (audience, posters), as a share of the frame
TRACK_RADIUS = 0.07   # a face within this share of the frame width continues a track
SMOOTH = 3            # samples on each side used to smooth mouth movement (~0.6 s)
DEAD_ZONE = 0.06      # how far (share of frame width) the subject may drift before re-framing
HOLD = 3              # samples the subject must stay outside the dead zone to re-frame
MIN_SHOT = 1.0        # seconds; shorter shots are merged into the previous one
STATIC_MOTION = 0.12  # average mouth movement below this = a photo, not a person
MAX_GAP = 1.5         # seconds without faces to bridge with the last position; longer gaps centre the crop
ACTION_W = 160        # width of the tiny frames used to find on-screen action (animation)


class ReframeUnavailable(RuntimeError):
    """Face tracking can't run (OpenCV missing or model download failed)."""


@dataclass
class Face:
    cx: float      # centre x, 0..1 of the frame width
    area: float    # 0..1 of the frame area
    motion: float  # mouth movement relative to the rest of the face (~1+ while talking, 0 when unknown)


@dataclass
class Track:
    cx: float
    seen: dict[int, Face] = field(default_factory=dict)


def needs_tracking(media: dict) -> bool:
    """Only sources wider than 9:16 have room to move the crop sideways."""
    return media["width"] / media["height"] > OUT_W / OUT_H * 1.05


def _ensure_model(model: tuple[str, int, list[str]] = YUNET) -> Path:
    try:
        return models.ensure(model)
    except models.ModelUnavailable as exc:
        raise ReframeUnavailable(f"Face tracking is unavailable: {exc}") from exc


def _box_diff(cur, prev, x0: float, y0: float, x1: float, y1: float) -> float:
    h, w = cur.shape
    xa, xb = max(0, int(x0)), min(w, int(x1))
    ya, yb = max(0, int(y0)), min(h, int(y1))
    if xb - xa < 2 or yb - ya < 2:
        return 0.0
    a = cur[ya:yb, xa:xb].astype("int16")
    b = prev[ya:yb, xa:xb].astype("int16")
    return float(abs(a - b).mean())


def _overlaps(box: tuple[float, float, float, float], others: list[tuple[float, float, float, float]]) -> bool:
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    return any(ox <= cx <= ox + ow and oy <= cy <= oy + oh for ox, oy, ow, oh in others)


def sample_faces(src: Path, start: float, dur: float, media: dict,
                 animation: bool = False) -> tuple[list[list[Face]], list[float | None]]:
    """Faces in each sample (SAMPLE_FPS per second) of the source range, and (animation
    only) the centre x of the on-screen action per sample (None when unclear)."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ReframeUnavailable("OpenCV isn't installed (pip install opencv-python-headless).") from exc
    model = _ensure_model()
    anime = None
    if animation:
        try:
            anime = cv2.CascadeClassifier(str(_ensure_model(ANIME)))
            if anime.empty():
                anime = None
        except (ReframeUnavailable, AttributeError) as exc:  # AttributeError: OpenCV 5 has no cascades
            print(f"[reframe] anime face detector unavailable, using faces + action only: {exc}")
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise MediaError("ffmpeg not found.")

    w = DETECT_W
    h = max(2, round(DETECT_W * media["height"] / media["width"] / 2) * 2)
    frame_bytes = w * h * 3
    detector = cv2.FaceDetectorYN.create(str(model), "", (w, h), 0.6, 0.3, 20)
    proc = subprocess.Popen(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
         "-map", "0:v:0", "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
    )
    samples: list[list[Face]] = []
    actions: list[float | None] = []
    prev_gray = None
    prev_small = None
    small_h = max(2, round(ACTION_W * h / w))
    min_anime = max(24, h // 12)
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            _, found = detector.detect(frame)
            faces = []
            boxes: list[tuple[float, float, float, float]] = []
            for f in (found if found is not None else []):
                x, y, fw, fh = (float(v) for v in f[:4])
                area = fw * fh / (w * h)
                if area < MIN_FACE_AREA:
                    continue
                boxes.append((x, y, fw, fh))
                motion = 0.0
                if prev_gray is not None:
                    # Landmarks: right eye, left eye, nose, right mouth corner, left mouth corner.
                    eye_y = (f[5] + f[7]) / 2
                    mouth_y = (f[11] + f[13]) / 2
                    mx0, mx1 = min(f[10], f[12]) - 0.15 * fw, max(f[10], f[12]) + 0.15 * fw
                    mouth = _box_diff(gray, prev_gray, mx0, mouth_y - 0.18 * fh, mx1, mouth_y + 0.22 * fh)
                    # Dividing by the eye area's change cancels out head and camera movement:
                    # a talking mouth moves more than the rest of the face.
                    eyes = _box_diff(gray, prev_gray, x, eye_y - 0.15 * fh, x + fw, eye_y + 0.10 * fh)
                    motion = mouth / (eyes + 4.0)
                faces.append(Face(cx=(x + fw / 2) / w, area=area, motion=motion))
            if anime is not None:
                found_a = anime.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=5,
                                                 minSize=(min_anime, min_anime))
                for (x, y, fw, fh) in (found_a if len(found_a) else []):
                    x, y, fw, fh = float(x), float(y), float(fw), float(fh)
                    if fw * fh / (w * h) < MIN_FACE_AREA or _overlaps((x, y, fw, fh), boxes):
                        continue
                    boxes.append((x, y, fw, fh))
                    motion = 0.0
                    if prev_gray is not None:
                        # No landmarks here: anime mouths sit in the lower part of the box, eyes in the middle.
                        mouth = _box_diff(gray, prev_gray, x + 0.2 * fw, y + 0.62 * fh, x + 0.8 * fw, y + 0.98 * fh)
                        eyes = _box_diff(gray, prev_gray, x, y + 0.25 * fh, x + fw, y + 0.5 * fh)
                        motion = mouth / (eyes + 4.0)
                    faces.append(Face(cx=(x + fw / 2) / w, area=fw * fh / (w * h), motion=motion))
            if animation:
                small = cv2.resize(gray, (ACTION_W, small_h), interpolation=cv2.INTER_AREA)
                action = None
                if prev_small is not None:
                    moved = cv2.absdiff(small, prev_small) > 18
                    share = float(moved.mean())
                    # Too little = noise; too much = a camera pan or a scene cut, not a subject.
                    if 0.003 < share < 0.25:
                        cols = moved.sum(axis=0)
                        action = float((cols * np.arange(ACTION_W)).sum() / cols.sum() + 0.5) / ACTION_W
                actions.append(action)
                prev_small = small
            samples.append(faces)
            prev_gray = gray
    finally:
        proc.stdout.close()
        proc.wait()
    return samples, actions


def scene_cuts(src: Path, start: float, dur: float) -> list[float]:
    """Exact times (relative to `start`) where the source cuts to another camera angle."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return []
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-map", "0:v:0",
         "-vf", "scale=320:-2,select='gt(scene,0.3)',metadata=print:file=-", "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    cuts = []
    for line in proc.stdout.splitlines():
        if "pts_time:" in line:
            try:
                cuts.append(float(line.rsplit("pts_time:", 1)[1].split()[0]))
            except ValueError:
                pass
    return cuts


def _snap(shots: list[tuple[float, float]], cuts: list[float]) -> list[tuple[float, float]]:
    """Move each re-frame onto the camera cut that caused it. Samples are 0.2 s apart, so
    without this the crop would change a few frames after the angle changes."""
    out = shots[:1]
    for i, (t, cx) in enumerate(shots[1:], start=1):
        near = [c for c in cuts if t - 1.5 / SAMPLE_FPS <= c <= t + 0.5 / SAMPLE_FPS and c > out[-1][0]]
        out.append((min(near, key=lambda c: abs(c - t)) if near else t, cx))
    return out


def _build_tracks(samples: list[list[Face]], animation: bool = False) -> list[Track]:
    tracks: list[Track] = []
    for i, faces in enumerate(samples):
        taken: set[int] = set()
        for face in sorted(faces, key=lambda f: f.area, reverse=True):
            best, best_d = None, TRACK_RADIUS
            for ti, tr in enumerate(tracks):
                d = abs(tr.cx - face.cx)
                if ti not in taken and d < best_d:
                    best, best_d = ti, d
            if best is None:
                tracks.append(Track(cx=face.cx))
                best = len(tracks) - 1
            tr = tracks[best]
            tr.seen[i] = face
            tr.cx = tr.cx * 0.7 + face.cx * 0.3
            taken.add(best)
    # Drop flickers (false detections) and faces that never move: photos and posters on
    # the wall. Short tracks stay, because multi-cam edits show each angle only briefly.
    # In animation a character holding still is normal, so only flickers are dropped.
    def alive(t: Track) -> bool:
        if len(t.seen) < 3:
            return False
        if animation:
            return True
        motions = [f.motion for i, f in t.seen.items() if i > 0]
        return bool(motions) and sum(motions) / len(motions) >= STATIC_MOTION
    keep = [t for t in tracks if alive(t)]
    return keep or sorted(tracks, key=lambda t: len(t.seen), reverse=True)[:1]


def _speaker_path(samples: list[list[Face]], tracks: list[Track], crop_frac: float) -> list[float | None]:
    """Centre x of the chosen subject for every sample (None = no face nearby).
    `crop_frac` is the 9:16 crop's width as a share of the frame width."""
    n = len(samples)
    path: list[float | None] = []
    current: Track | None = None
    for i in range(n):
        lo, hi = max(0, i - SMOOTH), min(n, i + SMOOTH + 1)
        candidates = []
        for tr in tracks:
            # Present now, or on both sides of a missed detection. Looking only ahead would
            # switch before the camera does; looking only back would switch late.
            before = [tr.seen[j] for j in range(max(0, i - 2), i) if j in tr.seen]
            after = [tr.seen[j] for j in range(i + 1, min(n, i + 3)) if j in tr.seen]
            if i in tr.seen:
                near = [tr.seen[i]]
            elif before and after:
                near = before + after
            else:
                continue
            motions = [tr.seen[j].motion for j in range(lo, hi) if j in tr.seen]
            candidates.append((tr, max(f.area for f in near), sum(motions) / len(motions)))
        if not candidates:
            path.append(None)
            continue
        biggest = max(a for _, a, _ in candidates)
        candidates = [c for c in candidates if c[1] >= 0.35 * biggest]  # skip faces far in the background
        xs = [(c[0].seen[i].cx if i in c[0].seen else c[0].cx) for c in candidates]
        if max(xs) - min(xs) <= 0.7 * crop_frac:
            # Everyone fits in the crop: show the whole group instead of cutting between them.
            current = max(candidates, key=lambda c: c[2])[0]
            path.append((max(xs) + min(xs)) / 2)
            continue
        by_motion = sorted(candidates, key=lambda c: c[2], reverse=True)
        chosen = current if any(c[0] is current for c in candidates) else None
        top = by_motion[0]
        if chosen is None:
            # Nobody tracked yet (or they left): the clearer speaker, else the biggest face.
            clear = len(by_motion) == 1 or top[2] > 1.25 * by_motion[1][2]
            chosen = top[0] if clear else max(candidates, key=lambda c: c[1])[0]
        elif top[0] is not chosen:
            mine = next(c[2] for c in candidates if c[0] is chosen)
            if top[2] > 1.35 * mine and top[2] > 0.8:  # switch only when someone else clearly talks more
                chosen = top[0]
        current = chosen
        face = chosen.seen.get(i)
        path.append(face.cx if face else chosen.cx)
    return path


def _shots(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """(time, centre) samples -> [(shot start, centre)], holding still inside a dead zone."""
    shots: list[list] = []  # [start, [centres]]
    pending: list[tuple[float, float]] = []
    for t, cx in points:
        if not shots:
            shots.append([0.0, [cx]])
            continue
        pos = statistics.median(shots[-1][1])
        if abs(cx - pos) <= DEAD_ZONE:
            shots[-1][1].extend(c for _, c in pending)
            shots[-1][1].append(cx)
            pending = []
            continue
        pending.append((t, cx))
        spread = max(c for _, c in pending) - min(c for _, c in pending)
        if len(pending) >= HOLD and spread <= DEAD_ZONE * 2:
            shots.append([pending[0][0], [c for _, c in pending]])
            pending = []
        elif spread > DEAD_ZONE * 2:
            pending = pending[-1:]
    merged: list[list] = []
    for i, (start, centres) in enumerate(shots):
        end = shots[i + 1][0] if i + 1 < len(shots) else float("inf")
        if merged and end - start < MIN_SHOT:
            merged[-1][1].extend(centres)
        else:
            merged.append([start, list(centres)])
    shots_out: list[tuple[float, float]] = []
    for start, centres in merged:
        pos = statistics.median(centres)
        if shots_out and abs(pos - shots_out[-1][1]) <= DEAD_ZONE:
            continue  # same framing as the previous shot: no cut needed
        shots_out.append((start, pos))
    return shots_out


def _smooth_actions(actions: list[float | None]) -> list[float | None]:
    """Median over ~1 s, so the crop follows the action without twitching."""
    out: list[float | None] = []
    for i in range(len(actions)):
        near = [a for a in actions[max(0, i - 2):i + 3] if a is not None]
        out.append(statistics.median(near) if len(near) >= 2 else None)
    return out


def plan(src: Path, start: float, dur: float, media: dict, timeline: Timeline,
         animation: bool = False) -> str | None:
    """ffmpeg expression for the crop centre (0..1 of the width) over output time `t`,
    or None when there was nothing to follow (the caller then uses a centre crop)."""
    samples, actions = sample_faces(src, start, dur, media, animation)
    actions = _smooth_actions(actions) if animation else [None] * len(samples)
    if not any(samples) and not any(a is not None for a in actions):
        return None
    tracks = _build_tracks(samples, animation) if any(samples) else []
    crop_frac = media["height"] * OUT_W / OUT_H / media["width"]
    path = _speaker_path(samples, tracks, crop_frac) if tracks else [None] * len(samples)
    # Bridge short gaps (someone looks away) with the last position. Long gaps are
    # usually a different camera angle with no usable face: those follow the on-screen
    # action (animation) or get centred.
    filled: list[float] = []
    last, i = next((p for p in path if p is not None), 0.5), 0
    while i < len(path):
        if path[i] is not None:
            last = path[i]
            filled.append(last)
            i += 1
            continue
        j = i
        while j < len(path) and path[j] is None:
            j += 1
        if (j - i) / SAMPLE_FPS <= MAX_GAP and any(p is not None for p in path):  # `last` = nearest face
            filled.extend([last] * (j - i))
        else:
            held = None
            for k in range(i, j):
                held = actions[k] if actions[k] is not None else held
                filled.append(held if held is not None else 0.5)
        i = j
    points: list[tuple[float, float]] = []
    for i, last in enumerate(filled):
        t_out = timeline.to_output(i / SAMPLE_FPS)
        if t_out is not None:
            points.append((t_out, last))
    if not points:
        return None
    shots = _shots(points)
    if len(shots) > 1:
        cuts = [o for c in scene_cuts(src, start, dur) if (o := timeline.to_output(c, snap_forward=True)) is not None]
        shots = _snap(shots, cuts)
    expr = f"{shots[-1][1]:.4f}"
    for i in range(len(shots) - 2, -1, -1):
        expr = f"if(lt(t,{shots[i + 1][0]:.3f}),{shots[i][1]:.4f},{expr})"
    return expr
