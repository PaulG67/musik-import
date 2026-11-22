"""Tags lesen und schreiben, in den Formaten, die Navidrome indiziert."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from pathlib import Path

from mutagen import MutagenError
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    ID3,
    ID3NoHeaderError,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    TXXX,
    UFID,
    USLT,
)
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

AUDIO_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".mp4", ".aac"}

MB_OWNER = "http://musicbrainz.org"
MB_ALBUM = "MusicBrainz Album Id"
MB_ARTIST = "MusicBrainz Artist Id"
MB_ALBUMARTIST = "MusicBrainz Album Artist Id"
MB_RELEASEGROUP = "MusicBrainz Release Group Id"
MB_RECORDING = "MusicBrainz Recording Id"


class TagError(RuntimeError):
    pass


@dataclass
class Tags:
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    genre: str = ""
    date: str = ""
    track: int | None = None
    track_total: int | None = None
    disc: int | None = None
    disc_total: int | None = None
    lyrics: str = ""
    has_cover: bool = False
    duration: float | None = None
    mb_recording_id: str = ""
    mb_release_id: str = ""
    mb_artist_id: str = ""
    mb_albumartist_id: str = ""
    mb_releasegroup_id: str = ""

    def to_public(self) -> dict:
        data = asdict(self)
        data["has_lyrics"] = bool((self.lyrics or "").strip())
        data.pop("lyrics", None)
        return data


def is_audio(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXT


def image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def read_tags(path: Path) -> Tags:
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            return _read_mp3(path)
        if suffix == ".flac":
            return _read_vorbis(path, FLAC)
        if suffix == ".ogg":
            return _read_vorbis(path, OggVorbis)
        if suffix == ".opus":
            return _read_vorbis(path, OggOpus)
        if suffix in {".m4a", ".mp4", ".aac"}:
            return _read_mp4(path)
    except TagError:
        raise
    except (MutagenError, OSError) as exc:
        raise TagError(f"{path.name}: {exc}") from exc
    raise TagError(f"Format nicht unterstützt: {suffix or path.name}")


def write_tags(path: Path, tags: Tags, *, cover: bytes | None, only_missing: bool) -> None:
    current = _read_existing(path)
    merged = merge_tags(current, tags, only_missing=only_missing)
    use_cover = cover or None
    if not cover or (only_missing and current.has_cover):
        use_cover = None
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            _write_mp3(path, merged, use_cover)
            return
        if suffix == ".flac":
            _write_vorbis(path, merged, use_cover, FLAC)
            return
        if suffix == ".ogg":
            _write_vorbis(path, merged, use_cover, OggVorbis)
            return
        if suffix == ".opus":
            _write_vorbis(path, merged, use_cover, OggOpus)
            return
        if suffix in {".m4a", ".mp4", ".aac"}:
            _write_mp4(path, merged, use_cover)
            return
    except TagError:
        raise
    except (MutagenError, OSError) as exc:
        raise TagError(f"{path.name}: {exc}") from exc
    raise TagError(f"Format nicht unterstützt: {suffix or path.name}")


def merge_tags(current: Tags, incoming: Tags, *, only_missing: bool) -> Tags:
    data = asdict(current)
    for key, value in asdict(incoming).items():
        if key in {"has_cover", "duration"}:
            continue
        if value in ("", None):
            continue
        if only_missing and data.get(key) not in ("", None):
            continue
        data[key] = value
    data["has_cover"] = current.has_cover
    data["duration"] = current.duration
    return Tags(**data)


def _read_existing(path: Path) -> Tags:
    if path.suffix.lower() == ".mp3" and not path.exists():
        return Tags()
    try:
        return read_tags(path)
    except TagError:
        if path.suffix.lower() == ".mp3":
            return Tags()
        raise


def _first(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return _first(value[0]) if value else ""
    return str(value).strip()


def _pair(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    left, _, right = str(value).strip().partition("/")

    def num(part: str) -> int | None:
        part = part.strip()
        return int(part) if part.isdigit() and int(part) > 0 else None

    return num(left), num(right)


def _duration(audio) -> float | None:
    info = getattr(audio, "info", None)
    length = getattr(info, "length", None) if info else None
    if not length:
        return None
    return float(length)


def _read_mp3(path: Path) -> Tags:
    tags = Tags()
    id3 = None
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = None
    except (MutagenError, OSError) as exc:
        if not path.exists():
            return tags
        raise TagError(f"{path.name}: {exc}") from exc
    if id3 is not None:
        tags.title = _first(id3.get("TIT2"))
        tags.artist = _first(id3.get("TPE1"))
        tags.albumartist = _first(id3.get("TPE2"))
        tags.album = _first(id3.get("TALB"))
        tags.genre = _first(id3.get("TCON"))
        tags.date = _first(id3.get("TDRC"))
        tags.track, tags.track_total = _pair(_first(id3.get("TRCK")))
        tags.disc, tags.disc_total = _pair(_first(id3.get("TPOS")))
        for frame in id3.getall("USLT"):
            text = _first(getattr(frame, "text", ""))
            if text:
                tags.lyrics = text
                break
        tags.has_cover = any(isinstance(frame, APIC) for frame in id3.values())
        tags.mb_recording_id = _ufid(id3) or _txxx(id3, MB_RECORDING)
        tags.mb_release_id = _txxx(id3, MB_ALBUM)
        tags.mb_artist_id = _txxx(id3, MB_ARTIST)
        tags.mb_albumartist_id = _txxx(id3, MB_ALBUMARTIST)
        tags.mb_releasegroup_id = _txxx(id3, MB_RELEASEGROUP)
    try:
        from mutagen.mp3 import MP3

        tags.duration = _duration(MP3(path))
    except (MutagenError, OSError):
        pass
    return tags


def _txxx(id3: ID3, desc: str) -> str:
    for frame in id3.getall("TXXX"):
        if (frame.desc or "") == desc:
            return _first(frame.text)
    return ""


def _ufid(id3: ID3) -> str:
    for frame in id3.getall("UFID"):
        if frame.owner == MB_OWNER and frame.data:
            return bytes(frame.data).decode("utf-8", "replace").strip()
    return ""


def _put_txxx(id3: ID3, desc: str, value: str) -> None:
    kept = [frame for frame in id3.getall("TXXX") if (frame.desc or "") != desc]
    id3.delall("TXXX")
    for frame in kept:
        id3.add(frame)
    if value:
        id3.add(TXXX(encoding=3, desc=desc, text=value))


def _write_mp3(path: Path, tags: Tags, cover: bytes | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()
    _put_text(id3, "TIT2", TIT2(encoding=3, text=tags.title) if tags.title else None)
    _put_text(id3, "TPE1", TPE1(encoding=3, text=tags.artist) if tags.artist else None)
    _put_text(id3, "TPE2", TPE2(encoding=3, text=tags.albumartist) if tags.albumartist else None)
    _put_text(id3, "TALB", TALB(encoding=3, text=tags.album) if tags.album else None)
    _put_text(id3, "TCON", TCON(encoding=3, text=tags.genre) if tags.genre else None)
    _put_text(id3, "TDRC", TDRC(encoding=3, text=tags.date) if tags.date else None)
    if tags.track:
        text = f"{tags.track}/{tags.track_total}" if tags.track_total else str(tags.track)
        _put_text(id3, "TRCK", TRCK(encoding=3, text=text))
    if tags.disc:
        text = f"{tags.disc}/{tags.disc_total}" if tags.disc_total else str(tags.disc)
        _put_text(id3, "TPOS", TPOS(encoding=3, text=text))
    if tags.lyrics:
        id3.delall("USLT")
        id3.add(USLT(encoding=3, lang="und", desc="", text=tags.lyrics))
    if cover:
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=image_mime(cover), type=3, desc="Cover", data=cover))
    _put_txxx(id3, MB_ALBUM, tags.mb_release_id)
    _put_txxx(id3, MB_ARTIST, tags.mb_artist_id)
    _put_txxx(id3, MB_ALBUMARTIST, tags.mb_albumartist_id)
    _put_txxx(id3, MB_RELEASEGROUP, tags.mb_releasegroup_id)
    _put_txxx(id3, MB_RECORDING, tags.mb_recording_id)
    if tags.mb_recording_id:
        id3.delall("UFID")
        id3.add(UFID(owner=MB_OWNER, data=tags.mb_recording_id.encode("utf-8")))
    id3.save(path, v2_version=3)


def _put_text(id3: ID3, key: str, frame) -> None:
    id3.delall(key)
    if frame is not None:
        id3.add(frame)


def _read_vorbis(path: Path, cls) -> Tags:
    try:
        audio = cls(path)
    except (MutagenError, OSError) as exc:
        raise TagError(f"{path.name}: {exc}") from exc
    tags = Tags()
    tags.title = _first(audio.get("title"))
    tags.artist = _first(audio.get("artist"))
    tags.album = _first(audio.get("album"))
    tags.albumartist = _first(audio.get("albumartist"))
    tags.genre = _first(audio.get("genre"))
    tags.date = _first(audio.get("date")) or _first(audio.get("year"))
    tags.track, tags.track_total = _pair(_first(audio.get("tracknumber")))
    if tags.track_total is None:
        total, _ = _pair(_first(audio.get("tracktotal")))
        tags.track_total = total
    tags.disc, tags.disc_total = _pair(_first(audio.get("discnumber")))
    tags.lyrics = _first(audio.get("lyrics")) or _first(audio.get("unsyncedlyrics"))
    tags.mb_recording_id = _first(audio.get("musicbrainz_trackid"))
    tags.mb_release_id = _first(audio.get("musicbrainz_albumid"))
    tags.mb_artist_id = _first(audio.get("musicbrainz_artistid"))
    tags.mb_albumartist_id = _first(audio.get("musicbrainz_albumartistid"))
    tags.mb_releasegroup_id = _first(audio.get("musicbrainz_releasegroupid"))
    pictures = list(getattr(audio, "pictures", []) or [])
    keys = {key.lower() for key in audio.keys()}
    tags.has_cover = bool(pictures) or "metadata_block_picture" in keys
    tags.duration = _duration(audio)
    return tags


def _write_vorbis(path: Path, tags: Tags, cover: bytes | None, cls) -> None:
    try:
        audio = cls(path)
    except (MutagenError, OSError) as exc:
        raise TagError(f"{path.name}: {exc}") from exc
    _set_vorbis(audio, "title", tags.title)
    _set_vorbis(audio, "artist", tags.artist)
    _set_vorbis(audio, "album", tags.album)
    _set_vorbis(audio, "albumartist", tags.albumartist)
    _set_vorbis(audio, "genre", tags.genre)
    _set_vorbis(audio, "date", tags.date)
    if tags.track:
        _set_vorbis(audio, "tracknumber", str(tags.track))
    if tags.track_total:
        _set_vorbis(audio, "tracktotal", str(tags.track_total))
    if tags.disc:
        _set_vorbis(audio, "discnumber", str(tags.disc))
    if tags.lyrics:
        _set_vorbis(audio, "lyrics", tags.lyrics)
        _set_vorbis(audio, "unsyncedlyrics", tags.lyrics)
    _set_vorbis(audio, "musicbrainz_trackid", tags.mb_recording_id)
    _set_vorbis(audio, "musicbrainz_albumid", tags.mb_release_id)
    _set_vorbis(audio, "musicbrainz_artistid", tags.mb_artist_id)
    _set_vorbis(audio, "musicbrainz_albumartistid", tags.mb_albumartist_id)
    _set_vorbis(audio, "musicbrainz_releasegroupid", tags.mb_releasegroup_id)
    if cover:
        _attach_picture(audio, cover)
    audio.save()


def _set_vorbis(audio, key: str, value: str) -> None:
    if value:
        audio[key] = [value]


def _attach_picture(audio, cover: bytes) -> None:
    picture = Picture()
    picture.type = 3
    picture.mime = image_mime(cover)
    picture.desc = "Cover"
    picture.data = cover
    if isinstance(audio, FLAC):
        audio.clear_pictures()
        audio.add_picture(picture)
        return
    audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]


def _read_mp4(path: Path) -> Tags:
    try:
        audio = MP4(path)
    except (MutagenError, OSError) as exc:
        raise TagError(f"{path.name}: {exc}") from exc
    tags = Tags()
    tags.title = _first(audio.get("\xa9nam"))
    tags.artist = _first(audio.get("\xa9ART"))
    tags.albumartist = _first(audio.get("aART"))
    tags.album = _first(audio.get("\xa9alb"))
    tags.genre = _first(audio.get("\xa9gen"))
    tags.date = _first(audio.get("\xa9day"))
    tags.lyrics = _first(audio.get("\xa9lyr"))
    trkn = audio.get("trkn") or []
    if trkn:
        tags.track = trkn[0][0] or None
        tags.track_total = trkn[0][1] or None
    disk = audio.get("disk") or []
    if disk:
        tags.disc = disk[0][0] or None
        tags.disc_total = disk[0][1] or None
    tags.has_cover = bool(audio.get("covr"))
    tags.mb_release_id = _mp4_free(audio, MB_ALBUM)
    tags.mb_artist_id = _mp4_free(audio, MB_ARTIST)
    tags.mb_albumartist_id = _mp4_free(audio, MB_ALBUMARTIST)
    tags.mb_releasegroup_id = _mp4_free(audio, MB_RELEASEGROUP)
    tags.mb_recording_id = _mp4_free(audio, MB_RECORDING)
    tags.duration = _duration(audio)
    return tags


def _mp4_free(audio: MP4, name: str) -> str:
    raw = audio.get(f"----:com.apple.iTunes:{name}")
    if not raw:
        return ""
    value = raw[0]
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value).strip()


def _write_mp4(path: Path, tags: Tags, cover: bytes | None) -> None:
    try:
        audio = MP4(path)
    except (MutagenError, OSError) as exc:
        raise TagError(f"{path.name}: {exc}") from exc
    if tags.title:
        audio["\xa9nam"] = [tags.title]
    if tags.artist:
        audio["\xa9ART"] = [tags.artist]
    if tags.albumartist:
        audio["aART"] = [tags.albumartist]
    if tags.album:
        audio["\xa9alb"] = [tags.album]
    if tags.genre:
        audio["\xa9gen"] = [tags.genre]
    if tags.date:
        audio["\xa9day"] = [tags.date]
    if tags.lyrics:
        audio["\xa9lyr"] = [tags.lyrics]
    if tags.track:
        audio["trkn"] = [(int(tags.track), int(tags.track_total or 0))]
    if tags.disc:
        audio["disk"] = [(int(tags.disc), int(tags.disc_total or 0))]
    _set_free(audio, MB_ALBUM, tags.mb_release_id)
    _set_free(audio, MB_ARTIST, tags.mb_artist_id)
    _set_free(audio, MB_ALBUMARTIST, tags.mb_albumartist_id)
    _set_free(audio, MB_RELEASEGROUP, tags.mb_releasegroup_id)
    _set_free(audio, MB_RECORDING, tags.mb_recording_id)
    if cover:
        fmt = MP4Cover.FORMAT_PNG if image_mime(cover) == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
    audio.save()


def _set_free(audio: MP4, name: str, value: str) -> None:
    if value:
        audio[f"----:com.apple.iTunes:{name}"] = [value.encode("utf-8")]
