#!/usr/bin/env python3
"""
CAIN Video Pipeline Demo -- End-to-End Video Generation with CAIN Standard

Demonstrates the complete pipeline:
  HTML Template -> Puppeteer Screen Recording (CAIN config) -> compress_video -> Analyze

This demo uses the existing `output_cain.html` template (CAIN Accountability System
framework visualization) and runs it through the CAIN-optimized pipeline.

Pipeline Steps:
  1. TEMPLATE:   output_cain.html -- rich animated HTML with emotion web + seven deadly sins
  2. RECORD:     puppeteer-screen-recorder with CAIN config (High profile, CRF 23, medium preset)
  3. COMPRESS:   compress_video.py --profile cain (H.264 High, CRF 23, GOP 250)
  4. ANALYZE:    VIDEO_ANALYZER_WORM.py -- compare output against CAIN standards
  5. REPORT:     size comparison, compliance check, estimated savings

Usage:
    python cain_video_pipeline_demo.py                 # Run the full pipeline
    python cain_video_pipeline_demo.py --step record   # Run a specific step
    python cain_video_pipeline_demo.py --dry-run       # Show what would be done

CAIN Standard (from reverse engineering 12+ CODEX videos, June 2026):
    Framerate:  25 fps
    Resolution: 1920x1080 (or original)
    Codec:      H.264 High profile
    CRF:        23 (balance of quality vs file size)
    GOP:        250 frames (~10s keyframe interval)
    B-frames:   2 (enables inter-frame compression)
    Preset:     medium (better compression than ultrafast)

See LESSONS_MASTER.md #64-67 for full analysis.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

# --- Paths ---------------------------------------------------------

AZAZAEL_ROOT = Path(__file__).resolve().parent.parent
CODEX_DIR = AZAZAEL_ROOT / "src" / "CODEX"
TEMPLATES_DIR = CODEX_DIR / "SESSION_TEMPLATE" / "templates"
RECORDERS_DIR = CODEX_DIR / "SESSION_TEMPLATE" / "recorders"
VIDEOS_DIR = CODEX_DIR / "SESSION_TEMPLATE" / "videos"
OUTPUT_DIR = AZAZAEL_ROOT / "output" / "videos" / "demos"

# --- Demo configuration --------------------------------------------

DEMO_CONFIG = {
    "name": "cain_accountability_demo",
    "html_template": OUTPUT_DIR / "templates" / "cain_demo.html",
    "recorder_script": OUTPUT_DIR / "recorders" / "record_cain_demo.js",
    "raw_video": OUTPUT_DIR / "videos" / "cain_demo_raw.mp4",
    "compressed_video": OUTPUT_DIR / "videos" / "cain_demo_compressed.mp4",
    "report_file": OUTPUT_DIR / "cain_pipeline_report.txt",
}

# Available HTML templates to choose from
AVAILABLE_TEMPLATES = {
    "cain": "output_cain.html",
    "ultimate": "consequences_ultimate.html",
    "failure": "failure_report.html",
    "session": "session_report.html",
    "neon": "output_neon.html",
    "cosmic": "output_cosmic.html",
    "glass": "output_glass.html",
    "mystical": "output_mystical.html",
    "grimoire": "output_grimoire.html",
    "ethereal": "output_ethereal.html",
}

# CAIN Standard encoding settings
CAIN_FPS = 25
CAIN_CRF = 23
CAIN_PROFILE = "high"
CAIN_GOP = 250
CAIN_PRESET = "medium"
CAIN_AUDIO = "128k"
CAIN_VIEWPORT_WIDTH = 1280
CAIN_VIEWPORT_HEIGHT = 720

# --- Step 1: Prepare HTML Template ---------------------------------

STEP1_DOC = """
STEP 1: HTML TEMPLATE
=====================
Purpose: Create or select a visually rich HTML page to record.

The CAIN Accountability System HTML template (output_cain.html) contains:
  - 7 Deadly Sins & Mythological Archetypes Mapping
  - Multi-Modal Psychoanalysis (Freudian, Jungian, CBT, etc.)
  - Complete Emotion Accountability Web (interconnected causal chains)
  - Financial Ledger & Money Snake ($183 total damage per incident)
  - Stakeholder Impact Matrix (12-party responsibility analysis)
  - Obfuscation Analysis (hidden information layer detection)
  - Archetype Analysis (Hero, Wizard, Queen, Innocent, etc.)

