#!/usr/bin/env python3
# coding: utf-8
"""CAP-PATCH-GEN -- propose the diagnostic-cap fix as a unified diff.

This tool never edits source. It only PROPOSES: it finds the diagnostic
truncations that are still capped at 100 characters and writes a unified diff.
tools/self_modify.py then applies it, evaluates it, and keeps or reverts it.

That split is deliberate. The thing that proposes a change and the thing that
decides whether it survives must not be the same thing, or "evaluation" becomes
a formality -- which is exactly how the mesh got a fitness function whose
optimum was "set every gene to 1.0".

    python tools/cap_patch_gen.py                    # scan src/, write the diff
    python tools/self_modify.py --patch logs/cap_fix_remainder.diff \
        --tier2 <band>                               # apply, evaluate, keep
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
OLD = "[:100]"
NEW = "[:800]"


def _candidates(src: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(src.rglob("*.py")):
        if any(part.startswith(".") for part in p.relative_to(ROOT).parts):
            continue
        try:
            # read_bytes, NOT read_text: read_text translates newlines, so a
            # diff generated from it is LF-only and `git apply` rejects every
            # CRLF file. The whole 46-file patch failed on exactly that.
            if OLD in p.read_bytes().decode("utf-8", errors="replace"):
                out.append(p)
        except OSError:
            continue
    return out


def build_diff(src: Path, skip: Sequence[str]) -> tuple[str, list[str], int, int]:
    chunks: list[str] = []
    changed: list[str] = []
    sites = 0
    for p in _candidates(src):
        rel = p.relative_to(ROOT).as_posix()
        if rel in skip:
            continue
        text = p.read_bytes().decode("utf-8", errors="replace")
        if not text.endswith("\n"):
            text += "\n"  # keep the diff applicable for files with no final newline
        old = text.splitlines(keepends=True)
        new = [ln.replace(OLD, NEW) for ln in old]
        if new == old:
            continue
        sites += sum(1 for a, b in zip(old, new) if a != b)
        changed.append(rel)
        chunks.append("".join(difflib.unified_diff(
            old, new, fromfile="a/" + rel, tofile="b/" + rel, n=3)))
    return "".join(chunks), changed, sites, len(changed)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="src")
    ap.add_argument("--out", default="logs/cap_fix_remainder.diff")
    ap.add_argument("--skip", nargs="*",
                    default=["src/CORE/WORMS/expansion/spine.py",
                             "src/CORE/data_retention.py"],
                    help="files already fixed (skipped, not re-proposed)")
    ap.add_argument("--format", choices=("subs", "diff"), default="subs",
                    help="subs = byte-exact substitution patch (default); diff = unified diff for review")
    ap.add_argument("--list", action="store_true", help="print files, write nothing")
    args = ap.parse_args(argv)
    if args.format == "subs" and args.out.endswith(".diff"):
        args.out = args.out[:-5] + ".json"

    src = ROOT / args.src
    patch, changed, sites, nfiles = build_diff(src, args.skip)
    if args.list:
        for rel in changed:
            print(rel)
        print("%d file(s), %d site(s)" % (nfiles, sites))
        return 0
    if not patch.strip():
        print("nothing to propose: no %s sites remain outside the skip list" % OLD)
        return 0
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "subs":
        out.write_text(json.dumps({"encoding": "utf-8",
                                   "subs": {rel: [[OLD, NEW]] for rel in changed}},
                                  indent=1), encoding="utf-8")
    else:
        out.write_text(patch, encoding="utf-8")
    print("proposed: %d file(s), %d site(s) -> %s (%s)"
          % (nfiles, sites, args.out, args.format))
    print("apply with: python tools/self_modify.py --patch %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
