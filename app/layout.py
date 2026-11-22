"""Zielpfade in der Navidrome-Bibliothek."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.textutil import norm, sanitize, similarity

PRESETS: dict[str, str] = {
    "artist_album": "{albumartist}/{album}/{track} - {title}",
    "artist_year_album": "{albumartist}/{year} - {album}/{track} - {title}",
    "artist_album_year": "{albumartist}/{album} ({year})/{track} - {title}",
    "artist_album_cd": "{albumartist}/{album}/{discfolder}{track} - {title}",
}

PRESET_LABELS: dict[str, str] = {
    "artist_album": "Interpret / Album / 01 - Titel",
    "artist_year_album": "Interpret / Jahr - Album / 01 - Titel",
    "artist_album_year": "Interpret / Album (Jahr) / 01 - Titel",
    "artist_album_cd": "Interpret / Album / CD / 01 - Titel",
    "custom": "Eigenes Muster",
}

_EMPTY_PARENS = re.compile(r"\(\s*\)|\[\s*\]")
_EDGE_DASH = re.compile(r"^\s*[-._]+\s*|\s*[-._]+\s*$")
_MULTI_SPACE = re.compile(r"\s{2,}")
_TOKEN = re.compile(r"\{([a-z]+)\}")


@dataclass
class Fields:
    albumartist: str = ""
    artist: str = ""
    album: str = ""
    title: str = ""
    year: str = ""
    genre: str = ""
    track: int | None = None
    disc: int | None = None
    disc_total: int | None = None
    ext: str = ".mp3"

    def mapping(self) -> dict[str, str]:
        track = f"{int(self.track):02d}" if self.track else ""
        disc_n = int(self.disc) if self.disc else 0
        multi = disc_n > 1 or (self.disc_total or 0) > 1
        discfolder = f"CD{disc_n}/" if multi and disc_n else ""
        ext = self.ext if str(self.ext).startswith(".") else f".{self.ext or 'mp3'}"
        return {
            "albumartist": sanitize(self.albumartist or self.artist, "Unbekannt"),
            "artist": sanitize(self.artist or self.albumartist, "Unbekannt"),
            "album": sanitize(self.album, "Unbekanntes Album"),
            "title": sanitize(self.title, "Unbekannter Titel"),
            "year": sanitize(self.year, ""),
            "genre": sanitize(self.genre, "Ohne Genre"),
            "track": track,
            "disc": str(disc_n) if disc_n else "",
            "discfolder": discfolder,
            "ext": ext.lower(),
        }


def pattern_for(settings: dict) -> str:
    layout = settings.get("layout") or "artist_year_album"
    if layout == "custom":
        custom = (settings.get("custom_pattern") or "").strip()
        if custom:
            return custom
    return PRESETS.get(layout, PRESETS["artist_year_album"])


def validate_pattern(pattern: str) -> str | None:
    text = (pattern or "").strip().replace("\\", "/")
    if not text:
        return "Muster ist leer"
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return "Muster muss relativ zum Musikordner sein"
    if ".." in text.split("/"):
        return "Muster darf den Musikordner nicht verlassen"
    if "{title}" not in text:
        return "Muster braucht {title}"
    return None


def render_pattern(pattern: str, fields: Fields) -> str:
    values = fields.mapping()
    include_ext = "{ext}" not in pattern

    def repl(match: re.Match) -> str:
        return values.get(match.group(1), "")

    rendered = _TOKEN.sub(repl, pattern.replace("\\", "/"))
    parts: list[str] = []
    for raw in rendered.split("/"):
        cleaned = _clean_component(raw)
        if cleaned:
            parts.append(cleaned)
    if not parts:
        parts = [values["title"] or "Unbekannter Titel"]
    if include_ext:
        ext = values["ext"]
        if not parts[-1].lower().endswith(ext):
            parts[-1] = f"{parts[-1]}{ext}"
    return "/".join(parts)


def _clean_component(text: str) -> str:
    text = _EMPTY_PARENS.sub("", text or "")
    text = _EDGE_DASH.sub("", text)
    text = _MULTI_SPACE.sub(" ", text).strip(" .")
    return text


def find_artist_dir(root: Path, albumartist: str) -> Path | None:
    target = norm(albumartist)
    if not target or not root.is_dir():
        return None
    for child in root.iterdir():
        if child.is_dir() and not child.name.startswith(".") and norm(child.name) == target:
            return child
    return None


def _album_dir_matches(dirname: str, album: str) -> bool:
    if similarity(dirname, album) >= 0.92:
        return True
    wanted = norm(album)
    if len(wanted) < 2:
        return False
    return wanted == norm(dirname) or wanted in norm(dirname).split()


def find_existing_album(root: Path, fields: Fields) -> Path | None:
    artist_dir = find_artist_dir(root, fields.albumartist or fields.artist)
    if artist_dir is None or not fields.album:
        return None
    matches = [
        child
        for child in artist_dir.iterdir()
        if child.is_dir() and not child.name.startswith(".") and _album_dir_matches(child.name, fields.album)
    ]
    if not matches:
        return None
    if fields.year:
        for child in matches:
            if fields.year in child.name:
                return child
    if len(matches) == 1:
        return matches[0]
    return None


def destination(root: Path, fields: Fields, settings: dict) -> Path:
    if settings.get("prefer_existing", True):
        existing = find_existing_album(root, fields)
        if existing is not None:
            values = fields.mapping()
            filename = f"{values['track']} - {values['title']}" if values["track"] else values["title"]
            filename = _clean_component(filename) or values["title"]
            ext = values["ext"]
            if not filename.lower().endswith(ext):
                filename = f"{filename}{ext}"
            discfolder = values["discfolder"]
            return existing / discfolder / filename if discfolder else existing / filename
    return root / render_pattern(pattern_for(settings), fields)


def within_root(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
