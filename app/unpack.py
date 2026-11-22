"""ZIP-Dateien im Import auspacken, bevor die Musik analysiert wird."""

from __future__ import annotations

import hashlib
import shutil
import time
import zipfile
from pathlib import Path

from app.ownership import ensure_dir, hand_over
from app.store import load, now, update
from app.textutil import sanitize

MAX_FILES = 5000
MAX_BYTES = 8 * 1024 * 1024 * 1024
_JUNK = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}


class UnpackError(RuntimeError):
    pass


def unpack_pending(root: Path, *, force: bool, stable_seconds: int) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    for _ in range(5):
        archives = _zip_files(root)
        if not archives:
            break
        extracted = 0
        for path in archives:
            level, message, done = _unpack_one(root, path, force=force, stable_seconds=stable_seconds)
            if message:
                messages.append((level, message))
            if done:
                extracted += 1
        if not extracted:
            break
    return messages


def _zip_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not path.is_file() or path.suffix.lower() != ".zip" or path.name.startswith("."):
            continue
        rel = path.relative_to(root)
        if any(part in {"_aussortiert", "_duplikate", "_fehler"} or part.startswith(".") for part in rel.parts[:-1]):
            continue
        found.append(path)
    return found


def _unpack_one(root: Path, path: Path, *, force: bool, stable_seconds: int) -> tuple[str, str, bool]:
    rel = path.relative_to(root).as_posix()
    key = f"zip:{rel}"
    try:
        fingerprint = _fingerprint(path)
    except OSError as exc:
        return "error", f"{path.name}: {exc}", False
    previous = (load().get("items") or {}).get(key) or {}
    if not force and previous.get("fingerprint") == fingerprint and previous.get("status") == "error":
        return "", "", False
    if not force and not _settled(path, stable_seconds):
        return "", "", False
    parent = path.parent
    temp = parent / f".unpack-{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:12]}"
    try:
        count = _extract(path, temp)
        if count == 0:
            raise UnpackError("Die ZIP enthält keine Dateien")
        target = _unique_dir(parent, _folder_name(path, temp))
        ensure_dir(target.parent)
        temp.rename(target)
        _give(target)
        path.unlink()
    except (OSError, zipfile.BadZipFile, UnpackError) as exc:
        shutil.rmtree(temp, ignore_errors=True)
        message = _message(path.name, exc)
        _remember(key, fingerprint, message)
        return "error", f"{path.name}: {message}", False
    _forget(key)
    return "info", f"{path.name} ausgepackt nach {target.name}", True


def _extract(archive: Path, dest: Path) -> int:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as handle:
            infos = [info for info in handle.infolist() if not info.is_dir()]
            if any(info.flag_bits & 0x1 for info in infos):
                raise UnpackError("Die ZIP ist passwortgeschützt")
            _guard(archive, infos)
            count = 0
            for info in infos:
                parts = _member_parts(info.filename)
                if parts is None:
                    raise UnpackError("Die ZIP enthält einen ungültigen Pfad")
                if not parts or _junk(parts):
                    continue
                target = dest.joinpath(*[_component(part, file=index == len(parts) - 1) for index, part in enumerate(parts)])
                if dest.resolve() not in target.resolve().parents:
                    raise UnpackError("Die ZIP enthält einen ungültigen Pfad")
                ensure_dir(target.parent)
                written = 0
                with handle.open(info, "r") as source, target.open("wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_BYTES:
                            raise UnpackError("Die ZIP ist zu gross")
                        output.write(chunk)
                count += 1
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return count


def _guard(archive: Path, infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_FILES:
        raise UnpackError("Die ZIP enthält zu viele Dateien")
    total = sum(max(0, info.file_size) for info in infos)
    if total > MAX_BYTES:
        raise UnpackError("Die ZIP ist zu gross")
    try:
        packed = max(archive.stat().st_size, 1)
    except OSError:
        packed = 1
    if total > 50 * 1024 * 1024 and total / packed > 100:
        raise UnpackError("Die ZIP ist ungewöhnlich stark komprimiert")


def _member_parts(name: str) -> list[str] | None:
    text = (name or "").replace("\\", "/").strip()
    if not text or text.endswith("/"):
        return []
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return parts


def _junk(parts: list[str]) -> bool:
    return any(part.lower() in _JUNK or part.startswith(".") for part in parts)


def _component(name: str, *, file: bool) -> str:
    cleaned = sanitize(name, "Datei" if file else "Ordner")
    return cleaned or ("Datei" if file else "Ordner")


def _folder_name(archive: Path, extracted: Path) -> str:
    children = [item for item in extracted.iterdir() if not item.name.startswith(".")]
    stem = sanitize(archive.stem, "Archiv") or "Archiv"
    if len(children) == 1 and children[0].is_dir() and children[0].name.casefold() == stem.casefold():
        inner = children[0]
        for item in inner.iterdir():
            item.rename(extracted / item.name)
        inner.rmdir()
    return stem


def _unique_dir(root: Path, name: str) -> Path:
    candidate = root / name
    if not candidate.exists():
        return candidate
    number = 2
    while (root / f"{name} ({number})").exists():
        number += 1
    return root / f"{name} ({number})"


def _give(path: Path) -> None:
    hand_over(path)
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts)):
        hand_over(child)


def _settled(path: Path, seconds: int) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age >= seconds


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _message(name: str, exc: BaseException) -> str:
    if isinstance(exc, UnpackError):
        return str(exc)
    if isinstance(exc, zipfile.BadZipFile):
        return "Die Datei ist keine lesbare ZIP"
    return str(exc)


def _remember(key: str, fingerprint: str, reason: str) -> None:
    def mut(data):
        data["items"][key] = {
            "fingerprint": fingerprint,
            "status": "error",
            "score": 0,
            "reason": reason,
            "dest": "",
            "cover": "",
            "files": [],
            "updated": now(),
        }

    update(mut)


def _forget(key: str) -> None:
    def mut(data):
        data["items"].pop(key, None)

    update(mut)
