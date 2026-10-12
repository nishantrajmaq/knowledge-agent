"""Builds the Teams / Microsoft 365 Copilot app package (knowledge-agent.zip).

Run from anywhere:  python teams-app/build_package.py
Needs only the standard library. Re-run after editing manifest.json.
"""
import json
import math
import struct
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _png(path: Path, size: int, pixel) -> None:
    """Write an RGBA PNG; pixel(x, y) returns an (r, g, b, a) tuple."""
    raw = b"".join(
        b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(size)) for y in range(size)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _magnifier(size: int):
    """Coverage 0..1 of a magnifying glass (ring + handle) at (x, y), anti-aliased."""
    cx = cy = size * 0.42
    radius, thick = size * 0.26, size * 0.075
    # handle runs from the ring edge towards the bottom-right corner
    hx0 = cx + radius * math.cos(math.pi / 4)
    hy0 = cy + radius * math.sin(math.pi / 4)
    hx1, hy1 = size * 0.80, size * 0.80
    hlen2 = (hx1 - hx0) ** 2 + (hy1 - hy0) ** 2

    def coverage(x: int, y: int) -> float:
        px, py = x + 0.5, y + 0.5
        ring = abs(math.hypot(px - cx, py - cy) - radius)
        t = max(0.0, min(1.0, ((px - hx0) * (hx1 - hx0) + (py - hy0) * (hy1 - hy0)) / hlen2))
        handle = math.hypot(px - (hx0 + t * (hx1 - hx0)), py - (hy0 + t * (hy1 - hy0)))
        d = min(ring, handle)
        return max(0.0, min(1.0, thick - d + 0.5))

    return coverage


def build() -> Path:
    # Color icon: 192x192, white magnifier on Microsoft blue.
    cov = _magnifier(192)
    _png(
        HERE / "color.png",
        192,
        lambda x, y: (
            round(0 + (255 - 0) * cov(x, y)),
            round(120 + (255 - 120) * cov(x, y)),
            round(212 + (255 - 212) * cov(x, y)),
            255,
        ),
    )
    # Outline icon: 32x32, white on transparent (Teams requirement).
    cov32 = _magnifier(32)
    _png(HERE / "outline.png", 32, lambda x, y: (255, 255, 255, round(255 * cov32(x, y))))

    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    out = HERE / "knowledge-agent.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(HERE / "manifest.json", "manifest.json")
        z.write(HERE / "color.png", "color.png")
        z.write(HERE / "outline.png", "outline.png")
    print(f"Built {out} (app '{manifest['name']['short']}' v{manifest['version']})")
    return out


if __name__ == "__main__":
    build()
