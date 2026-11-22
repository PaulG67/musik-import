from app.identify import Hint, identify


RELEASE = {
    "id": "rel-1",
    "title": "21",
    "date": "2011-01-24",
    "status": "Official",
    "artist-credit": [
        {"name": "Adele", "joinphrase": "", "artist": {"id": "art-1", "name": "Adele"}}
    ],
    "release-group": {"id": "rg-1", "title": "21", "primary-type": "Album"},
    "genres": [{"name": "Soul", "count": 3}],
    "media": [
        {
            "position": 1,
            "track-count": 2,
            "tracks": [
                {
                    "number": "1",
                    "title": "Rolling in the Deep",
                    "recording": {
                        "id": "rec-1",
                        "title": "Rolling in the Deep",
                        "length": 228000,
                        "artist-credit": [
                            {"name": "Adele", "joinphrase": "", "artist": {"id": "art-1", "name": "Adele"}}
                        ],
                    },
                },
                {
                    "number": "2",
                    "title": "Rumour Has It",
                    "recording": {
                        "id": "rec-2",
                        "title": "Rumour Has It",
                        "length": 223000,
                        "artist-credit": [
                            {"name": "Adele", "joinphrase": "", "artist": {"id": "art-1", "name": "Adele"}}
                        ],
                    },
                },
            ],
        }
    ],
}


class Client:
    def search_releases(self, query):
        assert "21" in query
        return [{"id": RELEASE["id"], "title": RELEASE["title"]}]

    def lookup_release(self, mbid):
        assert mbid == "rel-1"
        return RELEASE

    def search_recordings(self, query):
        return []

    def lookup_recording(self, mbid):
        return None


def test_album_tracks_share_one_release():
    hints = [
        Hint(key="a", title="Rolling in the Deep", artist="Adele", albumartist="Adele", album="21", track=1, year="2011"),
        Hint(key="b", title="Rumour Has It", artist="Adele", albumartist="Adele", album="21", track=2, year="2011"),
    ]
    metas = identify(hints, Client())
    assert [meta.title for meta in metas] == ["Rolling in the Deep", "Rumour Has It"]
    assert {meta.mb_recording_id for meta in metas} == {"rec-1", "rec-2"}
    assert {meta.mb_release_id for meta in metas} == {"rel-1"}
    assert metas[0].genre == "Soul"
    assert metas[0].year == "2011"
    assert metas[0].track == 1
    assert all(meta.score >= 70 for meta in metas)
    assert all(meta.source == "musicbrainz" for meta in metas)


def test_local_names_win_when_online_is_weak():
    class Weak(Client):
        def search_releases(self, query):
            return []

        def search_recordings(self, query):
            return [
                {
                    "id": "other",
                    "score": 40,
                    "title": "Something Else",
                    "artist-credit": [{"name": "Other", "artist": {"id": "x", "name": "Other"}}],
                    "releases": [],
                }
            ]

    hint = Hint(
        key="a",
        title="Rolling in the Deep",
        artist="Adele",
        albumartist="Adele",
        album="21",
        track=1,
        year="2011",
    )
    meta = identify([hint], Weak())[0]
    assert meta.title == "Rolling in the Deep"
    assert meta.source == "tags"
    assert meta.score >= 70
