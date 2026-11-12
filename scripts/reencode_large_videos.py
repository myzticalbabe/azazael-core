#!/usr/bin/env python3
"""
Re-encode Large Videos — Batch Video Compression with CAIN Standard

Scans drives for large video files (>.50 MB) and re-encodes them using the
CAIN standard established from reverse engineering (25fps, 1920x1080,
H.264 High, CRF 23, GOP 250). See LESSONS_MASTER.md #64-65 for details.

Usage:
    python reencode_large_videos.py --scan G:/AZAZAEL
    python reencode_large_videos.py --scan G:/AZAZAEL --dry-run
    python reencode_large_videos.py --scan G:/AZAZAEL --min-size 100
    python reencode_large_videos.py --scan G:/AZAZAEL --force-reencode
    python reencode_large_videos.py --scan /c/Users/clint/Desktop --output ./compressed
    python reencode_large_videos.py --report-only
    python reencode_large_videos.py --scan G:/AZAZAEL --exclude-cain  # Skip CAIN-named files
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─── CAIN Standard (HEVC edition 2026-10-01; was H.264/25fps/CRF23) ──────

CAIN_FPS = 30
CAIN_CRF = 26
CAIN_CODEC = "libx265"
CAIN_PROFILE = "high"  # x264 only; omitted for libx265 (no 'high')
CAIN_GOP = 300
CAIN_AUDIO = "128k"
CAIN_SCALE = 1.0  # Keep original resolution (downscale to 1080p handled by compress_video)

# Default thresholds
DEFAULT_MIN_SIZE_MB = 50
DEFAULT_OUTPUT_DIR = "compressed"

# ─── Video detection ───────────────────────────────────────────────

VIDEO_EXTENSIONS = {".mp4", ".wmv", ".webm", ".avi", ".mov", ".mkv"}
CODEC_MAP = {
    "mp4": "libx265",
    "wmv": "wmv2",
}


def find_videos(directory: str, min_size_mb: float = DEFAULT_MIN_SIZE_MB,
                recursive: bool = True, exclude_cain: bool = False) -> List[Path]:
    """Find video files above the minimum size threshold.

    Args:
        directory: Directory to scan
        min_size_mb: Minimum file size in MB
        recursive: Whether to scan subdirectories
        exclude_cain: Whether to exclude CAIN-named files

    Returns:
        Sorted list of video file paths
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        print(f"  [WARN] Directory not found: {directory}")
        return []

    pattern = "**/*" if recursive else "*"
    videos = []

    for f in sorted(dir_path.glob(pattern)):
        if not f.is_file():
            continue
        if f.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        size_mb = f.stat().st_size / (1024 * 1024)
        if size_mb < min_size_mb:
            continue
        if exclude_cain and "cain" in f.stem.lower():
            continue

        videos.append(f)

    return videos


def get_video_info(video_path: Path) -> Dict:
    """Get video metadata using ffprobe (basic info only)."""
    info = {
        "path": str(video_path),
        "size_mb": round(video_path.stat().st_size / (1024 * 1024), 2),
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "codec": "unknown",
        "profile": "unknown",
        "has_b_frames": -1,
    }

    if not shutil.which("ffprobe"):
        return info

    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return info

        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        info["duration"] = round(float(fmt.get("duration", 0)), 1)

        for stream in data.get("streams", []):
            if stream["codec_type"] == "video":
                info["width"] = stream.get("width", 0)
                info["height"] = stream.get("height", 0)
                fps_str = stream.get("r_frame_rate", "0/1")
                num, den = fps_str.split("/")
                info["fps"] = round(int(num) / int(den), 2) if int(den) else 0
                info["codec"] = stream.get("codec_name", "unknown")
                info["profile"] = stream.get("profile", "unknown")
                info["has_b_frames"] = stream.get("has_b_frames", -1)
                break
    except Exception:
        pass

    return info


