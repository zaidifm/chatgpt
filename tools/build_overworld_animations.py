from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

DIRECTIONS = ("down", "right", "up", "left")
SHOWCASE_IDS = (
    "1", "4", "7", "25", "39", "52", "54", "58", "94", "104", "113",
    "129", "133", "143", "150", "151", "196", "197", "201", "212", "248",
    "282", "384", "448", "493",
)
VARIANTS = (
    ("normal", "pokemon/overworld"),
    ("shiny", "pokemon/overworld/shiny"),
    ("female", "pokemon/overworld/female"),
    ("shiny-female", "pokemon/overworld/shiny/female"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_image(archive: zipfile.ZipFile, member: str) -> Image.Image:
    with archive.open(member) as handle:
        return Image.open(io.BytesIO(handle.read())).convert("RGBA")


def keys_for(archive: zipfile.ZipFile, prefix: str) -> list[str]:
    base = f"{prefix}/down/"
    keys = {
        name[len(base):-4]
        for name in archive.namelist()
        if name.startswith(base)
        and name.endswith(".png")
        and "/frame2/" not in name
        and "/" not in name[len(base):]
    }
    return sorted(
        keys,
        key=lambda value: (
            int(value.split("-")[0]) if value.split("-")[0].isdigit() else 10**9,
            value,
        ),
    )


def animation_frames(
    archive: zipfile.ZipFile,
    prefix: str,
    key: str,
    scale: int = 2,
) -> list[Image.Image]:
    raw: list[Image.Image] = []
    for direction in DIRECTIONS:
        first = read_image(archive, f"{prefix}/{direction}/{key}.png")
        second = read_image(archive, f"{prefix}/{direction}/frame2/{key}.png")
        raw.extend((first, second, first, second))

    width = max(frame.width for frame in raw)
    height = max(frame.height for frame in raw)
    frames: list[Image.Image] = []
    for frame in raw:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(frame, ((width - frame.width) // 2, height - frame.height))
        if scale != 1:
            canvas = canvas.resize((width * scale, height * scale), Image.Resampling.NEAREST)
        frames.append(canvas)
    return frames


def save_gif(frames: list[Image.Image], output: Path, duration: int = 155) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        disposal=2,
        optimize=True,
        transparency=0,
    )


def save_webp(frames: list[Image.Image], output: Path, duration: int = 155) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output,
        "WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        lossless=True,
        method=6,
    )


def build_parade(
    archive: zipfile.ZipFile,
    output: Path,
    keys: list[str],
) -> None:
    cells = [(key, animation_frames(archive, "pokemon/overworld", key)) for key in keys]
    columns = min(5, max(1, len(cells)))
    rows = (len(cells) + columns - 1) // columns
    cell_width, cell_height = 150, 126
    output_frames: list[Image.Image] = []

    for frame_index in range(16):
        canvas = Image.new(
            "RGBA",
            (columns * cell_width, rows * cell_height),
            (20, 24, 31, 255),
        )
        draw = ImageDraw.Draw(canvas)
        for index, (key, frames) in enumerate(cells):
            x = (index % columns) * cell_width
            y = (index // columns) * cell_height
            draw.rounded_rectangle(
                (x + 6, y + 6, x + cell_width - 6, y + cell_height - 6),
                radius=12,
                fill=(35, 42, 54, 255),
                outline=(74, 85, 104, 255),
                width=2,
            )
            sprite = frames[frame_index]
            canvas.alpha_composite(
                sprite,
                (x + (cell_width - sprite.width) // 2, y + 12 + (82 - sprite.height) // 2),
            )
            label = f"#{key}"
            bounds = draw.textbbox((0, 0), label)
            draw.text(
                (x + (cell_width - (bounds[2] - bounds[0])) // 2, y + 94),
                label,
                fill=(235, 239, 245, 255),
            )
        output_frames.append(canvas)
    save_gif(output_frames, output, duration=150)


def build_directional_atlas(
    archive: zipfile.ZipFile,
    output: Path,
    keys: list[str],
) -> None:
    columns = min(5, max(1, len(keys)))
    rows = (len(keys) + columns - 1) // columns
    cell_width, cell_height = 180, 112
    canvas = Image.new(
        "RGBA", (columns * cell_width, rows * cell_height), (18, 22, 29, 255)
    )
    draw = ImageDraw.Draw(canvas)

    for index, key in enumerate(keys):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        draw.rounded_rectangle(
            (x + 5, y + 5, x + cell_width - 5, y + cell_height - 5),
            radius=10,
            fill=(33, 39, 50, 255),
            outline=(68, 78, 96, 255),
            width=2,
        )
        for direction_index, direction in enumerate(DIRECTIONS):
            sprite = read_image(
                archive, f"pokemon/overworld/{direction}/{key}.png"
            ).resize((64, 64), Image.Resampling.NEAREST)
            if sprite.width > 40 or sprite.height > 64:
                ratio = min(40 / sprite.width, 64 / sprite.height)
                sprite = sprite.resize(
                    (max(1, int(sprite.width * ratio)), max(1, int(sprite.height * ratio))),
                    Image.Resampling.NEAREST,
                )
            canvas.alpha_composite(
                sprite,
                (
                    x + 12 + direction_index * 40 + (40 - sprite.width) // 2,
                    y + 16 + (64 - sprite.height) // 2,
                ),
            )
        draw.text((x + 12, y + 88), f"#{key}", fill=(237, 240, 246, 255))

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, optimize=True)


def build(archive_path: Path, output: Path) -> dict[str, object]:
    if output.exists():
        shutil.rmtree(output)
    lab = output / "media-lab" / "overworld"
    generated = output / "_generated"
    lab.mkdir(parents=True)
    manifest: dict[str, object] = {
        "source_archive": archive_path.name,
        "generator": "tools/build_overworld_animations.py",
        "variants": {},
    }

    with zipfile.ZipFile(archive_path) as archive:
        variant_sets = [
            (label, prefix, keys_for(archive, prefix)) for label, prefix in VARIANTS
        ]
        variant_sets = [item for item in variant_sets if item[2]]
        normal_keys = next(
            (keys for label, _, keys in variant_sets if label == "normal"), []
        )
        showcase = [key for key in SHOWCASE_IDS if key in set(normal_keys)]
        if not showcase:
            showcase = normal_keys[:25]

        for label, prefix, keys in variant_sets:
            manifest["variants"][label] = {
                "prefix": prefix,
                "count": len(keys),
                "keys": keys,
            }
            for key in keys:
                save_gif(
                    animation_frames(archive, prefix, key),
                    generated / label / f"{key}.gif",
                )

        for key in showcase:
            frames = animation_frames(archive, "pokemon/overworld", key, scale=3)
            save_gif(frames, lab / "samples" / f"{key}.gif")
            save_webp(frames, lab / "samples" / f"{key}.webp")

        build_parade(archive, lab / "overworld-parade.gif", showcase)
        build_directional_atlas(archive, lab / "directional-atlas.png", showcase)

    atlas = lab / "overworld-motion-atlas.zip"
    with zipfile.ZipFile(atlas, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(generated.rglob("*.gif")):
            bundle.write(path, path.relative_to(generated).as_posix())
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    shutil.rmtree(generated)

    manifest["sample_ids"] = showcase
    manifest["outputs"] = {}
    for path in sorted(lab.rglob("*")):
        if path.is_file():
            manifest["outputs"][path.relative_to(output).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    (lab / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return {
        "output": str(output),
        "pack_bytes": atlas.stat().st_size,
        "pack_sha256": sha256_file(atlas),
        "variants": {
            key: value["count"] for key, value in manifest["variants"].items()
        },
        "files": sum(1 for path in output.rglob("*") if path.is_file()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.archive, args.output), indent=2))


if __name__ == "__main__":
    main()
