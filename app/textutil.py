"""Namen normalisieren und für Dateipfade säubern."""

from __future__ import annotations

import re
import unicodedata

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACE = re.compile(r"\s+")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = _SPACE.sub(" ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


def tokens(value: str | None) -> set[str]:
    return {part for part in norm(value).split() if part}


def similarity(left: str | None, right: str | None) -> float:
    a, b = norm(left), norm(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 3 and (
        longer.startswith(shorter + " ")
        or longer.endswith(" " + shorter)
        or shorter in longer.split()
    ):
        return 0.92
    ta, tb = tokens(left), tokens(right)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def sanitize(value: str | None, fallback: str) -> str:
    text = _INVALID.sub(" ", value or "")
    text = _SPACE.sub(" ", text).strip(" .")
    if not text:
        return fallback
    if text.upper() in _RESERVED:
        text = f"{text}_"
    text = text[:120].strip(" .")
    return text or fallback


def year_of(value: str | None) -> str:
    match = re.search(r"(?:19|20)\d{2}", value or "")
    return match.group(0) if match else ""
