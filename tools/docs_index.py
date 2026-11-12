#!/usr/bin/env python3
"""docs_index.py — generate docs/INDEX.md from path + first heading + date.

No hand maintenance: re-run to regenerate. Output is deterministic (sorted by path).

Usage:
    python docs_index.py [--root docs/] [--out docs/INDEX.md]
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def first_heading(path: Path) -> str:
    """Extract the first markdown heading from a file."""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^#{1,6}\s+(.+)$", line.strip())
            if m:
                return m.group(1).strip()
    except OSError as exc:
        note_failure("tools/docs_index.py", exc)
        pass
    return "(no heading)"


def file_date(path: Path) -> str:
    """File modification date (UTC)."""
    try:
        mtime = path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    except OSError as exc:
        note_failure("tools/docs_index.py", exc)
        return "unknown"


def generate_index(docs_dir: Path) -> str:
    """Generate a markdown index of all .md files under docs_dir."""
    md_files = sorted(docs_dir.rglob("*.md"))
    lines = [
        "# Documentation Index",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"by `tools/docs_index.py`. Re-run to refresh.",
        "",
        f"**{len(md_files)} documents**",
        "",
        "| Path | First heading | Modified |",
        "|---|---|---|",
    ]
    for f in md_files:
        rel = f.relative_to(docs_dir.parent).as_posix()
        heading = first_heading(f)
        date = file_date(f)
        lines.append(f"| `{rel}` | {heading} | {date} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(DOCS), help="docs directory")
    ap.add_argument("--out", default=str(DOCS / "INDEX.md"), help="output file")
    args = ap.parse_args()

    docs_dir = Path(args.root).resolve()
    if not docs_dir.is_dir():
        print(f"error: {docs_dir} is not a directory", file=__import__("sys").stderr)
        return 2

    index = generate_index(docs_dir)
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(index, encoding="utf-8")
    print(f"Wrote {index.count(chr(10))} lines to {out_path}")
    return 0


if __name__ == "__main__":
    import sys

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    sys.exit(main())