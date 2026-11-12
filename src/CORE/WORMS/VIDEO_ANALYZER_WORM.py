# coding: utf-8
"""
VIDEO_ANALYZER_WORM — Video Reverse Engineering & Standards Compliance Worm

Analyzes video files using ffprobe and reports:
- Resolution, framerate, codec, profile, B-frames, bitrate, file size
- Comparison against CAIN encoding standards (25fps, 1920x1080, H.264 High, CRF 23, GOP 250)
- Estimated file size savings with proper encoding
- Batch analysis mode for scanning directories

Derived from reverse engineering of 12+ CODEX videos (5.9 KB to 477 MB):
- Small videos (PIL+ffmpeg): H.264 High, 2 B-frames, CRF 23 -> micro-sized
- CAIN MP4 (puppeteer default): H.264 Constrained Baseline, 0 B-frames -> 477 MB
- Root cause: puppeteer-screen-recorder's Constrained Baseline forces every frame
  to be a keyframe — zero inter-frame compression
See LESSONS_MASTER.md #64-65 for full reverse engineering results.

Usage:
    from CORE.WORMS.VIDEO_ANALYZER_WORM import VideoAnalyzerWorm

    # Single video analysis
    result = VideoAnalyzerWorm.analyze_video("path/to/video.mp4")
    
    # Batch directory scan
    results = VideoAnalyzerWorm.scan_directory("path/to/videos/")
    
    # As a worm (via ClusterWORMZ)
    worm = VideoAnalyzerWorm()
    result = worm.execute()
"""

import argparse
import json
import os
import subprocess
import datetime
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# --- CAIN Video Standards ------------------------------------------

# Established from complete_remorse_cain.mp4 reverse engineering (June 2026)
# and cross-referenced against 12+ CODEX videos (5.9 KB to 477 MB)
CAIN_FPS = 25          # Standard framerate for all future video generation
CAIN_WIDTH = 1920      # Standard 1080p width
CAIN_HEIGHT = 1080     # Standard 1080p height
CAIN_CODEC = "h264"    # H.264 codec
CAIN_PROFILE = "High"  # High profile enables B-frames for inter-frame compression
CAIN_CRF = 23          # Constant Rate Factor (balance of quality vs size)
CAIN_GOP = 250         # Keyframe interval (~10s at 25fps)
CAIN_B_FRAMES = 2      # B-frames enable temporal compression
CAIN_BITRATE_KBPS = 4000  # Target bitrate for 1080p@25fps

# Size thresholds
LARGE_FILE_MB = 50     # Files above this are flagged for re-encoding
COMPRESSION_RATIO_OK = 0.5  # MB per minute minimum (below = inefficient)

# --- Worm Registration ---------------------------------------------

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("VIDEO_ANALYZER_WORM:L62", exc)
    def register_worm(
        category: str = "utility",
        priority: int = 5,
        cooldown_seconds: float = 30.0,
        description: str = "",
        tags=None,
    ):
        def decorator(cls):
            return cls
        return decorator


