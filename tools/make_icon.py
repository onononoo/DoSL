"""
tools/make_icon.py -- writes assets/dosl.ico without an imaging library.

A Windows .ICO is a directory of images, and each image here is a legacy
BMP: a BITMAPINFOHEADER whose height is doubled (the second half is the
1-bit AND mask, a holdover from 1995 that is still mandatory), followed by
bottom-up 32-bit BGRA rows.

Pillow would do this in four lines. Pillow is also 3MB, and this file is
the Department's only dependency-free means of having a logo.

    python tools/make_icon.py [--out assets/dosl.ico] [--preview]
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 256)

#: The logo, at 16x16, one character per pixel. Everything else is scaled
#: from this by nearest-neighbour, which at these sizes is the right answer.
GLYPH = [
    "................",
    "................",
    "....ccccccc.....",
    "...cCCCCCCCc....",
    "..cCCCCCCCCCc...",
    "..bbbbbbbbbbb...",
    "..gGgGgGgGgGg...",
    "..rrrrrrrrrrr...",
    "..yYyYyYyYyYy...",
    "..bbbbbbbbbbb...",
    "..cCCCCCCCCCc...",
    "...cccccccccc...",
    "................",
    ".....sssss......",
    "................",
    "................",
]

#: BGRA, because that is the order a BMP stores them in.
PALETTE: dict[str, tuple[int, int, int, int]] = {
    ".": (0, 0, 0, 0),              # transparent
    "c": (120, 160, 196, 255),      # crust, shaded
    "C": (150, 193, 226, 255),      # crust, lit
    "b": (96, 133, 170, 255),       # crumb edge
    "g": (60, 140, 70, 255),        # lettuce, shaded
    "G": (80, 172, 88, 255),        # lettuce, lit
    "r": (48, 48, 176, 255),        # tomato
    "y": (60, 190, 222, 255),       # cheese, shaded
    "Y": (90, 214, 240, 255),       # cheese, lit
    "s": (70, 70, 70, 255),         # the shadow it casts on the desk
}


def sample(x: int, y: int, size: int) -> tuple[int, int, int, int]:
    """Nearest-neighbour lookup into GLYPH for a size x size image."""
    source_x = x * len(GLYPH[0]) // size
    source_y = y * len(GLYPH) // size
    return PALETTE[GLYPH[source_y][source_x]]


def bmp_image(size: int) -> bytes:
    """One ICO sub-image: BITMAPINFOHEADER + BGRA rows + AND mask."""
    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        size,        # biWidth
        size * 2,    # biHeight -- doubled to cover the mask that follows
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression = BI_RGB
        size * size * 4,
        0, 0, 0, 0,
    )

    pixels = bytearray()
    for y in range(size - 1, -1, -1):  # BMP rows run bottom to top
        for x in range(size):
            pixels.extend(sample(x, y, size))

    # The AND mask is ignored for 32-bit images but must still be present,
    # padded to a 4-byte boundary per row. All zero means "use the alpha".
    mask_stride = ((size + 31) // 32) * 4
    mask = bytes(mask_stride * size)

    return header + bytes(pixels) + mask


def build_ico(sizes: tuple[int, ...] = SIZES) -> bytes:
    images = [bmp_image(size) for size in sizes]
    offset = 6 + 16 * len(images)

    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, image in zip(sizes, images):
        directory.extend(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,   # 0 means 256 in this byte
            size if size < 256 else 0,
            0, 0, 1, 32,
            len(image), offset))
        offset += len(image)

    return bytes(directory) + b"".join(images)


def preview() -> str:
    """The glyph as text, for checking it before committing to binary."""
    shades = {".": "  ", "c": "()", "C": "()", "b": "==", "g": "%%", "G": "%%",
              "r": "@@", "y": "##", "Y": "##", "s": "..", }
    return "\n".join("".join(shades[c] for c in row) for row in GLYPH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parents[1] / "assets" / "dosl.ico")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()

    if args.preview:
        print(preview())

    blob = build_ico()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(blob)
    print(f"{args.out}  {len(blob)} bytes, {len(SIZES)} sizes: "
          f"{', '.join(str(s) for s in SIZES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
