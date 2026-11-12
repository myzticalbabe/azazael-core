#!/usr/bin/env python3
"""Build every demo asset from one music clip.

Usage:
    python make_assets.py                     # synthesise a test clip
    python make_assets.py path/to/song.mp4    # use your own music video

Outputs (demo/assets/): sample.mp4 (streamable), frames.bin (mode B),
raw.bin (mode D). Stdlib only; ffmpeg does the codec work.
"""
from __future__ import annotations

import glob
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "demo", "assets")
# A full-track transcode is the slow step; a wedge past this is a defect, not a wait.
FFMPEG_TIMEOUT = 900


def run(*args: str) -> None:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"ffmpeg exceeded {FFMPEG_TIMEOUT}s: {' '.join(args[:6])}...") from None
    except OSError as exc:  # not on PATH, or not executable here
        raise SystemExit(f"cannot run {args[0]}: {exc}") from None
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-2000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(args[:4])}...")


def synthesise(path: str) -> None:
    """6 s of colour bars with a sweeping tone so the bands actually move."""
    run(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=30",
        "-f", "lavfi", "-i",
        "aevalsrc=0.4*sin(2*PI*(220+120*sin(2*PI*0.6*t))*t):s=48000:d=6",
        "-t", "6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-movflags", "+faststart", "-c:a", "aac", "-b:a", "96k",
        path,
    )


def pack_frames(src: str, fps: int, width: int, height: int, out: str,
                start: float = 0.0, duration: float | None = None) -> tuple[int, int]:
    tmp = tempfile.mkdtemp(prefix="avfb-")
    try:
        run(
            "ffmpeg", "-y", "-ss", str(start),
            *(["-t", str(duration)] if duration else []), "-i", src,
            "-vf", f"fps={fps},scale={width}:{height}", "-q:v", "5",
            os.path.join(tmp, "f%04d.jpg"),
        )
        frames = sorted(glob.glob(os.path.join(tmp, "f*.jpg")))
        if not frames:
            raise SystemExit("no frames extracted")
        blobs = []
        for f in frames:
            with open(f, "rb") as fh:
                blobs.append(fh.read())
        # magic, count, w, h, fps, offsets[n+1], then JPEG payloads
        offset = 20 + 4 * (len(blobs) + 1)
        offsets, cursor = [], offset
        for b in blobs:
            offsets.append(cursor)
            cursor += len(b)
        offsets.append(cursor)
        with open(out, "wb") as fh:
            fh.write(b"AVFB")
            fh.write(struct.pack("<4I", len(blobs), width, height, fps))
            fh.write(struct.pack(f"<{len(offsets)}I", *offsets))
            for b in blobs:
                fh.write(b)
        return len(blobs), os.path.getsize(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pack_raw(src: str, fps: int, width: int, height: int, out: str,
             start: float = 0.0, duration: float | None = None) -> tuple[int, int]:
    rgb = os.path.join(tempfile.mkdtemp(prefix="avrd-"), "raw.rgb")
    run(
        "ffmpeg", "-y", "-ss", str(start),
        *(["-t", str(duration)] if duration else []), "-i", src,
        "-vf", f"fps={fps},scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", rgb,
    )
    with open(rgb, "rb") as fh:
        data = fh.read()
    stride = width * height * 3
    n = len(data) // stride
    with open(out, "wb") as fh:
        fh.write(b"AVRD")
        fh.write(struct.pack("<4I", n, width, height, fps))
        fh.write(data[: n * stride])
    shutil.rmtree(os.path.dirname(rgb), ignore_errors=True)
    return n, os.path.getsize(out)


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    mp4 = os.path.join(ASSETS, "sample.mp4")

    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        src = os.path.abspath(sys.argv[1])
        if not os.path.exists(src):
            raise SystemExit(f"no such file: {src}")
        print(f"input: {src} ({os.path.getsize(src)/1048576:.2f} MB)")
        # Re-encode to H.264/AAC whatever the source was: a browser may not carry
        # the codec the download happened to use (this library has AV1+Opus mp4s).
        run(
            "ffmpeg", "-y", "-i", src,
            "-vf", "scale=-2:720",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-movflags", "+faststart", "-c:a", "aac", "-b:a", "96k", mp4,
        )
        # The blobs are cooked from a short excerpt: at 12 fps a full 3.6-minute
        # track is ~2600 frames, which is exactly the size problem mode B shows.
        excerpt = (30.0, 12.0)
    else:
        synthesise(mp4)
        excerpt = (0.0, None)
        print("input: synthesised 6 s test clip")

    start, dur = excerpt
    n_b, size_b = pack_frames(mp4, fps=12, width=640, height=360,
                             out=os.path.join(ASSETS, "frames.bin"), start=start, duration=dur)
    n_d, size_d = pack_raw(mp4, fps=6, width=320, height=180,
                          out=os.path.join(ASSETS, "raw.bin"), start=start, duration=dur)

    print(f"sample.mp4  {os.path.getsize(mp4)/1048576:7.2f} MB  streamable, faststart")
    print(f"frames.bin  {size_b/1048576:7.2f} MB  {n_b} frames @12fps 640x360 JPEG")
    print(f"raw.bin     {size_d/1048576:7.2f} MB  {n_d} frames @6fps 320x180 RGB24")


if __name__ == "__main__":
    main()
