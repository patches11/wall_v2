#!/usr/bin/env python3
"""
fetch_samples.py — grab short test clips for the wall via yt-dlp.

Searches a curated list of terms (good 25x25 material) and, by default, shows
each candidate (title, length, channel, ...) and asks before downloading. Picked
clips are trimmed to a short section and capped in resolution since we only ever
downscale to 25x25. Output lands in samples/<category>/ ready for convert.py.

Examples:
    python fetch_samples.py --list
    python fetch_samples.py                       # ask per video, all categories
    python fetch_samples.py --category ambient --seconds 30
    python fetch_samples.py --category party --count 3   # offer 3 candidates per term
    python fetch_samples.py --yes                 # skip prompts, take the top result
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Curated search terms. Each entry is what gets fed to yt-dlp's ytsearch.
TERMS = {
    "ambient": [
        "lava lamp 4K",
        "ink in water slow motion",
        "paint mixing macro",
        "jellyfish tank 4K",
        "northern lights 4K aurora time lapse",
        "crackling fireplace 4K yule log",
        "ambient gradient color flow background loop",
        "sky time lapse clouds",
    ],
    "party": [
        "fireworks 4K compilation",
        "trippy music visualizer audio reactive",
        "plasma screensaver psychedelic loop",
        "kaleidoscope 4K loop",
        "synthwave neon retrowave loop",
        "Tetris gameplay",
        "Space Invaders gameplay",
    ],
}


class Quit(Exception):
    """Raised to abort the whole run."""


def have(cmd):
    return shutil.which(cmd) is not None


def fmt_duration(s):
    if not s:
        return "?"
    s = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_views(v):
    if not v:
        return "?"
    return f"{int(v):,}"


def fmt_date(d):
    if d and len(d) == 8:
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return d or "?"


def existing_ids(dest):
    """Video IDs already downloaded into `dest` (parsed from the [id] in filenames)."""
    ids = set()
    if dest.is_dir():
        for p in dest.iterdir():
            m = re.search(r"\[([A-Za-z0-9_-]{6,})\]", p.name)
            if m:
                ids.add(m.group(1))
    return ids


def get_candidates(term, count):
    """Return metadata dicts for the top `count` search results (no download)."""
    cmd = [
        "yt-dlp", f"ytsearch{count}:{term}",
        "--dump-json", "--skip-download", "--no-warnings",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def describe(meta):
    w, h = meta.get("width"), meta.get("height")
    res = f"{w}x{h}" if w and h else "?"
    return (
        f"  title   : {meta.get('title', '?')}\n"
        f"  length  : {fmt_duration(meta.get('duration'))}    "
        f"res: {res}    views: {fmt_views(meta.get('view_count'))}    "
        f"date: {fmt_date(meta.get('upload_date'))}\n"
        f"  channel : {meta.get('uploader') or meta.get('channel') or '?'}\n"
        f"  url     : {meta.get('webpage_url', '?')}"
    )


def prompt(meta):
    """Ask whether to download. Returns 'y', 'n', 'a' (all), or raises Quit."""
    print(describe(meta))
    while True:
        ans = input("  download? [y]es / [n]o / [a]ll / [q]uit: ").strip().lower()
        if ans in ("y", "yes"):
            return "y"
        if ans in ("n", "no", ""):
            return "n"
        if ans in ("a", "all"):
            return "a"
        if ans in ("q", "quit"):
            raise Quit


def download(meta, category, outdir, seconds, height):
    """Download one already-chosen video by its URL."""
    dest = outdir / category
    dest.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(dest / "%(title).60B [%(id)s].%(ext)s")
    cmd = [
        "yt-dlp",
        meta.get("webpage_url") or meta["id"],
        "--no-playlist",
        "-f", f"bv*[height<={height}]+ba/b[height<={height}]/b",
        "--merge-output-format", "mp4",
        "-o", out_tmpl,
        "--no-overwrites",
    ]
    if seconds:
        cmd += ["--download-sections", f"*0-{seconds}", "--force-keyframes-at-cuts"]
    return subprocess.run(cmd).returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Download test clips via yt-dlp.")
    ap.add_argument("--category", choices=list(TERMS) + ["all"], default="all",
                    help="which term set to fetch (default all)")
    ap.add_argument("--count", type=int, default=1,
                    help="candidates offered per term (default 1)")
    ap.add_argument("--seconds", type=int, default=20,
                    help="trim each clip to first N seconds (0 = full video, default 20)")
    ap.add_argument("--height", type=int, default=720,
                    help="max video height to download (default 720)")
    ap.add_argument("--outdir", default="samples", help="output directory (default samples/)")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="skip prompts and download every candidate")
    ap.add_argument("--list", action="store_true", help="list the curated terms and exit")
    args = ap.parse_args()

    if args.list:
        for cat, terms in TERMS.items():
            print(f"\n{cat}:")
            for t in terms:
                print(f"  - {t}")
        return

    if not have("yt-dlp"):
        ap.error("yt-dlp not found on PATH (pip install yt-dlp)")
    if args.seconds and not have("ffmpeg"):
        ap.error("ffmpeg needed for --seconds trimming (or pass --seconds 0)")

    auto = args.yes
    if not auto and not sys.stdin.isatty():
        print("(no interactive terminal — downloading top results without prompting)")
        auto = True

    cats = list(TERMS) if args.category == "all" else [args.category]
    outdir = Path(args.outdir)
    ok = fail = skipped = 0
    try:
        for cat in cats:
            for term in TERMS[cat]:
                print(f"\n=== [{cat}] {term} ===")
                cands = get_candidates(term, args.count)
                if not cands:
                    print("  (no results)")
                    continue
                have_ids = existing_ids(outdir / cat)
                for meta in cands:
                    if meta.get("id") in have_ids:
                        print(f"  already downloaded: {meta.get('title', '?')}")
                        skipped += 1
                        continue
                    if not auto:
                        choice = prompt(meta)
                        if choice == "a":
                            auto = True
                        elif choice != "y":
                            skipped += 1
                            continue
                    else:
                        print(f"  downloading: {meta.get('title', '?')}")
                    if download(meta, cat, outdir, args.seconds, args.height):
                        ok += 1
                    else:
                        fail += 1
                        print(f"  (failed: {meta.get('title', '?')})", file=sys.stderr)
    except (Quit, KeyboardInterrupt):
        print("\nstopped.")

    print(f"\nDone: {ok} downloaded, {skipped} skipped, {fail} failed. Files in {outdir}/")
    print("Next: python batch_convert.py    (or: python convert.py <file> -o clip.wv25)")


if __name__ == "__main__":
    main()
