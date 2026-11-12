#!/usr/bin/env python3
"""
Azazael Video Compressor - Optimized Video Encoding Pipeline

Compresses large PPM-generated videos (~600MB) down to ~16MB using:
- CRF 28+ (Constant Rate Factor) instead of CRF 20
- veryfast/ultrafast preset instead of medium
- Resolution-aware scaling for smaller file sizes
- .mp4 (H.264) and .wmv (Windows Media) dual-format output
- Two-pass encoding for optimal quality/size ratio

Usage:
    python compress_video.py --input video.mp4
    python compress_video.py --input video.mp4 --crf 28 --preset veryfast
    python compress_video.py --input video.mp4 --target-size 16 --formats mp4,wmv
    python compress_video.py --batch input_dir/          # compress all videos
    python compress_video.py --input video.mp4 --analyze  # analyze only, no transcode
"""

import os
import json
import shutil
import uuid
import argparse
import subprocess
from pathlib import Path
from typing import List
from pathlib import Path as _P

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# --- Configuration -----------------------------------------------------------

try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # never break the host
    note_failure("compress_video:core-paths-import", exc)
    _get_project_root_safe = None

#: Repo root for the env fallback below. MEASURED 2026-10-11: the fallback used to be
#: `Path.home() / "azazael"` -- a DIFFERENT, stale checkout from this repo -- so with
#: AZAZAEL_ROOT unset the default output dir was built under the WRONG tree.
#: `parents[3]` is this file's own tree (CODEX/VIDEOS/compress_video.py) and answers only
#: the case where CORE.paths is unimportable.
_DEFAULT_ROOT = (str(_get_project_root_safe()) if _get_project_root_safe is not None
                 else str(Path(__file__).resolve().parents[3]))

AZAZAEL_ROOT = os.environ.get(
    "AZAZAEL_ROOT",
    os.environ.get("Azazael_ROOT", _DEFAULT_ROOT)
)

DEFAULT_OUTPUT_DIR = Path(AZAZAEL_ROOT) / "output" / "videos" / "compressed"

# CAIN Standard — HEVC edition (2026-10-01, edge0 compact recipe as standard):
#   Framerate: 30 fps   (was 25; matches capture + edge0 scroll_video.js)
#   Resolution: 1920x1080 (standard 1080p)
#   Codec: HEVC / libx265, CRF 26 (was H.264 High CRF 23 @4Mbps, ~5x the bytes)
#   Keyframes: every 300 frames (~10s at 30fps, preserves prior cadence)
#   Loop-safe: +faststart, even dimensions enforced, no audio tail
#   Target: ~4-7 MB per 45s 1080p (measured edge0BAT: oxford_compact 3.9-6.7MB)
CAIN_FPS = 30
CAIN_CRF = 26
CAIN_CODEC = "libx265"
CAIN_PROFILE = "high"  # x264 only; omitted automatically for libx265 (no 'high')
CAIN_GOP = 300

# Encoding profiles
PROFILES = {
    "small":   {"crf": 32, "preset": "ultrafast", "scale": 0.5,  "audio": "64k"},
    "medium":  {"crf": 28, "preset": "veryfast",  "scale": 0.75, "audio": "96k"},
    "high":    {"crf": CAIN_CRF, "preset": "medium", "scale": 1.0,  "audio": "128k", "profile": CAIN_PROFILE, "gop": CAIN_GOP},
    "cain":    {"crf": CAIN_CRF, "preset": "medium", "scale": 1.0, "audio": "128k", "profile": CAIN_PROFILE, "gop": CAIN_GOP},
}

DEFAULT_PROFILE = "cain"  # CAIN standard: 30fps, 1920x1080, HEVC CRF 26, GOP 300

# --- Helpers -----------------------------------------------------------------

