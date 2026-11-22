import os
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="musik-import-"))
os.environ["MUSIK_IMPORT_WATCH"] = "0"
os.environ["IMPORT_DIR"] = str(_root / "import")
os.environ["MUSIC_DIR"] = str(_root / "music")
os.environ["CONFIG_DIR"] = str(_root / "config")
os.environ["NAVIDROME_URL"] = ""
os.environ["NAVIDROME_USER"] = ""
os.environ["NAVIDROME_PASSWORD"] = ""
os.environ["ACOUSTID_API_KEY"] = ""
os.environ["LAYOUT"] = ""
