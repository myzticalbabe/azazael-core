#!/usr/bin/env python3
"""
Azazael Matrix Cascade Animation
3000+ pieces of information cascading down with dynamic colors.

DEPRECATED for HTML/MP4 animation work (2026-10-01, operator directive):
PIL stills do not render animation richly — production route is browser
capture (Puppeteer) of CSS/canvas templates. This module stays for
static frame synthesis only; do not extend it for video.
"""

import random
import math
import os
import subprocess
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Configuration
NUM_DROPS = 3000
WIDTH, HEIGHT = 1280, 720
FPS = 30
DURATION_SECONDS = 45
TOTAL_FRAMES = FPS * DURATION_SECONDS
AZAZAEL_ROOT = os.environ.get("AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", str(os.path.expanduser("~/azazael"))))
OUTPUT_DIR = os.path.join(AZAZAEL_ROOT, "output", "matrix_frames")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Information data
SYSTEMS = [
    # Tier 1 - WORMS
    ("SYSTEMZ", "CPU|Memory|Disk|Network|Processes|--status|--cpu", "cyan"),
    ("PROCESSZ", "Enumeration|Management|Spawning|--list|--kill", "magenta"),
    ("DISKZ", "Partition|IO|Usage|Analyze|--usage|--io", "yellow"),
    ("NETWORXZ", "Interfaces|Connections|Stats|Ping|--interfaces", "lime"),
    ("CLOCKZ", "Time|Sync|Schedule|Tasks|--time|--uptime", "orange"),
    ("WORMZ", "BIBL Terminology Crawler|--all|--delta", "pink"),
    # Tier 2 - CLUSTERS
    ("BIBL CLUSTER", "WORMZ + TRIMZ - Content Crawling", "cyan"),
    ("SYSTEM CLUSTER", "SYSTEMZ + PROCESSZ + DISKZ - Monitoring", "magenta"),
    ("NETWORK CLUSTER", "NETWORXZ + CLOCKZ + DISKZ - Health", "yellow"),
    ("SECURITY CLUSTER", "PROCESSZ + NETWORXZ - Auditing", "lime"),
    ("ANALYSIS CLUSTER", "METAZ + METRICZ + DEPENDZ - Analysis", "orange"),
    ("VALIDATION CLUSTER", "VALIDZ + DEADZ + ERRORZ - Validation", "pink"),
    ("HEALTH CLUSTER", "HEALTHZ + OPTIMIZ + SYSTEMZ - Health", "cyan"),
    # Tier 3 - METAWORMS
    ("METAZ", "Metadata Extraction|--scan|--json", "magenta"),
    ("DEPENDZ", "Dependency Mapping|--tree|--graph", "yellow"),
    ("VALIDZ", "Syntax Validation|--strict", "lime"),
    ("DEADZ", "Dead Code Detection|--unused", "orange"),
    ("METRICZ", "Code Metrics|--cyclomatic", "pink"),
    ("ERRORZ", "Error Crawling|--scan|--fix", "cyan"),
    ("LOGZ", "Log Analysis", "magenta"),
    ("CHANGEZ", "Change Tracking", "yellow"),
    ("TRIMZ", "Content Trimming", "lime"),
    ("OPTIMIZ", "Performance Optimization", "orange"),
    ("HEALTHZ", "System Health", "pink"),
    # Tier 4 - HYDRAMETA
    ("HYDRAMETA", "ML Optimization|Genetic Algorithms", "lime"),
    ("POPULATION TUNING", "Parameter Optimization", "cyan"),
    ("PERSISTENCE", "State|Execution History", "magenta"),
    # Coordination
    ("TERENCE", "Error Resolution|RL|Causal Analysis", "orange"),
    ("HORUS", "24 Protocols|Visualization", "pink"),
    # Support Systems
    ("SHORTY", "Executable Shortcuts", "yellow"),
    ("TARBABY", "Binary Builder", "lime"),
    ("PERSEPHONE", "Cross-Platform", "cyan"),
    ("POINDEXTER", "Mathematical Indexer", "magenta"),
    ("BIBL", "Terminology Crawler", "orange"),
]

# Extended data for more drops
EXTENDED_DATA = []
for _ in range(100):
    for name, desc, color in SYSTEMS:
        EXTENDED_DATA.append((name, desc, color))


def get_random_color():
    """Get random fluorescent color."""
    colors = [
        (0, 255, 255),  # cyan
        (255, 0, 255),  # magenta
        (255, 255, 0),  # yellow
        (0, 255, 0),  # lime
        (255, 165, 0),  # orange
        (255, 192, 203),  # pink
        (0, 255, 128),  # spring green
        (128, 0, 255),  # violet
        (255, 255, 255),  # white
    ]
    return random.choice(colors)


class MatrixDrop:
    def __init__(self, idx):
        self.idx = idx
        self.reset()

    def reset(self):
        self.x = random.randint(0, WIDTH)
        self.y = random.randint(-HEIGHT, 0)
        self.speed = random.uniform(2, 8)
        self.char = random.choice(["1", "0"])
        self.size = random.randint(10, 24)
        self.color = get_random_color()
        self.trail = []
        self.max_trail = random.randint(5, 20)
        self.info_idx = random.randint(0, len(EXTENDED_DATA) - 1)
        self.show_info = random.random() < 0.15
        self.info_text = ""
        self.fontsize = random.randint(12, 18)

    def update(self, frame):
        self.y += self.speed
        self.trail.append((self.x, self.y))
        if len(self.trail) > self.max_trail:
            self.trail.pop(0)

        # Reset if off screen
        if self.y > HEIGHT + 50:
            if random.random() < 0.3:
                self.reset()
            else:
                # Move to different x position
                self.x = random.randint(0, WIDTH)
                self.y = random.randint(-200, -50)
                self.trail = []

        # Change character occasionally
        if random.random() < 0.05:
            self.char = random.choice(["1", "0"])

        # Occasionally show info
        if self.show_info and random.random() < 0.02:
            name, desc, color = EXTENDED_DATA[self.info_idx]
            self.info_text = f"{name}: {desc.split('|')[0]}"


