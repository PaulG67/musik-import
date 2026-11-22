"""Interpret, Album und Titel aus Datei- und Ordnernamen lesen."""

from __future__ import annotations

import re

from app.textutil import year_of

_DISC_DIR = re.compile(r"^(?:cd|disc|disk)\s*0*(\d{1,2})$", re.IGNORECASE)
_TRACK_PREFIX = re.compile(
    r"^(?:(?P<disc>\d{1,2})\s*[-.]\s*)?(?P<track>\d{1,3})\s*[-._]\s*(?P<rest>.+)$"
)
_YEAR_TAIL = re.compile(
    r"^(?P<body>.+?)\s*[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]\s*$"
)


def disc_from_dirname(name: str) -> int | None:
    match = _DISC_DIR.match((name or "").strip())
    if not match:
        return None
    number = int(match.group(1))
    return number or None


def parse_folder(name: str) -> dict:
    """`Interpret - Album (Jahr)`, `Album (Jahr)` oder nur der Albumname."""
    raw = (name or "").strip()
    year = ""
    year_match = _YEAR_TAIL.match(raw)
    if year_match:
        raw = year_match.group("body").strip()
        year = year_match.group(2)
    artist = ""
    album = raw
    if " - " in raw:
        artist, album = [part.strip() for part in raw.split(" - ", 1)]
    return {
        "artist": artist,
        "albumartist": artist,
        "album": album,
        "year": year or year_of(name),
    }


def parse_filename(stem: str) -> dict:
    title = (stem or "").strip()
    artist = ""
    track = None
    disc = None
    match = _TRACK_PREFIX.match(title)
    if match:
        track = int(match.group("track")) or None
        if match.group("disc"):
            disc = int(match.group("disc")) or None
        title = match.group("rest").strip()
    if " - " in title:
        left, right = [part.strip() for part in title.split(" - ", 1)]
        if left and right and not left.isdigit():
            artist = left
            title = right
    return {"artist": artist, "title": title, "track": track, "disc": disc}


def folder_hint(key: str) -> dict:
    """Gruppenordner `Interpret/Album` oder `Interpret - Album (Jahr)`."""
    parts = [part for part in (key or "").replace("\\", "/").split("/") if part and part != "."]
    if not parts:
        return {"artist": "", "albumartist": "", "album": "", "year": ""}
    parsed = parse_folder(parts[-1])
    if not parsed["artist"] and len(parts) >= 2 and disc_from_dirname(parts[-2]) is None:
        parent = parts[-2]
        if disc_from_dirname(parent) is None:
            parsed["artist"] = parent
            parsed["albumartist"] = parent
    return parsed
