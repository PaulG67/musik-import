"""Lokale Hinweise mit MusicBrainz-Aufnahmen und Releases abgleichen."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from app.textutil import norm, similarity, year_of


@dataclass
class Hint:
    key: str
    path: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    track: int | None = None
    track_total: int | None = None
    disc: int | None = None
    disc_total: int | None = None
    year: str = ""
    genre: str = ""
    duration: float | None = None
    mb_recording_id: str = ""
    mb_release_id: str = ""


@dataclass
class TrackMeta:
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    year: str = ""
    genre: str = ""
    track: int | None = None
    track_total: int | None = None
    disc: int | None = None
    disc_total: int | None = None
    mb_recording_id: str = ""
    mb_release_id: str = ""
    mb_artist_id: str = ""
    mb_albumartist_id: str = ""
    mb_releasegroup_id: str = ""
    score: int = 0
    source: str = "tags"

    def to_dict(self) -> dict:
        return asdict(self)


def identify(hints: list[Hint], client) -> list[TrackMeta]:
    online = _online(hints, client)
    return [_choose(hint, meta) for hint, meta in zip(hints, online)]


def meta_from_hint(hint: Hint) -> TrackMeta:
    score = 0
    if hint.title:
        score += 30
    if hint.artist or hint.albumartist:
        score += 25
    if hint.album:
        score += 20
    if hint.track:
        score += 5
    if hint.year:
        score += 3
    return TrackMeta(
        title=hint.title,
        artist=hint.artist,
        album=hint.album,
        albumartist=hint.albumartist or hint.artist,
        year=hint.year,
        genre=hint.genre,
        track=hint.track,
        track_total=hint.track_total,
        disc=hint.disc,
        disc_total=hint.disc_total,
        mb_recording_id=hint.mb_recording_id,
        mb_release_id=hint.mb_release_id,
        score=min(score, 100),
        source="tags" if score >= 50 else "filename",
    )


def join_credit(credit) -> tuple[str, str]:
    if not credit:
        return "", ""
    parts: list[str] = []
    first_id = ""
    for index, part in enumerate(credit):
        if not isinstance(part, dict):
            continue
        name = part.get("name") or (part.get("artist") or {}).get("name") or ""
        if index == 0:
            first_id = (part.get("artist") or {}).get("id") or ""
        parts.append(name)
        parts.append(part.get("joinphrase") or "")
    return "".join(parts).strip(), first_id


def flatten_media(release: dict) -> list[dict]:
    albumartist, albumartist_id = join_credit(release.get("artist-credit"))
    rows = []
    media = release.get("media") or []
    for medium in media:
        disc = int(medium.get("position") or 1)
        tracks = medium.get("tracks") or []
        total = medium.get("track-count") or len(tracks) or None
        for track in tracks:
            recording = track.get("recording") or {}
            artist, artist_id = join_credit(
                recording.get("artist-credit") or track.get("artist-credit") or release.get("artist-credit")
            )
            number = _parse_num(track.get("number") or track.get("position"))
            rows.append(
                {
                    "title": recording.get("title") or track.get("title") or "",
                    "artist": artist,
                    "artist_id": artist_id,
                    "albumartist": albumartist,
                    "albumartist_id": albumartist_id,
                    "track": number,
                    "track_total": int(total) if total else None,
                    "disc": disc,
                    "disc_total": len(media) or None,
                    "length_ms": recording.get("length") or track.get("length"),
                    "recording_id": recording.get("id") or "",
                }
            )
    return rows


def score_pair(hint: Hint, track: dict, release: dict) -> int:
    score = 0
    title_sim = similarity(hint.title, track.get("title"))
    if title_sim == 1:
        score += 50
    elif title_sim >= 0.85:
        score += 36
    elif title_sim >= 0.6:
        score += 22
    artist_sim = max(
        similarity(hint.artist, track.get("artist")),
        similarity(hint.albumartist, track.get("albumartist")),
        similarity(hint.albumartist or hint.artist, _release_artist(release)),
    )
    if artist_sim == 1:
        score += 25
    elif artist_sim >= 0.8:
        score += 16
    album_sim = similarity(hint.album, release.get("title"))
    if album_sim == 1:
        score += 15
    elif album_sim >= 0.85:
        score += 10
    if hint.track and track.get("track") and int(hint.track) == int(track["track"]):
        score += 8
        if not hint.title:
            score += 20
    if hint.disc and track.get("disc") and int(hint.disc) == int(track["disc"]):
        score += 4
    if hint.year and year_of(str(release.get("date") or "")) == hint.year:
        score += 6
    if hint.duration and track.get("length_ms"):
        delta = abs(float(hint.duration) - float(track["length_ms"]) / 1000)
        if delta <= 3:
            score += 10
        elif delta <= 8:
            score += 4
    return min(score, 100)


def assign_tracks(hints: list[Hint], release: dict) -> list[TrackMeta | None]:
    tracks = flatten_media(release)
    used: set[int] = set()
    chosen: list[tuple[int, int] | None] = [None] * len(hints)
    for index, hint in enumerate(hints):
        title = norm(hint.title)
        if not title:
            continue
        hits = [pos for pos, track in enumerate(tracks) if pos not in used and norm(track["title"]) == title]
        if len(hits) == 1:
            used.add(hits[0])
            chosen[index] = (hits[0], score_pair(hint, tracks[hits[0]], release))
    for index, hint in enumerate(hints):
        if chosen[index] is not None:
            continue
        best_pos = None
        best_score = 0
        for pos, track in enumerate(tracks):
            if pos in used:
                continue
            score = score_pair(hint, track, release)
            if score > best_score:
                best_score = score
                best_pos = pos
        if best_pos is not None and best_score >= 45:
            used.add(best_pos)
            chosen[index] = (best_pos, best_score)
    metas: list[TrackMeta | None] = []
    for index, hint in enumerate(hints):
        if chosen[index] is None:
            metas.append(None)
            continue
        pos, score = chosen[index]
        metas.append(meta_from_track(hint, tracks[pos], release, score))
    return metas


def meta_from_track(hint: Hint, track: dict, release: dict, score: int) -> TrackMeta:
    release_group = release.get("release-group") or {}
    return TrackMeta(
        title=track.get("title") or hint.title,
        artist=track.get("artist") or hint.artist,
        album=release.get("title") or hint.album,
        albumartist=track.get("albumartist") or track.get("artist") or hint.albumartist,
        year=year_of(str(release.get("date") or "")) or hint.year,
        genre=_genre(release) or hint.genre,
        track=track.get("track") or hint.track,
        track_total=track.get("track_total") or hint.track_total,
        disc=track.get("disc") or hint.disc,
        disc_total=track.get("disc_total") or hint.disc_total,
        mb_recording_id=track.get("recording_id") or "",
        mb_release_id=release.get("id") or "",
        mb_artist_id=track.get("artist_id") or "",
        mb_albumartist_id=track.get("albumartist_id") or "",
        mb_releasegroup_id=release_group.get("id") or "",
        score=int(score),
        source="musicbrainz",
    )


def meta_from_recording(hint: Hint, recording: dict) -> TrackMeta:
    releases = recording.get("releases") or []
    release = _pick_release(hint, releases) or {}
    artist, artist_id = join_credit(recording.get("artist-credit"))
    albumartist, albumartist_id = join_credit(release.get("artist-credit"))
    if not albumartist:
        albumartist, albumartist_id = artist, artist_id
    track_no = None
    track_total = None
    disc = None
    length = recording.get("length")
    for medium in release.get("media") or []:
        disc = medium.get("position") or disc
        track_total = medium.get("track-count") or track_total
        for track in medium.get("tracks") or []:
            track_no = _parse_num(track.get("number") or track.get("position")) or track_no
            length = length or track.get("length")
    fake_track = {
        "title": recording.get("title") or "",
        "artist": artist,
        "albumartist": albumartist or artist,
        "track": track_no,
        "disc": int(disc) if disc else None,
        "length_ms": length,
    }
    fake_release = release or {"title": hint.album, "date": "", "artist-credit": recording.get("artist-credit")}
    score = score_pair(hint, fake_track, fake_release)
    mb_score = int(recording.get("score") or 0)
    if mb_score:
        score = int(min(100, score * 0.7 + mb_score * 0.3))
    release_group = release.get("release-group") or {}
    return TrackMeta(
        title=recording.get("title") or hint.title,
        artist=artist or hint.artist,
        album=release.get("title") or hint.album,
        albumartist=albumartist or artist or hint.albumartist,
        year=year_of(str(release.get("date") or "")) or hint.year,
        genre=_genre(release) or _genre(recording) or hint.genre,
        track=track_no or hint.track,
        track_total=int(track_total) if track_total else hint.track_total,
        disc=int(disc) if disc else hint.disc,
        mb_recording_id=recording.get("id") or "",
        mb_release_id=release.get("id") or "",
        mb_artist_id=artist_id,
        mb_albumartist_id=albumartist_id or artist_id,
        mb_releasegroup_id=release_group.get("id") or "",
        score=score,
        source="musicbrainz",
    )


def _online(hints: list[Hint], client) -> list[TrackMeta | None]:
    found: list[TrackMeta | None] = [None] * len(hints)
    if not hints:
        return found
    albumish = len(hints) > 1 or any(hint.album for hint in hints)
    if albumish:
        album_hits = _identify_album(hints, client)
        if album_hits is not None:
            found = album_hits
    for index, hint in enumerate(hints):
        current = found[index]
        if current is not None and current.score >= 70:
            continue
        single = _identify_recording(hint, client)
        if single is not None and (current is None or single.score > current.score):
            found[index] = single
    return found


def _identify_album(hints: list[Hint], client) -> list[TrackMeta | None] | None:
    album = _majority(hint.album for hint in hints)
    artist = _majority(hint.albumartist or hint.artist for hint in hints)
    parts = []
    if album:
        parts.append(f'release:"{_esc(album)}"')
    if artist:
        parts.append(f'artist:"{_esc(artist)}"')
    if not parts:
        return None
    briefs = client.search_releases(" AND ".join(parts)) or []
    best: list[TrackMeta | None] | None = None
    best_score = -1
    for brief in briefs[:3]:
        mbid = brief.get("id")
        if not mbid:
            continue
        full = brief if brief.get("media") else client.lookup_release(mbid)
        if not full:
            continue
        assigned = assign_tracks(hints, full)
        score = _release_score(assigned)
        if score > best_score:
            best_score = score
            best = assigned
    return best


def _identify_recording(hint: Hint, client) -> TrackMeta | None:
    if hint.mb_recording_id and hasattr(client, "lookup_recording"):
        recording = client.lookup_recording(hint.mb_recording_id)
        if recording:
            meta = meta_from_recording(hint, recording)
            if meta.score >= 45:
                return meta
    parts = []
    if hint.title:
        parts.append(f'recording:"{_esc(hint.title)}"')
    if hint.artist or hint.albumartist:
        parts.append(f'artist:"{_esc(hint.artist or hint.albumartist)}"')
    if hint.album:
        parts.append(f'release:"{_esc(hint.album)}"')
    if not parts or not hasattr(client, "search_recordings"):
        return None
    found = client.search_recordings(" AND ".join(parts)) or []
    best = None
    best_score = 0
    for recording in found[:5]:
        meta = meta_from_recording(hint, recording)
        if meta.score > best_score:
            best = meta
            best_score = meta.score
    if best is not None and best.score >= 45:
        return best
    return None


def _choose(hint: Hint, online: TrackMeta | None) -> TrackMeta:
    local = meta_from_hint(hint)
    if online is None:
        return local
    if online.score >= local.score:
        return online
    if online.score < 50:
        return local
    copied = replace(local)
    copied.mb_recording_id = copied.mb_recording_id or online.mb_recording_id
    copied.mb_release_id = copied.mb_release_id or online.mb_release_id
    copied.mb_artist_id = copied.mb_artist_id or online.mb_artist_id
    copied.mb_albumartist_id = copied.mb_albumartist_id or online.mb_albumartist_id
    copied.mb_releasegroup_id = copied.mb_releasegroup_id or online.mb_releasegroup_id
    copied.genre = copied.genre or online.genre
    copied.year = copied.year or online.year
    copied.track = copied.track or online.track
    copied.track_total = copied.track_total or online.track_total
    copied.disc = copied.disc or online.disc
    copied.disc_total = copied.disc_total or online.disc_total
    if not copied.album:
        copied.album = online.album
    if not copied.albumartist:
        copied.albumartist = online.albumartist
    return copied


def _release_score(assigned: list[TrackMeta | None]) -> int:
    if not assigned:
        return 0
    scores = [item.score if item else 0 for item in assigned]
    matched = sum(1 for item in assigned if item)
    base = sum(scores) / len(scores)
    if matched == len(assigned):
        base += 8
    return int(min(base, 100))


def _majority(values) -> str:
    counts: dict[str, dict] = {}
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        key = norm(text)
        bucket = counts.setdefault(key, {"n": 0, "label": text})
        bucket["n"] += 1
        bucket["label"] = text
    if not counts:
        return ""
    return max(counts.values(), key=lambda item: item["n"])["label"]


def _pick_release(hint: Hint, releases: list[dict]) -> dict | None:
    if not releases:
        return None

    def rank(release: dict) -> int:
        score = 0
        if (release.get("status") or "").lower() == "official":
            score += 10
        primary = ((release.get("release-group") or {}).get("primary-type") or "")
        if primary == "Album":
            score += 5
        score += int(similarity(hint.album, release.get("title")) * 20)
        if hint.year and year_of(str(release.get("date") or "")) == hint.year:
            score += 8
        return score

    return max(releases, key=rank)


def _release_artist(release: dict) -> str:
    name, _ = join_credit(release.get("artist-credit"))
    return name


def _genre(obj: dict | None) -> str:
    if not obj:
        return ""
    genres = obj.get("genres") or []
    if genres and isinstance(genres[0], dict):
        return genres[0].get("name") or ""
    tags = obj.get("tags") or []
    if tags and isinstance(tags[0], dict):
        return tags[0].get("name") or ""
    return ""


def _parse_num(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _esc(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')
