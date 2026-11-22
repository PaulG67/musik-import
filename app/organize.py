"""Import analysieren, Tags ergänzen und in die Bibliothek verschieben."""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
import time
from pathlib import Path

from app.config import config_dir, import_dir, load_settings, music_dir
from app.identify import Hint, TrackMeta, meta_from_hint
from app.layout import Fields, destination, within_root
from app.metadata import WebMetadata
from app.navidrome import start_scan
from app.parse import disc_from_dirname, folder_hint, parse_filename
from app.store import load, now, update
from app.tags import AUDIO_EXT, TagError, Tags, image_mime, read_tags, write_tags
from app.textutil import year_of

logger = logging.getLogger("musik-import")

IGNORED_DIRS = {"_aussortiert", "_duplikate", "_fehler"}
COVER_NAMES = ("cover.jpg", "cover.jpeg", "folder.jpg", "front.jpg", "cover.png", "folder.png")

_lock = threading.Lock()


def scan_once(source=None, *, force: bool = False) -> dict:
    if not _lock.acquire(blocking=False):
        return {"busy": True, "moved": 0, "held": 0}
    try:
        settings = load_settings()
        active = source or WebMetadata(settings)
        result = _scan(active, settings, force=force)
        if result["moved"] and settings.get("scan_after_import") and settings.get("navidrome_url"):
            ok, message = start_scan(
                settings["navidrome_url"],
                settings["navidrome_user"],
                settings["navidrome_password"],
            )
            _log("info" if ok else "warn", message)
        return result
    finally:
        _lock.release()


def inbox() -> dict:
    settings = load_settings()
    state = load()
    groups = []
    for group in collect_groups():
        item = (state.get("items") or {}).get(group["key"]) or {}
        stable = _stable(group["paths"], int(settings["stable_seconds"]))
        fingerprint = _fingerprint(group["paths"]) if group["paths"] else ""
        if item.get("files") and item.get("fingerprint") == fingerprint:
            files = item["files"]
        else:
            files = [_public_file(path, group) for path in group["paths"]]
        groups.append(
            {
                "key": group["key"],
                "single": group["single"],
                "stable": stable,
                "status": item.get("status") or ("pending" if not stable else "new"),
                "score": item.get("score"),
                "reason": item.get("reason")
                or ("Wartet, bis der Kopiervorgang fertig ist" if not stable else "Noch nicht analysiert"),
                "dest": item.get("dest") or "",
                "cover": bool(item.get("cover")),
                "files": files,
            }
        )
    return {"groups": groups}


def cover_path(key: str) -> Path | None:
    from app.store import load

    item = (load().get("items") or {}).get(key) or {}
    raw = item.get("cover") or ""
    if not raw:
        return None
    path = Path(raw)
    root = (config_dir() / "covers").resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def apply_group(key: str, overrides: dict | None = None, source=None) -> dict:
    return _with_group(key, trust=True, overrides=overrides or {}, source=source)


def retry_group(key: str, source=None) -> dict:
    return _with_group(key, trust=False, overrides=None, source=source)


def dismiss_group(key: str) -> dict:
    group = _find_group(key)
    if group is None:
        return {"status": "error", "message": "Nicht mehr im Import"}
    if not _lock.acquire(blocking=True, timeout=120):
        return {"status": "error", "message": "Import läuft bereits"}
    try:
        for path in group["paths"]:
            if path.exists():
                _sideline(path, "_aussortiert", key)
        _log("info", f"{key}: nicht importiert")

        def mut(data):
            data["items"].pop(key, None)

        update(mut)
        return {"status": "dismissed"}
    finally:
        _lock.release()


def collect_groups(root: Path | None = None) -> list[dict]:
    root = root or import_dir()
    if not root.is_dir():
        return []
    buckets: dict[str, dict] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not _is_import_audio(root, path):
            continue
        folder = path.parent
        single = False
        if (
            folder != root
            and disc_from_dirname(folder.name) is not None
            and folder.parent != root
        ):
            folder = folder.parent
        if folder == root:
            key = path.relative_to(root).as_posix()
            single = True
        else:
            key = folder.relative_to(root).as_posix()
        bucket = buckets.setdefault(key, {"key": key, "single": single, "paths": []})
        bucket["paths"].append(path)
    return [buckets[key] for key in sorted(buckets)]


