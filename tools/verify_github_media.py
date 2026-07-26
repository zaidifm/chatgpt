from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "media-lab/overworld")
manifest = json.loads((root / "manifest.json").read_text())

for relative, receipt in manifest["files"].items():
    path = root / relative
    assert path.stat().st_size == receipt["bytes"], relative
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"], relative

with zipfile.ZipFile(root / "overworld-mini-source.zip") as archive:
    assert archive.testzip() is None
    assert len(archive.namelist()) == 48

print("verified committed media", len(manifest["files"]), "files")
