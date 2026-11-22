"""Weboberfläche für Musik-Import."""

from __future__ import annotations

import logging
import os

from flask import Flask, abort, jsonify, render_template, request, send_file

from app.config import config_dir, import_dir, load_settings, music_dir, save_settings
from app.layout import PRESET_LABELS, Fields, pattern_for, render_pattern, validate_pattern
from app.navidrome import ping
from app.organize import apply_group, cover_path, dismiss_group, inbox, retry_group, scan_once
from app.store import load

logger = logging.getLogger("musik-import")

_SECRET_KEYS = ("navidrome_password", "acoustid_api_key")


def create_app() -> Flask:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    for path in (import_dir(), music_dir(), config_dir()):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Ordner nicht angelegt: %s", path)

    app = Flask(__name__)

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "import": str(import_dir()),
            "import_ok": import_dir().is_dir(),
            "music": str(music_dir()),
            "music_ok": music_dir().is_dir(),
        }

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/settings")
    def get_settings():
        return jsonify(_public_settings(load_settings()))

    @app.post("/api/settings")
    def post_settings():
        payload = request.get_json(silent=True) or {}
        current = load_settings()
        for key in list(current):
            if key in payload and key not in _SECRET_KEYS:
                current[key] = payload[key]
        for key in _SECRET_KEYS:
            if payload.get(key):
                current[key] = payload[key]
        try:
            saved = save_settings(current)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_public_settings(saved))

    @app.get("/api/preview")
    def preview():
        settings = load_settings()
        layout = request.args.get("layout") or settings["layout"]
        custom = request.args.get("pattern") if request.args.get("pattern") is not None else settings["custom_pattern"]
        trial = dict(settings)
        trial["layout"] = layout
        trial["custom_pattern"] = custom
        if layout == "custom":
            error = validate_pattern(custom)
            if error:
                return jsonify({"error": error}), 400
        sample = Fields(
            albumartist="Adele",
            artist="Adele",
            album="21",
            title="Rolling in the Deep",
            year="2011",
            genre="Pop",
            track=1,
            disc=1,
            ext=".flac",
        )
        return jsonify({"path": render_pattern(pattern_for(trial), sample)})

    @app.get("/api/inbox")
    def get_inbox():
        return jsonify(inbox())

    @app.get("/api/log")
    def get_log():
        entries = list(reversed((load().get("log") or [])[-100:]))
        return jsonify({"entries": entries})

    @app.get("/api/cover")
    def get_cover():
        path = cover_path(request.args.get("key") or "")
        if path is None:
            abort(404)
        return send_file(path)

    @app.post("/api/scan")
    def post_scan():
        return jsonify(scan_once(force=True))

    @app.post("/api/groups/apply")
    def post_apply():
        payload = request.get_json(silent=True) or {}
        key = payload.get("key") or ""
        if not key:
            return jsonify({"error": "Ordner fehlt"}), 400
        return jsonify(apply_group(key, payload))

    @app.post("/api/groups/retry")
    def post_retry():
        payload = request.get_json(silent=True) or {}
        key = payload.get("key") or ""
        if not key:
            return jsonify({"error": "Ordner fehlt"}), 400
        return jsonify(retry_group(key))

    @app.post("/api/groups/dismiss")
    def post_dismiss():
        payload = request.get_json(silent=True) or {}
        key = payload.get("key") or ""
        if not key:
            return jsonify({"error": "Ordner fehlt"}), 400
        return jsonify(dismiss_group(key))

    @app.post("/api/navidrome/ping")
    def post_ping():
        payload = request.get_json(silent=True) or {}
        settings = load_settings()
        url = (payload.get("navidrome_url") or settings["navidrome_url"]).strip()
        user = (payload.get("navidrome_user") or settings["navidrome_user"]).strip()
        password = payload.get("navidrome_password") or settings["navidrome_password"]
        ok, message = ping(url, user, password)
        return jsonify({"ok": ok, "message": message})

    if os.environ.get("MUSIK_IMPORT_WATCH", "1") != "0":
        from app.watcher import start_watcher

        start_watcher()
    return app


def _public_settings(settings: dict) -> dict:
    public = dict(settings)
    public["navidrome_password_set"] = bool(public.get("navidrome_password"))
    public["acoustid_api_key_set"] = bool(public.get("acoustid_api_key"))
    for key in _SECRET_KEYS:
        public.pop(key, None)
    public["layouts"] = PRESET_LABELS
    public["import_dir"] = str(import_dir())
    public["music_dir"] = str(music_dir())
    return public


app = create_app()
