#!/usr/bin/env python3
"""Split tall full-page screenshots into overlapping tiles that are readable when viewed.

Very tall images get downscaled until the text is unreadable, so view the tiles, never the
full capture.

    python3 tile.py .website-audit/shots/dashboard --out .website-audit/tiles/dashboard
    python3 tile.py shot.png [more.png ...] [--tile-height 1600] [--overlap 100] [--max-width 1440]

Inputs can be PNG/JPEG/WebP files or directories (searched recursively). Images no taller than
about one tile are copied unchanged as a single tile. Prints every tile path it writes.
"""
import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("tile.py: Pillow is missing. Install it with: python3 -m pip install --user pillow")

Image.MAX_IMAGE_PIXELS = None  # full-page captures legitimately exceed PIL's bomb guard
EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def collect(inputs):
    files = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in EXTS and ".tile-" not in f.name)
        elif p.is_file():
            files.append(p)
        else:
            print(f"tile.py: skipping missing input {p}", file=sys.stderr)
    return files


def tile_image(src, out_dir, tile_height, overlap, max_width):
    img = Image.open(src)
    if max_width and img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    out_dir.mkdir(parents=True, exist_ok=True)
    step = tile_height - overlap
    if img.height <= tile_height * 1.15:
        tops = [0]
        tile_height = img.height
    else:
        tops = list(range(0, img.height - overlap, step))
        # Pull the last tile up so it is full height instead of a thin sliver.
        if tops and tops[-1] + tile_height > img.height:
            tops[-1] = max(0, img.height - tile_height)
        tops = sorted(set(tops))
    written = []
    total = len(tops)
    for i, top in enumerate(tops, 1):
        box = (0, top, img.width, min(img.height, top + tile_height))
        name = f"{src.stem}.tile-{i:02d}-of-{total:02d}{src.suffix.lower()}"
        dest = out_dir / name
        img.crop(box).save(dest)
        written.append((dest, box))
    return img.size, written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="image files or directories")
    ap.add_argument("--out", help="output directory (default: a tiles/ folder next to each image)")
    ap.add_argument("--tile-height", type=int, default=1600, help="tile height in px (default 1600)")
    ap.add_argument("--overlap", type=int, default=100, help="vertical overlap between tiles in px (default 100)")
    ap.add_argument("--max-width", type=int, default=0, help="downscale wider images to this width first (0 = keep)")
    a = ap.parse_args()
    if a.overlap >= a.tile_height:
        sys.exit("tile.py: --overlap must be smaller than --tile-height")

    files = collect(a.inputs)
    if not files:
        sys.exit("tile.py: no images found")
    for f in files:
        out_dir = Path(a.out) if a.out else f.parent / "tiles"
        (w, h), written = tile_image(f, out_dir, a.tile_height, a.overlap, a.max_width)
        print(f"{f} ({w}x{h}) -> {len(written)} tile(s)")
        for dest, box in written:
            print(f"  {dest}  rows {box[1]}-{box[3]}")


if __name__ == "__main__":
    main()