def process_group(group: dict, settings: dict, source, *, trust: bool = False, overrides: dict | None = None) -> dict:
    paths = [path for path in group["paths"] if path.exists()]
    if not paths:
        return {"status": "empty", "count": 0}
    originals = []
    hints = []
    for path in paths:
        try:
            tags = read_tags(path)
        except TagError as exc:
            _remember(group, status="error", score=0, reason=str(exc), dest="", cover="", files=[])
            _log("error", f"{group['key']}: {exc}")
            return {"status": "error", "count": 0, "message": str(exc)}
        hint = hint_from(path, tags, group)
        originals.append((path, tags, hint))
        hints.append(hint)
    if overrides:
        hints = _apply_overrides(hints, overrides)
    if trust:
        metas = []
        for hint in hints:
            meta = meta_from_hint(hint)
            meta.score = 100
            meta.source = "user"
            metas.append(meta)
    else:
        metas = source.enrich(hints)
    cover = _cover_bytes(paths, group, metas, settings, source)
    decisions = []
    for (path, tags, _hint), hint, meta in zip(originals, hints, metas):
        decisions.append({"path": path, "tags": tags, "hint": hint, "meta": meta, "score": 100 if trust else meta.score})
    min_score = 0 if trust else int(settings["min_score"])
    ready = all(_named(item["meta"], item["hint"]) and item["score"] >= min_score for item in decisions)
    if settings.get("auto_move", True) is False and not trust:
        ready = False
    if not ready:
        _remember_held(group, decisions, cover, settings)
        return {"status": "held", "count": 0, "message": "Bitte prüfen"}
    return _commit(group, decisions, cover, settings, source)


def hint_from(path: Path, tags: Tags, group: dict) -> Hint:
    root = import_dir()
    file_meta = parse_filename(path.stem)
    folder_meta = {"artist": "", "albumartist": "", "album": "", "year": ""}
    if not group.get("single"):
        folder_meta = folder_hint(group["key"])
    disc = tags.disc or file_meta.get("disc")
    for part in path.relative_to(root).parts[:-1]:
        found = disc_from_dirname(part)
        if found and not disc:
            disc = found

    def pick(*values):
        for value in values:
            if value:
                return value
        return ""

    return Hint(
        key=path.relative_to(root).as_posix(),
        path=str(path),
        title=pick(tags.title, file_meta.get("title")),
        artist=pick(tags.artist, file_meta.get("artist"), folder_meta.get("artist")),
        album=pick(tags.album, folder_meta.get("album")),
        albumartist=pick(
            tags.albumartist,
            folder_meta.get("albumartist"),
            tags.artist,
            file_meta.get("artist"),
            folder_meta.get("artist"),
        ),
        track=tags.track or file_meta.get("track"),
        track_total=tags.track_total,
        disc=disc,
        disc_total=tags.disc_total,
        year=year_of(tags.date) or folder_meta.get("year") or "",
        genre=tags.genre,
        duration=tags.duration,
        mb_recording_id=tags.mb_recording_id,
        mb_release_id=tags.mb_release_id,
    )


def _scan(source, settings: dict, *, force: bool) -> dict:
    moved = 0
    held = 0
    from app.store import load

    state = load()
    for group in collect_groups():
        if not group["paths"]:
            continue
        try:
            fingerprint = _fingerprint(group["paths"])
        except OSError:
            continue
        previous = (state.get("items") or {}).get(group["key"]) or {}
        if (
            not force
            and previous.get("fingerprint") == fingerprint
            and previous.get("status") in {"held", "error"}
        ):
            held += 1
            continue
        if not force and not _stable(group["paths"], int(settings["stable_seconds"])):
            continue
        outcome = process_group(group, settings, source, trust=False)
        if outcome["status"] == "moved":
            moved += int(outcome.get("count") or 0)
        elif outcome["status"] in {"held", "error"}:
            held += 1
        state = load()
    return {"busy": False, "moved": moved, "held": held}


def _with_group(key: str, *, trust: bool, overrides: dict | None, source) -> dict:
    group = _find_group(key)
    if group is None:
        return {"status": "error", "message": "Nicht mehr im Import"}
    if not _lock.acquire(blocking=True, timeout=180):
        return {"status": "error", "message": "Import läuft bereits"}
    try:
        settings = load_settings()
        active = source or WebMetadata(settings)
        result = process_group(group, settings, active, trust=trust, overrides=overrides)
        if result.get("status") == "moved" and settings.get("scan_after_import") and settings.get("navidrome_url"):
            ok, message = start_scan(
                settings["navidrome_url"],
                settings["navidrome_user"],
                settings["navidrome_password"],
            )
            _log("info" if ok else "warn", message)
        return result
    finally:
        _lock.release()