def generate_frame(frame_num):
    """Generate a single PPM frame."""
    # Dark background with gradient
    bg_r = 10 + int(5 * math.sin(frame_num * 0.01))
    bg_g = 10 + int(5 * math.cos(frame_num * 0.01))
    bg_b = 26 + int(5 * math.sin(frame_num * 0.015))

    pixels = []
    for y in range(HEIGHT):
        row = []
        for x in range(WIDTH):
            # Base background with subtle grid
            grid_x = x % 40
            grid_y = y % 40
            grid_bright = 3 if (grid_x < 2 or grid_y < 2) else 0

            r = bg_r + grid_bright
            g = bg_g + grid_bright
            b = bg_b + grid_bright
            row.append((r, g, b))
        pixels.append(row)

    # Draw all drops
    for drop in drops:
        drop.update(frame_num)

        # Draw trail
        for i, (tx, ty) in enumerate(drop.trail):
            if 0 <= ty < HEIGHT and 0 <= tx < WIDTH:
                alpha = i / len(drop.trail) if drop.trail else 0
                brightness = int(255 * alpha * 0.8)
                r = min(255, drop.color[0] + brightness)
                g = min(255, drop.color[1] + brightness)
                b = min(255, drop.color[2] + brightness)
                pixels[int(ty)][int(tx)] = (r, g, b)

        # Draw head
        if 0 <= drop.y < HEIGHT and 0 <= drop.x < WIDTH:
            pixels[int(drop.y)][int(drop.x)] = drop.color

        # Draw info text (simplified - just colored pixels for now)
        if drop.show_info and drop.info_text:
            info_y = int(drop.y + drop.size + 5)
            if 0 <= info_y < HEIGHT:
                for i, ch in enumerate(drop.info_text[:20]):
                    info_x = int(drop.x + i * drop.size * 0.6)
                    if 0 <= info_x < WIDTH:
                        pixels[info_y][info_x] = drop.color

    # Add title at top
    title = "Azazael ECOSYSTEM"
    title_x = (WIDTH - len(title) * 20) // 2
    for i, ch in enumerate(title):
        for dy in range(32):
            for dx in range(20):
                px = title_x + i * 20 + dx
                py = 20 + dy
                if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                    pixels[py][px] = (200, 200, 255)

    # Convert to PPM format
    lines = [f"P3\n{WIDTH} {HEIGHT}\n255"]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            r, g, b = pixels[y][x]
            lines.append(f"{r} {g} {b}")

    return "\n".join(lines)


if __name__ == "__main__":
    print("=" * 60)
    print("Azazael Matrix Cascade Animation Generator")
    print(f"Generating {NUM_DROPS} drops x {TOTAL_FRAMES} frames = {TOTAL_FRAMES} frames")
    print("=" * 60)

    # Initialize drops
    drops = [MatrixDrop(i) for i in range(NUM_DROPS)]

    # Generate all frames
    print("\nGenerating frames...")
    for frame in range(TOTAL_FRAMES):
        ppm_content = generate_frame(frame)

        frame_file = f"{OUTPUT_DIR}/frame_{frame:04d}.ppm"
        with open(frame_file, "w", encoding="utf-8") as f:
            f.write(ppm_content)

        if frame % 100 == 0:
            print(f"  Frame {frame}/{TOTAL_FRAMES} ({frame * 100 // TOTAL_FRAMES}%)")

    print(f"\nGenerated {TOTAL_FRAMES} frames in {OUTPUT_DIR}")

    # Compile to video
    print("\nCompiling video with FFmpeg...")
    output_dir = os.path.join(AZAZAEL_ROOT, "output", "videos")
    os.makedirs(output_dir, exist_ok=True)
    output_video = os.path.join(output_dir, "Azazael_MATRIX_CASCADE.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", f"{OUTPUT_DIR}/frame_%04d.ppm",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-prese", "medium", "-crf", "20", "-", str(DURATION_SECONDS),
        output_video
    ], capture_output=True, timeout=1800)

    print(f"\nVideo saved to: {output_video}")

    # Disk guard: PPM intermediates are ~2.7MB/frame (~3.6GB per 45s run).
    # Delete them after a successful encode unless KEEP_FRAMES=1.
    import glob as _glob
    keep = os.environ.get("KEEP_FRAMES", "0") == "1"
    if not keep and os.path.exists(output_video) and os.path.getsize(output_video) > 0:
        victim = _glob.glob(f"{OUTPUT_DIR}/frame_*.ppm")
        for vf in victim:
            try:
                os.remove(vf)
            except OSError as exc:
                note_failure("matrix_animation:L256", exc)
        print(f"Cleaned {len(victim)} PPM intermediates (KEEP_FRAMES=1 to retain).")
    else:
        print("Kept PPM intermediates (encode missing or KEEP_FRAMES=1).")
    print("\nTo play: vlc {output_video}")