The template uses CSS animations, gradient backgrounds, keyframe animations,
and interactive hover effects that make for compelling video content.

Available templates:
"""


def step1_prepare_template(template_name: str = "cain",
                           dry_run: bool = False) -> Optional[Path]:
    """Select and prepare the HTML template for recording.

    Args:
        template_name: Key from AVAILABLE_TEMPLATES
        dry_run: If True, only print what would be done

    Returns:
        Path to the prepared HTML file, or None if dry run
    """
    html_file = TEMPLATES_DIR / AVAILABLE_TEMPLATES.get(template_name, "output_cain.html")

    if not html_file.exists():
        print(f"  [FAIL] Template not found: {html_file}")
        print(f"  Available: {', '.join(AVAILABLE_TEMPLATES.keys())}")
        return None

    size_kb = html_file.stat().st_size // 1024
    print(f"\n  {'='*60}")
    print(f"  STEP 1: HTML Template Selection")
    print(f"  {'='*60}")
    print(f"  Template: {html_file.name}")
    print(f"  Size:     {size_kb} KB")
    print(f"  Content:  CAIN Accountability System -- Emotion Web + Sins Analysis")

    if dry_run:
        print(f"  [DRY-RUN] Would use: {html_file}")
        return None

    # Ensure output directories exist
    (OUTPUT_DIR / "templates").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "recorders").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "videos").mkdir(parents=True, exist_ok=True)

    # Copy template to demo output
    dest = OUTPUT_DIR / "templates" / html_file.name
    shutil.copy2(str(html_file), str(dest))
    print(f"  [OK] Template ready: {dest}")
    print(f"  Preview: Open in browser or proceed to Step 2 (Puppeteer recording)")
    print()

    return dest


# --- Step 2: Generate Puppeteer Recorder Script --------------------

STEP2_DOC = """
STEP 2: PUPPETEER RECORDER (CAIN Standard Config)
====================================================
Purpose: Record HTML scrolling as MP4 using puppeteer-screen-recorder.

The recorder uses CAIN Standard encoding options:
  - videoCrf: 23         (Constant Rate Factor -- balance of quality vs size)
  - videoPreset: 'medium' (Better compression than 'ultrafast' default)
  - videoPixelFormat: 'yuv420p' (Standard 4:2:0 chroma subsampling)
  - videoBitrate: 4000   (4 Mbps target for 1080p@25fps)

These options override puppeteer-screen-recorder's defaults which produce
H.264 Constrained Baseline (0 B-frames = every frame is a keyframe).
See LESSONS_MASTER.md #64-66 for the full analysis.

The recorder script will:
  1. Launch headless Chromium via puppeteer
  2. Set viewport to 1280x720 (standard)
  3. Open the HTML file
  4. Start recording with CAIN config
  5. Auto-scroll through the full document
  6. Stop recording and save MP4

Requirements: Node.js + puppeteer + puppeteer-screen-recorder (npm installed)
"""


def step2_generate_recorder(html_path: Path, raw_output: Path,
                            dry_run: bool = False) -> Optional[str]:
    """Generate a puppeteer recorder script with CAIN standard config.

    Args:
        html_path: Path to the HTML file to record
        raw_output: Path for the raw MP4 output
        dry_run: If True, only print what would be done

    Returns:
        Path to the generated recorder script, or None if dry run
    """
    print(f"\n  {'='*60}")
    print(f"  STEP 2: Puppeteer Recorder Script (CAIN Config)")
    print(f"  {'='*60}")
    print(f"  CAIN Standard:")
    print(f"    videoCrf:        {CAIN_CRF}")
    print(f"    videoPreset:     '{CAIN_PRESET}'")
    print(f"    videoPixelFormat: 'yuv420p'")
    print(f"    videoBitrate:    {4000} kbps")
    print(f"  Effect:           H.264 High profile + B-frames enabled")
    print(f"  Issue avoided:    Constrained Baseline (477 MB for 7 min)")
    print()

    if dry_run:
        print(f"  [DRY-RUN] Would generate recorder script at:")
        print(f"    {DEMO_CONFIG['recorder_script']}")
        print(f"    With CAIN options shown above")
        return None

    script_content = f"""const puppeteer = require('puppeteer');
const {{ PuppeteerScreenRecorder }} = require('puppeteer-screen-recorder');

