from app.ownership import _owner_for, ensure_dir


def test_new_file_follows_album_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("PUID", "99")
    monkeypatch.setenv("PGID", "100")
    album = tmp_path / "Ace of Base" / "Happy Nation"
    album.mkdir(parents=True)
    track = album / "01 - All That She Wants.flac"
    track.write_bytes(b"audio")
    uid, gid = _owner_for(track)
    parent = album.stat()
    if parent.st_uid == 0:
        assert (uid, gid) == (99, 100)
    else:
        assert (uid, gid) == (parent.st_uid, parent.st_gid)


def test_ensure_dir_creates_missing_folders(tmp_path):
    target = tmp_path / "Interpret" / "Album"
    ensure_dir(target)
    assert target.is_dir()
    ensure_dir(target)
    assert target.is_dir()
