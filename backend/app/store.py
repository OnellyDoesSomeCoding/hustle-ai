"""Project storage: one project.json per project folder.

The in-memory dict is the source of truth while the server runs; the API reads
from memory and only writes touch disk (avoids Windows file-lock issues).
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import PROJECTS_DIR


class ProjectStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._projects: dict[str, dict[str, Any]] = {}
        self._load_all()

    # ---- paths -------------------------------------------------------------
    @staticmethod
    def dir(project_id: str) -> Path:
        return PROJECTS_DIR / project_id

    # ---- persistence -------------------------------------------------------
    def _load_all(self) -> None:
        for path in PROJECTS_DIR.glob("*/project.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._projects[data["id"]] = data
            except Exception as exc:  # corrupt file: skip, don't crash startup
                print(f"[store] could not load {path}: {exc}")

    def _save(self, project: dict[str, Any]) -> None:
        folder = self.dir(project["id"])
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "project.json.tmp"
        tmp.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, folder / "project.json")
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))

    # ---- CRUD --------------------------------------------------------------
    def create(self, *, title: str, source: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        project_id = uuid.uuid4().hex[:12]
        project = {
            "id": project_id,
            "title": title,
            "created_at": time.time(),
            "updated_at": time.time(),
            "source": source,
            "options": options,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "message": "Waiting in queue",
            "error": None,
            "media": None,
            "language": None,
            "curation_mode": None,
            "clips": [],
        }
        with self._lock:
            self._projects[project_id] = project
            self._save(project)
        return copy.deepcopy(project)

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            project = self._projects.get(project_id)
            return copy.deepcopy(project) if project else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [copy.deepcopy(p) for p in self._projects.values()]
        return sorted(items, key=lambda p: p["created_at"], reverse=True)

    def update(self, project_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            project = self._projects[project_id]
            project.update(fields)
            project["updated_at"] = time.time()
            self._save(project)
            return copy.deepcopy(project)

    def delete(self, project_id: str) -> bool:
        with self._lock:
            if project_id not in self._projects:
                return False
            del self._projects[project_id]
        shutil.rmtree(self.dir(project_id), ignore_errors=True)
        return True


store = ProjectStore()
