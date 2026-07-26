from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    source = args.source
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    webp = Image.open(source / "samples" / "25.webp")
    frames = [frame.convert("RGBA") for frame in ImageSequence.Iterator(webp)]
    durations = [frame.info.get("duration", 155) for frame in ImageSequence.Iterator(webp)]
    assert len(frames) == 16

    gif_path = output / "pikachu-turntable.gif"
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        optimize=True,
        transparency=0,
    )

    strip = Image.new("RGBA", (4 * frames[0].width, 4 * frames[0].height), (20, 24, 31, 255))
    for index, frame in enumerate(frames):
        strip.alpha_composite(frame, ((index % 4) * frame.width, (index // 4) * frame.height))
    strip_path = output / "pikachu-frame-strip.png"
    strip.save(strip_path, optimize=True)

    atlas = Image.open(source / "directional-atlas.png").convert("RGBA")
    viewport_width = min(360, atlas.width)
    positions = list(range(0, max(1, atlas.width - viewport_width + 1), 90))
    if positions[-1] != atlas.width - viewport_width:
        positions.append(atlas.width - viewport_width)
    scan_frames = []
    for x in positions + positions[-2:0:-1]:
        canvas = Image.new("RGBA", (viewport_width, atlas.height + 22), (20, 24, 31, 255))
        canvas.alpha_composite(atlas.crop((x, 0, x + viewport_width, atlas.height)), (0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.text((8, atlas.height + 4), f"atlas viewport x={x}", fill=(235, 239, 245, 255))
        scan_frames.append(canvas)
    scan_path = output / "directional-atlas-scan.gif"
    scan_frames[0].save(
        scan_path,
        save_all=True,
        append_images=scan_frames[1:],
        duration=220,
        loop=0,
        disposal=2,
        optimize=True,
    )

    manifest = {
        "inputs": {
            "directional-atlas.png": sha256(source / "directional-atlas.png"),
            "samples/25.webp": sha256(source / "samples" / "25.webp"),
        },
        "outputs": {},
        "webp_frames": len(frames),
        "atlas_scan_frames": len(scan_frames),
    }
    for path in (gif_path, strip_path, scan_path):
        manifest["outputs"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
