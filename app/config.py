"""Pfade und Einstellungen. Die Web-UI speichert die Auswahl unter CONFIG_DIR."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.layout import PRESETS, validate_pattern
from app.ownership import ensure_dir, hand_over

DEFAULTS = {
    "layout": "artist_year_album",
    "custom_pattern": "{albumartist}/{year} - {album}/{track} - {title}",
    "fetch_cover": True,
    "fetch_lyrics": True,
    "embed_cover": True,
    "write_cover_file": True,
    "overwrite_tags": False,
    "auto_move": True,
    "min_score": 70,
    "prefer_existing": True,
    "scan_after_import": True,
    "navidrome_url": "",
    "navidrome_user": "",
    "navidrome_password": "",
    "acoustid_api_key": "",
    "stable_seconds": 8,
    "watch": True,
}

_BOOL = {
    "fetch_cover",
    "fetch_lyrics",
    "embed_cover",
    "write_cover_file",
    "overwrite_tags",
    "auto_move",
    "prefer_existing",
    "scan_after_import",
    "watch",
}

_LAYOUTS = set(PRESETS) | {"custom"}


def import_dir() -> Path:
    return Path(os.environ.get("IMPORT_DIR", "/import"))


def music_dir() -> Path:
    return Path(os.environ.get("MUSIC_DIR", "/music"))


def config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR", "/config"))


def _env_settings() -> dict:
    out: dict = {}
    mapping = {
        "LAYOUT": "layout",
        "CUSTOM_PATTERN": "custom_pattern",
        "NAVIDROME_URL": "navidrome_url",
        "NAVIDROME_USER": "navidrome_user",
        "NAVIDROME_PASSWORD": "navidrome_password",
        "ACOUSTID_API_KEY": "acoustid_api_key",
    }
    for env_name, key in mapping.items():
        if os.environ.get(env_name):
            out[key] = os.environ[env_name]
    for key in _BOOL:
        env_name = key.upper()
        if env_name in os.environ:
            out[key] = os.environ[env_name].strip().lower() in {"1", "true", "yes", "on"}
    if os.environ.get("MIN_SCORE", "").isdigit():
        out["min_score"] = int(os.environ["MIN_SCORE"])
    if os.environ.get("STABLE_SECONDS", "").isdigit():
        out["stable_seconds"] = int(os.environ["STABLE_SECONDS"])
    return out


def load_settings() -> dict:
    data = dict(DEFAULTS)
    data.update(_env_settings())
    path = config_dir() / "settings.json"
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if isinstance(stored, dict):
            data.update({key: stored[key] for key in DEFAULTS if key in stored})
    return _coerce(data)


def save_settings(incoming: dict) -> dict:
    current = load_settings()
    for key in DEFAULTS:
        if key in incoming:
            current[key] = incoming[key]
    clean = _coerce(current)
    if clean["layout"] == "custom":
        error = validate_pattern(clean["custom_pattern"])
        if error:
            raise ValueError(error)
    path = config_dir() / "settings.json"
    ensure_dir(path.parent)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    hand_over(path)
    return clean


def _coerce(data: dict) -> dict:
    out = dict(DEFAULTS)
    out.update(data)
    for key in _BOOL:
        out[key] = bool(out.get(key))
    try:
        out["min_score"] = max(0, min(100, int(out.get("min_score"))))
    except (TypeError, ValueError):
        out["min_score"] = 70
    try:
        out["stable_seconds"] = max(3, min(180, int(out.get("stable_seconds"))))
    except (TypeError, ValueError):
        out["stable_seconds"] = 8
    layout = str(out.get("layout") or "artist_year_album")
    out["layout"] = layout if layout in _LAYOUTS else "artist_year_album"
    out["custom_pattern"] = str(out.get("custom_pattern") or DEFAULTS["custom_pattern"]).strip()
    for key in ("navidrome_url", "navidrome_user", "navidrome_password", "acoustid_api_key"):
        out[key] = str(out.get(key) or "").strip()
    return out
