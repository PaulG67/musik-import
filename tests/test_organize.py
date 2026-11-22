from pathlib import Path

import pytest

from app.identify import TrackMeta, meta_from_hint
from app.organize import _plan, _sideline, collect_groups, process_group
from app.tags import Tags, read_tags, write_tags


class FakeSource:
    def enrich(self, hints):
        metas = []
        for hint in hints:
            meta = meta_from_hint(hint)
            meta.score = 92
            meta.source = "musicbrainz"
            meta.mb_release_id = "rel-1"
            meta.mb_releasegroup_id = "rg-1"
            meta.genre = "Soul"
            meta.year = meta.year or "2011"
            metas.append(meta)
        return metas

    def cover_for(self, meta: TrackMeta):
        return b"\xff\xd8\xff" + b"\x11" * 48 + b"\xff\xd9"

    def lyrics_for(self, meta, duration):
        return "We could have had it all", "[00:01.00] We could have had it all"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    imported = tmp_path / "import"
    music = tmp_path / "music"
    config = tmp_path / "config"
    imported.mkdir()
    music.mkdir()
    config.mkdir()
    monkeypatch.setenv("IMPORT_DIR", str(imported))
    monkeypatch.setenv("MUSIC_DIR", str(music))
    monkeypatch.setenv("CONFIG_DIR", str(config))
    return imported, music


def test_groups_album_with_disc(roots):
    imported, _music = roots
    path = imported / "Adele" / "21" / "CD1" / "01 - Rolling in the Deep.mp3"
    write_tags(path, Tags(), cover=None, only_missing=False)
    groups = collect_groups(imported)
    assert [group["key"] for group in groups] == ["Adele/21"]
    assert groups[0]["single"] is False


def test_import_moves_into_library(roots):
    imported, music = roots
    path = imported / "Adele - 21 (2011)" / "01 - Rolling in the Deep.mp3"
    write_tags(path, Tags(), cover=None, only_missing=False)
    group = collect_groups()[0]
    settings = {
        "layout": "artist_year_album",
        "custom_pattern": "",
        "fetch_cover": True,
        "fetch_lyrics": True,
        "embed_cover": True,
        "write_cover_file": True,
        "overwrite_tags": False,
        "auto_move": True,
        "min_score": 70,
        "prefer_existing": False,
        "scan_after_import": False,
    }
    result = process_group(group, settings, FakeSource(), trust=False)
    assert result["status"] == "moved"
    assert result["count"] == 1
    assert not path.exists()
    dest = music / "Adele" / "2011 - 21" / "01 - Rolling in the Deep.mp3"
    assert dest.is_file()
    tags = read_tags(dest)
    assert tags.title == "Rolling in the Deep"
    assert tags.albumartist == "Adele"
    assert tags.album == "21"
    assert tags.genre == "Soul"
    assert tags.has_cover
    assert "had it all" in tags.lyrics
    assert (dest.parent / "cover.jpg").is_file()
    assert dest.with_suffix(".lrc").read_text(encoding="utf-8").startswith("[00:01.00]")


def test_low_confidence_stays_in_import(roots):
    imported, music = roots
    path = imported / "losesong.mp3"
    write_tags(path, Tags(), cover=None, only_missing=False)

    class Unsure(FakeSource):
        def enrich(self, hints):
            meta = TrackMeta(score=20, source="filename")
            return [meta for _ in hints]

        def cover_for(self, meta):
            return None

        def lyrics_for(self, meta, duration):
            return "", ""

    group = collect_groups()[0]
    result = process_group(
        group,
        {
            "layout": "artist_album",
            "auto_move": True,
            "min_score": 70,
            "prefer_existing": True,
            "fetch_cover": False,
            "fetch_lyrics": False,
            "embed_cover": False,
            "write_cover_file": False,
            "overwrite_tags": False,
        },
        Unsure(),
    )
    assert result["status"] == "held"
    assert path.exists()
    assert list(music.rglob("*.mp3")) == []


def test_existing_target_is_left_alone(tmp_path: Path):
    src = tmp_path / "neu.mp3"
    dest = tmp_path / "01 - Rolling in the Deep.mp3"
    src.write_bytes(b"new-audio")
    dest.write_bytes(b"already-there")
    _place, kind = _plan(src, dest)
    assert kind == "duplicate"


def test_unwritable_library_stays_explained(roots, monkeypatch):
    imported, _music = roots
    path = imported / "Ace of Base - Happy Nation" / "01 - All That She Wants.mp3"
    write_tags(path, Tags(), cover=None, only_missing=False)

    def denied(src, dst):
        raise PermissionError(13, "Permission denied", dst)

    monkeypatch.setattr("app.organize.shutil.move", denied)
    group = collect_groups()[0]
    result = process_group(
        group,
        {
            "layout": "artist_album",
            "custom_pattern": "",
            "fetch_cover": False,
            "fetch_lyrics": False,
            "embed_cover": False,
            "write_cover_file": False,
            "overwrite_tags": False,
            "auto_move": True,
            "min_score": 70,
            "prefer_existing": True,
            "scan_after_import": False,
        },
        FakeSource(),
        trust=False,
    )
    assert result["status"] == "error"
    assert result["message"].startswith("Keine Schreibrechte für")
    assert path.exists()


def test_duplicate_is_set_aside(roots):
    imported, _music = roots
    path = imported / "song.mp3"
    write_tags(path, Tags(title="Song", artist="Adele"), cover=None, only_missing=False)
    _sideline(path, "_duplikate", "einzeln")
    assert not path.exists()
    parked = [item for item in (imported / "_duplikate").rglob("*.mp3") if item.is_file()]
    assert len(parked) == 1
