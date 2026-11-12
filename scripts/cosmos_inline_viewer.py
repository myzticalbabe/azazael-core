#!/usr/bin/env python3
"""cosmos_inline_viewer.py — inline a repo's Cosmos viewer into a single
self-contained HTML file so it works standalone from file:// with no
sibling folders or script-tag network requests.

Usage:
  python3 cosmos_inline_viewer.py <viewer-dir> <output.html>
    <viewer-dir>  folder containing cosmos-viewer.html, viewer.bundle.js, cosmos/
    <output.html> single-file output (data + bundle inlined)
"""
import os
import re
import sys


def inline_viewer(viewer_dir: str, out_path: str) -> None:
    html_path = os.path.join(viewer_dir, "cosmos-viewer.html")
    bundle_path = os.path.join(viewer_dir, "viewer.bundle.js")
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    # Inline each <script src="cosmos/*.js"> chunk.
    def inline_chunk(m: re.Match) -> str:
        src = m.group(1)
        if not src.startswith("cosmos/"):
            return m.group(0)
        chunk_path = os.path.join(viewer_dir, src.replace("/", os.sep))
        with open(chunk_path, encoding="utf-8") as f:
            return "<script>\n" + f.read() + "\n</script>"

    html = re.sub(r'<script src="([^"]+)"></script>', inline_chunk, html)

    # Inline the bundle (the last remaining external script).
    def inline_bundle(m: re.Match) -> str:
        src = m.group(1)
        if not src.endswith(".js"):
            return m.group(0)
        bundle = os.path.join(viewer_dir, os.path.basename(src))
        with open(bundle, encoding="utf-8") as f:
            return "<script>\n" + f.read() + "\n</script>"

    html = re.sub(r'<script src="([^"]+)"></script>', inline_bundle, html)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(out_path)
    print(f"wrote {out_path} ({size:,} bytes)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    inline_viewer(sys.argv[1], sys.argv[2])
