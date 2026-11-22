from pathlib import Path

from app.layout import Fields, destination, render_pattern, validate_pattern, within_root


def test_year_album_pattern():
    fields = Fields(
        albumartist="Adele",
        artist="Adele",
        album="21",
        title="Rolling in the Deep",
        year="2011",
        track=1,
        ext=".flac",
    )
    pattern = "{albumartist}/{year} - {album}/{track} - {title}"
    assert render_pattern(pattern, fields) == "Adele/2011 - 21/01 - Rolling in the Deep.flac"


def test_missing_year_is_dropped():
    fields = Fields(albumartist="Adele", album="21", title="Hello", track=1, ext=".mp3")
    rendered = render_pattern("{albumartist}/{year} - {album}/{track} - {title}", fields)
    assert rendered == "Adele/21/01 - Hello.mp3"


def test_missing_track_number():
    fields = Fields(albumartist="Adele", album="21", title="Hello", ext=".mp3")
    rendered = render_pattern("{albumartist}/{album}/{track} - {title}", fields)
    assert rendered == "Adele/21/Hello.mp3"


def test_disc_folder_only_when_needed():
    single = Fields(albumartist="Adele", album="21", title="Hello", track=1, disc=1, ext=".mp3")
    multi = Fields(albumartist="Adele", album="21", title="Hello", track=1, disc=2, ext=".mp3")
    pattern = "{albumartist}/{album}/{discfolder}{track} - {title}"
    assert render_pattern(pattern, single) == "Adele/21/01 - Hello.mp3"
    assert render_pattern(pattern, multi) == "Adele/21/CD2/01 - Hello.mp3"


def test_pattern_rejects_parent_dir():
    assert validate_pattern("{albumartist}/../{title}")
    assert validate_pattern("{albumartist}/{album}/{title}") is None


def test_reuses_existing_album(tmp_path: Path):
    album = tmp_path / "Adele" / "21 (2011)"
    album.mkdir(parents=True)
    (album / "alt.txt").write_text("x", encoding="utf-8")
    fields = Fields(albumartist="The Adele", album="21", title="Hello", year="2011", track=4, ext=".mp3")
    dest = destination(tmp_path, fields, {"prefer_existing": True, "layout": "artist_album"})
    assert dest.parent == album
    assert dest.name == "04 - Hello.mp3"
    assert within_root(tmp_path, dest)


def test_ambiguous_existing_album_uses_pattern(tmp_path: Path):
    (tmp_path / "Adele" / "21").mkdir(parents=True)
    (tmp_path / "Adele" / "21 Deluxe").mkdir(parents=True)
    fields = Fields(albumartist="Adele", album="21", title="Hello", track=1, ext=".mp3")
    dest = destination(tmp_path, fields, {"prefer_existing": True, "layout": "artist_album"})
    assert dest.as_posix().endswith("Adele/21/01 - Hello.mp3")
    assert "Deluxe" not in dest.as_posix()