/**
 * CAIN Video Pipeline Demo -- Puppeteer Recorder
 *
 * See LESSONS_MASTER.md #01-#02 for encoding rationale, #04 for scroll polling pattern
 *
 * Uses CAIN Standard encoding options to produce H.264 High profile
 * with B-frames, avoiding the Constrained Baseline default that caused
 * the 477 MB CAIN MP4 (7:08 @ 2074x1418, 0 B-frames, every frame keyframe).
 *
 * CAIN Standard:
 *   - videoCrf:        {CAIN_CRF}
 *   - videoPreset:     '{CAIN_PRESET}'
 *   - videoPixelFormat: 'yuv420p'
 *   - videoBitrate:    {4000} kbps
 *
 * See LESSONS_MASTER.md #64-66 for full reverse engineering analysis.
 */

(async () => {{
  const browser = await puppeteer.launch();
  const page = await browser.newPage();

  // Set viewport to standard resolution
  await page.setViewport({{ width: {CAIN_VIEWPORT_WIDTH}, height: {CAIN_VIEWPORT_HEIGHT} }});

  // CAIN Standard: H.264 High profile, CRF 23, medium preset, 4 Mbps
  // These options override puppeteer-screen-recorder's defaults which
  // produce Constrained Baseline with 0 B-frames.
  const recorder = new PuppeteerScreenRecorder(page, {{
    videoCrf: {CAIN_CRF},
    videoPreset: '{CAIN_PRESET}',
    videoPixelFormat: 'yuv420p',
    videoBitrate: 4000,
  }});

  // Load the HTML template
  await page.goto('file:///{html_path.as_posix()}');
  await recorder.start('{raw_output.as_posix()}');

  // Auto-scroll through the document
  let scrollComplete = false;

  await page.evaluate(() => {{
    const scrollStep = 30;
    const scrollDelay = 600;

    const scrollInterval = setInterval(() => {{
      if (window.scrollY + window.innerHeight >= document.body.scrollHeight - 50) {{
        scrollComplete = true;
        clearInterval(scrollInterval);
        return;
      }}
      window.scrollBy(0, scrollStep);
    }}, scrollDelay);
  }});

  // Wait for scroll to complete
  while (!scrollComplete) {{
    await new Promise(r => setTimeout(r, 500));
    const isComplete = await page.evaluate(() => {{
      return window.scrollY + window.innerHeight >= document.body.scrollHeight - 50;
    }});
    if (isComplete) break;
  }}

  // Hold at end for 2 seconds
  await new Promise(r => setTimeout(r, 2000));

  await recorder.stop();
  await browser.close();

  console.log('CAIN demo video saved: {raw_output.name}');
}})();
"""

    # Write the recorder script
    script_path = DEMO_CONFIG["recorder_script"]
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_content, encoding="utf-8")

    print(f"  [OK] Recorder script generated: {script_path}")
    print(f"  Run it with: node \"{script_path}\"")
    print(f"  Output:      {raw_output}")
    print()

    return str(script_path)


# --- Step 3: Compress with CAIN Standard ---------------------------

STEP3_DOC = """
STEP 3: COMPRESS VIDEO (CAIN Standard Profile)
=================================================
Purpose: Compress the raw MP4 using CAIN standard settings.

Uses compress_video.py's 'cain' profile which applies:
  - Codec:      H.264 High profile (enables B-frames)
  - CRF:        23 (Constant Rate Factor)
  - Preset:     medium
  - GOP:        250 frames (~10s keyframe interval)
  - Audio:      AAC 128kbps

