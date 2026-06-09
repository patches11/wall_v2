"""
wv25.py — shared format + colorspace helpers for the wall_v2 video tools.

The .wv25 container is a tiny header followed by raw row-major RGB frames, one
byte per channel, exactly matching the Teensy's `frameBuf` / `showBuf` layout
(625 LEDs * 3 = 1875 bytes per 25x25 frame).

    off size field
    0   4    magic = b"WV25"
    4   1    version = 1
    5   1    width  = 25
    6   1    height = 25
    7   1    fps    (1..60)
    8   4    frame_count (uint32 little-endian)
    12  ...  frame_count * (W*H*3) bytes, row-major RGB

The frames stored on disk are LED-native bytes (what the LEDs receive directly).
All gamma/brightness emulation for the on-screen preview lives in led_to_screen.
"""

import struct

import numpy as np

MAGIC = b"WV25"
VERSION = 1
WIDTH = 25
HEIGHT = 25
FRAME_BYTES = WIDTH * HEIGHT * 3  # 1875

_HEADER = struct.Struct("<4sBBBBI")  # magic, version, w, h, fps, frame_count
HEADER_SIZE = _HEADER.size  # 12


def write_wv25(path, frames, fps):
    """Write frames (ndarray [n, 25, 25, 3] uint8, RGB) to a .wv25 file."""
    frames = np.ascontiguousarray(frames, dtype=np.uint8)
    if frames.ndim != 4 or frames.shape[1:] != (HEIGHT, WIDTH, 3):
        raise ValueError(f"frames must be [n, {HEIGHT}, {WIDTH}, 3], got {frames.shape}")
    fps = int(round(fps))
    if not (1 <= fps <= 60):
        raise ValueError(f"fps must be 1..60, got {fps}")
    n = frames.shape[0]
    header = _HEADER.pack(MAGIC, VERSION, WIDTH, HEIGHT, fps, n)
    with open(path, "wb") as f:
        f.write(header)
        f.write(frames.tobytes())


def read_wv25(path):
    """Read a .wv25 file. Returns (fps, frames) where frames is [n, 25, 25, 3] uint8."""
    with open(path, "rb") as f:
        head = f.read(HEADER_SIZE)
        if len(head) < HEADER_SIZE:
            raise ValueError("file too small to contain a .wv25 header")
        magic, version, w, h, fps, n = _HEADER.unpack(head)
        if magic != MAGIC:
            raise ValueError(f"bad magic {magic!r}, not a .wv25 file")
        if version != VERSION:
            raise ValueError(f"unsupported version {version}")
        if (w, h) != (WIDTH, HEIGHT):
            raise ValueError(f"expected {WIDTH}x{HEIGHT}, got {w}x{h}")
        data = f.read(n * w * h * 3)
    if len(data) != n * w * h * 3:
        raise ValueError("truncated frame data")
    frames = np.frombuffer(data, dtype=np.uint8).reshape(n, h, w, 3)
    return fps, frames


# ── Colorspace emulation ──────────────────────────────────────────────
# WS2812B LEDs are roughly linear in PWM duty, so we treat the stored byte as a
# linear light intensity. To show that emitted light faithfully on an sRGB
# monitor we scale by the wall's brightness and then encode with a display
# gamma. Lower `gamma` -> brighter midtones on screen. Tune by eye vs the wall.

# Optional WS2812B white-point nudge: the green die runs strong relative to red
# and blue, so a neutral byte triple looks slightly green on the panel. This
# row-normalised matrix reproduces that tint in the preview. Set use_matrix=False
# to disable.
_WS2812B_MATRIX = np.array(
    [
        [1.00, 0.00, 0.00],
        [0.00, 1.10, 0.00],
        [0.00, 0.00, 0.92],
    ],
    dtype=np.float32,
)


def led_to_screen(rgb_u8, brightness=255, gamma=2.2, use_matrix=True):
    """Map LED-native RGB bytes to sRGB bytes for an on-screen preview.

    rgb_u8     : ndarray (..., 3) uint8 of LED values
    brightness : 0..255 wall brightness (linear scale, like FastLED)
    gamma      : display gamma; emitted**(1/gamma) is sent to the monitor
    """
    lin = rgb_u8.astype(np.float32) / 255.0
    if use_matrix:
        lin = lin @ _WS2812B_MATRIX.T
    lin *= float(brightness) / 255.0
    np.clip(lin, 0.0, 1.0, out=lin)
    screen = np.power(lin, 1.0 / float(gamma))
    return np.clip(screen * 255.0 + 0.5, 0, 255).astype(np.uint8)