def _codec_available(codec_name):
    """Check if a specific ffmpeg encoder codec is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        return codec_name in result.stdout
    except Exception as exc:
        note_failure("compress_video:L69", exc)
        return False


def check_ffmpeg():
    """Verify ffmpeg is available."""
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found. Install: sudo apt install ffmpeg")
        return False
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    print(f"  ffmpeg: {result.stdout.split(chr(10))[0]}")
    return True


def probe_video(input_path):
    """Extract video metadata using ffprobe or fallback estimation."""
    info = {
        "path": str(input_path),
        "size_mb": round(os.path.getsize(input_path) / (1024 * 1024), 2),
        "duration": 0,
        "width": 0,
        "height": 0,
        "fps": 0,
        "codec": "unknown",
        "bitrate_kbps": 0,
    }

    if shutil.which("ffprobe"):
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(input_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if result.returncode == 0:
                data = json.loads(result.stdout)

                fmt = data.get("format", {})
                info["duration"] = float(fmt.get("duration", 0))
                info["bitrate_kbps"] = int(fmt.get("bit_rate", 0)) // 1000 if fmt.get("bit_rate") else 0

                for stream in data.get("streams", []):
                    if stream["codec_type"] == "video":
                        info["width"] = stream.get("width", 0)
                        info["height"] = stream.get("height", 0)
                        fps_str = stream.get("r_frame_rate", "0/1")
                        num, den = fps_str.split("/")
                        info["fps"] = round(int(num) / int(den), 2) if int(den) else 0
                        info["codec"] = stream.get("codec_name", "unknown")
                        break
        except Exception as e:
            print(f"  [WARN] ffprobe failed: {e}")

    # Estimate bitrate from file size if probe failed
    if info["duration"] > 0 and info["bitrate_kbps"] == 0:
        info["bitrate_kbps"] = int((info["size_mb"] * 8 * 1024) / info["duration"])

    return info


def calculate_target_bitrate(size_mb, duration_sec):
    """Calculate video bitrate needed to hit target file size."""
    # Reserve 10% for audio + container overhead
    target_total_kbits = (size_mb * 8 * 1024) * 0.9
    if duration_sec > 0:
        return int(target_total_kbits / duration_sec)
    return 2000  # fallback


def encode_gif(input_path, output_path, fps=15, width=960):
    """Two-pass palette GIF: small looping output for short clips.

    Missing functionality added 2026-10-01 (no gif path existed).
    Caps at 15fps / 960px wide; longer inputs should stay mp4.
    """
    output_path = _P(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_file = output_path.with_suffix(".gif")
    palette = str(output_path.parent / ".gif_palette.png")

    info = probe_video(input_path)
    print(f"\n  Input:  {info['path']} ({info['size_mb']} MB)")
    if info["duration"] > 30:
        print("  [WARN] GIF over 30s gets large; consider mp4 instead")

    vf = f"fps={fps},scale={width}:-1:flags=lanczos"
    cmd1 = ["ffmpeg", "-y", "-i", str(input_path), "-vf", f"{vf},palettegen",
            palette]
    r1 = subprocess.run(cmd1, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if r1.returncode != 0 or not Path(palette).exists():
        print(f"  [FAIL] palettegen: {r1.stderr[:200]}")
        return False
    cmd2 = ["ffmpeg", "-y", "-i", str(input_path), "-i", palette,
            "-lavfi", f"{vf} [x]; [x][1:v] paletteuse",
            "-loop", "0", str(out_file)]
    print(f"  GIF:    {fps}fps / {width}px / loop forever")
    r2 = subprocess.run(cmd2, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    try:
        Path(palette).unlink()
    except OSError as exc:
        note_failure("compress_video:L169", exc)
    if r2.returncode == 0 and out_file.exists():
        mb = round(os.path.getsize(out_file) / (1024 * 1024), 2)
        print(f"  [OK] {out_file.name} ({mb} MB, looping)")
        return True
    print(f"  [FAIL] paletteuse: {r2.stderr[:200]}")
    return False


def compress_video(input_path, output_path, profile=None,
                   crf=None, preset=None, scale=None, audio_bitrate=None,
                   target_size_mb=None, two_pass=False, fmt="mp4", codec=None):
    """Compress video with ffmpeg using optimized settings.

    Args:
        input_path: Source video file
        output_path: Destination (auto-suffixed with format)
        profile: Named profile ('small','medium','high') overriding individual params
        crf: Constant Rate Factor (0-51, higher = smaller file, 28 recommended)
        preset: ffmpeg preset (ultrafast, veryfast, fast, medium, slow)
        scale: Resolution scale factor (0.5 = half resolution)
        audio_bitrate: Audio bitrate (e.g., '96k')
        target_size_mb: Target output size in MB (overrides CRF)
        two_pass: Use two-pass encoding for better quality at target size
        fmt: Output format ('mp4', 'gif' or 'wmv'; wmv is legacy)
        codec: Override ('libx265' default per 2026-10-01 A/B; 'libx264'
            for high-motion content where x264 holds luma better at
            matched size — measured 2026-10-01: x264/23 14.8MB SSIM
            0.963 vs x265/26 14.4MB SSIM 0.942 on 720p scroll motion)
    """
    if fmt == "gif":
        return encode_gif(input_path, output_path)
    # Apply profile defaults
    if profile and profile in PROFILES:
        p = PROFILES[profile]
        crf = crf if crf is not None else p["crf"]
        preset = preset if preset is not None else p["preset"]
        scale = scale if scale is not None else p["scale"]
        audio_bitrate = audio_bitrate if audio_bitrate is not None else p["audio"]
    else:
        crf = crf or 28
        preset = preset or "veryfast"
        scale = scale or 0.75
        audio_bitrate = audio_bitrate or "96k"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Probe input
    info = probe_video(input_path)
    print(f"\n  Input:  {info['path']} ({info['size_mb']} MB)")
    print(f"  Size:   {info['width']}x{info['height']} @ {info['fps']}fps")
    print(f"  Length: {info['duration']:.1f}s")

    # Build ffmpeg filter chain
    filters = []
    new_width = int(info["width"] * scale) if info["width"] else 0
    new_height = int(info["height"] * scale) if info["height"] else 0

    if scale < 1.0 and new_width > 0:
        # Ensure even dimensions for codec compatibility
        new_width = new_width - (new_width % 2)
        new_height = new_height - (new_height % 2)
        filters.append(f"scale={new_width}:{new_height}")
        print(f"  Scale:  {new_width}x{new_height} ({scale*100:.0f}%)")

    # fps filter for consistency — CAIN standard is 30 (edge0 recipe)
    if info["fps"] > CAIN_FPS:
        filters.append(f"fps={CAIN_FPS}")
        print(f"  FPS:    {CAIN_FPS} (from {info['fps']})")

    # Format-specific settings
    if fmt == "mp4":
        codec = codec or CAIN_CODEC
        container = "mp4"
        pix_fmt = "yuv420p"
        profile_opt = (PROFILES[profile]["profile"] if profile and profile in PROFILES and "profile" in PROFILES[profile] else CAIN_PROFILE)
        gop_opt = (PROFILES[profile]["gop"] if profile and profile in PROFILES and "gop" in PROFILES[profile] else CAIN_GOP)
        # libx265 has no 'high' profile (x264-only); omit -profile:v for HEVC
        profile_args = [] if codec == "libx265" else ["-profile:v", profile_opt]
    elif fmt == "wmv":
        # Note: ffmpeg wmv2 encoder support varies by build.
        # Falls back to msmpeg4v2 or mpeg4 if wmv2 unavailable.
        if _codec_available("wmv2"):
            codec = "wmv2"
        elif _codec_available("msmpeg4v2"):
            codec = "msmpeg4v2"
        else:
            codec = "mpeg4"
        container = "asf"
        pix_fmt = "yuv420p"
    else:
        print(f"  Unknown format: {fmt}")
        return False

    out_file = output_path.with_suffix(f".{container}")

    # Two-pass encoding with target bitrate
    if target_size_mb and info["duration"] > 0:
        bitrate = calculate_target_bitrate(target_size_mb, info["duration"])
        print(f"  Target: {target_size_mb} MB -> bitrate {bitrate} kbps (two-pass)")

        # Unique passlog per file to avoid collision in batch mode
        passlog = str(output_path.parent / f".passlog_{uuid.uuid4().hex[:8]}")

        # Pass 1 (with CAIN standard profile + GOP)
        cmd1 = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", codec, "-b:v", f"{bitrate}k",
            *profile_args,
            "-preset", preset,
            "-g", str(gop_opt),
            "-pass", "1",
            "-passlogfile", passlog,
            "-f", container, "-an", "/dev/null"
        ]
        if filters:
            cmd1.insert(-12, "-vf")
            cmd1.insert(-12, ",".join(filters))

        print(f"\n  Pass 1: {' '.join(cmd1)[:120]}...")
        r1 = subprocess.run(cmd1, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)

        # Pass 2 (with CAIN standard profile + GOP)
        cmd2 = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", codec, "-b:v", f"{bitrate}k",
            *profile_args,
            "-preset", preset,
            "-g", str(gop_opt),
            "-pass", "2",
            "-passlogfile", passlog,
            "-c:a", "aac", "-b:a", audio_bitrate,
            "-pix_fmt", pix_fmt,
            str(out_file)
        ]
        if filters:
            cmd2.insert(-13, "-vf")
            cmd2.insert(-13, ",".join(filters))

        print(f"  Pass 2: {' '.join(cmd2)[:120]}...")
        r2 = subprocess.run(cmd2, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)

        # Cleanup pass logs (use passlog parent dir for reliable glob)
        passlog_path = Path(passlog)
        for logfile in passlog_path.parent.glob(f"{passlog_path.name}*.log*"):
            try:
                logfile.unlink()
            except OSError as exc:
                note_failure("compress_video:L318", exc)

        success = r2.returncode == 0

    else:
        # Single-pass CRF encoding (with CAIN standard profile + GOP)
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", codec, "-crf", str(crf),
            *profile_args,
            "-preset", preset,
            "-g", str(gop_opt),
            "-c:a", "aac", "-b:a", audio_bitrate,
            "-pix_fmt", pix_fmt,
            "-movflags", "+faststart",
            str(out_file)
        ]
        if filters:
            cmd.insert(-11, "-vf")
            cmd.insert(-11, ",".join(filters))

        if profile_opt != "high":
            print(f"  Profile: {profile_opt}  |  GOP: {gop_opt}")

        print(f"\n  CRF: {crf}  |  Preset: {preset}  |  Audio: {audio_bitrate}")
        print(f"  Command: {' '.join(cmd)[:150]}...")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        success = result.returncode == 0

    if success and out_file.exists():
        out_size = round(os.path.getsize(out_file) / (1024 * 1024), 2)
        reduction = round((1 - out_size / info["size_mb"]) * 100, 1) if info["size_mb"] > 0 else 0
        print(f"\n  [OK] Compressed: {out_file}")
        print(f"    Size: {out_size} MB (down {reduction}%)")
        return True
    else:
        print("\n  [FAIL] Compression failed")
        return False


# --- CLI ---------------------------------------------------------------------

def cmd_compress(args):
    """Compress a single video."""
    if not check_ffmpeg():
        return

    compress_video(
        input_path=args.input,
        output_path=args.output or f"{Path(args.input).stem}_compressed",
        profile=args.profile,
        crf=args.crf,
        preset=args.preset,
        scale=args.scale,
        audio_bitrate=args.audio,
        target_size_mb=args.target_size,
        two_pass=args.two_pass,
        fmt=args.format or "mp4",
        codec=args.codec,
    )


def cmd_batch(args):
    """Compress all videos in a directory."""
    if not check_ffmpeg():
        return

    input_dir = Path(args.input_dir)
    video_exts = {".mp4", ".wmv", ".webm", ".avi", ".mov", ".mkv"}
    videos = [f for f in input_dir.iterdir() if f.suffix.lower() in video_exts]

    if not videos:
        print(f"  No videos found in {input_dir}")
        return

    print(f"\n  Found {len(videos)} video(s) in {input_dir}")
    total_before = sum(os.path.getsize(v) / (1024 * 1024) for v in videos)
    print(f"  Total size: {total_before:.1f} MB")

    success = 0
    for i, video in enumerate(videos, 1):
        print(f"\n  [{i}/{len(videos)}] {video.name}")
        out = Path(args.output_dir or DEFAULT_OUTPUT_DIR) / video.stem
        if compress_video(
            input_path=str(video),
            output_path=str(out),
            profile=args.profile,
            crf=args.crf,
            preset=args.preset,
            scale=args.scale,
            target_size_mb=args.target_size,
            two_pass=args.two_pass,
            fmt=args.format or "mp4",
            codec=args.codec,
        ):
            success += 1

    # Summary
    out_dir = Path(args.output_dir or DEFAULT_OUTPUT_DIR)
    compressed = list(out_dir.glob(f"*.{args.format or 'mp4'}"))
    total_after = sum(os.path.getsize(c) / (1024 * 1024) for c in compressed)
    print(f"\n{'='*50}")
    print(f"  Batch complete: {success}/{len(videos)} compressed")
    print(f"  Before: {total_before:.1f} MB -> After: {total_after:.1f} MB")
    print(f"  Saved: {total_before - total_after:.1f} MB")


def cmd_analyze(args):
    """Analyze video without compressing."""
    info = probe_video(args.input)
    print(f"\n  Video Analysis: {Path(args.input).name}")
    print(f"  {'-'*40}")
    print(f"  File size:      {info['size_mb']} MB")
    print(f"  Duration:       {info['duration']:.1f}s")
    print(f"  Resolution:     {info['width']}x{info['height']}")
    print(f"  Frame rate:     {info['fps']} fps")
    print(f"  Codec:          {info['codec']}")
    print(f"  Bitrate:        {info['bitrate_kbps']} kbps")

    if info["duration"] > 0:
        # Predict compressed sizes
        print("\n  Predicted sizes (single-pass CRF):")
        for profile_name, p in PROFILES.items():
            pred_bitrate = calculate_target_bitrate(16, info["duration"]) if profile_name == "medium" else 0
            pred_size = (info["duration"] * (p.get("crf", 28) * 200)) / (8 * 1024)  # rough heuristic
            new_w = int(info["width"] * p["scale"]) if info["width"] else 0
            new_h = int(info["height"] * p["scale"]) if info["height"] else 0
            print(f"    {profile_name:<8} CRF={p['crf']} preset={p['preset']:<10} scale={new_w}x{new_h}")


def cmd_formats(args):
    """list available formats and profiles."""
    print("\n  Output Formats:")
    print("    mp4  - HEVC/AAC in MP4 container (universal, standard)")
    print("    gif  - looping GIF via two-pass palette (short clips)")
    print("    wmv  - Windows Media Video 9 in ASF container (legacy)")
    print("\n  Encoding Profiles:")
    for name, p in PROFILES.items():
        prof = p.get('profile', '-')
        gop = p.get('gop', '-')
        print(f"    {name:<8} CRF={p['crf']}  preset={p['preset']:<10}  scale={p['scale']}  audio={p['audio']}  profile={prof}  gop={gop}")
    print("\n  Notes:")
    print("    - CRF 0-51 (higher = smaller file, 28 recommended)")
    print("    - CRF 28 on a 600MB PPM video -> ~16MB output")
    print("    - Two-pass encoding gives ~15% better quality at same bitrate")
    print("    - Resolution scaling has biggest impact on file size")


def main():
    parser = argparse.ArgumentParser(
    description="Azazael Video Compressor - Optimize video file sizes",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  compress_video.py --input matrix_cascade.mp4
  compress_video.py --input video.mp4 --profile small
  compress_video.py --input video.mp4 --target-size 16 --two-pass
  compress_video.py --input video.mp4 --crf 28 --preset veryfast --scale 0.75
  compress_video.py --input video.mp4 --format wmv
  compress_video.py --batch ./videos/ --profile medium
  compress_video.py --input video.mp4 --analyze
  compress_video.py --formats
        """,
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # Compress
    p = sub.add_parser("compress", help="Compress a single video")
    p.add_argument("--input", required=True, help="Input video file")
    p.add_argument("--output", help="Output path prefix (no extension)")
    p.add_argument("--profile", choices=list(PROFILES.keys()), help="Encoding profile")
    p.add_argument("--crf", type=int, help="Constant Rate Factor (0-51)")
    p.add_argument("--preset", help="ffmpeg preset")
    p.add_argument("--scale", type=float, help="Resolution scale factor (0.1-1.0)")
    p.add_argument("--audio", help="Audio bitrate (e.g., 96k)")
    p.add_argument("--target-size", type=float, help="Target output size in MB")
    p.add_argument("--two-pass", action="store_true", help="Use two-pass encoding")
    p.add_argument("--format", choices=["mp4", "gif", "wmv"], help="Output format")
    p.add_argument("--codec", choices=["libx265", "libx264"],
                   help="Video codec override (default libx265; libx264 for high-motion luma)")

    # Batch
    p = sub.add_parser("batch", help="Compress all videos in directory")
    p.add_argument("--input-dir", required=True, help="Directory containing videos")
    p.add_argument("--output-dir", help="Output directory")
    p.add_argument("--profile", choices=list(PROFILES.keys()), help="Encoding profile")
    p.add_argument("--crf", type=int, help="Constant Rate Factor")
    p.add_argument("--preset", help="ffmpeg preset")
    p.add_argument("--scale", type=float, help="Resolution scale factor")
    p.add_argument("--target-size", type=float, help="Target output size in MB")
    p.add_argument("--two-pass", action="store_true", help="Use two-pass encoding")
    p.add_argument("--format", choices=["mp4", "gif", "wmv"], help="Output format")
    p.add_argument("--codec", choices=["libx265", "libx264"],
                   help="Video codec override (default libx265; libx264 for high-motion luma)")

    # Analyze
    p = sub.add_parser("analyze", help="Analyze video without compressing")
    p.add_argument("--input", required=True, help="Input video file")

    # Formats
    sub.add_parser("formats", help="Show available formats and profiles")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "compress": cmd_compress,
        "batch": cmd_batch,
        "analyze": cmd_analyze,
        "formats": cmd_formats,
    }

    func = commands.get(args.command)
    if func:
        func(args)


if __name__ == "__main__":
    main()
