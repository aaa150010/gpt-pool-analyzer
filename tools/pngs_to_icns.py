#!/usr/bin/env python3
import struct
import sys
from pathlib import Path


ENTRIES = [
    ("icon_16x16.png", "icp4"),
    ("icon_32x32.png", "icp5"),
    ("icon_32x32@2x.png", "icp6"),
    ("icon_128x128.png", "ic07"),
    ("icon_256x256.png", "ic08"),
    ("icon_512x512.png", "ic09"),
    ("icon_512x512@2x.png", "ic10"),
]


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: pngs_to_icns.py ICONSET_DIR OUTPUT.icns", file=sys.stderr)
        return 2

    iconset = Path(sys.argv[1])
    output = Path(sys.argv[2])
    chunks = []
    for filename, icon_type in ENTRIES:
        path = iconset / filename
        if not path.exists():
            continue
        data = path.read_bytes()
        chunks.append(icon_type.encode("ascii") + struct.pack(">I", len(data) + 8) + data)

    if not chunks:
        print("No PNG icon entries found.", file=sys.stderr)
        return 1

    body = b"".join(chunks)
    output.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
