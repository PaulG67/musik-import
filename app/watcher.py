"""Beobachtet den Import-Ordner."""

from __future__ import annotations

import logging
import threading
import time

from app.config import load_settings
from app.organize import scan_once

logger = logging.getLogger("musik-import")

_started = False


def start_watcher() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="musik-import-watch", daemon=True).start()


def _loop() -> None:
    while True:
        try:
            settings = load_settings()
            if settings.get("watch", True):
                scan_once()
        except Exception:
            logger.exception("Watcher")
        time.sleep(5)
