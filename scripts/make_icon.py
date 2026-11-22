"""Draw icon.png without extra dependencies."""

import struct
import zlib
from pathlib import Path

W = H = 128
BG = (42, 22, 15, 255)
GOLD = (231, 184, 74, 255)
CREAM = (247, 239, 228, 255)
INK = (138, 61, 22, 255)

img = [[(0, 0, 0, 0) for _ in range(W)] for _ in range(H)]


def setp(x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if 0 <= x < W and 0 <= y < H:
        img[y][x] = color


def inside_round(x: int, y: int, radius: int = 28) -> bool:
    if radius <= x < W - radius and 0 <= y < H:
        return True
    if radius <= y < H - radius and 0 <= x < W:
        return True
    corners = (
        (radius, radius),
        (W - 1 - radius, radius),
        (radius, H - 1 - radius),
        (W - 1 - radius, H - 1 - radius),
    )
    for cx, cy in corners:
        if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
            return True
    return False


def disc(cx: int, cy: int, radius: int, color: tuple[int, int, int, int]) -> None:
    rr = radius * radius
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= rr:
                setp(x, y, color)


def round_rect(x0: int, y0: int, w: int, h: int, radius: int, color: tuple[int, int, int, int]) -> None:
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            dx = 0
            if x < x0 + radius:
                dx = x0 + radius - x
            elif x >= x0 + w - radius:
                dx = x - (x0 + w - radius - 1)
            dy = 0
            if y < y0 + radius:
                dy = y0 + radius - y
            elif y >= y0 + h - radius:
                dy = y - (y0 + h - radius - 1)
            if dx * dx + dy * dy <= radius * radius:
                setp(x, y, color)


def line(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int, int], thick: int = 2) -> None:
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for i in range(steps + 1):
        x = round(x0 + (x1 - x0) * i / steps)
        y = round(y0 + (y1 - y0) * i / steps)
        for dy in range(-thick, thick + 1):
            for dx in range(-thick, thick + 1):
                if dx * dx + dy * dy <= thick * thick:
                    setp(x + dx, y + dy, color)


for y in range(H):
    for x in range(W):
        if inside_round(x, y):
            setp(x, y, BG)

disc(52, 58, 34, GOLD)
disc(52, 58, 13, BG)
disc(52, 58, 4, GOLD)
round_rect(74, 74, 40, 34, 8, CREAM)
line(86, 91, 102, 91, INK, 2)
line(94, 84, 102, 91, INK, 2)
line(94, 98, 102, 91, INK, 2)

raw = bytearray()
for row in img:
    raw.append(0)
    for red, green, blue, alpha in row:
        raw.extend((red, green, blue, alpha))


def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    + chunk(b"IEND", b"")
)
Path("icon.png").write_bytes(png)
print(len(png))
