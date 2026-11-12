#!/usr/bin/env python
# coding: utf-8
"""YouTube transcript fetcher (azazael-youtube-digest skill engine).

Stdlib-only. Subtitles first via yt-dlp, never ASR. Usage:
    python youtube_transcript.py fetch "<url-or-id>" [--lang en] [--format text|json]
    python youtube_transcript.py batch --input urls.txt [--out .cache/yt] [--lang en]
        [--delay 1.0] [--retries 3] [--manifest manifest.json]
    python youtube_transcript.py parse-id "<url-or-id>"

Single fetch prints transcript text (default) or JSON to stdout.
Batch prints one slim JSON record per video (no text) and caches full
transcripts under --out/<video_id>.json. Exit codes: 0 ok, 2 no
subtitles, 3 blocked/rate-limited, 4 private/removed, 1 other error.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_PATTERNS = [
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/shorts/([A-Za-z0-9_-]{11})"),
    re.compile(r"/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"/live/([A-Za-z0-9_-]{11})"),
]


def parse_id(s):
    s = (s or "").strip().strip("'\"")
    if ID_RE.match(s):
        return s
    for pat in URL_PATTERNS:
        m = pat.search(s)
        if m:
            return m.group(1)
    return None


def _run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def _pick_sub_file(tmpdir, vid):
    cands = sorted(Path(tmpdir).glob(vid + ".*"))
    for pref in (".json3", ".srv3", ".srv2", ".srv1", ".vtt", ".ttml"):
        for c in cands:
            if c.suffix == pref or c.name.endswith(pref):
                return c
    for c in cands:
        if ".en" in c.name or c.suffix in (".json", ".xml"):
            return c
    return None


def _json3_to_segments(data):
    segs = []
    for ev in data.get("events", []):
        if ev.get("aAppend"):
            continue
        start = int(ev.get("tStartMs", 0)) // 1000
        text = "".join(s.get("utf8", "") for s in ev.get("segs", []) or [])
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            segs.append({"start": start, "text": text})
    return segs


def _vtt_to_segments(text):
    segs, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"(\d+):(\d+):(\d+)[.,]\d+\s*-->", line)
        if m:
            h, mi, s = map(int, m.groups())
            cur = {"start": h * 3600 + mi * 60 + s, "text": ""}
        elif cur is not None and line and not line.isdigit() and "-->" not in line:
            line = re.sub(r"<[^>]+>", "", line)
            cur["text"] = (cur["text"] + " " + line).strip()
        elif not line and cur and cur["text"]:
            segs.append(cur)
            cur = None
    if cur and cur["text"]:
        segs.append(cur)
    return segs


def dedup_segments(segs):
    out, last = [], ""
    for s in segs:
        t = s["text"]
        if t and t != last:
            out.append(s)
            last = t
    return out


def fetch(vid, lang="en", retries=3):
    """Returns (status, payload). payload = dict or error string."""
    base = ("https://www.youtube.com/watch?v=" + vid)
    # Pass 1: metadata only (--print suppresses subtitle writes, so this
    # must be a separate call from the subtitle download below).
    rc, meta, err = _run(
        [sys.executable, "-m", "yt_dlp", "--skip-download",
         "--js-runtimes", "node",
         "--print", "%(title)s\t%(channel)s\t%(duration)s", base])
    if rc != 0:
        low = (err or "").lower()
        if any(k in low for k in ("sign in to confirm", "http error 403", "http error 429",
                                  "too many requests", "rate-limit", "sabrdetect")):
            return "blocked", err
        if any(k in low for k in ("private video", "video unavailable", "removed",
                                  "has been deleted", "not available")):
            return "unavailable", err
        return "error", err
    lines = meta.split("\t")
    title = lines[0] if len(lines) > 0 else ""
    channel = lines[1] if len(lines) > 1 else ""
    duration = lines[2] if len(lines) > 2 else ""
    # Pass 2: subtitles.
    with tempfile.TemporaryDirectory(prefix="ytdigest_") as tmp:
        cmd = [sys.executable, "-m", "yt_dlp", "--skip-download",
               "--js-runtimes", "node",
               "--write-subs", "--write-auto-subs",
               "--sub-langs", lang + "," + lang + "-orig",
               "--sub-format", "json3/srv3/vtt/best",
               "-o", str(Path(tmp) / "%(id)s.%(ext)s"), base]
        err = ""
        for attempt in range(max(1, retries)):
            rc, _, err = _run(cmd)
            if rc == 0:
                break
            low = err.lower()
            if any(k in low for k in ("sign in to confirm", "http error 403", "http error 429",
                                      "too many requests", "rate-limit", "sabrdetect")):
                return "blocked", err
            time.sleep(2 ** attempt)
        sub = _pick_sub_file(tmp, vid)
        if sub is None:
            return "no-subtitles", {"title": title, "channel": channel, "duration": duration}
        raw = sub.read_bytes()
        try:
            text = raw.decode("utf-8", "replace")
        except Exception:
            text = ""
        if sub.suffix in (".json3",) or '"events"' in text[:500]:
            try:
                segs = _json3_to_segments(json.loads(text))
            except Exception:
                segs = []
        else:
            segs = _vtt_to_segments(text)
        segs = dedup_segments(segs)
        if not segs:
            return "no-subtitles", {"title": title, "channel": channel, "duration": duration}
        return "ok", {"video_id": vid, "title": title, "channel": channel,
                      "duration": duration, "segments": segs}


def fmt_text(payload):
    def ts(s):
        return "%d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)
    lines = ["# %(title)s" % payload, "Channel: %(channel)s | Duration: %(duration)ss" % payload, ""]
    for s in payload["segments"]:
        lines.append("[%s](https://youtu.be/%s?t=%d) %s" % (
            ts(s["start"]), payload["video_id"], s["start"], s["text"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("url")
    f.add_argument("--lang", default="en")
    f.add_argument("--format", default="text", choices=("text", "json"))
    f.add_argument("--retries", type=int, default=3)
    b = sub.add_parser("batch")
    b.add_argument("--input", required=True)
    b.add_argument("--out", default=".cache/yt")
    b.add_argument("--lang", default="en")
    b.add_argument("--delay", type=float, default=1.0)
    b.add_argument("--retries", type=int, default=3)
    b.add_argument("--manifest")
    b.add_argument("--refresh", action="store_true")
    p = sub.add_parser("parse-id")
    p.add_argument("url")
    a = ap.parse_args(argv)

    if a.cmd == "parse-id":
        vid = parse_id(a.url)
        print(vid or "UNPARSEABLE")
        return 0 if vid else 1

    if a.cmd == "fetch":
        vid = parse_id(a.url)
        if not vid:
            print("error: unparseable URL/id", file=sys.stderr)
            return 1
        status, payload = fetch(vid, a.lang, a.retries)
        if status != "ok":
            print(json.dumps({"status": status, "video_id": vid, "detail": payload}))
            return {"no-subtitles": 2, "blocked": 3, "unavailable": 4}.get(status, 1)
        if a.format == "json":
            print(json.dumps({"status": "ok", **payload}, ensure_ascii=False))
        else:
            print(fmt_text(payload))
        return 0

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    seen = set()
    for line in Path(a.input).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        vid = parse_id(line)
        if not vid or vid in seen:
            continue
        seen.add(vid)
        cache = out / (vid + ".json")
        if cache.exists() and not a.refresh:
            try:
                payload = json.loads(cache.read_text(encoding="utf-8"))
                manifest.append({k: payload.get(k) for k in
                                 ("video_id", "title", "channel", "duration")}
                                | {"segments": len(payload.get("segments", [])),
                                   "cached": True})
                print(json.dumps(manifest[-1], ensure_ascii=False))
                continue
            except Exception:
                print("youtube_transcript: cache write failed", file=sys.stderr)
        status, payload = fetch(vid, a.lang, a.retries)
        if status == "ok":
            cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            manifest.append({"video_id": vid, "title": payload["title"],
                             "channel": payload["channel"], "duration": payload["duration"],
                             "segments": len(payload["segments"]), "cached": False})
        else:
            manifest.append({"video_id": vid, "status": status,
                             "detail": str(payload)[:300]})
        print(json.dumps(manifest[-1], ensure_ascii=False))
        time.sleep(a.delay)
    if a.manifest:
        Path(a.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
