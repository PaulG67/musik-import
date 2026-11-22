import json

from app.navidrome import ping, start_scan, token_for


class Response:
    def __init__(self, payload, url):
        self.payload = payload
        self.url = url

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_token_matches_subsonic_scheme():
    assert token_for("secret", "abc") == __import__("hashlib").md5(b"secretabc").hexdigest()


def test_start_scan_reads_ok_status():
    seen = {}

    def opener(request, timeout=0):
        seen["url"] = request.full_url
        return Response({"subsonic-response": {"status": "ok"}}, request.full_url)

    ok, message = start_scan("http://navidrome:4533", "paul", "secret", opener=opener)
    assert ok
    assert message == "Scan gestartet"
    assert "/rest/startScan.view" in seen["url"]
    assert "u=paul" in seen["url"]


def test_ping_reports_failure():
    def opener(request, timeout=0):
        return Response({"subsonic-response": {"status": "failed", "error": {"message": "no"}}}, "")

    ok, message = ping("http://navidrome:4533", "paul", "secret", opener=opener)
    assert not ok
    assert message == "no"


def test_missing_credentials():
    ok, message = start_scan("", "", "")
    assert not ok
    assert "nicht eingerichtet" in message
