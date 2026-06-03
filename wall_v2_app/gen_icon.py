"""
gen_icon.py — generate static/icons/icon-180.png for iOS PWA home screen.

iOS ignores manifest icons and exclusively uses <link rel="apple-touch-icon">,
which must point to a PNG file.  Run this once (alongside gen_cert.py).
No third-party libraries needed — uses only stdlib struct and zlib.
"""

import struct, zlib, os, math

SIZE   = 180
BG     = (0x0a, 0x0a, 0x0a)       # app background colour

# 4×4 grid of LED-dot colours (row-major)
COLORS = [
    (233,  69,  96), (245, 166,  35), (248, 231,  28), (126, 211,  33),
    ( 74, 144, 217), (189,  16, 224), (233,  69,  96), ( 74, 144, 217),
    (245, 166,  35), (126, 211,  33), (189,  16, 224), (248, 231,  28),
    (126, 211,  33), (233,  69,  96), (245, 166,  35), ( 74, 144, 217),
]

DOT_R  = 17     # dot radius in pixels
STEP   = 44     # grid step (centre-to-centre)
ORIGIN = 24     # centre of first dot


def write_png(path, width, height, rgb_pixels):
    """Write a 24-bit RGB PNG using only stdlib."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)

    raw = bytearray()
    for y in range(height):
        raw.append(0)   # filter = None
        for x in range(width):
            r, g, b = rgb_pixels[y * width + x]
            raw += bytes([r, g, b])

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', zlib.compress(bytes(raw), 9)))
        f.write(chunk(b'IEND', b''))


def make_pixels():
    pixels = []
    for py in range(SIZE):
        for px in range(SIZE):
            color = BG
            for i in range(16):
                col, row = i % 4, i // 4
                cx = ORIGIN + col * STEP
                cy = ORIGIN + row * STEP
                dx, dy = px - cx, py - cy
                if dx * dx + dy * dy <= DOT_R * DOT_R:
                    # Soft anti-aliased edge: blend with background at the rim
                    dist = math.sqrt(dx * dx + dy * dy)
                    if dist > DOT_R - 1:
                        t = DOT_R - dist          # 0..1 at the edge
                        dc = COLORS[i]
                        color = tuple(int(BG[c] + (dc[c] - BG[c]) * t) for c in range(3))
                    else:
                        color = COLORS[i]
                    break
            pixels.append(color)
    return pixels


OUT = os.path.join(os.path.dirname(__file__), 'static', 'icons', 'icon-180.png')
write_png(OUT, SIZE, SIZE, make_pixels())
print(f"Generated {OUT}")
