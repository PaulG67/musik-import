"""Navidrome nach dem Einsortieren neu einlesen lassen."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import urllib.parse
import urllib.request

logger = logging.getLogger("musik-import")


def token_for(password: str, salt: str) -> str:
    return hashlib.md5((password + salt).encode("utf-8")).hexdigest()


def subsonic_call(method: str, url: str, user: str, password: str, opener=None) -> tuple[bool, str]:
    base = (url or "").rstrip("/")
    if not base or not user or not password:
        return False, "Navidrome ist nicht eingerichtet"
    salt = secrets.token_hex(6)
    params = {
        "u": user,
        "t": token_for(password, salt),
        "s": salt,
        "v": "1.16.1",
        "c": "MusikImport",
        "f": "json",
    }
    endpoint = method if method.endswith(".view") else f"{method}.view"
    target = f"{base}/rest/{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(target, headers={"User-Agent": "MusikImport/1.0"})
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.info("Navidrome %s fehlgeschlagen: %s", method, exc)
        return False, f"Navidrome nicht erreichbar: {exc}"
    payload = body.get("subsonic-response") or {}
    if payload.get("status") != "ok":
        error = (payload.get("error") or {}).get("message") or "Navidrome hat die Anfrage abgelehnt"
        return False, error
    if method == "ping":
        version = payload.get("serverVersion") or payload.get("version") or ""
        kind = payload.get("type") or "navidrome"
        return True, f"{kind} {version}".strip()
    return True, "Scan gestartet"


def start_scan(url: str, user: str, password: str, opener=None) -> tuple[bool, str]:
    return subsonic_call("startScan", url, user, password, opener=opener)


def ping(url: str, user: str, password: str, opener=None) -> tuple[bool, str]:
    return subsonic_call("ping", url, user, password, opener=opener)
