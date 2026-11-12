#!/usr/bin/env python3
# coding: utf-8
"""skills_find -- look up an archived package skill instead of loading it.

Why this exists
---------------
Until 2026-10-30 the tree registered **1,042 `azazael-pkg-*` skills** alongside
203 real ones. Their descriptions alone measured 138,964 bytes (~34.7k tokens),
and every client that lists skills paid that on every request -- roughly four
times the cost of the canon `AGENTS.md`, for a catalogue the model then has to
ignore. Worse, 54 package names existed twice (`azazael-pkg-anyio` and
`azazael-pkg314t-anyio`), so the list also taught the model that it is noise.

They are not gone: they are archived, byte for byte, at:

    skills.pkg-archive/source/<name>.md          (source corpus)
    skills.pkg-archive/load/<name>/SKILL.md      (former load path)
    tools/data/pkg_index.json                    (generated index, 1,042 rows)

Usage
-----
    python tools/skills_find.py anyio            # search by package/name/summary
    python tools/skills_find.py --layer pkg314t  # only the 3.14t/3.15t builds
    python tools/skills_find.py --show azazael-pkg-anyio   # print the archived skill
    python tools/skills_find.py --stats          # index health

Exit codes: 0 = at least one row, 1 = no match, 2 = index missing/corrupt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError) as _exc:
        # Legacy/redirected stream: note it on the other stream instead of a
        # silent pass, then continue with whatever encoding we were given.
        sys.stderr.write(f"note: stream not reconfigured to utf-8 ({_exc})\n")

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "tools" / "data" / "pkg_index.json"
ARCHIVE = ROOT / "skills.pkg-archive"


def load_index(path: Path = INDEX) -> dict:
    if not path.exists():
        raise SystemExit(f"index missing: {path}\nRegenerate it from {ARCHIVE/'source'}.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"index corrupt: {path}: {exc}")
    if not isinstance(data.get("skills"), list):
        raise SystemExit(f"index has no 'skills' list: {path}")
    return data


def search(rows: list[dict], query: str, layer: str | None = None) -> list[dict]:
    q = query.lower()
    hits = [
        r
        for r in rows
        if (layer is None or r.get("layer") == layer)
        and (
            q in r.get("package", "").lower()
            or q in r.get("name", "").lower()
            or q in r.get("summary", "").lower()
        )
    ]
    # exact package match first, then prefix, then the rest
    hits.sort(key=lambda r: (r.get("package", "").lower() != q, not r.get("package", "").lower().startswith(q), r.get("name", "")))
    return hits


def show(name: str) -> int:
    for candidate in (ARCHIVE / "source" / f"{name}.md", ARCHIVE / "load" / name / "SKILL.md"):
        if candidate.exists():
            sys.stdout.write(candidate.read_text(encoding="utf-8", errors="replace"))
            print(f"\n\n[archived from {candidate.relative_to(ROOT)}]")
            return 0
    print(f"not in the archive: {name}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Search the archived azazael-pkg-* skills.")
    ap.add_argument("query", nargs="?", help="package name or free text")
    ap.add_argument("--layer", choices=("pkg", "pkg314t"), help="restrict to one layer")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--show", metavar="NAME", help="print one archived SKILL.md")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args(argv)

    data = load_index()
    rows = data["skills"]

    if args.stats:
        layers: dict[str, int] = {}
        for r in rows:
            layers[r.get("layer", "?")] = layers.get(r.get("layer", "?"), 0) + 1
        dupes = sorted({r["package"] for r in rows if sum(1 for x in rows if x["package"] == r["package"]) > 1})
        print(f"index: {INDEX.relative_to(ROOT)}  ({data.get('generated', '?')})")
        print(f"  rows:      {len(rows)}")
        print(f"  by layer:  {layers}")
        print(f"  packages appearing in BOTH layers: {len(dupes)}")
        print(f"  archive:   {ARCHIVE.relative_to(ROOT)}")
        return 0

    if args.show:
        return show(args.show)

    if not args.query:
        ap.print_help()
        return 2

    hits = search(rows, args.query, args.layer)
    if not hits:
        print(f"no archived package skill matches {args.query!r} ({len(rows)} rows searched)")
        return 1

    for r in hits[: args.limit]:
        print(f"{r['name']}  [{r['layer']}]")
        print(f"    {r.get('summary', '')[:180]}")
        print(f"    read: python tools/skills_find.py --show {r['name']}")
    if len(hits) > args.limit:
        print(f"... {len(hits) - args.limit} more (raise --limit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