def _find_group(key: str) -> dict | None:
    for group in collect_groups():
        if group["key"] == key:
            return group
    return None


def _commit(group, decisions, cover: bytes | None, settings: dict, source) -> dict:
    music = music_dir()
    try:
        music.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _remember(group, status="error", score=0, reason=str(exc), dest="", cover="", files=[])
        return {"status": "error", "count": 0, "message": str(exc)}
    moved = []
    duplicates = []
    try:
        for item in decisions:
            path: Path = item["path"]
            merged = _compose_tags(item["tags"], item["meta"], item["hint"], settings)
            lyrics_synced = ""
            if _want_lyrics(item["tags"], settings):
                try:
                    plain, lyrics_synced = source.lyrics_for(
                        item["meta"], item["tags"].duration or item["hint"].duration
                    )
                except Exception:
                    logger.exception("Songtext")
                    plain, lyrics_synced = "", ""
                if plain and (settings.get("overwrite_tags") or not merged.lyrics):
                    merged.lyrics = plain
            file_cover = cover if settings.get("embed_cover", True) else None
            write_tags(path, merged, cover=file_cover, only_missing=not settings.get("overwrite_tags"))
            fields = _fields(merged, path)
            dest = destination(music, fields, settings)
            if not within_root(music, dest):
                raise TagError("Ziel liegt ausserhalb der Bibliothek")
            place, kind = _plan(path, dest)
            if kind == "duplicate":
                duplicates.append(path)
                continue
            place.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(place))
            if lyrics_synced:
                place.with_suffix(".lrc").write_text(lyrics_synced + "\n", encoding="utf-8")
            if cover and settings.get("write_cover_file", True):
                _write_cover_file(place.parent, cover)
            moved.append((path, place, merged))
    except (TagError, OSError) as exc:
        logger.exception("Import von %s", group["key"])
        _remember(group, status="error", score=0, reason=str(exc), dest="", cover="", files=[])
        _log("error", f"{group['key']}: {exc}")
        return {"status": "error", "count": len(moved), "message": str(exc)}
    for path in duplicates:
        if path.exists():
            _sideline(path, "_duplikate", group["key"])

    def mut(data):
        data["items"].pop(group["key"], None)
        for src, dest, merged in moved:
            rel = dest.relative_to(music).as_posix()
            data["log"].append(
                {
                    "time": now(),
                    "level": "info",
                    "message": f"{merged.artist or merged.albumartist} – {merged.title} → {rel}",
                    "src": src.name,
                    "dest": rel,
                }
            )
        if duplicates:
            data["log"].append(
                {
                    "time": now(),
                    "level": "warn",
                    "message": f"{group['key']}: {len(duplicates)} Duplikat(e) nach _duplikate gelegt",
                }
            )

    update(mut)
    status = "moved" if moved else ("duplicate" if duplicates else "empty")
    return {"status": status, "count": len(moved), "duplicates": len(duplicates)}


def _compose_tags(current: Tags, meta: TrackMeta, hint: Hint, settings: dict) -> Tags:
    overwrite = bool(settings.get("overwrite_tags"))

    def pick(old, new):
        if new in ("", None):
            return old
        if overwrite or old in ("", None):
            return new
        return old

    out = Tags()
    out.title = pick(current.title, meta.title or hint.title)
    out.artist = pick(current.artist, meta.artist or hint.artist)
    out.albumartist = pick(
        current.albumartist,
        meta.albumartist or meta.artist or hint.albumartist or hint.artist,
    )
    out.album = pick(current.album, meta.album or hint.album) or "Singles"
    out.genre = pick(current.genre, meta.genre or hint.genre)
    out.date = pick(current.date, meta.year or hint.year)
    out.track = pick(current.track, meta.track if meta.track is not None else hint.track)
    out.track_total = pick(
        current.track_total,
        meta.track_total if meta.track_total is not None else hint.track_total,
    )
    out.disc = pick(current.disc, meta.disc if meta.disc is not None else hint.disc)
    out.disc_total = pick(current.disc_total, meta.disc_total)
    out.lyrics = current.lyrics
    out.mb_recording_id = pick(current.mb_recording_id, meta.mb_recording_id)
    out.mb_release_id = pick(current.mb_release_id, meta.mb_release_id)
    out.mb_artist_id = pick(current.mb_artist_id, meta.mb_artist_id)
    out.mb_albumartist_id = pick(current.mb_albumartist_id, meta.mb_albumartist_id)
    out.mb_releasegroup_id = pick(current.mb_releasegroup_id, meta.mb_releasegroup_id)
    return out


