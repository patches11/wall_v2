#!/usr/bin/env python3
"""
convert.py — square-crop a video and downscale it to a 25x25 .wv25 clip.

Pipes the input through ffmpeg (center square crop + area downscale + fps
resample) and writes the resulting frames as a compact .wv25 file that the
wall_v2 simulator can preview (and a future firmware addition can play from SD).

Examples:
    python convert.py clip.mp4 -o clip.wv25
    python convert.py clip.webm -o clip.wv25 --fps 24 --start 5 --duration 8
    python convert.py clip.mp4 -o clip.wv25 --gamma 2.2   # darken washed-out clips
"""

import argparse
import shutil
import subprocess
import sys

import numpy as np

import wv25

CROP_EXPR = {
    "center": "crop='min(iw,ih)':'min(iw,ih)'",
    "top":    "crop='min(iw,ih)':'min(iw,ih)':(iw-min(iw,ih))/2:0",
    "bottom": "crop='min(iw,ih)':'min(iw,ih)':(iw-min(iw,ih))/2:ih-min(iw,ih)",
}


def build_ffmpeg_cmd(inp, fps, start, duration, crop):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", inp]
    if duration is not None:
        cmd += ["-t", str(duration)]
    vf = f"{CROP_EXPR[crop]},scale={wv25.WIDTH}:{wv25.HEIGHT}:flags=area,fps={fps}"
    cmd += ["-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    return cmd


def probe_duration(inp):
    """Best-effort input duration in seconds via ffprobe, or None."""
    if shutil.which("ffprobe") is None:
        return None
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", inp],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _progress(n, total):
    if total:
        bar_w = 24
        filled = min(bar_w, n * bar_w // total)
        bar = "#" * filled + "-" * (bar_w - filled)
        pct = min(100, n * 100 // total)
        msg = f"\r  [{bar}] {n}/{total} frames ({pct}%)"
    else:
        msg = f"\r  decoded {n} frames"
    print(msg, end="", file=sys.stderr, flush=True)


def decode_frames(inp, fps, start, duration, crop, show_progress=False, total=None):
    """Run ffmpeg and collect each 1875-byte frame as it streams out of stdout."""
    cmd = build_ffmpeg_cmd(inp, fps, start, duration, crop)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = []
    try:
        while True:
            chunk = proc.stdout.read(wv25.FRAME_BYTES)
            if len(chunk) < wv25.FRAME_BYTES:
                break  # EOF (a trailing partial frame, if any, is discarded)
            frames.append(np.frombuffer(chunk, dtype=np.uint8))
            if show_progress and len(frames) % 8 == 0:
                _progress(len(frames), total)
        proc.stdout.close()
        ret = proc.wait()
        err = proc.stderr.read().decode("utf-8", "replace").strip()
    finally:
        if proc.poll() is None:
            proc.kill()
    if show_progress:
        _progress(len(frames), total or len(frames))
        print(file=sys.stderr)
    if ret != 0:
        raise RuntimeError(f"ffmpeg failed (exit {ret}):\n{err}")
    if not frames:
        raise RuntimeError(f"ffmpeg produced no frames.\n{err}")
    return np.stack(frames).reshape(-1, wv25.HEIGHT, wv25.WIDTH, 3)


def apply_gamma(frames, gamma):
    if gamma == 1.0:
        return frames
    lut = np.clip(((np.arange(256) / 255.0) ** gamma) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return lut[frames]


def main():
    ap = argparse.ArgumentParser(description="Convert a video to a 25x25 .wv25 clip.")
    ap.add_argument("input", help="input video (any ffmpeg-readable format)")
    ap.add_argument("-o", "--output", required=True, help="output .wv25 path")
    ap.add_argument("--fps", type=int, default=24, help="output frame rate (1..60, default 24)")
    ap.add_argument("--start", type=float, default=None, help="start offset in seconds")
    ap.add_argument("--duration", type=float, default=None, help="clip length in seconds")
    ap.add_argument("--crop", choices=list(CROP_EXPR), default="center",
                    help="square-crop anchor (default center)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="optional gamma baked into the output (default 1.0 = none)")
    args = ap.parse_args()

    if not (1 <= args.fps <= 60):
        ap.error("--fps must be 1..60")
    if shutil.which("ffmpeg") is None:
        ap.error("ffmpeg not found on PATH")

    print(f"Decoding {args.input} -> {wv25.WIDTH}x{wv25.HEIGHT} @ {args.fps}fps ...", flush=True)
    clip_dur = args.duration
    if clip_dur is None:
        full = probe_duration(args.input)
        if full is not None:
            clip_dur = max(0.0, full - (args.start or 0.0))
    total = int(clip_dur * args.fps) if clip_dur else None
    frames = decode_frames(args.input, args.fps, args.start, args.duration, args.crop,
                           show_progress=True, total=total)
    frames = apply_gamma(frames, args.gamma)

    wv25.write_wv25(args.output, frames, args.fps)

    n = frames.shape[0]
    size = wv25.HEADER_SIZE + n * wv25.FRAME_BYTES
    print(f"Wrote {args.output}: {n} frames, {n / args.fps:.1f}s, {size} bytes")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
