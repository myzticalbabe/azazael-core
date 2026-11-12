"""SOTA technique-source worker: extract YT description + arXiv abstract per video.

Usage: python3 sota_tech_source.py <yt_id> <arxiv_id> <batch_dir>
Writes <batch_dir>/technique/<yt_id>.txt. Idempotent, single-video, parallel-safe.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def _fetch_arxiv(ax):
    """Return (title, abstract), tolerating missing CA certs (Store-Python)
    and network failures — caller still writes the description file."""
    try:
        try:
            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = None
        req = urllib.request.Request(
            "https://export.arxiv.org/api/query?id_list=" + ax,
            headers={"User-Agent": "azazael-sota-harvest/1.0"},
        )
        raw = urllib.request.urlopen(req, timeout=30, context=ctx).read()
        entry = ET.fromstring(raw).find("{http://www.w3.org/2005/Atom}entry")
        title = " ".join((entry.find("{http://www.w3.org/2005/Atom}title").text or "").split())
        abstract = " ".join((entry.find("{http://www.w3.org/2005/Atom}summary").text or "").split())
        return title, abstract
    except (urllib.error.URLError, ssl.SSLError, ET.ParseError) as exc:
        return "ABSTRACT_FETCH_FAILED", "arxiv fetch failed (" + type(exc).__name__ + "); see video description"


def main() -> None:
    vid, ax, batch = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(os.path.join(batch, "technique"), exist_ok=True)
    with open(os.path.join(batch, vid + ".info.json"), encoding="utf-8", errors="replace") as fh:
        info = json.load(fh)
    title, abstract = _fetch_arxiv(ax)
    desc = (info.get("description") or "")[:3000]
    lines = [
        "VIDEO: " + str(info.get("title")),
        "ARXIV: " + ax + " | " + title,
        "ABSTRACT: " + abstract,
        "DESCRIPTION:",
        desc,
        "",
    ]
    out = os.path.join(batch, "technique", vid + ".txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(vid + " source bytes: " + str(os.path.getsize(out)))


if __name__ == "__main__":
    main()
