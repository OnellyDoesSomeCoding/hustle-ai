"""Small models downloaded on first use into data/models (each with mirrors)."""

from __future__ import annotations

from pathlib import Path

import httpx

from ..config import DATA_DIR

MODELS_DIR = DATA_DIR / "models"


class ModelUnavailable(RuntimeError):
    pass


def ensure(model: tuple[str, int, list[str]]) -> Path:
    """`model` = (file name, minimum size in bytes, mirror URLs). Returns the local path."""
    name, min_size, urls = model
    path = MODELS_DIR / name
    if path.exists() and path.stat().st_size > min_size:
        return path
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    errors = []
    for url in urls:
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            if tmp.stat().st_size > min_size:
                tmp.replace(path)
                return path
            errors.append(f"{url}: incomplete download")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
        tmp.unlink(missing_ok=True)
    raise ModelUnavailable(f"Could not download {name} ({'; '.join(errors)}).")
