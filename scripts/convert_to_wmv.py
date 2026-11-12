#!/usr/bin/env python3
"""
Convert to WMV — Batch conversion of scrolling MP4s to Windows Media Video format.

Converts all *_scrolling.mp4 files in a source directory to .wmv using ffmpeg's
wmv2 encoder at 4000 kbps video / 128 kbps audio.

Usage:
    python scripts/convert_to_wmv.py                          # Convert azazael local MP4s
    python scripts/convert_to_wmv.py --source D:/              # Convert from D: drive
    python scripts/convert_to_wmv.py --source . --pattern *.mp4  # Custom pattern
    python scripts/convert_to_wmv.py --dry-run                 # Preview only
"""

import argparse
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional


def find_targets(source: Path, pattern: str = "*_scrolling.mp4",
                 extra_files: Optional[List[str]] = None) -> List[Path]:
    """Find MP4 files matching the pattern and extra files."""
    targets = list(source.glob(pattern))
    if extra_files:
        for f in extra_files:
            p = source / f
            if p.exists() and p not in targets:
                targets.append(p)
    return sorted(targets)


def convert_file(input_path: Path, output_path: Path,
                 video_bitrate: str = "4000k", audio_bitrate: str = "128k",
                 dry_run: bool = False) -> bool:
    """Convert a single MP4 file to WMV format.

    Args:
        input_path: Path to input MP4
        output_path: Path for output WMV
        video_bitrate: WMV2 video bitrate (default 4000k)
        audio_bitrate: WMAv2 audio bitrate (default 128k)
        dry_run: If True, only print what would be done

    Returns:
        True if conversion succeeded or dry run
    """
    if output_path.exists():
        out_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"  [SKIP] {output_path.name} ({out_mb:.0f} MB, already exists)")
        return True

    sz_mb = input_path.stat().st_size / (1024 * 1024)
    print(f"  Converting: {input_path.name} ({sz_mb:.1f} MB) -> {output_path.name}")

    if dry_run:
        print(f"    [DRY-RUN] Would run ffmpeg with wmv2, {video_bitrate}")
        return True

    start = time.time()
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(input_path),
                "-c:v", "wmv2",
                "-b:v", video_bitrate,
                "-c:a", "wmav2",
                "-b:a", audio_bitrate,
                str(output_path),
            ],
            capture_output=True, text=True, timeout=1200, encoding="utf-8", errors="replace"
        )
        elapsed = time.time() - start

        if result.returncode == 0 and output_path.exists():
            out_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"    [OK] {elapsed:.0f}s: {sz_mb:.1f} MB -> {out_mb:.1f} MB")
            return True
        else:
            print(f"    [FAIL] ffmpeg error: {result.stderr[:150]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"    [TIMEOUT] Exceeded 1200s limit")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Batch convert scrolling MP4s to WMV format"
    )
    parser.add_argument("--source", default=str(Path.cwd()),
                        help="Source directory (default: current directory)")
    parser.add_argument("--pattern", default="*_scrolling.mp4",
                        help="File glob pattern (default: *_scrolling.mp4)")
    parser.add_argument("--extra", nargs="*", default=[],
                        help="Extra files to include (e.g. complete_remorse_cain.mp4)")
    parser.add_argument("--output", help="Output directory (default: same as source)")
    parser.add_argument("--video-bitrate", default="4000k",
                        help="WMV2 video bitrate (default: 4000k)")
    parser.add_argument("--audio-bitrate", default="128k",
                        help="WMAv2 audio bitrate (default: 128k)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be converted")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_dir():
        print(f"[FAIL] Source directory not found: {source}")
        return

    targets = find_targets(source, args.pattern, args.extra or None)
    if not targets:
        print(f"[INFO] No files matching '{args.pattern}' in {source}")
        return

    if args.dry_run:
        print(f"\n[DRY-RUN] Would convert {len(targets)} file(s):")
        for t in targets:
            sz = t.stat().st_size / (1024 * 1024)
            print(f"  {t.name} ({sz:.1f} MB) -> {t.stem}.wmv")
        return

    output_dir = Path(args.output or source).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nBatch WMV Conversion")
    print(f"  Source: {source}")
    print(f"  Output: {output_dir}")
    print(f"  Files:  {len(targets)}")
    print(f"  Codec:  wmv2 @ {args.video_bitrate} / wmav2 @ {args.audio_bitrate}")
    print()

    ok = 0
    fail = 0
    skip = 0
    for target in targets:
        output_path = output_dir / f"{target.stem}.wmv"
        if output_path.exists():
            out_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"  [SKIP] {output_path.name} ({out_mb:.0f} MB, exists)")
            skip += 1
            continue
        if convert_file(target, output_path, args.video_bitrate, args.audio_bitrate):
            ok += 1
        else:
            fail += 1

    print(f"\nDone: {ok} converted, {skip} skipped, {fail} failed")


if __name__ == "__main__":
    main()
