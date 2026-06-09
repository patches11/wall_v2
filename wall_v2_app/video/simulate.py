#!/usr/bin/env python3
"""
simulate.py — play a .wv25 clip on the computer with an LED-accurate preview.

Renders each frame the way the physical wall_v2 panel shows it: 25x25 square LED
pixels with thin black gaps, colour-mapped through the WS2812B gamma/brightness
model in wv25.led_to_screen. Use it to judge a clip before it goes on the wall.

Examples:
    python simulate.py clip.wv25
    python simulate.py clip.wv25 --loop --scale 28
    python simulate.py clip.wv25 --brightness 180 --gamma 2.4

Keys:  space=pause/play   [ / ]=step   + / -=brightness   , / .=gamma   q/Esc=quit
"""

import argparse
import sys

import cv2
import numpy as np

import wv25

WINDOW = "wall_v2 simulator"


def lit_mask(scale, gap):
    """1D boolean over a cell of `scale` px: True for the lit area, False in the gap."""
    idx = np.arange(scale)
    return idx < (scale - gap)


def render(frame, scale, gap, brightness, gamma):
    """Build a BGR canvas of square LED pixels for one 25x25 RGB frame."""
    screen = wv25.led_to_screen(frame, brightness=brightness, gamma=gamma)  # [25,25,3] RGB
    big = np.repeat(np.repeat(screen, scale, axis=0), scale, axis=1)        # [625,625,3]
    if gap > 0:
        m1d = lit_mask(scale, gap)
        line = np.tile(m1d, wv25.HEIGHT)  # mask across full canvas axis
        mask = line[:, None] & line[None, :]
        big[~mask] = 0
    return cv2.cvtColor(big, cv2.COLOR_RGB2BGR)


def main():
    ap = argparse.ArgumentParser(description="Preview a .wv25 clip as LED square pixels.")
    ap.add_argument("input", help="input .wv25 file")
    ap.add_argument("--scale", type=int, default=24, help="pixels per LED cell (default 24)")
    ap.add_argument("--gap", type=int, default=2, help="black gap px between cells (default 2)")
    ap.add_argument("--brightness", type=int, default=255, help="wall brightness 0..255")
    ap.add_argument("--gamma", type=float, default=2.2, help="display gamma (default 2.2)")
    ap.add_argument("--fps", type=int, default=None, help="override playback fps")
    ap.add_argument("--loop", action="store_true", help="loop the clip")
    args = ap.parse_args()

    if args.gap >= args.scale:
        ap.error("--gap must be smaller than --scale")

    fps, frames = wv25.read_wv25(args.input)
    fps = args.fps or fps
    n = frames.shape[0]
    brightness = max(0, min(255, args.brightness))
    gamma = max(0.1, args.gamma)
    delay = max(1, int(round(1000.0 / fps)))

    print(f"{args.input}: {n} frames @ {fps}fps ({n / fps:.1f}s). "
          f"space=pause [ ]=step +/-=bright ,/.=gamma q=quit")

    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    i = 0
    paused = False
    while True:
        canvas = render(frames[i], args.scale, args.gap, brightness, gamma)
        cv2.setWindowTitle(
            WINDOW, f"wall_v2  frame {i + 1}/{n}  bright {brightness}  gamma {gamma:.1f}"
            + ("  [paused]" if paused else "")
        )
        cv2.imshow(WINDOW, canvas)

        key = cv2.waitKey(0 if paused else delay) & 0xFF

        if key in (ord("q"), 27):  # q or Esc
            break
        elif key == ord(" "):
            paused = not paused
        elif key == ord("]"):
            paused = True
            i = (i + 1) % n
            continue
        elif key == ord("["):
            paused = True
            i = (i - 1) % n
            continue
        elif key in (ord("+"), ord("=")):
            brightness = min(255, brightness + 15)
            continue
        elif key in (ord("-"), ord("_")):
            brightness = max(0, brightness - 15)
            continue
        elif key == ord("."):
            gamma = min(4.0, gamma + 0.1)
            continue
        elif key == ord(","):
            gamma = max(0.1, gamma - 0.1)
            continue

        # closing the window via the title-bar X
        if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break

        if not paused:
            i += 1
            if i >= n:
                if args.loop:
                    i = 0
                else:
                    break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
