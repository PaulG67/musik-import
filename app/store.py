"""Zustand und Verlauf unter CONFIG_DIR."""

from __future__ import annotations

import json
import threading
from datetime import datetime

from app.config import config_dir

_lock = threading.Lock()


def load() -> dict:
    path = config_dir() / "state.json"
    if not path.is_file():
        return {"items": {}, "log": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": {}, "log": []}
    if not isinstance(data, dict):
        return {"items": {}, "log": []}
    data.setdefault("items", {})
    data.setdefault("log", [])
    return data


def update(mutator):
    with _lock:
        data = load()
        mutator(data)
        data["log"] = list(data.get("log") or [])[-300:]
        path = config_dir() / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")
