#!/usr/bin/env python3
"""Quick test: generate one video with drawtext + fontfile."""
import subprocess, os, sys

FONT = "C:/Windows/Fonts/consola.ttf"
FONT_B = "C:/Windows/Fonts/consolab.ttf"
OUT = "/c/Users/clint/Desktop/AZAZAEL_Standalone/Critique/videos"
os.makedirs(OUT, exist_ok=True)

filters = [
    "fps=30",
    "drawgrid=w=50:h=50:color=cyan@0.02",
    f"drawtext=fontfile={FONT_B}:text='AZAZAEL ECOSYSTEM':fontsize=38:fontcolor=white:x=(w-text_w)/2:y=15",
    f"drawtext=fontfile={FONT}:text='Free-Threaded Agent Architecture':fontsize=18:fontcolor=cyan:x=(w-text_w)/2:y=55",
    f"drawtext=fontfile={FONT}:text='TIER 1 - WORMS':fontsize=24:fontcolor=yellow:x=50:y=140",
    f"drawtext=fontfile={FONT}:text='SYSTEMZ  PROCESSZ  DISKZ  NETWORXZ  CLOCKZ  WORMZ':fontsize=16:fontcolor=0x00ff00:x=50:y=180",
]

cmd = ["ffmpeg", "-y", "-f", "lavfi",
    "-i", "color=c=0x0a0a1a:d=5:s=1280x720:r=30",
    "-vf", ",".join(filters),
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "23", "-t", "5",
    f"{OUT}/AZAZAEL_TEST_Ecosystem.mp4"]

print(f"Running: {' '.join(cmd[:5])}...")
result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")

if result.returncode == 0:
    sz = os.path.getsize(f"{OUT}/AZAZAEL_TEST_Ecosystem.mp4") / (1024*1024)
    print(f"SUCCESS: {sz:.2f} MB -> AZAZAEL_TEST_Ecosystem.mp4")
    sys.exit(0)
else:
    print(f"STDERR: {result.stderr[-500:]}")
    sys.exit(1)