def _fields(tags: Tags, path: Path) -> Fields:
    return Fields(
        albumartist=tags.albumartist or tags.artist,
        artist=tags.artist or tags.albumartist,
        album=tags.album or "Singles",
        title=tags.title or path.stem,
        year=year_of(tags.date),
        genre=tags.genre,
        track=tags.track,
        disc=tags.disc,
        disc_total=tags.disc_total,
        ext=path.suffix.lower() or ".mp3",
    )


def _named(meta: TrackMeta, hint: Hint) -> bool:
    title = meta.title or hint.title
    artist = meta.artist or meta.albumartist or hint.artist or hint.albumartist
    return bool(title and artist)


def _want_lyrics(tags: Tags, settings: dict) -> bool:
    if not settings.get("fetch_lyrics", True):
        return False
    if tags.lyrics and not settings.get("overwrite_tags"):
        return False
    return True


def _cover_bytes(paths, group, metas, settings, source) -> bytes | None:
    if not settings.get("fetch_cover", True):
        return _local_cover(paths, group)
    primary = next((meta for meta in metas if meta.mb_release_id), metas[0] if metas else None)
    data = None
    if primary is not None:
        try:
            data = source.cover_for(primary)
        except Exception:
            logger.exception("Cover")
            data = None
    return data or _local_cover(paths, group)


def _local_cover(paths, group) -> bytes | None:
    start = paths[0].parent
    root = import_dir().resolve()
    current = start.resolve()
    seen = set()
    while current not in seen:
        seen.add(current)
        for name in COVER_NAMES:
            candidate = current / name
            try:
                if candidate.is_file() and candidate.stat().st_size < 20 * 1024 * 1024:
                    return candidate.read_bytes()
            except OSError:
                continue
        if current == root or root not in current.parents:
            break
        current = current.parent
    top = import_dir() / group["key"]
    if top.is_dir():
        for name in COVER_NAMES:
            candidate = top / name
            if candidate.is_file():
                return candidate.read_bytes()
    return None


def _write_cover_file(folder: Path, cover: bytes) -> None:
    if any((folder / name).exists() for name in COVER_NAMES):
        return
    name = "cover.png" if image_mime(cover) == "image/png" else "cover.jpg"
    (folder / name).write_bytes(cover)


def _plan(src: Path, dest: Path) -> tuple[Path, str]:
    if dest.exists():
        return dest, "duplicate"
    return dest, "move"


def _sideline(path: Path, folder: str, key: str) -> None:
    dest_dir = import_dir() / folder / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / path.name
    if target.exists():
        target = target.with_name(f"{path.stem}-{int(time.time())}{path.suffix}")
    shutil.move(str(path), str(target))


def _remember_held(group, decisions, cover: bytes | None, settings: dict) -> None:
    cover_file = ""
    if cover:
        digest = hashlib.sha1(group["key"].encode("utf-8")).hexdigest()[:16]
        ext = ".png" if image_mime(cover) == "image/png" else ".jpg"
        path = config_dir() / "covers" / f"{digest}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cover)
        cover_file = str(path)
    files = []
    scores = []
    for item in decisions:
        meta = item["meta"]
        hint = item["hint"]
        files.append(
            {
                "rel": hint.key,
                "name": item["path"].name,
                "title": meta.title or hint.title,
                "artist": meta.artist or hint.artist,
                "album": meta.album or hint.album,
                "albumartist": meta.albumartist or hint.albumartist,
                "year": meta.year or hint.year,
                "track": meta.track if meta.track is not None else hint.track,
                "disc": meta.disc if meta.disc is not None else hint.disc,
                "genre": meta.genre or hint.genre,
                "score": item["score"],
                "source": meta.source,
                "has_cover": bool(item["tags"].has_cover or cover),
                "has_lyrics": bool(item["tags"].lyrics),
                "size": item["path"].stat().st_size if item["path"].exists() else 0,
            }
        )
        scores.append(item["score"])
    score = min(scores) if scores else 0
    preview = ""
    if decisions:
        sample = decisions[0]
        merged = _compose_tags(sample["tags"], sample["meta"], sample["hint"], settings)
        try:
            dest = destination(music_dir(), _fields(merged, sample["path"]), settings)
            preview = dest.relative_to(music_dir()).as_posix()
        except (OSError, ValueError):
            preview = ""
    reason = _reason(score, files, settings)
    _remember(group, status="held", score=score, reason=reason, dest=preview, cover=cover_file, files=files)
    _log("info", f"{group['key']}: {reason}")


