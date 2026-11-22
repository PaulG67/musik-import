import os
import zipfile
from pathlib import Path

from app.organize import collect_groups
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
    notes = unpack_pending(imported, force=True, stable_seconds=0)
    assert any("ausgepackt" in message for _, message in notes)
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
    notes = unpack_pending(imported, force=True, stable_seconds=0)
    assert archive.is_file()
    assert not (tmp_path / "secret.mp3").exists()
    assert notes and notes[0][0] == "error"
    again = unpack_pending(imported, force=False, stable_seconds=0)
    assert again == []


def test_fresh_zip_waits_until_the_copy_settles(tmp_path, monkeypatch):
    imported = _root(tmp_path, monkeypatch)
    archive = imported / "neu.zip"
    _zip(archive, {"01 - Song.mp3": b"audio"})
    os.utime(archive, None)
    notes = unpack_pending(imported, force=False, stable_seconds=600)
    assert notes == []
    assert archive.is_file()
    assert not list(imported.rglob("*.mp3"))
