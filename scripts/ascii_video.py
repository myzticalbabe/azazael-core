#!/usr/bin/env python3
"""ASCII video synthesis — bitmap-font scenes piped to ffmpeg (HEVC standard).

Missing functionality built 2026-10-01: bitmap_font.py synthesized frames
but stopped at PPM bytes with no encode path. This closes it:
scene script (deterministic, seeded) -> RGB24 frames -> rawvideo pipe
-> ffmpeg libx265 CRF 26 30fps +faststart -> looping-capable mp4.

No browser, no PIL, no network. Same output bytes for same script
(determinism is verified, not asserted — see --verify-determinism).

Usage:
    python scripts/ascii_video.py --out demo.mp4 --seconds 5
    python scripts/ascii_video.py --out demo.mp4 --seconds 5 --gif
    python scripts/ascii_video.py --verify-determinism
"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bitmap_font import (  # noqa: E402
    render_text, GREEN, CYAN, WHITE, GOLD, DARK_BG,
)

FPS = 30
WIDTH = 640
HEIGHT = 360
SCALE = 4  # 5x7 cell at 4x = 24x32 px per char -> ~26 cols x 11 rows
BG = DARK_BG

# -- deterministic demo scene (VHS-style script, data not code) ---------------
DEMO_SCRIPT = [
    {"kind": "typewriter", "text": "AZAZAEL ASCII VIDEO",
     "scale": 4, "color": "GREEN", "pos": "center", "t0": 0.0, "dur": 1.5},
    {"kind": "typewriter", "text": "frames not files",
     "scale": 3, "color": "CYAN", "pos": "center", "t0": 1.5, "dur": 1.5},
    {"kind": "scroll", "text": "deterministic Cain HEVC gif",
     "scale": 3, "color": "GOLD", "pos": "center", "t0": 3.0, "dur": 2.0},
]

COLORS = {"GREEN": GREEN, "CYAN": CYAN, "WHITE": WHITE, "GOLD": GOLD}


def _blit(frame, frame_w, frame_h, rects, ox=0, oy=0):
    """Draw rects offset by (ox, oy); same clamp rules as bitmap_font."""
    for (rx, ry, rw, rh, rr, rg, rb) in rects:
        rx += ox
        ry += oy
        if rx + rw <= 0 or ry + rh <= 0 or rx >= frame_w or ry >= frame_h:
            continue
        for y in range(max(0, ry), min(frame_h, ry + rh)):
            base = y * frame_w * 3
            for x in range(max(0, rx), min(frame_w, rx + rw)):
                i = base + x * 3
                frame[i] = rr
                frame[i + 1] = rg
                frame[i + 2] = rb


def _layout(text, scale, color_name):
    w, h, rects = render_text(text, scale=scale,
                              colors=[COLORS[color_name]])
    return w, h, rects


def render_frame(t, script=DEMO_SCRIPT):
    """Render one frame at time t (seconds). Pure function of t."""
    px = bytes((BG[0], BG[1], BG[2])) * (WIDTH * HEIGHT)
    frame = bytearray(px)
    for ev in script:
        if not (ev["t0"] <= t < ev["t0"] + ev["dur"]):
            continue
        local = (t - ev["t0"]) / ev["dur"]  # 0..1
        w, h, rects = _layout(ev["text"], ev["scale"], ev["color"])
        ox = (WIDTH - w) // 2
        oy = (HEIGHT - h) // 2
        if ev["kind"] == "typewriter":
            nchars = int(len(ev["text"]) * min(1.0, local * 1.1))
            shown_w, _, shown = _layout(ev["text"][:nchars],
                                        ev["scale"], ev["color"])
            _blit(frame, WIDTH, HEIGHT, shown,
                  ox=(WIDTH - shown_w) // 2, oy=oy)
        elif ev["kind"] == "scroll":
            _blit(frame, WIDTH, HEIGHT, rects, ox=ox,
                  oy=int(HEIGHT - (HEIGHT + h) * local))
        else:  # static hold
            _blit(frame, WIDTH, HEIGHT, rects, ox=ox, oy=oy)
    return bytes(frame)


def encode_mp4(seconds, out_path, fps=FPS, show_progress=True):
    """Pipe frames to ffmpeg; returns (path, sha256)."""
    n = int(seconds * fps)
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{WIDTH}x{HEIGHT}", "-r", str(fps), "-i", "-",
           "-c:v", "libx265", "-crf", "26", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           "-an", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    digest = hashlib.sha256()
    for f in range(n):
        raw = render_frame(f / fps)
        digest.update(raw)
        proc.stdin.write(raw)
        if show_progress and f % 60 == 0:
            print(f"  frame {f}/{n}", flush=True)
    proc.stdin.close()
    proc.wait(timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg encode failed")
    return out_path, digest.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="ascii_demo.mp4")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--gif", action="store_true",
                    help="also emit looping gif via compress_video")
    ap.add_argument("--verify-determinism", action="store_true",
                    help="render twice, compare frame hashes")
    args = ap.parse_args()

    if args.verify_determinism:
        n = int(args.seconds * args.fps)
        h1 = hashlib.sha256()
        h2 = hashlib.sha256()
        for f in range(n):
            h1.update(render_frame(f / args.fps))
        for f in range(n):
            h2.update(render_frame(f / args.fps))
        print("deterministic:", h1.hexdigest() == h2.hexdigest())
        print("frames:", n, "hash:", h1.hexdigest()[:16])
        return 0 if h1.hexdigest() == h2.hexdigest() else 1

    out, digest = encode_mp4(args.seconds, args.out, fps=args.fps)
    size_mb = round(Path(out).stat().st_size / (1024 * 1024), 2)
    print(f"[OK] {out} ({size_mb} MB, {args.seconds}s @{args.fps}fps)")
    print(f"  frames sha256: {digest[:16]}")

    if args.gif:
        sys.path.insert(0, str(_SRC / "CODEX" / "VIDEOS"))
        from compress_video import encode_gif
        gif_out = str(Path(args.out).with_suffix(""))
        print("gif:", encode_gif(args.out, gif_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
