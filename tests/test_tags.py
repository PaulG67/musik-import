from pathlib import Path

from app.tags import Tags, read_tags, write_tags


def test_mp3_roundtrip(tmp_path: Path):
    path = tmp_path / "song.mp3"
    cover = b"\xff\xd8\xff" + b"\x00" * 64 + b"\xff\xd9"
    write_tags(
        path,
        Tags(
            title="Rolling in the Deep",
            artist="Adele",
            albumartist="Adele",
            album="21",
            genre="Soul",
            date="2011",
            track=1,
            track_total=12,
            lyrics="We could have had it all",
            mb_recording_id="rec-1",
            mb_release_id="rel-1",
        ),
        cover=cover,
        only_missing=False,
    )
    tags = read_tags(path)
    assert tags.title == "Rolling in the Deep"
    assert tags.artist == "Adele"
    assert tags.album == "21"
    assert tags.track == 1
    assert tags.track_total == 12
    assert tags.date.startswith("2011")
    assert tags.lyrics == "We could have had it all"
    assert tags.has_cover
    assert tags.mb_release_id == "rel-1"
    assert tags.mb_recording_id == "rec-1"

    write_tags(
        path,
        Tags(title="Falsch", genre="Pop"),
        cover=b"\x89PNG\r\n" + b"\x00" * 40,
        only_missing=True,
    )
    kept = read_tags(path)
    assert kept.title == "Rolling in the Deep"
    assert kept.genre == "Soul"
    assert kept.lyrics == "We could have had it all"
