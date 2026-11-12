"""Reproducible TikTok favorites batch loop: download -> analyze -> manifest -> delete.
One-time setup: Firefox installed + logged into TikTok (yt-dlp reads Firefox
cookies while it runs; no closing, no exports, no ceremony).
Usage (py315t, has yt-dlp):
  python tools/tiktok_batch.py --url <collection-url> --batch <name> [--max 10]
                               [--skip-dl] [--delete]
Writes batch/<name>/MANIFEST.md + frames/ + specs.json, then optionally
deletes the mp4s (frames + manifest stay for analysis).
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BATCHES = ROOT / "state" / "tiktok_batches"

PY = sys.executable  # py315t interpreter (yt-dlp installed)


def run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-2000:]


def ffprobe(path):
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams",
           "-of", "json", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
        return json.loads(r.stdout or "{}")
    except Exception as e:
        return {"error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="TikTok collection/favorites URL")
    ap.add_argument("--batch", required=True, help="batch name, e.g. fav-01")
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--browser", default="firefox")
    ap.add_argument("--skip-dl", action="store_true")
    ap.add_argument("--delete", action="store_true",
                    help="delete mp4s after analysis (frames+manifest kept)")
    a = ap.parse_args()

    bdir = BATCHES / a.batch
    vdir = bdir / "videos"
    fdir = bdir / "frames"
    vdir.mkdir(parents=True, exist_ok=True)
    fdir.mkdir(parents=True, exist_ok=True)

    if not a.skip_dl:
        print(f"[1/4] download max={a.max} via {a.browser} cookies ...")
        rc, out = run([
            PY, "-m", "yt_dlp", "--cookies-from-browser", a.browser,
            "--max-downloads", str(a.max),
            "--write-info-json", "--write-thumbnail",
            "-P", str(vdir), "-o", "%(id)s.%(ext)s",
            "--no-playlist" if "/collection/" in a.url else "--flat-playlist",
            a.url,
        ], timeout=1800)
        print("  rc =", rc)
        if rc != 0:
            print(out[-1200:])
            return 2

    print("[2/4] probe + frames ...")
    specs = []
    for mp4 in sorted(vdir.glob("*.mp4")):
        info = ffprobe(mp4)
        streams = info.get("streams", [])
        vst = next((s for s in streams if s.get("codec_type") == "video"), {})
        ast = next((s for s in streams if s.get("codec_type") == "audio"), {})
        dur = float(info.get("format", {}).get("duration") or 0)
        spec = {
            "id": mp4.stem, "file": mp4.name,
            "mb": round(mp4.stat().st_size / 1048576, 2),
            "vcodec": vst.get("codec_name"), "w": vst.get("width"),
            "h": vst.get("height"),
            "fps": vst.get("r_frame_rate") or vst.get("avg_frame_rate"),
            "duration": round(dur, 2),
            "acodec": ast.get("codec_name"),
            "descr": "",
        }
        mid = dur / 2 if dur else 1.0
        frame = fdir / f"{mp4.stem}_mid.png"
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(round(mid, 2)),
             "-i", str(mp4), "-frames:v", "1", str(frame)], timeout=180)
        strip = fdir / f"{mp4.stem}_strip.png"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
             "-vf", "fps=1,scale=320:-1,tile=8x8", "-frames:v", "1",
             str(strip)], timeout=300)
        spec["mid"] = frame.name if frame.exists() else ""
        spec["strip"] = strip.name if strip.exists() else ""
        ij = vdir / f"{mp4.stem}.info.json"
        if ij.exists():
            try:
                meta = json.loads(ij.read_text(encoding="utf-8", errors="replace"))
                spec["descr"] = str(meta.get("description") or meta.get("title") or "")[:200]
            except Exception as e:
                print("tiktok_batch: unreadable %s (%s), caption left blank"
                      % (ij.name, type(e).__name__), file=sys.stderr)
                pass
        specs.append(spec)
        print(f"  {spec['id']} {spec['w']}x{spec['h']} {spec['fps']} {spec['duration']}s {spec['mb']}MB")

    (bdir / "specs.json").write_text(json.dumps(specs, indent=1), encoding="utf-8")
    lines = [f"# batch {a.batch}", f"n={len(specs)}  source={a.url}", ""]
    for s in specs:
        lines.append(f"## {s['id']} — {s['w']}x{s['h']} {s['fps']} {s['duration']}s {s['mb']}MB")
        lines.append(f"frames: {s['mid']} {s['strip']}")
        if s["descr"]:
            lines.append(f"caption: {s['descr']}")
        lines.append("technique: ")
        lines.append("")
    (bdir / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[3/4] manifest -> {bdir / 'MANIFEST.md'}")

    if a.delete:
        n = 0
        for mp4 in vdir.glob("*.mp4"):
            mp4.unlink()
            n += 1
        print(f"[4/4] deleted {n} mp4s (frames + manifest + specs kept)")
    else:
        print("[4/4] kept videos (pass --delete to remove)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
