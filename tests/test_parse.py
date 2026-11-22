from app.parse import disc_from_dirname, folder_hint, parse_filename, parse_folder


def test_folder_with_year():
    parsed = parse_folder("Adele - 21 (2011)")
    assert parsed["artist"] == "Adele"
    assert parsed["album"] == "21"
    assert parsed["year"] == "2011"


def test_album_only_folder():
    parsed = parse_folder("Kind of Blue (1959)")
    assert parsed["artist"] == ""
    assert parsed["album"] == "Kind of Blue"
    assert parsed["year"] == "1959"


def test_filename_track_and_artist():
    parsed = parse_filename("01 - Adele - Rolling in the Deep")
    assert parsed["track"] == 1
    assert parsed["artist"] == "Adele"
    assert parsed["title"] == "Rolling in the Deep"


def test_filename_title_only():
    parsed = parse_filename("03 - Rumour Has It")
    assert parsed["track"] == 3
    assert parsed["artist"] == ""
    assert parsed["title"] == "Rumour Has It"


def test_disc_folder():
    assert disc_from_dirname("CD2") == 2
    assert disc_from_dirname("Disc 1") == 1
    assert disc_from_dirname("Adele") is None


def test_nested_folder_hint():
    parsed = folder_hint("Adele/21")
    assert parsed["artist"] == "Adele"
    assert parsed["album"] == "21"