def _reason(score: int, files: list[dict], settings: dict) -> str:
    if score >= int(settings["min_score"]) and files and all(item.get("title") and (item.get("artist") or item.get("albumartist")) for item in files):
        return "Bereit, automatisches Verschieben ist aus"
    missing = []
    if any(not item.get("title") for item in files):
        missing.append("Titel")
    if any(not (item.get("artist") or item.get("albumartist")) for item in files):
        missing.append("Interpret")
    if any(not item.get("album") for item in files):
        missing.append("Album")
    if not missing:
        missing.append("unsichere Zuordnung")
    return "Bitte prüfen: " + ", ".join(missing)


def _remember(group, *, status: str, score: int, reason: str, dest: str, cover: str, files: list) -> None:
    try:
        fingerprint = _fingerprint([path for path in group["paths"] if path.exists()])
    except OSError:
        fingerprint = ""

    def mut(data):
        data["items"][group["key"]] = {
            "fingerprint": fingerprint,
            "status": status,
            "score": score,
            "reason": reason,
            "dest": dest,
            "cover": cover,
            "files": files,
            "updated": now(),
        }

    update(mut)


def _apply_overrides(hints: list[Hint], overrides: dict) -> list[Hint]:
    from dataclasses import replace

    by_rel = {}
    for item in overrides.get("files") or []:
        by_rel[item.get("rel") or item.get("path") or ""] = item
    album = (overrides.get("album") or "").strip()
    albumartist = (overrides.get("albumartist") or "").strip()
    year = (overrides.get("year") or "").strip()
    genre = (overrides.get("genre") or "").strip()
    updated = []
    for hint in hints:
        row = by_rel.get(hint.key) or {}
        updated.append(
            replace(
                hint,
                title=(row.get("title") or "").strip() or hint.title,
                artist=(row.get("artist") or "").strip() or albumartist or hint.artist,
                album=album or (row.get("album") or "").strip() or hint.album,
                albumartist=albumartist or hint.albumartist,
                year=year or hint.year,
                genre=genre or hint.genre,
                track=_optional_int(row.get("track"), hint.track),
                disc=_optional_int(row.get("disc"), hint.disc),
            )
        )
    return updated


def _optional_int(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number or fallback


def _public_file(path: Path, group: dict) -> dict:
    try:
        tags = read_tags(path)
    except TagError:
        tags = Tags()
    hint = hint_from(path, tags, group)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "rel": hint.key,
        "name": path.name,
        "title": hint.title,
        "artist": hint.artist,
        "album": hint.album,
        "albumartist": hint.albumartist,
        "year": hint.year,
        "track": hint.track,
        "disc": hint.disc,
        "genre": hint.genre,
        "score": None,
        "source": "",
        "has_cover": tags.has_cover,
        "has_lyrics": bool(tags.lyrics),
        "size": size,
    }


def _is_import_audio(root: Path, path: Path) -> bool:
    if not path.is_file() or path.name.startswith("."):
        return False
    if path.suffix.lower() not in AUDIO_EXT:
        return False
    rel = path.relative_to(root)
    return not any(part in IGNORED_DIRS or part.startswith(".") for part in rel.parts[:-1])


def _stable(paths: list[Path], seconds: int) -> bool:
    newest = 0.0
    now_ts = time.time()
    for path in paths:
        try:
            newest = max(newest, path.stat().st_mtime, path.parent.stat().st_mtime)
        except OSError:
            return False
    return now_ts - newest >= seconds


def _fingerprint(paths: list[Path]) -> str:
    parts = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        stat = path.stat()
        parts.append(f"{path.as_posix()}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def _log(level: str, message: str) -> None:
    def mut(data):
        data["log"].append({"time": now(), "level": level, "message": message})

    update(mut)
