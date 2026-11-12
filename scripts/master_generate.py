#!/usr/bin/env python3
"""
AZAZAEL Master Video & HTML Generator v2.0
===========================================
Uses pure-Python bitmap font rendering (no fontconfig, no drawtext)
to generate .mp4 videos, then converts to .wmv.
Also copies Mermaid HTML diagrams.
"""

import json
import os
import shutil
import sys
import subprocess
from pathlib import Path
from datetime import datetime

# Add scripts dir for imports
sys.path.insert(0, str(Path(__file__).parent))

from video_frame_generator import (
    generate_template_video, generate_dissertation_video, convert_to_wmv,
)
from bitmap_font import WHITE, CYAN, PURPLE, PINK, GREEN, GOLD, RED

# ─── Configuration ───────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
VIDEO_TEMPLATES_DIR = TEMPLATES_DIR / "video"
MERMAID_TEMPLATES_DIR = TEMPLATES_DIR / "mermaid"

USERPROFILE = os.environ.get("USERPROFILE", str(Path.home()))
OUTPUT_BASE = Path(USERPROFILE) / "Desktop" / "AZAZAEL_Standalone" / "Critique"
VIDEO_OUTPUT = OUTPUT_BASE / "videos"
MERMAID_OUTPUT = OUTPUT_BASE / "mermaid"
HTML_OUTPUT = OUTPUT_BASE / "html"

VIDEO_TEMPLATES = [
    "ecosystem_overview.json",
    "worm_architecture.json",
    "inference_studio.json",
    "animation_protocols.json",
    "cluster_topology.json",
    "data_flow.json",
    "metaworms.json",
    "production_build.json",
    "coordination_layer.json",
    "system_health.json",
]


def main():
    print("=" * 60)
    print("  AZAZAEL Master Video & HTML Generator v2.0")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  Output: {OUTPUT_BASE}")
    print("=" * 60)

    # Ensure directories
    VIDEO_OUTPUT.mkdir(parents=True, exist_ok=True)
    MERMAID_OUTPUT.mkdir(parents=True, exist_ok=True)
    HTML_OUTPUT.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Generate dissertation video ──────────────────────
    print("\n[1/4] Generating Comprehensive Dissertation Video...")
    disc_path = VIDEO_OUTPUT / "AZAZAEL_Dissertation.mp4"
    if disc_path.exists():
        print(f"  SKIP (exists): {disc_path.name}")
    elif generate_dissertation_video(str(disc_path)):
        print(f"  Dissertation: {os.path.getsize(disc_path)/1024/1024:.1f} MB")
    else:
        print("  Dissertation: FAILED")

    # ── Step 2: Generate template videos ─────────────────────────
    print("\n[2/4] Generating Template Videos...")
    generated = []

    for tmpl_name in VIDEO_TEMPLATES:
        tmpl_path = VIDEO_TEMPLATES_DIR / tmpl_name
        if not tmpl_path.exists():
            print(f"  SKIP: {tmpl_name} not found")
            continue

        # Derive output name
        stem = tmpl_name.replace(".json", "")
        readable = stem.replace("_", " ").title().replace(" ", "_")
        out_path = VIDEO_OUTPUT / f"AZAZAEL_{readable}.mp4"

        if out_path.exists():
            print(f"  SKIP (exists): {out_path.name}")
            generated.append(out_path)
            continue

        if generate_template_video(str(tmpl_path), str(out_path)):
            generated.append(out_path)

    print(f"\n  Generated {len(generated)} template videos")
    generated.append(disc_path)

    # ── Step 3: Convert all to .wmv ──────────────────────────────
    print("\n[3/4] Converting to .wmv...")
    wmv_count = 0
    for mp4_path in generated:
        if mp4_path.exists():
            if convert_to_wmv(mp4_path):
                wmv_count += 1
    print(f"  Converted {wmv_count} .wmv files")

    # ── Step 4: Copy Mermaid HTMLs ───────────────────────────────
    print("\n[4/4] Copying Mermaid Diagrams...")
    mermaid_count = 0
    if MERMAID_TEMPLATES_DIR.exists():
        for html_file in sorted(MERMAID_TEMPLATES_DIR.glob("*.html")):
            dest = MERMAID_OUTPUT / html_file.name
            shutil.copy2(html_file, dest)
            print(f"  Copied: {html_file.name}")
            mermaid_count += 1
    print(f"  {mermaid_count} Mermaid diagrams copied")

    # ── Summary ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  DELIVERABLE SUMMARY")
    print("=" * 60)

    mp4s = sorted(VIDEO_OUTPUT.glob("*.mp4"))
    wmvs = sorted(VIDEO_OUTPUT.glob("*.wmv"))
    mermaids = sorted(MERMAID_OUTPUT.glob("*.html"))
    htmls = sorted(HTML_OUTPUT.glob("*.html"))

    mp4_total = sum(os.path.getsize(f) for f in mp4s) / (1024 * 1024)
    wmv_total = sum(os.path.getsize(f) for f in wmvs) / (1024 * 1024)

    print(f"\n  .mp4 videos: {len(mp4s)} ({mp4_total:.1f} MB)")
    for m in mp4s:
        print(f"    {m.name} ({os.path.getsize(m)/1024:.0f} KB)")

    print(f"\n  .wmv videos: {len(wmvs)} ({wmv_total:.1f} MB)")
    for w in wmvs:
        print(f"    {w.name} ({os.path.getsize(w)/1024:.0f} KB)")

    print(f"\n  Mermaid HTML: {len(mermaids)} files")
    for mh in mermaids:
        print(f"    {mh.name}")

    print(f"\n  Dissertation HTML: {len(htmls)} files")
    for h in htmls:
        print(f"    {h.name}")

    print(f"\n  All deliverables in: {OUTPUT_BASE}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
