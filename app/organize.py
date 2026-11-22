"""Import analysieren, Tags ergänzen und in die Bibliothek verschieben."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from app.config import config_dir, import_dir, load_settings, music_dir
from app.identify import Hint, TrackMeta, meta_from_hint
from app.layout import Fields, destination, within_root
from app.metadata import WebMetadata
from app.navidrome import start_scan
from app.ownership import ensure_dir, explain_io, hand_over
from app.parse import disc_from_dirname, folder_hint, parse_filename
from app.store import load, now, update
from app.tags import (
    AUDIO_EXT,
    TagError,
    Tags,
    image_mime,
    is_better,
    quality_label,
    read_cover,
    read_quality,
    read_tags,
    write_tags,
)
from app.textutil import similarity, year_of
from app.unpack import unpack_pending

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
                "cover": _group_has_art(group, item),
                "files": files,
            }
        )
    return {"groups": groups}


def cover_for(key: str):
    cached = cover_path(key)
    if cached is not None:
        return cached
    group = _find_group(key)
    if group is None:
        return None
    for path in group["paths"]:
        data = read_cover(path)
        if data:
            return data
    return _local_cover(group["paths"], group)


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


def delete_import(key: str, rels: list[str]) -> dict:
    group = _find_group(key)
    if group is None:
        return {"status": "error", "message": "Nicht mehr im Import"}
    wanted = {item for item in rels if item}
    if not wanted:
        return {"status": "error", "message": "Datei fehlt"}
    if not _lock.acquire(blocking=True, timeout=120):
        return {"status": "error", "message": "Import läuft bereits"}
    try:
        root = import_dir().resolve()
        removed = []
        for path in group["paths"]:
            rel = path.relative_to(import_dir()).as_posix()
            if rel not in wanted or not path.exists():
                continue
            resolved = path.resolve()
            if resolved != root and root not in resolved.parents:
                continue
            path.unlink()
            sidecar = path.with_suffix(".lrc")
            if sidecar.is_file():
                sidecar.unlink()
            removed.append(path.name)
        if removed:
            _log("info", "Aus dem Import gelöscht: " + ", ".join(removed))

        def mut(data):
            data["items"].pop(key, None)

        update(mut)
        return {"status": "deleted", "count": len(removed), "message": f"{len(removed)} Datei(en) aus dem Import gelöscht"}
    finally:
        _lock.release()


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


def process_group(group: dict, settings: dict, source, *, trust: bool = False, overrides: dict | None = None, index: dict | None = None) -> dict:
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
    return _commit(group, decisions, cover, settings, source, index if index is not None else {})


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
    index: dict = {}
    notes, fresh = unpack_pending(
        import_dir(),
        force=force,
        stable_seconds=int(settings["stable_seconds"]),
    )
    for level, message in notes:
        _log(level, message)
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
            and previous.get("status") in {"held", "error", "duplicate"}
        ):
            held += 1
            continue
        if (
            not force
            and not _from_archive(group["key"], fresh)
            and not _stable(group["paths"], int(settings["stable_seconds"]))
        ):
            continue
        outcome = process_group(group, settings, source, trust=False, index=index)
        if int(outcome.get("count") or 0):
            moved += int(outcome["count"])
        if outcome["status"] in {"held", "error", "duplicate"}:
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
        if int(result.get("count") or 0) and settings.get("scan_after_import") and settings.get("navidrome_url"):
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


def _commit(group, decisions, cover: bytes | None, settings: dict, source, index: dict) -> dict:
    music = music_dir()
    try:
        ensure_dir(music)
    except OSError as exc:
        message = explain_io(exc)
        _remember(group, status="error", score=0, reason=message, dest="", cover="", files=[])
        return {"status": "error", "count": 0, "message": message}
    moved = []
    kept = []
    try:
        for item in decisions:
            path: Path = item["path"]
            merged = _compose_tags(item["tags"], item["meta"], item["hint"], settings)
            fields = _fields(merged, path)
            dest = destination(music, fields, settings)
            if not within_root(music, dest):
                raise TagError("Ziel liegt ausserhalb der Bibliothek")
            wanted = _wanted(merged, item)
            matches = _find_matches(music, dest, wanted, index)
            same_paths = set()
            for candidate in matches:
                try:
                    same_paths.add(candidate.resolve())
                except OSError:
                    continue
            try:
                dest_taken = dest.exists() and dest.resolve() not in same_paths
            except OSError:
                dest_taken = dest.exists()
            if dest_taken:
                kept.append(_blocked_row(item, dest))
                continue
            if matches:
                existing = max(matches, key=_quality_rank)
                new_quality = read_quality(path)
                old_quality = read_quality(existing)
                if not is_better(new_quality, old_quality):
                    kept.append(_kept_row(music, item, existing, new_quality, old_quality))
                    continue
                replaced = existing
            else:
                replaced = None
            lyrics_synced = _attach_lyrics(item, merged, settings, source)
            file_cover = cover if settings.get("embed_cover", True) else None
            write_tags(path, merged, cover=file_cover, only_missing=not settings.get("overwrite_tags"))
            _place_file(path, dest, replaced)
            if lyrics_synced:
                sidecar = dest.with_suffix(".lrc")
                sidecar.write_text(lyrics_synced + "\n", encoding="utf-8")
                hand_over(sidecar)
            if cover and settings.get("write_cover_file", True):
                _write_cover_file(dest.parent, cover)
            if replaced is not None:
                new_label = quality_label(path.suffix, new_quality)
                old_label = quality_label(replaced.suffix, old_quality)
                detail = f"ersetzt ({new_label} statt {old_label})"
            else:
                detail = ""
            moved.append((path, dest, merged, detail))
            index["rows"] = None
    except (TagError, OSError) as exc:
        logger.exception("Import von %s", group["key"])
        message = explain_io(exc)
        _remember(group, status="error", score=0, reason=message, dest="", cover="", files=[])
        _log("error", f"{group['key']}: {message}")
        return {"status": "error", "count": len(moved), "message": message}

    def mut(data):
        data["items"].pop(group["key"], None)
        for src, dest, merged, detail in moved:
            rel = dest.relative_to(music).as_posix()
            message = f"{merged.artist or merged.albumartist} – {merged.title} → {rel}"
            if detail:
                message = f"{merged.artist or merged.albumartist} – {merged.title} {detail} → {rel}"
            data["log"].append(
                {
                    "time": now(),
                    "level": "info",
                    "message": message,
                    "src": src.name,
                    "dest": rel,
                }
            )

    update(mut)
    if kept:
        files = [item["file"] for item in kept]
        reason = files[0]["note"] if len(files) == 1 else f"{len(files)} Titel sind schon in der Bibliothek, in gleicher oder besserer Qualität."
        kept_group = {"key": group["key"], "paths": [item["path"] for item in kept]}
        preview = files[0].get("existing") or ""
        _remember(
            kept_group,
            status="duplicate",
            score=min(item["score"] for item in kept),
            reason=reason,
            dest=preview,
            cover=_store_cover(group["key"], cover),
            files=files,
        )
        _log("info", f"{group['key']}: {reason}")
        message = reason
        if moved:
            message = f"{len(moved)} Datei(en) einsortiert. {reason}"
        return {"status": "duplicate", "count": len(moved), "kept": len(kept), "message": message}
    status = "moved" if moved else "empty"
    return {"status": status, "count": len(moved), "kept": 0, "message": f"{len(moved)} Datei(en) einsortiert" if moved else ""}


def _attach_lyrics(item, merged: Tags, settings: dict, source) -> str:
    if not _want_lyrics(item["tags"], settings):
        return ""
    try:
        plain, synced = source.lyrics_for(item["meta"], item["tags"].duration or item["hint"].duration)
    except Exception:
        logger.exception("Songtext")
        return ""
    if plain and (settings.get("overwrite_tags") or not merged.lyrics):
        merged.lyrics = plain
    return synced or ""


def _place_file(src: Path, dest: Path, replaced: Path | None) -> None:
    ensure_dir(dest.parent)
    temporary = dest.with_name(f".{dest.name}.musik-import")
    shutil.copy2(src, temporary)
    try:
        os.replace(temporary, dest)
    except OSError:
        if temporary.exists():
            temporary.unlink()
        raise
    hand_over(dest)
    if replaced is not None and replaced.exists():
        try:
            same = replaced.resolve() == dest.resolve()
        except OSError:
            same = False
        if not same:
            replaced.unlink()
            sidecar = replaced.with_suffix(".lrc")
            new_sidecar = dest.with_suffix(".lrc")
            if sidecar.is_file() and sidecar.resolve() != new_sidecar.resolve():
                sidecar.unlink()
    if src.exists():
        try:
            if src.resolve() != dest.resolve():
                src.unlink()
        except OSError:
            logger.exception("Import-Datei bleibt liegen: %s", src)


def _wanted(merged: Tags, item) -> dict:
    return {
        "title": merged.title,
        "artist": merged.artist or merged.albumartist,
        "albumartist": merged.albumartist,
        "mb": merged.mb_recording_id,
        "duration": item["tags"].duration or item["hint"].duration,
    }


def _find_matches(music: Path, dest: Path, wanted: dict, index: dict) -> list[Path]:
    if not wanted.get("title") and not wanted.get("mb"):
        return [dest] if dest.is_file() else []
    matches = _match_paths(wanted, _nearby_paths(music, dest))
    if matches:
        return matches
    rows = index.get("rows")
    if rows is None:
        rows = [_row_for(path) for path in _audio_under(music) if not _in_import(path)]
        index["rows"] = rows
    return [row["path"] for row in rows if _same(wanted, row)]


def _nearby_paths(music: Path, dest: Path) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        if path in seen or _in_import(path):
            return
        seen.add(path)
        paths.append(path)

    if dest.is_file():
        add(dest)
    folders = []
    if dest.parent.is_dir():
        folders.append(dest.parent)
    artist = dest.parent.parent
    if artist.is_dir() and artist != dest.parent and artist != music and music in artist.parents:
        folders.append(artist)
    for folder in folders:
        for path in _audio_under(folder):
            add(path)
    return paths


def _match_paths(wanted: dict, paths: list[Path]) -> list[Path]:
    return [path for path in paths if _same(wanted, _row_for(path))]


def _audio_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in AUDIO_EXT:
            continue
        parts = path.relative_to(root).parts[:-1]
        if any(part in IGNORED_DIRS or part.startswith(".") for part in parts):
            continue
        found.append(path)
    return found


def _row_for(path: Path) -> dict:
    try:
        tags = read_tags(path)
    except TagError:
        tags = Tags()
    parsed = parse_filename(path.stem)
    return {
        "path": path,
        "title": tags.title or parsed.get("title") or "",
        "artist": tags.artist or parsed.get("artist") or "",
        "albumartist": tags.albumartist or "",
        "mb": tags.mb_recording_id,
        "duration": tags.duration,
    }


def _same(wanted: dict, row: dict) -> bool:
    if wanted.get("mb") and row.get("mb") and wanted["mb"] == row["mb"]:
        return True
    if similarity(wanted.get("title"), row.get("title")) < 0.92:
        return False
    artist = wanted.get("artist") or wanted.get("albumartist") or ""
    if not artist:
        return False
    others = [row.get("artist") or "", row.get("albumartist") or ""]
    if not any(similarity(artist, item) >= 0.92 for item in others if item):
        return False
    left = wanted.get("duration") or 0
    right = row.get("duration") or 0
    if left and right and abs(float(left) - float(right)) > 10:
        return False
    return True


def _in_import(path: Path) -> bool:
    root = import_dir().resolve()
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _quality_rank(path: Path) -> tuple[int, int, int, int]:
    quality = read_quality(path)
    if quality.lossless:
        return (1, quality.bits, quality.sample_rate, quality.bitrate)
    return (0, 0, 0, quality.bitrate)


def _public_row(item, *, note: str, existing: str, duplicate: bool) -> dict:
    meta = item["meta"]
    hint = item["hint"]
    path: Path = item["path"]
    return {
        "path": path,
        "score": item["score"],
        "file": {
            "rel": hint.key,
            "name": path.name,
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
            "has_cover": bool(item["tags"].has_cover),
            "has_lyrics": bool(item["tags"].lyrics),
            "size": path.stat().st_size if path.exists() else 0,
            "duplicate": duplicate,
            "existing": existing,
            "note": note,
        },
    }


def _kept_row(music: Path, item, existing: Path, new_quality, old_quality) -> dict:
    try:
        rel = existing.relative_to(music).as_posix()
    except ValueError:
        rel = existing.name
    new_label = quality_label(item["path"].suffix, new_quality)
    old_label = quality_label(existing.suffix, old_quality)
    note = f"Schon vorhanden ({old_label}): {rel}. Diese Datei hat {new_label} und bleibt im Import."
    return _public_row(item, note=note, existing=rel, duplicate=True)


def _blocked_row(item, dest: Path) -> dict:
    try:
        rel = dest.relative_to(music_dir()).as_posix()
    except ValueError:
        rel = dest.name
    note = f"Der Name ist schon belegt ({rel}) und gehört zu einem anderen Titel. Diese Datei bleibt im Import."
    return _public_row(item, note=note, existing=rel, duplicate=True)


def _store_cover(key: str, cover: bytes | None) -> str:
    if not cover:
        return ""
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    ext = ".png" if image_mime(cover) == "image/png" else ".jpg"
    path = config_dir() / "covers" / f"{digest}{ext}"
    ensure_dir(path.parent)
    path.write_bytes(cover)
    hand_over(path)
    return str(path)


def _group_has_art(group, item) -> bool:
    raw = item.get("cover") or ""
    if raw and Path(raw).is_file():
        return True
    for path in group["paths"]:
        if read_cover(path):
            return True
    if group["paths"] and _local_cover(group["paths"], group):
        return True
    return False


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
    for path in paths:
        embedded = read_cover(path)
        if embedded:
            return embedded
    local = _local_cover(paths, group)
    if local or not settings.get("fetch_cover", True):
        return local
    primary = next((meta for meta in metas if meta.mb_release_id), metas[0] if metas else None)
    data = None
    if primary is not None:
        try:
            data = source.cover_for(primary)
        except Exception:
            logger.exception("Cover")
            data = None
    return data or local


def _local_cover(paths, group) -> bytes | None:
    start = paths[0].parent
    root = import_dir().resolve()
    current = start.resolve()
    seen = set()
    while current not in seen:
        seen.add(current)
        found = _folder_cover(current)
        if found is not None:
            return found.read_bytes()
        if current == root or root not in current.parents:
            break
        current = current.parent
    top = import_dir() / group["key"]
    found = _folder_cover(top)
    if found is not None:
        return found.read_bytes()
    return None


def _folder_cover(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    wanted = {name.lower() for name in COVER_NAMES}
    images = []
    try:
        children = list(folder.iterdir())
    except OSError:
        return None
    for child in children:
        if not child.is_file() or child.name.startswith("."):
            continue
        try:
            if child.stat().st_size >= 20 * 1024 * 1024:
                continue
        except OSError:
            continue
        if child.name.lower() in wanted:
            return child
        if child.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            images.append(child)
    if len(images) == 1:
        return images[0]
    return None


def _write_cover_file(folder: Path, cover: bytes) -> None:
    if _folder_cover(folder) is not None:
        return
    name = "cover.png" if image_mime(cover) == "image/png" else "cover.jpg"
    target = folder / name
    target.write_bytes(cover)
    hand_over(target)


def _sideline(path: Path, folder: str, key: str) -> None:
    dest_dir = import_dir() / folder / key
    ensure_dir(dest_dir)
    target = dest_dir / path.name
    if target.exists():
        target = target.with_name(f"{path.stem}-{int(time.time())}{path.suffix}")
    shutil.move(str(path), str(target))
    hand_over(target)


def _remember_held(group, decisions, cover: bytes | None, settings: dict) -> None:
    cover_file = _store_cover(group["key"], cover)
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


def _from_archive(key: str, fresh: set[str]) -> bool:
    return any(key == folder or key.startswith(folder + "/") for folder in fresh)


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
