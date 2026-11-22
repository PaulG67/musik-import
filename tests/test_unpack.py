import os
import zipfile
from pathlib import Path

from app.identify import TrackMeta, meta_from_hint
from app.organize import _scan, collect_groups, inbox
from app.tags import Tags, write_tags
from app.unpack import unpack_pending


def _root(tmp_path, monkeypatch):
    imported = tmp_path / "import"
    imported.mkdir()
    monkeypatch.setenv("IMPORT_DIR", str(imported))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    return imported


def _zip(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as handle:
        for name, payload in members.items():
            handle.writestr(name, payload)


def test_zip_with_loose_tracks_becomes_a_folder(tmp_path, monkeypatch):
    imported = _root(tmp_path, monkeypatch)
    _zip(imported / "Adele - 21.zip", {"01 - Rolling in the Deep.mp3": b"audio", "cover.jpg": b"jpg"})
    notes, folders = unpack_pending(imported, force=True, stable_seconds=0)
    assert any("ausgepackt" in message for _, message in notes)
    assert "Adele - 21" in folders
    assert not (imported / "Adele - 21.zip").exists()
    assert (imported / "Adele - 21" / "01 - Rolling in the Deep.mp3").is_file()
    assert [group["key"] for group in collect_groups()] == ["Adele - 21"]


def test_matching_wrapper_folder_is_not_doubled(tmp_path, monkeypatch):
    imported = _root(tmp_path, monkeypatch)
    _zip(imported / "Adele - 21.zip", {"Adele - 21/01 - Rolling in the Deep.mp3": b"audio"})
    unpack_pending(imported, force=True, stable_seconds=0)
    assert (imported / "Adele - 21" / "01 - Rolling in the Deep.mp3").is_file()
    assert not (imported / "Adele - 21" / "Adele - 21").exists()


def test_zip_inside_zip_is_unpacked(tmp_path, monkeypatch):
    imported = _root(tmp_path, monkeypatch)
    inner = tmp_path / "inner.zip"
    _zip(inner, {"01 - Song.mp3": b"audio"})
    _zip(imported / "pack.zip", {"album.zip": inner.read_bytes()})
    unpack_pending(imported, force=True, stable_seconds=0)
    assert list(imported.rglob("*.mp3"))
    assert not list(imported.rglob("*.zip"))


def test_path_escape_is_rejected(tmp_path, monkeypatch):
    imported = _root(tmp_path, monkeypatch)
    archive = imported / "evil.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(zipfile.ZipInfo("../secret.mp3"), b"nope")
    notes, _folders = unpack_pending(imported, force=True, stable_seconds=0)
    assert archive.is_file()
    assert not (tmp_path / "secret.mp3").exists()
    assert notes and notes[0][0] == "error"
    again, _folders = unpack_pending(imported, force=False, stable_seconds=0)
    assert again == []


def test_fresh_zip_waits_until_the_copy_settles(tmp_path, monkeypatch):
    imported = _root(tmp_path, monkeypatch)
    archive = imported / "neu.zip"
    _zip(archive, {"01 - Song.mp3": b"audio"})
    os.utime(archive, None)
    notes, folders = unpack_pending(imported, force=False, stable_seconds=600)
    assert notes == []
    assert folders == set()
    assert archive.is_file()
    assert not list(imported.rglob("*.mp3"))


def test_cover_in_the_archive_shows_on_the_overview(tmp_path, monkeypatch):
    imported = _root(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    _zip(imported / "Adele - 21.zip", {"01 - Song.mp3": b"audio", "Cover.JPG": b"\xff\xd8\xff" + b"\x11" * 32 + b"\xff\xd9"})
    unpack_pending(imported, force=True, stable_seconds=0)
    group = inbox()["groups"][0]
    assert group["cover"] is True


def test_unpacked_album_is_analysed_without_waiting_again(tmp_path, monkeypatch):
    imported = _root(tmp_path, monkeypatch)
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setenv("MUSIC_DIR", str(music))
    track = tmp_path / "01 - Rolling in the Deep.mp3"
    write_tags(
        track,
        Tags(title="Rolling in the Deep", artist="Adele", album="21", albumartist="Adele", track=1),
        cover=None,
        only_missing=False,
    )
    _zip(imported / "Adele - 21.zip", {"01 - Rolling in the Deep.mp3": track.read_bytes()})
    os.utime(imported / "Adele - 21.zip", (time_old(), time_old()))

    class Source:
        def enrich(self, hints):
            metas = []
            for hint in hints:
                meta = meta_from_hint(hint)
                meta.score = 92
                meta.source = "musicbrainz"
                metas.append(meta)
            return metas

        def cover_for(self, meta: TrackMeta):
            return b"\xff\xd8\xff" + b"\x11" * 16 + b"\xff\xd9"

        def lyrics_for(self, meta, duration):
            return "Text", ""

    result = _scan(
        Source(),
        {
            "stable_seconds": 600,
            "min_score": 70,
            "auto_move": False,
            "layout": "artist_album",
            "custom_pattern": "",
            "fetch_cover": True,
            "fetch_lyrics": True,
            "embed_cover": True,
            "write_cover_file": True,
            "overwrite_tags": False,
            "prefer_existing": True,
            "scan_after_import": False,
        },
        force=False,
    )
    assert result["moved"] == 0
    group = inbox()["groups"][0]
    assert group["cover"] is True
    assert group["score"] == 92
    assert group["files"][0]["source"] == "musicbrainz"
    assert group["files"][0]["has_cover"] is True


def time_old() -> float:
    return __import__("time").time() - 1000
