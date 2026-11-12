"""SOTA single-video fetch worker: mp4 + info-json + frames + technique source.

Usage: python sota_fetch_video.py <yt_id> <ep> <arxiv> <batch_dir>
Idempotent (skips mp4 if present), single-video, parallel-safe.
Steps: yt-dlp (<=720p mp4 + info-json) -> ffmpeg mid + 8 thumbs + strip
-> sota_tech_source.py technique txt. Prints artifact sizes.
"""
import glob
import json
import os
import subprocess
import sys
import time

YT_BASE = "https://www.youtube.com/watch?v="


def run(cmd, timeout):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"FAILED {' '.join(cmd)}\n{p.stderr[-2000:]}")
    return p


def _glob_exists(batch, vid):
    return any(True for _ in glob.glob(os.path.join(batch, vid + ".mp4.*")))


def main() -> None:
    vid, ep, ax, batch = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    os.makedirs(batch, exist_ok=True)
    os.makedirs(os.path.join(batch, "frames"), exist_ok=True)
    mp4 = os.path.join(batch, vid + ".mp4")
    info = os.path.join(batch, vid + ".info.json")

    if os.path.exists(mp4):
        print(vid + " mp4 present, skipping download")
    elif (os.path.exists(os.path.join(batch, vid + ".webm"))
          or _glob_exists(batch, vid)):
        print(vid + " media present, skipping download (normalize only)")
    else:
        # YouTube 403/SABR reality: default client is throttled; fall back
        # to the android player client, then wait+retry (EP465 precedent).
        attempts = [
            ["yt-dlp", "-f", "bv*[height<=720]+ba/b[height<=720]/b"],
            ["yt-dlp", "-f", "bv*+ba/b",
             "--extractor-args", "youtube:player_client=android"],
            ["yt-dlp", "-f", "bv*+ba/b",
             "--extractor-args", "youtube:player_client=ios"],
        ]
        base = ["--remux-video", "mp4", "--write-info-json", "--no-playlist",
                "--concurrent-fragments", "4",
                "-o", os.path.join(batch, vid + ".%(ext)s"),
                YT_BASE + vid]
        last_exc = None
        for i, head in enumerate(attempts):
            try:
                run(head + base, timeout=1500)
                last_exc = None
                break
            except RuntimeError as exc:
                last_exc = exc
                print(f"{vid} attempt {i + 1}/3 failed, waiting 60s")
                time.sleep(60)
        if last_exc is not None:
            raise last_exc

    # normalize: remux any non-mp4 media to batch-standard mp4, fix names
    for stray in glob.glob(os.path.join(batch, vid + ".mp4.*")):
        if stray.endswith(".info.json"):
            os.replace(stray, info)
        elif not stray.endswith(".mp4"):
            run(["ffmpeg", "-y", "-i", stray, "-c", "copy", mp4], timeout=600)
            os.remove(stray)
    if os.path.exists(os.path.join(batch, vid + ".webm")) and not os.path.exists(mp4):
        run(["ffmpeg", "-y", "-i", os.path.join(batch, vid + ".webm"),
             "-c", "copy", mp4], timeout=600)

    with open(info, encoding="utf-8", errors="replace") as fh:
        meta = json.load(fh)
    dur = float(meta.get("duration") or 0)
    print(f"{vid} EP{ep}: {dur:.0f}s, "
          f"{os.path.getsize(mp4) / 1e6:.1f}MB, "
          f"{meta.get('width')}x{meta.get('height')}")

    # frames: mid + 8 thumbs at fractions, strip = 8x1 tile
    fracs = [i / 8.0 for i in range(8)]
    thumbs = []
    for i, f in enumerate(fracs):
        t = dur * f
        out = os.path.join(batch, "frames", f"{vid}_t{i}.jpg")
        thumbs.append(out)
        if not os.path.exists(out):
            run(["ffmpeg", "-y", "-ss", str(t), "-i", mp4,
                 "-frames:v", "1", "-q:v", "5", out], timeout=300)
    mid = os.path.join(batch, "frames", vid + "_mid.jpg")
    if not os.path.exists(mid):
        run(["ffmpeg", "-y", "-ss", str(dur / 2), "-i", mp4,
             "-frames:v", "1", "-q:v", "4", mid], timeout=300)
    strip = os.path.join(batch, "frames", vid + "_strip.jpg")
    if not os.path.exists(strip):
        inputs = []
        for th in thumbs:
            inputs += ["-i", th]
        run(["ffmpeg", "-y"] + inputs +
            ["-filter_complex", "tile=8x1", "-q:v", "5", strip], timeout=300)

    tech = os.path.join(batch, "technique", vid + ".txt")
    if not os.path.exists(tech):
        here = os.path.dirname(os.path.abspath(__file__))
        run([sys.executable, os.path.join(here, "sota_tech_source.py"),
             vid, ax, batch], timeout=120)
    print(f"{vid} done: mid={os.path.getsize(mid)} "
          f"strip={os.path.getsize(strip)} tech={os.path.getsize(tech)}")


if __name__ == "__main__":
    main()