def check_cain_compliance(info: Dict) -> Tuple[bool, List[str]]:
    """Check if a video already meets CAIN standards (skip re-encode)."""
    issues = []

    # Codec must be H.264 or H.265
    if info["codec"] not in ("h264", "h265", "hevc", "libx264"):
        issues.append(f"codec={info['codec']}")

    # Profile must not be Constrained Baseline
    if "baseline" in info.get("profile", "").lower():
        issues.append(f"profile={info['profile']} (Constrained Baseline)")

    # FPS should match CAIN standard (tolerate minor rounding)
    fps_tolerance = 2.0
    if info.get("fps", 0) > 0 and abs(info["fps"] - CAIN_FPS) > fps_tolerance:
        issues.append(f"fps={info['fps']} (standard: {CAIN_FPS})")

    # Resolution should be at or below 1080p
    if info["width"] > 1920 or info["height"] > 1080:
        issues.append(f"resolution={info['width']}x{info['height']}")

    # Must have B-frames for inter-frame compression
    bframes = info.get("has_b_frames", 0)
    if bframes is not None and bframes == 0 and info.get("profile") != "unknown":
        issues.append(f"b_frames=0 (no inter-frame compression)")

    return len(issues) == 0, issues


def estimate_output_size(info: Dict) -> float:
    """Estimate output size with CAIN standard encoding.

    CRF 23 @ 1080p: ~12-18 MB/min for typical content
    """
    if info["duration"] <= 0:
        return info["size_mb"] * 0.3  # Assume 70% reduction
    mb_per_min = 15  # Conservative estimate for CRF 23
    estimated = (info["duration"] / 60) * mb_per_min
    return round(estimated, 1)


def reencode_video(input_path: Path, output_path: Path,
                   dry_run: bool = False,
                   force: bool = False) -> Dict:
    """Re-encode a single video using CAIN standard settings.

    Uses compress_video.py if available, otherwise falls back to direct ffmpeg.

    Args:
        input_path: Path to source video
        output_path: Path for re-encoded output
        dry_run: If True, only print what would be done
        force: If True, re-encode even if already compliant

    Returns:
        Dict with success status, sizes, and timing
    """
    info = get_video_info(input_path)
    already_compliant, issues = check_cain_compliance(info)

    result = {
        "file": input_path.name,
        "input_mb": info["size_mb"],
        "estimated_mb": estimate_output_size(info),
        "already_compliant": already_compliant,
        "issues": issues,
        "action": "skip" if (already_compliant and not force) else "encode",
        "success": False,
        "output_path": None,
        "duration_s": 0,
    }

    if result["action"] == "skip":
        return result

    # Try using compress_video.py first (preferred)
    compress_video_py = (
        Path(__file__).resolve().parent.parent
        / "src" / "CODEX" / "VIDEOS" / "compress_video.py"
    )

    if dry_run:
        if compress_video_py.exists():
            result["cmd"] = (
                f"python {compress_video_py} compress "
                f"--input \"{input_path}\" "
                f"--output \"{output_path.with_suffix('')}\" "
                f"--profile cain"
            )
        else:
            result["cmd"] = (
            f"ffmpeg -y -i \"{input_path}\" "
            f"-c:v {CAIN_CODEC} -crf {CAIN_CRF} "
            f"-preset {CAIN_PRESET} "
            f"-g {CAIN_GOP} "
            f"-c:a aac -b:a {CAIN_AUDIO} "
            f"-pix_fmt yuv420p "
            f"-movflags +faststart "
            f"\"{output_path}\""
            )
        result["success"] = True
        return result

    start_time = time.time()

    if compress_video_py.exists():
        # Use compress_video.py with cain profile
        cmd = [
            sys.executable, str(compress_video_py), "compress",
            "--input", str(input_path),
            "--output", str(output_path.with_suffix("")),
            "--profile", "cain",
        ]
    else:
        # Direct ffmpeg with CAIN standard settings
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-c:v", CAIN_CODEC,
            "-crf", str(CAIN_CRF),
            "-preset", CAIN_PRESET,
            "-g", str(CAIN_GOP),
            "-c:a", "aac",
            "-b:a", CAIN_AUDIO,
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ]

    print(f"  Running: {' '.join(str(c) for c in cmd[:8])}...")
    process = subprocess.run(
        cmd, capture_output=True, text=True, timeout=3600,
        encoding="utf-8", errors="replace"  # 1h timeout per file
    )

    elapsed = round(time.time() - start_time, 1)
    result["duration_s"] = elapsed
    result["success"] = process.returncode == 0

    if process.returncode == 0 and output_path.exists():
        result["output_path"] = str(output_path)
        result["output_mb"] = round(output_path.stat().st_size / (1024 * 1024), 2)
        if info["size_mb"] > 0 and result["output_mb"]:
            result["savings_pct"] = round(
                (1 - result["output_mb"] / info["size_mb"]) * 100, 1
            )
        print(f"  [OK] Compressed in {elapsed}s: "
              f"{info['size_mb']} MB -> {result.get('output_mb', 0)} MB "
              f"(save {result.get('savings_pct', 0)}%)")
    else:
        print(f"  [FAIL] Error: {process.stderr[:200]}")

    return result


