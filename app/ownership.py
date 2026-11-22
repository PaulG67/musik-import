"""Neue Dateien dem Besitzer des Zielordners geben.

Der Prozess läuft als root, weil vorhandene Albenordner oft nicht dem
Unraid-Benutzer gehören. Frisch angelegte Dateien und Ordner übernehmen
danach den Besitzer des übergeordneten Ordners, damit Navidrome und die
Freigabe sie normal lesen können.
"""

from __future__ import annotations

import os
from pathlib import Path


def explain_io(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        target = getattr(exc, "filename", None) or ""
        if target:
            return f"Keine Schreibrechte für {target}"
        return "Keine Schreibrechte in der Musikbibliothek"
    return str(exc)


def ensure_dir(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        if created.exists():
            hand_over(created)


def hand_over(path: Path) -> None:
    if not _is_root() or not path.exists():
        return
    uid, gid = _owner_for(path)
    try:
        os.chown(path, uid, gid)
        os.chmod(path, 0o775 if path.is_dir() else 0o664)
    except OSError:
        return


def _owner_for(path: Path) -> tuple[int, int]:
    uid, gid = _ids()
    try:
        parent = path.parent.stat()
    except OSError:
        return uid, gid
    if parent.st_uid == 0:
        return uid, gid
    return parent.st_uid, parent.st_gid


def _ids() -> tuple[int, int]:
    return _number("PUID", 99), _number("PGID", 100)


def _number(name: str, fallback: int) -> int:
    try:
        return int(os.environ.get(name, "") or fallback)
    except ValueError:
        return fallback


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)
