#!/usr/bin/env python3
"""
batch_convert.py — convert every video in a folder to a 25x25 .wv25 clip.

Walks an input folder (default samples/), runs each video through the same
square-crop + downscale pipeline as convert.py, and writes the results to an
output folder with short, tidy names (YouTube titles are long and messy).

Examples:
    python batch_convert.py                         # samples/ -> out/
    python batch_convert.py samples -o out --fps 24
    python batch_convert.py samples --seconds 10 --overwrite
"""

import argparse
import re
import sys
from pathlib import Path

import convert
import wv25

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".flv", ".ts", ".gif"}


def slugify(stem, max_words=6, max_len=40):
    """Turn a messy video filename stem into a short kebab-case slug."""
    # Drop a trailing yt-dlp "[videoid]" tag.
    stem = re.sub(r"\s*\[[A-Za-z0-9_-]{6,}\]\s*$", "", stem)
    stem = stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    if not stem:
        return "clip"
    words = stem.split("-")[:max_words]
    out = ""
    for w in words:
        cand = f"{out}-{w}" if out else w
        if out and len(cand) > max_len:
            break  # stop on whole-word boundaries rather than cutting mid-word
        out = cand
    return out[:max_len] or "clip"


def unique(name, used):
    """Ensure the slug is unique within the output folder."""
    out = name
    i = 2
    while out in used:
        out = f"{name}-{i}"
        i += 1
    used.add(out)
    return out


def find_videos(indir):
    return sorted(p for p in indir.rglob("*") if p.suffix.lower() in VIDEO_EXTS)


def main():
    ap = argparse.ArgumentParser(description="Batch-convert videos to .wv25 clips.")
    ap.add_argument("indir", nargs="?", default="samples", help="input folder (default samples/)")
    ap.add_argument("-o", "--outdir", default="out", help="output folder (default out/)")
    ap.add_argument("--fps", type=int, default=24, help="output frame rate (1..60, default 24)")
    ap.add_argument("--seconds", type=float, default=None,
                    help="convert only the first N seconds of each video")
    ap.add_argument("--crop", choices=list(convert.CROP_EXPR), default="center",
                    help="square-crop anchor (default center)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="optional gamma baked into output (default 1.0 = none)")
    ap.add_argument("--overwrite", action="store_true", help="re-convert even if output exists")
    args = ap.parse_args()

    if not (1 <= args.fps <= 60):
        ap.error("--fps must be 1..60")
    indir = Path(args.indir)
    if not indir.is_dir():
        ap.error(f"input folder not found: {indir}")

    videos = find_videos(indir)
    if not videos:
        print(f"No videos found in {indir}/")
        return

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    used, ok, fail, skip = set(), 0, 0, 0
    for src in videos:
        name = unique(slugify(src.stem), used)
        dst = outdir / f"{name}.wv25"
        if dst.exists() and not args.overwrite:
            print(f"skip (exists): {dst.name}  <- {src.name}")
            skip += 1
            continue
        print(f"convert: {src.name}  ->  {dst.name}")
        try:
            frames = convert.decode_frames(str(src), args.fps, None, args.seconds, args.crop)
            frames = convert.apply_gamma(frames, args.gamma)
            wv25.write_wv25(dst, frames, args.fps)
            n = frames.shape[0]
            print(f"         {n} frames, {n / args.fps:.1f}s")
            ok += 1
        except (RuntimeError, ValueError) as e:
            print(f"         error: {e}", file=sys.stderr)
            fail += 1

    print(f"\nDone: {ok} converted, {skip} skipped, {fail} failed. Output in {outdir}/")


if __name__ == "__main__":
    main()