The 'cain' profile was derived from reverse engineering the 477 MB CAIN MP4.
Same content at CAIN standard would be ~80-120 MB (vs 477 MB).
"""


def step3_compress_video(raw_video: Path, compressed_output: Path,
                          dry_run: bool = False) -> bool:
    """Compress raw video using the CAIN standard profile.

    Args:
        raw_video: Path to the raw recorded MP4
        compressed_output: Path for the compressed output
        dry_run: If True, only print what would be done

    Returns:
        True if compression succeeded or dry run
    """
    print(f"\n  {'='*60}")
    print(f"  STEP 3: Compress Video (CAIN Profile)")
    print(f"  {'='*60}")

    if dry_run:
        print(f"  [DRY-RUN] Would run: python compress_video.py compress "
              f"--profile cain on raw video")
        return True

    if not raw_video.exists():
        print(f"  [SKIP] Raw video not found: {raw_video}")
        print(f"  Run step 2 first to record the video.")
        return False

    raw_size_mb = raw_video.stat().st_size / (1024 * 1024)
    print(f"  Input:   {raw_video.name} ({raw_size_mb:.1f} MB)")
    print(f"  Output:  {compressed_output.name}")
    print(f"  Profile: cain (CRF {CAIN_CRF}, preset {CAIN_PRESET}, "
          f"profile={CAIN_PROFILE}, GOP={CAIN_GOP})")
    print()

    if dry_run:
        print(f"  [DRY-RUN] Would run:")
        print(f"    python compress_video.py compress \\")
        print(f"      --input \"{raw_video}\" \\")
        print(f"      --output \"{compressed_output.with_suffix('')}\" \\")
        print(f"      --profile cain")
        return True

    # Try compress_video.py first
    compress_script = AZAZAEL_ROOT / "src" / "CODEX" / "VIDEOS" / "compress_video.py"
    if compress_script.exists():
        cmd = [
            sys.executable, str(compress_script), "compress",
            "--input", str(raw_video),
            "--output", str(compressed_output.with_suffix("")),
            "--profile", "cain",
        ]
        print(f"  Running: python compress_video.py compress --profile cain ...")
        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
        elapsed = time.time() - start

        if result.returncode == 0 and compressed_output.exists():
            compressed_mb = compressed_output.stat().st_size / (1024 * 1024)
            savings = (1 - compressed_mb / raw_size_mb) * 100
            print(f"  [OK] Compressed in {elapsed:.1f}s")
            print(f"    Size: {raw_size_mb:.1f} MB -> {compressed_mb:.1f} MB "
                  f"(save {savings:.1f}%)")
            return True
        else:
            print(f"  [FAIL] Compression error: {result.stderr[:200]}")
            return False
    else:
        # Direct ffmpeg fallback with CAIN standard
        compressed_output.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_video),
            "-c:v", "libx264",
            "-crf", str(CAIN_CRF),
            "-profile:v", CAIN_PROFILE,
            "-preset", CAIN_PRESET,
            "-g", str(CAIN_GOP),
            "-c:a", "aac",
            "-b:a", CAIN_AUDIO,
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(compressed_output),
        ]
        print(f"  Running: ffmpeg -i input.mp4 -c:v libx264 -crf {CAIN_CRF} ...")
        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
        elapsed = time.time() - start

        if result.returncode == 0 and compressed_output.exists():
            compressed_mb = compressed_output.stat().st_size / (1024 * 1024)
            savings = (1 - compressed_mb / raw_size_mb) * 100
            print(f"  [OK] Compressed in {elapsed:.1f}s")
            print(f"    Size: {raw_size_mb:.1f} MB -> {compressed_mb:.1f} MB "
                  f"(save {savings:.1f}%)")
            return True
        else:
            print(f"  [FAIL] ffmpeg error: {result.stderr[:200]}")
            return False


# --- Step 4: Analyze with VIDEO_ANALYZER_WORM ----------------------

STEP4_DOC = """
STEP 4: ANALYZE RESULTS (VIDEO_ANALYZER_WORM)
=================================================
Purpose: Verify the output video meets CAIN standards using the
VIDEO_ANALYZER_WORM (CORE/WORMS/VIDEO_ANALYZER_WORM.py).

The analyzer checks:
  - Framerate:    25 fps (tolerance: ?2)
  - Resolution:   ? 1920x1080
  - Codec:        H.264 or H.265
  - Profile:      Not Constrained Baseline
  - B-frames:     > 0 (inter-frame compression enabled)
  - File size:    MB/min efficiency

