def test_health_and_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path / "import"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MUSIK_IMPORT_WATCH", "0")
    from app.main import create_app

    client = create_app().test_client()
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json["ok"] is True
    assert health.json["import_ok"] is True

    saved = client.post(
        "/api/settings",
        json={
            "layout": "artist_album_year",
            "auto_move": False,
            "min_score": 80,
            "navidrome_url": "http://navidrome:4533",
            "navidrome_user": "paul",
            "navidrome_password": "secret",
        },
    )
    assert saved.status_code == 200
    assert saved.json["layout"] == "artist_album_year"
    assert saved.json["auto_move"] is False
    assert saved.json["navidrome_password_set"] is True
    assert "navidrome_password" not in saved.json

    again = client.post("/api/settings", json={"min_score": 65, "navidrome_password": ""})
    assert again.json["min_score"] == 65
    assert again.json["navidrome_password_set"] is True
    assert again.json["layout"] == "artist_album_year"

    preview = client.get("/api/preview?layout=artist_album")
    assert preview.status_code == 200
    assert preview.json["path"].endswith("01 - Rolling in the Deep.flac")

    rejected = client.post(
        "/api/settings",
        json={"layout": "custom", "custom_pattern": "../{title}"},
    )
    assert rejected.status_code == 400


def test_page_renders(tmp_path, monkeypatch):
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path / "import"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MUSIK_IMPORT_WATCH", "0")
    from app.main import create_app

    response = create_app().test_client().get("/")
    assert response.status_code == 200
    assert "Musik-Import" in response.get_data(as_text=True)
    assert "ZIP" in response.get_data(as_text=True)