def scan_and_reencode(directory: str, output_dir: Optional[str] = None,
                       min_size_mb: float = DEFAULT_MIN_SIZE_MB,
                       dry_run: bool = False,
                       force: bool = False,
                       exclude_cain: bool = False,
                       report_only: bool = False) -> Dict:
    """Scan a directory and re-encode all large videos.

    Args:
        directory: Directory to scan
        output_dir: Output directory for re-encoded files
        min_size_mb: Minimum file size in MB to process
        dry_run: If True, only show what would be done
        force: If True, re-encode even already-compliant files
        exclude_cain: If True, skip files with 'cain' in the name
        report_only: If True, only analyze and report, don't encode

    Returns:
        Dict with summary statistics
    """
    print(f"  Scanning: {directory}")
    print(f"  Min size: {min_size_mb} MB")
    print(f"  Dry run:  {dry_run}")
    print(f"  Force:    {force}")
    print(f"  {"="*50}")

    videos = find_videos(directory, min_size_mb, exclude_cain=exclude_cain)

    if not videos:
        print("  No videos found matching criteria.")
        return {
            "scanned": directory,
            "found": 0,
            "skipped_compliant": 0,
            "re_encoded": 0,
            "errors": 0,
            "total_input_mb": 0,
            "total_estimated_mb": 0,
        }

    print(f"  Found {len(videos)} video(s) > {min_size_mb} MB")
    print()

    # Prepare output directory
    out_root = Path(output_dir) if output_dir else Path(directory) / DEFAULT_OUTPUT_DIR
    if not dry_run:
        out_root.mkdir(parents=True, exist_ok=True)

    total_input = 0.0
    total_estimated = 0.0
    skipped_compliant = 0
    re_encoded = 0
    errors = 0
    results = []

    for i, video in enumerate(videos, 1):
        info = get_video_info(video)
        estimated = estimate_output_size(info)
        total_input += info["size_mb"]
        total_estimated += estimated

        compliant, issues = check_cain_compliance(info)

        print(f"  [{i}/{len(videos)}] {video.name} "
              f"({info['size_mb']} MB, {info['width']}x{info['height']})")

        if compliant and not force:
            print(f"         [SKIP] Already CAIN-compliant")
            skipped_compliant += 1
            results.append({
                "file": video.name,
                "action": "skip",
                "reason": "compliant",
            })
            continue

        if issues:
            for issue in issues:
                print(f"         [ISSUE] {issue}")

        if report_only or dry_run:
            print(f"         Estimated after CAIN: {estimated} MB "
                  f"(save {round((1 - estimated/info['size_mb'])*100, 1)}%)")
            if dry_run:
                results.append({
                    "file": video.name,
                    "action": "would_encode",
                    "input_mb": info["size_mb"],
                    "estimated_mb": estimated,
                })
            else:
                results.append({
                    "file": video.name,
                    "action": "reported",
                    "input_mb": info["size_mb"],
                    "estimated_mb": estimated,
                })
            continue

        # Re-encode
        rel_path = video.relative_to(directory) if video.is_relative_to(directory) else video.name
        out_path = out_root / rel_path
        out_path = out_path.with_suffix(".mp4")

        r = reencode_video(video, out_path, dry_run=False, force=force)
        results.append(r)

        if r["success"]:
            re_encoded += 1
        else:
            errors += 1
            print(f"         [ERROR] Failed to re-encode")

        print()

    # Summary
    savings = round((1 - total_estimated / total_input) * 100, 1) if total_input > 0 else 0

    print(f"  {"="*50}")
    print(f"  SUMMARY")
    print(f"  {"="*50}")
    print(f"  Videos found:       {len(videos)}")
    print(f"  Already compliant:  {skipped_compliant}")
    print(f"  Re-encoded:         {re_encoded}")
    print(f"  Errors:             {errors}")
    print(f"  Total input:        {total_input:.0f} MB")
    print(f"  Estimated output:   {total_estimated:.0f} MB")
    print(f"  Potential savings:  {total_input - total_estimated:.0f} MB ({savings}%)")
    print(f"  Output directory:   {out_root if not dry_run else '(dry run)'}")
    print(f"  CAIN Standard:      {CAIN_FPS}fps, 1920x1080, H.264 {CAIN_PROFILE}, CRF {CAIN_CRF}, GOP {CAIN_GOP}")
    print(f"  {"="*50}")

    return {
        "scanned": directory,
        "found": len(videos),
        "skipped_compliant": skipped_compliant,
        "re_encoded": re_encoded,
        "errors": errors,
        "total_input_mb": round(total_input, 1),
        "total_estimated_mb": round(total_estimated, 1),
        "potential_savings_mb": round(total_input - total_estimated, 1),
        "savings_pct": savings,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Re-encode Large Videos — Batch Video Compression with CAIN Standard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python reencode_large_videos.py --scan G:/AZAZAEL
  python reencode_large_videos.py --scan G:/AZAZAEL --dry-run
  python reencode_large_videos.py --scan G:/AZAZAEL --min-size 100
  python reencode_large_videos.py --scan G:/AZAZAEL --force-reencode
  python reencode_large_videos.py --scan /c/Users/clint/Desktop --output ./compressed
  python reencode_large_videos.py --report-only
  python reencode_large_videos.py --scan G:/AZAZAEL --exclude-cain
        """,
    )

    parser.add_argument("--scan", default="G:/AZAZAEL",
                        help="Directory to scan for videos (default: G:/AZAZAEL)")
    parser.add_argument("--output", help="Output directory for compressed videos")
    parser.add_argument("--min-size", type=float, default=DEFAULT_MIN_SIZE_MB,
                        help=f"Minimum file size in MB (default: {DEFAULT_MIN_SIZE_MB})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only show what would be done, don't encode")
    parser.add_argument("--force-reencode", action="store_true",
                        help="Re-encode even already-compliant files")
    parser.add_argument("--exclude-cain", action="store_true",
                        help="Skip files with 'cain' in the filename")
    parser.add_argument("--report-only", action="store_true",
                        help="Only analyze and report, don't encode (no dry run)")

    args = parser.parse_args()

    # Check prerequisites
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found. Install ffmpeg to use this script.")
        sys.exit(1)

    if not shutil.which("ffprobe"):
        print("WARN: ffprobe not found — will use file-size estimates only")

    scan_and_reencode(
        directory=args.scan,
        output_dir=args.output,
        min_size_mb=args.min_size,
        dry_run=args.dry_run,
        force=args.force_reencode,
        exclude_cain=args.exclude_cain,
        report_only=args.report_only,
    )


if __name__ == "__main__":
    main()
