"""MusicBrainz, Cover Art Archive, LRCLIB und optional AcoustID."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace

from app.identify import Hint, TrackMeta, identify, meta_from_hint
from app.textutil import similarity

logger = logging.getLogger("musik-import")

UA = "MusikImport/1.0 ( https://github.com/PaulG67/musik-import )"


class WebMetadata:
    def __init__(self, settings: dict, opener=None, sleeper=None, runner=None):
        self.settings = settings
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleeper or time.sleep
        self.runner = runner or subprocess.run
        self._last_mb = 0.0
        self._lock = threading.Lock()

    def enrich(self, hints: list[Hint]) -> list[TrackMeta]:
        prepared = self._apply_acoustid(hints)
        try:
            return identify(prepared, self)
        except Exception:
            logger.exception("MusicBrainz-Suche fehlgeschlagen")
            return [meta_from_hint(hint) for hint in prepared]

    def search_releases(self, query: str) -> list[dict]:
        data = self._get_json(
            "https://musicbrainz.org/ws/2/release",
            {"query": query, "fmt": "json", "limit": "5"},
        )
        if isinstance(data, dict):
            return data.get("releases") or []
        return []

    def lookup_release(self, mbid: str) -> dict | None:
        data = self._get_json(
            f"https://musicbrainz.org/ws/2/release/{mbid}",
            {"fmt": "json", "inc": "recordings+artists+artist-credits+release-groups+genres+media"},
        )
        return data if isinstance(data, dict) else None

    def search_recordings(self, query: str) -> list[dict]:
        data = self._get_json(
            "https://musicbrainz.org/ws/2/recording",
            {"query": query, "fmt": "json", "limit": "5"},
        )
        if isinstance(data, dict):
            return data.get("recordings") or []
        return []

    def lookup_recording(self, mbid: str) -> dict | None:
        data = self._get_json(
            f"https://musicbrainz.org/ws/2/recording/{mbid}",
            {"fmt": "json", "inc": "releases+artists+artist-credits+release-groups+genres"},
        )
        return data if isinstance(data, dict) else None

    def cover_for(self, meta: TrackMeta) -> bytes | None:
        if not self.settings.get("fetch_cover", True):
            return None
        if meta.mb_release_id:
            data = self._download(f"https://coverartarchive.org/release/{meta.mb_release_id}/front-1200")
            if data:
                return data
        if meta.mb_releasegroup_id:
            data = self._download(
                f"https://coverartarchive.org/release-group/{meta.mb_releasegroup_id}/front-1200"
            )
            if data:
                return data
        return self._itunes_cover(meta.albumartist or meta.artist, meta.album)

    def lyrics_for(self, meta: TrackMeta, duration: float | None) -> tuple[str, str]:
        if not self.settings.get("fetch_lyrics", True):
            return "", ""
        artist = meta.artist or meta.albumartist
        title = meta.title
        if not artist or not title:
            return "", ""
        params = {"artist_name": artist, "track_name": title}
        if meta.album:
            params["album_name"] = meta.album
        if duration:
            params["duration"] = str(int(duration))
        data = self._get_json("https://lrclib.net/api/get", params)
        if not isinstance(data, dict):
            found = self._get_json(
                "https://lrclib.net/api/search",
                {"artist_name": artist, "track_name": title},
            )
            data = _pick_lyric(found, duration, meta.album)
        if not isinstance(data, dict) or data.get("instrumental"):
            return "", ""
        return (data.get("plainLyrics") or "").strip(), (data.get("syncedLyrics") or "").strip()

    def _apply_acoustid(self, hints: list[Hint]) -> list[Hint]:
        key = (self.settings.get("acoustid_api_key") or "").strip()
        if not key:
            return hints
        updated = []
        for hint in hints:
            if hint.title and (hint.artist or hint.albumartist):
                updated.append(hint)
                continue
            fingerprint = self._fingerprint(hint)
            if not fingerprint:
                updated.append(hint)
                continue
            duration, signature = fingerprint
            data = self._get_json(
                "https://api.acoustid.org/v2/lookup",
                {
                    "client": key,
                    "meta": "recordings+releasegroups",
                    "duration": str(int(duration)),
                    "fingerprint": signature,
                },
            )
            recording = _best_acoustid(data if isinstance(data, dict) else None)
            if not recording:
                updated.append(hint)
                continue
            artists = recording.get("artists") or []
            artist = (artists[0].get("name") if artists else "") or hint.artist
            groups = recording.get("releasegroups") or []
            album = (groups[0].get("title") if groups else "") or hint.album
            updated.append(
                replace(
                    hint,
                    title=hint.title or recording.get("title") or "",
                    artist=hint.artist or artist,
                    album=hint.album or album or "",
                    albumartist=hint.albumartist or artist,
                    mb_recording_id=hint.mb_recording_id or recording.get("id") or "",
                    duration=hint.duration or duration,
                )
            )
        return updated

    def _fingerprint(self, hint: Hint) -> tuple[float, str] | None:
        if not hint.path:
            return None
        try:
            proc = self.runner(
                ["fpcalc", "-json", "-length", "120", hint.path],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if getattr(proc, "returncode", 1) != 0:
            return None
        try:
            data = json.loads(proc.stdout or "")
        except json.JSONDecodeError:
            return None
        if not data.get("fingerprint"):
            return None
        return float(data.get("duration") or 0), data["fingerprint"]

    def _itunes_cover(self, artist: str, album: str) -> bytes | None:
        term = " ".join(part for part in (artist, album) if part).strip()
        if not term:
            return None
        data = self._get_json(
            "https://itunes.apple.com/search",
            {"term": term, "entity": "album", "media": "music", "limit": "1"},
        )
        results = (data or {}).get("results") if isinstance(data, dict) else None
        if not results:
            return None
        url = results[0].get("artworkUrl100") or ""
        if not url:
            return None
        large = url.replace("100x100bb", "1000x1000bb").replace("100x100", "1000x1000")
        return self._download(large)

    def _get_json(self, url: str, params: dict | None) -> dict | list | None:
        full = url
        if params:
            full = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
        self._limit(full)
        request = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with self.opener(request, timeout=25) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                logger.info("HTTP %s für %s", exc.code, url)
            return None
        except Exception as exc:
            logger.info("Anfrage fehlgeschlagen %s: %s", url, exc)
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _download(self, url: str) -> bytes | None:
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*"})
        try:
            with self.opener(request, timeout=30) as response:
                data = response.read(16 * 1024 * 1024)
        except Exception:
            return None
        if not data or len(data) < 32:
            return None
        return data

    def _limit(self, url: str) -> None:
        if "musicbrainz.org" not in url:
            return
        with self._lock:
            wait = 1.1 - (time.monotonic() - self._last_mb)
            if wait > 0:
                self.sleep(wait)
            self._last_mb = time.monotonic()


def _best_acoustid(data: dict | None) -> dict | None:
    if not data or data.get("status") != "ok":
        return None
    for result in data.get("results") or []:
        if float(result.get("score") or 0) < 0.8:
            continue
        recordings = result.get("recordings") or []
        if recordings:
            return recordings[0]
    return None


def _pick_lyric(found, duration: float | None, album: str) -> dict | None:
    if not isinstance(found, list) or not found:
        return None

    def rank(item: dict) -> float:
        score = 0.0
        if album and similarity(album, item.get("albumName")) >= 0.85:
            score += 5
        if duration and item.get("duration"):
            score -= abs(int(duration) - int(item["duration"]))
        else:
            score -= 30
        return score

    return max(found, key=rank)