@register_worm(
    category="diagnostic",
    priority=5,
    cooldown_seconds=60.0,
    description="Video analyzer — runs ffprobe on target videos and reports encoding compliance against CAIN standards",
)
class VideoAnalyzerWorm:
    """Video Analysis Worm — reverse-engineer and validate video encoding."""

    VERSION = "1.0.0"

    # ffprobe availability cache (checked once, then cached)
    _ffprobe_checked = False
    _ffprobe_available = False

    @staticmethod
    def _check_ffprobe() -> bool:
        """Check if ffprobe is available on the system. Cached after first check."""
        if VideoAnalyzerWorm._ffprobe_checked:
            return VideoAnalyzerWorm._ffprobe_available
        available = subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
        ).returncode == 0
        VideoAnalyzerWorm._ffprobe_checked = True
        VideoAnalyzerWorm._ffprobe_available = available
        return available

    @staticmethod
    def probe_video(video_path: str) -> dict[str, Any]:
        """Run ffprobe on a single video file and extract all metadata.

        Args:
            video_path: Path to the video file

        Returns:
            dict with all probe results including:
            - file_size_mb, duration, width, height, fps, codec, profile
            - has_b_frames, bitrate_kbps, is_valid
        """
        info = {
            "path": str(video_path),
            "filename": Path(video_path).name,
            "file_size_mb": 0.0,
            "duration": 0.0,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "codec": "unknown",
            "codec_profile": "unknown",
            "has_b_frames": -1,
            "bitrate_kbps": 0,
            "is_valid": False,
            "error": None,
        }

        if not os.path.exists(video_path):
            info["error"] = "File not found"
            return info

        info["file_size_mb"] = round(os.path.getsize(video_path) / (1024 * 1024), 2)

        if not VideoAnalyzerWorm._check_ffprobe():
            info["error"] = "ffprobe not available"
            return info

        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(video_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if result.returncode != 0:
                info["error"] = "ffprobe failed"
                return info

            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            info["duration"] = round(float(fmt.get("duration", 0)), 2)
            info["bitrate_kbps"] = int(fmt.get("bit_rate", 0)) // 1000 if fmt.get("bit_rate") else 0

            for stream in data.get("streams", []):
                if stream["codec_type"] == "video":
                    info["width"] = stream.get("width", 0)
                    info["height"] = stream.get("height", 0)
                    fps_str = str(stream.get("r_frame_rate", "0/1"))
                    num, den = fps_str.split("/")
                    info["fps"] = round(int(num) / int(den), 2) if int(den) else 0
                    info["codec"] = stream.get("codec_name", "unknown")
                    info["codec_profile"] = stream.get("profile", "unknown")
                    info["has_b_frames"] = stream.get("has_b_frames", -1)
                    info["is_valid"] = True
                    break

            # Estimate bitrate from file size if ffprobe didn't report it
            if info["bitrate_kbps"] == 0 and info["duration"] > 0:
                info["bitrate_kbps"] = int(
                    (info["file_size_mb"] * 8 * 1024) / info["duration"]
                )

        except json.JSONDecodeError as e:
            note_failure("VIDEO_ANALYZER_WORM:L169", e)
            info["error"] = f"Invalid ffprobe output: {e}"
        except subprocess.TimeoutExpired as exc:
            note_failure("VIDEO_ANALYZER_WORM:L171", exc)
            info["error"] = "ffprobe timed out (30s)"
        except Exception as e:
            note_failure("VIDEO_ANALYZER_WORM:L173", e)
            info["error"] = str(e)

        return info

    @staticmethod
    def check_compliance(info: dict[str, Any]) -> dict[str, Any]:
        """Compare video metadata against CAIN encoding standards.

        Args:
            info: Video probe result from probe_video()

        Returns:
            dict with compliance flags and recommendations
        """
        checks = {
            "standard_fps": CAIN_FPS,
            "standard_width": CAIN_WIDTH,
            "standard_height": CAIN_HEIGHT,
            "standard_codec": CAIN_CODEC,
            "standard_profile": CAIN_PROFILE,
            "standard_b_frames": CAIN_B_FRAMES,
            "standard_bitrate_kbps": CAIN_BITRATE_KBPS,
            "actual_fps": info["fps"],
            "actual_width": info["width"],
            "actual_height": info["height"],
            "actual_codec": info["codec"],
            "actual_profile": info["codec_profile"],
            "actual_b_frames": info["has_b_frames"],
            "actual_bitrate_kbps": info["bitrate_kbps"],
            "fps_ok": False,
            "resolution_ok": False,
            "codec_ok": False,
            "profile_ok": False,
            "b_frames_ok": False,
            "overall_pass": False,
            "issues": [],
            "warnings": [],
        }

        # FPS check (tolerate minor rounding)
        fps_tolerance = 2.0
        checks["fps_ok"] = abs(info["fps"] - CAIN_FPS) <= fps_tolerance
        if not checks["fps_ok"]:
            checks["issues"].append(
                f"Non-standard framerate: {info['fps']}fps (standard: {CAIN_FPS}fps)"
            )

        # Resolution check
        checks["resolution_ok"] = info["width"] <= CAIN_WIDTH and info["height"] <= CAIN_HEIGHT
        if not checks["resolution_ok"]:
            checks["warnings"].append(
                f"Above 1080p: {info['width']}x{info['height']} "
                f"(standard: {CAIN_WIDTH}x{CAIN_HEIGHT})"
            )

        # Codec check
        checks["codec_ok"] = info["codec"] in ("h264", "libx264", "h265", "hevc")
        if not checks["codec_ok"]:
            checks["issues"].append(f"Non-standard codec: {info['codec']}")

        # Profile check — Constrained Baseline = no B-frames = huge file
        profile = (info.get("codec_profile") or "").lower()
        checks["profile_ok"] = "baseline" not in profile
        if not checks["profile_ok"]:
            checks["issues"].append(
                f"INefficient profile: {info['codec_profile']} "
                f"(no B-frames — every frame stored as keyframe)"
            )

        # B-frames check
        checks["b_frames_ok"] = info.get("has_b_frames", 0) > 0
        if not checks["b_frames_ok"]:
            checks["issues"].append(
                "Zero B-frames detected — no inter-frame compression"
            )

        # Size efficiency warning
        if info["duration"] > 0 and info["file_size_mb"] > 0:
            mb_per_min = info["file_size_mb"] / (info["duration"] / 60)
            if mb_per_min > 50:
                checks["warnings"].append(
                    f"Inefficient: {mb_per_min:.1f} MB/min "
                    f"(target: <25 MB/min with CAIN standard)"
                )

        checks["overall_pass"] = (
            checks["fps_ok"]
            and checks["codec_ok"]
            and checks["profile_ok"]
            and checks["b_frames_ok"]
        )

        return checks

    @staticmethod
    def estimate_savings(info: dict[str, Any]) -> dict[str, Any]:
        """Estimate potential file size savings with CAIN standard re-encoding.

        Args:
            info: Video probe result from probe_video()

        Returns:
            dict with current size, estimated size, savings percentage
        """
        result = {
            "current_mb": info["file_size_mb"],
            "estimated_high_mb": 0.0,
            "estimated_low_mb": 0.0,
            "savings_pct": 0.0,
        }

        if info["duration"] <= 0:
            return result

        duration_min = info["duration"] / 60

        # CRF 23 @ 1080p: ~12-18 MB/min for typical content
        # CRF 28 @ 1080p: ~6-10 MB/min
        result["estimated_low_mb"] = round(duration_min * 8, 1)   # CRF 28 aggressive
        result["estimated_high_mb"] = round(duration_min * 18, 1)  # CRF 23 high quality

        if info["file_size_mb"] > 0:
            result["savings_pct"] = round(
                (1 - result["estimated_high_mb"] / info["file_size_mb"]) * 100, 1
            )

        return result

    @staticmethod
    def analyze_video(video_path: str) -> dict[str, Any]:
        """Full analysis pipeline for a single video.

        Args:
            video_path: Path to the video file

        Returns:
            dict with probe, compliance, and savings data
        """
        info = VideoAnalyzerWorm.probe_video(video_path)
        if not info["is_valid"]:
            return {"probe": info, "compliance": None, "savings": None, "error": info.get("error")}

        compliance = VideoAnalyzerWorm.check_compliance(info)
        savings = VideoAnalyzerWorm.estimate_savings(info)

        return {
            "probe": info,
            "compliance": compliance,
            "savings": savings,
            "error": None,
        }

    @staticmethod
    def scan_directory(directory: str, recursive: bool = True,
                       min_size_mb: float = 0) -> list[dict[str, Any]]:
        """Scan a directory for video files and analyze each one.

        Args:
            directory: Path to directory to scan
            recursive: Whether to scan subdirectories
            min_size_mb: Minimum file size in MB (0 = include all)

        Returns:
            list of analysis results for each video found
        """
        video_exts = {".mp4", ".wmv", ".webm", ".avi", ".mov", ".mkv"}
        results = []

        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            return results

        pattern = "**/*" if recursive else "*"
        for f in sorted(dir_path.glob(pattern)):
            if f.is_file() and f.suffix.lower() in video_exts:
                size_mb = f.stat().st_size / (1024 * 1024)
                if size_mb >= min_size_mb:
                    analysis = VideoAnalyzerWorm.analyze_video(str(f))
                    results.append(analysis)

        return results

    @staticmethod
    def format_report(results: list[dict[str, Any]], 
                      verbose: bool = False) -> str:
        """Format analysis results as a human-readable report.

        Args:
            results: list of analysis results from analyze_video() or scan_directory()
            verbose: Whether to include detailed compliance info

        Returns:
            ASCII-formatted report string
        """
        lines = []
        lines.append("=" * 68)
        lines.append("  [ANALYZER] VIDEO ANALYSIS REPORT [ANALYZER]")
        lines.append("=" * 68)
        lines.append(f"  Date: {datetime.datetime.now().isoformat()}")
        lines.append(f"  Videos analyzed: {len(results)}")
        lines.append("")

        total_current = 0.0
        total_estimated = 0.0
        pass_count = 0
        fail_count = 0

        for i, r in enumerate(results, 1):
            probe = r.get("probe", {})
            compliance = r.get("compliance", {})
            savings = r.get("savings", {})

            if not probe.get("is_valid"):
                lines.append(f"  [{i}] {probe.get('filename', 'unknown')}")
                lines.append(f"       ERROR: {r.get('error', 'unknown')}")
                lines.append("")
                continue

            status = "PASS" if compliance and compliance.get("overall_pass") else "NEEDS WORK"
            if compliance and compliance.get("overall_pass"):
                pass_count += 1
            else:
                fail_count += 1

            lines.append(f"  [{i}] {probe['filename']}")
            lines.append(f"       Size: {probe['file_size_mb']} MB  |  "
                        f"Duration: {probe['duration']}s  |  "
                        f"Status: {status}")

            if verbose or not compliance.get("overall_pass", True):
                lines.append(f"       Res: {probe['width']}x{probe['height']}  |  "
                            f"FPS: {probe['fps']}  |  "
                            f"Codec: {probe['codec']}/{probe['codec_profile']}")
                lines.append(f"       B-frames: {probe['has_b_frames']}  |  "
                            f"Bitrate: {probe['bitrate_kbps']} kbps")

            if savings and savings["current_mb"] > 0:
                total_current += savings["current_mb"]
                total_estimated += savings["estimated_high_mb"]
                lines.append(f"       Current: {savings['current_mb']} MB -> "
                            f"Est. after CAIN: {savings['estimated_high_mb']} MB "
                            f"(save {savings['savings_pct']}%)")

            if compliance:
                for issue in compliance.get("issues", []):
                    lines.append(f"       [ISSUE] {issue}")
                for warning in compliance.get("warnings", []):
                    lines.append(f"       [WARN] {warning}")

            lines.append("")

        # Summary
        lines.append("-" * 68)
        lines.append(f"  Summary: {pass_count} pass, {fail_count} need work")
        if total_current > 0 and total_estimated > 0:
            saved = total_current - total_estimated
            pct = round((1 - total_estimated / total_current) * 100, 1) if total_current > 0 else 0
            lines.append(f"  Total current: {total_current:.1f} MB")
            lines.append(f"  Total estimated (CAIN standard): {total_estimated:.1f} MB")
            lines.append(f"  Potential space recovered: {saved:.1f} MB ({pct}%)")
        lines.append("=" * 68)
        lines.append("  Standards: 25fps | 1920x1080 | H.264 High | CRF 23 | GOP 250 | 2 B-frames")
        lines.append("  Use compress_video.py --profile cain to re-encode non-compliant files")
        lines.append("=" * 68)

        return "\n".join(lines)

    # --- Worm execute() interface ----------------------------------

    def execute(self, target: (str | None) = None,
                directory: (str | None) = None,
                min_size_mb: float = 1.0,
                verbose: bool = False) -> dict[str, Any]:
        """Execute worm analysis. Called by ClusterWORMZ.

        Args:
            target: Path to a specific video file (optional)
            directory: Directory to scan (optional)
            min_size_mb: Minimum file size in MB for directory scan
            verbose: Include detailed compliance info in report

        Returns:
            dict with success status, report, and analysis data
        """
        try:
            results: list[dict[str, Any]] = []
            if target:
                result = VideoAnalyzerWorm.analyze_video(target)
                results = [result]
            elif directory:
                results = VideoAnalyzerWorm.scan_directory(
                    directory, min_size_mb=min_size_mb
                )
            else:
                # Default: scan G: drive CODEX directory
                g_drive = Path("G:/AZAZAEL/AZAZAEL_ROOT/src/CODEX")
                if g_drive.exists():
                    results = VideoAnalyzerWorm.scan_directory(
                        str(g_drive), min_size_mb=min_size_mb
                    )
                else:
                    results = []

            report = VideoAnalyzerWorm.format_report(results, verbose=verbose)

            return {
                "success": True,
                "report": report,
                "videos_analyzed": len(results),
                "data": results,
            }
        except Exception as e:
            note_failure("VIDEO_ANALYZER_WORM:L485", e)
            return {"success": False, "error": str(e)}


# --- Module-level convenience functions ----------------------------

analyze_video = VideoAnalyzerWorm.analyze_video
scan_directory = VideoAnalyzerWorm.scan_directory
format_report = VideoAnalyzerWorm.format_report
check_compliance = VideoAnalyzerWorm.check_compliance
estimate_savings = VideoAnalyzerWorm.estimate_savings
probe_video = VideoAnalyzerWorm.probe_video


# --- CLI entry point -----------------------------------------------

def main():
    """CLI entry point for standalone video analysis."""
    parser = argparse.ArgumentParser(
    description="VIDEO_ANALYZER_WORM — Video Reverse Engineering & Standards Compliance",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  python VIDEO_ANALYZER_WORM.py --analyze video.mp4
  python VIDEO_ANALYZER_WORM.py --scan ./videos/
  python VIDEO_ANALYZER_WORM.py --scan G:/AZAZAEL --min-size 50
  python VIDEO_ANALYZER_WORM.py --scan ./videos/ --verbose
  python VIDEO_ANALYZER_WORM.py --check-ffprobe
        """,
    )

    parser.add_argument("--analyze", help="Analyze a single video file")
    parser.add_argument("--scan", help="Scan a directory for videos")
    parser.add_argument("--min-size", type=float, default=1.0,
                        help="Minimum file size in MB (default: 1.0)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed compliance info")
    parser.add_argument("--check-ffprobe", action="store_true",
                        help="Check if ffprobe is available")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")

    args = parser.parse_args()

    if args.check_ffprobe:
        available = VideoAnalyzerWorm._check_ffprobe()
        print(f"ffprobe available: {available}")
        return

    results = []

    if args.analyze:
        r = VideoAnalyzerWorm.analyze_video(args.analyze)
        results.append(r)
    elif args.scan:
        results = VideoAnalyzerWorm.scan_directory(
            args.scan, min_size_mb=args.min_size
        )
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(VideoAnalyzerWorm.format_report(results, verbose=args.verbose))


if __name__ == "__main__":
    main()
