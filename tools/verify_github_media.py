from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from PIL import Image

root = Path(sys.argv[1] if len(sys.argv) > 1 else "media-lab/overworld")
manifest = json.loads((root / "manifest.json").read_text())
for relative, receipt in manifest["files"].items():
    path = root / relative
    assert path.stat().st_size == receipt["bytes"], relative
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"], relative
    image = Image.open(path)
    if "frames" in receipt:
        assert getattr(image, "n_frames", 1) == receipt["frames"], relative
print("verified committed media", len(manifest["files"]), "files")
