"""Render the Cardy card-and-C icon as PNGs, using only Python's stdlib."""
import math
from pathlib import Path
import struct
import zlib

PURPLE = (114, 84, 163)
PALE = (207, 192, 227)
WHITE = (250, 247, 255)


def rounded_rect(x, y, left, top, width, height, radius, angle):
    center_x, center_y = left + width / 2, top + height / 2
    cosine, sine = math.cos(angle), math.sin(angle)
    dx, dy = x - center_x, y - center_y
    local_x = abs(dx * cosine + dy * sine) - (width / 2 - radius)
    local_y = abs(-dx * sine + dy * cosine) - (height / 2 - radius)
    return math.hypot(max(local_x, 0), max(local_y, 0)) + min(max(local_x, local_y), 0) <= radius


def line_distance(x, y, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    fraction = max(0, min(1, ((x - start[0]) * dx + (y - start[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - start[0] - fraction * dx, y - start[1] - fraction * dy)


def pixel(x, y):
    color = PURPLE
    if rounded_rect(x, y, 41, 40, 89, 101, 14, math.radians(-12)):
        color = PALE
    if rounded_rect(x, y, 53, 42, 89, 101, 14, math.radians(7)):
        color = WHITE
    dx, dy = x - 97, y - 92
    ring = abs(math.hypot(dx, dy) - 17) <= 3 and not (dx > 0 and abs(dy) < dx)
    caps = any(math.hypot(x - cx, y - cy) <= 3 for cx, cy in [(109, 80), (109, 104)])
    if ring or caps:
        color = PURPLE
    return color


def write_png(path, size):
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            # Four samples soften edges at every icon size.
            samples = [pixel((x + sx) * 180 / size, (y + sy) * 180 / size) for sx, sy in [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]]
            rows.extend(sum(sample[channel] for sample in samples) // 4 for channel in range(3))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def main():
    directory = Path(__file__).resolve().parent.parent / "cards" / "static" / "cards"
    for name, size in [("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)]:
        write_png(directory / name, size)
        print(f"Generated {name} ({size} x {size})")


if __name__ == "__main__":
    main()