The analyzer also estimates file size savings vs. the CAIN standard.
"""


def step4_analyze_video(video_path: Path, raw_video: Optional[Path] = None,
                         dry_run: bool = False) -> bool:
    """Analyze video with VIDEO_ANALYZER_WORM.

    Args:
        video_path: Path to the video to analyze
        raw_video: Optional path to the raw version for comparison
        dry_run: If True, only print what would be done

    Returns:
        True if analysis succeeded or dry run
    """
    print(f"\n  {'='*60}")
    print(f"  STEP 4: Analyze Results (VIDEO_ANALYZER_WORM)")
    print(f"  {'='*60}")

    if dry_run:
        print(f"  [DRY-RUN] Would run: python VIDEO_ANALYZER_WORM.py "
              f"--analyze on compressed video")
        if raw_video:
            print(f"  [DRY-RUN] Would also analyze raw video for comparison")
        return True

    if not video_path.exists():
        print(f"  [SKIP] Video not found: {video_path}")
        return False

    analyzer_script = AZAZAEL_ROOT / "src" / "CORE" / "WORMS" / "VIDEO_ANALYZER_WORM.py"
    if not analyzer_script.exists():
        print(f"  [SKIP] VIDEO_ANALYZER_WORM not found: {analyzer_script}")
        return False

    if dry_run:
        print(f"  [DRY-RUN] Would run:")
        print(f"    python VIDEO_ANALYZER_WORM.py --analyze \"{video_path}\"")
        if raw_video:
            print(f"    python VIDEO_ANALYZER_WORM.py --analyze \"{raw_video}\"")
        return True

    # Analyze the compressed video
    print(f"  Analyzing: {video_path.name} ({video_path.stat().st_size / (1024*1024):.1f} MB)")
    print()
    cmd_analyze = [sys.executable, str(analyzer_script), "--analyze", str(video_path)]
    result = subprocess.run(cmd_analyze, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")

    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"  [FAIL] Analysis error: {result.stderr[:200]}")

    # If raw video exists, compare before/after
    if raw_video and raw_video.exists():
        print(f"  {'='*60}")
        print(f"  BEFORE/AFTER COMPARISON")
        print(f"  {'='*60}")
        raw_mb = raw_video.stat().st_size / (1024 * 1024)
        compressed_mb = video_path.stat().st_size / (1024 * 1024)
        savings = (1 - compressed_mb / raw_mb) * 100 if raw_mb > 0 else 0
        print(f"  Raw:        {raw_mb:.1f} MB")
        print(f"  Compressed: {compressed_mb:.1f} MB")
        print(f"  Savings:    {savings:.1f}%")
        print()

        # Run compliance on raw video for comparison
        print(f"  Raw video compliance:")
        cmd_raw = [sys.executable, str(analyzer_script), "--analyze", str(raw_video)]
        raw_result = subprocess.run(cmd_raw, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if raw_result.returncode == 0:
            # Extract just the compliance lines
            for line in raw_result.stdout.split("\n"):
                if "ISSUE" in line or "FAIL" in line or "PASS" in line or "WARN" in line:
                    print(f"    {line.strip()}")

    return True


# --- Step 5: Generate Report ---------------------------------------

def step5_generate_report(html_path: Path, raw_video: Path,
                           compressed_video: Path,
                           results: Dict) -> str:
    """Generate a comprehensive pipeline report.

    Args:
        html_path: Path to the HTML template used
        raw_video: Path to the raw recorded video
        compressed_video: Path to the compressed video
        results: Dict of results from each step

    Returns:
        Report text
    """
    lines = []
    lines.append("=" * 68)
    lines.append("  [CAIN] CAIN VIDEO PIPELINE DEMO REPORT [CAIN]")
    lines.append("=" * 68)
    lines.append(f"  Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("  Pipeline: HTML -> Puppeteer Recording -> compress_video -> Analyze")
    lines.append("  Standard: CAIN (25fps | 1080p | H.264 High | CRF 23 | GOP 250)")
    lines.append("")

    lines.append("-" * 68)
    lines.append("  1. HTML TEMPLATE")
    lines.append("-" * 68)
    if html_path and html_path.exists():
        lines.append(f"     File: {html_path.name}")
        lines.append(f"     Size: {html_path.stat().st_size // 1024} KB")
    else:
        lines.append("     [NOT RUN]")
    lines.append("")

    lines.append("-" * 68)
    lines.append("  2. PUPPETEER RECORDING (CAIN Config)")
    lines.append("-" * 68)
    lines.append(f"     Config: videoCrf={CAIN_CRF}, videoPreset={CAIN_PRESET}")
    lines.append(f"             videoPixelFormat=yuv420p, videoBitrate=4000")
    if raw_video and raw_video.exists():
        raw_mb = raw_video.stat().st_size / (1024 * 1024)
        lines.append(f"     Output: {raw_video.name} ({raw_mb:.1f} MB)")
    else:
        lines.append(f"     Output: [NOT RECORDED]")
    lines.append("")

    lines.append("-" * 68)
    lines.append("  3. COMPRESS VIDEO (CAIN Profile)")
    lines.append("-" * 68)
    lines.append(f"     Profile: cain (CRF {CAIN_CRF}, preset {CAIN_PRESET})")
    if compressed_video and compressed_video.exists():
        comp_mb = compressed_video.stat().st_size / (1024 * 1024)
        lines.append(f"     Output: {compressed_video.name} ({comp_mb:.1f} MB)")
        if raw_video and raw_video.exists():
            raw_mb = raw_video.stat().st_size / (1024 * 1024)
            pct = (1 - comp_mb / raw_mb) * 100 if raw_mb > 0 else 0
            lines.append(f"     Savings: {raw_mb:.1f} MB -> {comp_mb:.1f} MB ({pct:.1f}%)")
    else:
        lines.append(f"     Output: [NOT COMPRESSED]")
    lines.append("")

    lines.append("-" * 68)
    lines.append("  4. COMPLIANCE CHECK")
    lines.append("-" * 68)
    lines.append("     Run VIDEO_ANALYZER_WORM for full compliance report:")
    lines.append(f"       python src/CORE/WORMS/VIDEO_ANALYZER_WORM.py --analyze \"{compressed_video}\"")
    lines.append("")

    lines.append("-" * 68)
    lines.append("  5. QUICK REFERENCE")
    lines.append("-" * 68)
    lines.append("     recorder scripts at:")
    lines.append("       src/CODEX/SESSION_TEMPLATE/recorders/")
    lines.append("     compress_video.py:")
    lines.append("       src/CODEX/VIDEOS/compress_video.py --profile cain")
    lines.append("     analyze with:")
    lines.append("       python src/CORE/WORMS/VIDEO_ANALYZER_WORM.py --analyze <file>")
    lines.append("     batch re-encode:")
    lines.append("       python scripts/reencode_large_videos.py --scan <dir> --dry-run")
    lines.append("")
    lines.append("=" * 68)
    lines.append("  CAIN Standard established June 2026")
    lines.append("  See LESSONS_MASTER.md #64-67 for reverse engineering details")
    lines.append("=" * 68)

    return "\n".join(lines)


# --- Full Pipeline -------------------------------------------------

def run_full_pipeline(template_name: str = "cain",
                      dry_run: bool = False,
                      skip_record: bool = False,
                      output: Optional[str] = None) -> bool:
    """Run the complete CAIN video pipeline demo.

    Args:
        template_name: Key from AVAILABLE_TEMPLATES
        dry_run: If True, only print what would be done
        skip_record: Skip the puppeteer recording step (assumes raw video exists)
        output: Custom output directory

    Returns:
        True if pipeline completed successfully
    """
    print()
    print("=" * 68)
    print("  CAIN VIDEO PIPELINE DEMO")
    print("  End-to-End: HTML -> Puppeteer -> Compress -> Analyze")
    print("=" * 68)
    print(f"  Template: {template_name}")
    print(f"  Dry run:  {dry_run}")
    print(f"  CAIN Standard:")
    print(f"    FPS:       {CAIN_FPS}")
    print(f"    CRF:       {CAIN_CRF}")
    print(f"    Profile:   {CAIN_PROFILE}")
    print(f"    GOP:       {CAIN_GOP}")
    print(f"    Preset:    {CAIN_PRESET}")
    print(f"  {'='*60}")
    print()

    results = {}

    # Step 1: Template
    html_file = step1_prepare_template(template_name, dry_run)
    if not html_file and not dry_run:
        print("  Pipeline failed at Step 1")
        return False

    # Step 2: Recorder
    raw_path = DEMO_CONFIG["raw_video"]
    script_path = DEMO_CONFIG["recorder_script"]
    if not skip_record:
        recorder = step2_generate_recorder(
            html_file or TEMPLATES_DIR / AVAILABLE_TEMPLATES[template_name],
            raw_path, dry_run
        )
        if not recorder and not dry_run:
            print("  Pipeline failed at Step 2")
            return False
    else:
        print(f"  [SKIP] Step 2 (recording)")

    # Check if raw video exists (from a previous run or manual recording)
    if not dry_run and raw_path.exists() and not skip_record:
        print(f"  [INFO] Raw video already exists: {raw_path}")

    # Step 3: Compress
    compressed_path = DEMO_CONFIG["compressed_video"]
    step3_compress_video(raw_path, compressed_path, dry_run)
    results["compressed"] = compressed_path.exists() if not dry_run else False

    # Step 4: Analyze
    step4_analyze_video(compressed_path, raw_path, dry_run)

    # Step 5: Generate report
    if not dry_run and output:
        report = step5_generate_report(html_file, raw_path, compressed_path, results)
        report_path = Path(output)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        print(f"\n  [OK] Report saved: {report_path}")

    print()
    print("=" * 68)
    print("  Pipeline demo complete!")
    print(f"  Tools used:")
    print(f"    - puppeteer-screen-recorder (with CAIN config)")
    print(f"    - compress_video.py --profile cain")
    print(f"    - VIDEO_ANALYZER_WORM.py")
    print(f"    - reencode_large_videos.py (for batch jobs)")
    print(f"  See LESSONS_MASTER.md #64-67 for the full analysis")
    print("=" * 68)

    return True


# --- CLI -----------------------------------------------------------

def cmd_info():
    """Print comprehensive pipeline documentation."""
    print()
    print(STEP1_DOC)
    for name, filename in sorted(AVAILABLE_TEMPLATES.items()):
        exists = "[x]" if (TEMPLATES_DIR / filename).exists() else "[ ]"
        print(f"    {exists} {name:<12} -> {filename}")
    print()
    print(STEP2_DOC)
    print(STEP3_DOC)
    print(STEP4_DOC)
    print()
    print("=" * 68)
    print("  FULL PIPELINE COMMAND")
    print("=" * 68)
    print("  # Run everything:")
    print("  python scripts/cain_video_pipeline_demo.py --template cain")
    print()
    print("  # Dry run (show what would happen):")
    print("  python scripts/cain_video_pipeline_demo.py --dry-run")
    print()
    print("  # Skip recording (use existing raw video):")
    print("  python scripts/cain_video_pipeline_demo.py --skip-record")
    print()
    print("  # Show available templates:")
    print("  python scripts/cain_video_pipeline_demo.py --info")
    print()
    print("  # Print this reference:")
    print("  python scripts/cain_video_pipeline_demo.py --info")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="CAIN Video Pipeline Demo -- End-to-End Video Generation with CAIN Standard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cain_video_pipeline_demo.py                          # Run full pipeline
  python cain_video_pipeline_demo.py --template cain          # Use CAIN template
  python cain_video_pipeline_demo.py --dry-run                # Show what would happen
  python cain_video_pipeline_demo.py --skip-record            # Use existing raw video
  python cain_video_pipeline_demo.py --info                   # Show pipeline documentation
  python cain_video_pipeline_demo.py --step record            # Run just the recording step
  python cain_video_pipeline_demo.py --step compress          # Run just the compress step
  python cain_video_pipeline_demo.py --step analyze           # Run just the analyze step
        """,
    )

    parser.add_argument("--template", choices=list(AVAILABLE_TEMPLATES.keys()),
                        default="cain",
                        help="HTML template to use (default: cain)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without executing")
    parser.add_argument("--skip-record", action="store_true",
                        help="Skip puppeteer recording (use existing raw video)")
    parser.add_argument("--output", help="Save pipeline report to file")
    parser.add_argument("--info", action="store_true",
                        help="Show pipeline documentation and exit")
    parser.add_argument("--step", choices=["template", "record", "compress", "analyze"],
                        help="Run a single step of the pipeline")

    args = parser.parse_args()

    if args.info:
        cmd_info()
        return

    if args.step:
        # Single step mode
        if args.step == "template":
            step1_prepare_template(args.template, args.dry_run)
        elif args.step == "record":
            html_file = TEMPLATES_DIR / AVAILABLE_TEMPLATES[args.template]
            step2_generate_recorder(html_file, DEMO_CONFIG["raw_video"], args.dry_run)
        elif args.step == "compress":
            step3_compress_video(DEMO_CONFIG["raw_video"],
                                  DEMO_CONFIG["compressed_video"], args.dry_run)
        elif args.step == "analyze":
            step4_analyze_video(DEMO_CONFIG["compressed_video"],
                                 DEMO_CONFIG["raw_video"], args.dry_run)
        return

    # Full pipeline
    run_full_pipeline(
        template_name=args.template,
        dry_run=args.dry_run,
        skip_record=args.skip_record,
        output=args.output,
    )


if __name__ == "__main__":
    main()