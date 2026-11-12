#!/usr/bin/env python3
"""Drop self-referencing entries from a BIBL corpus and rebuild its index.

The guard in WORMZ stops the loop; this removes what the loop already produced.
An entry is self-referencing when its `*Source:*` line names the very file that
contains it -- i.e. the corpus recorded having read itself. Measured on the
Windows tree 2026-10-31: 367,268 of F.md's 367,304 entries were self-sourced.

Dry run by default. Nothing is deleted that has external provenance.

Usage:
    python tools/bibl_prune.py src/ORCHESTRATION/BIBL
    python tools/bibl_prune.py src/ORCHESTRATION/BIBL --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SOURCE_RE = re.compile(r"(?m)^\*Source:.*$")


def _is_self_reference(block: str, path: Path) -> bool:
    m = SOURCE_RE.search(block)
    if not m:
        return False
    line = m.group(0)
    return path.name in line and "BIBL" in line


def prune(bibl: Path, apply: bool) -> dict:
    before = sum(p.stat().st_size for p in bibl.rglob("*") if p.is_file())
    report = {"files": 0, "dropped": 0, "kept": 0}
    survivors = {}

    for p in sorted(bibl.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "## " not in txt:
            continue
        head, *chunks = re.split(r"(?m)^## ", txt)
        keep, drop = [], 0
        for c in chunks:
            if _is_self_reference(c, p):
                drop += 1
            else:
                keep.append(c)
                term = c.splitlines()[0].strip() if c.splitlines() else ""
                if term:
                    body = []
                    for ln in c.splitlines()[1:]:
                        if ln.startswith("*Source:"):
                            break
                        if ln.strip():
                            body.append(ln.strip())
                    survivors.setdefault(term, body[0] if body else "")
        if not drop:
            continue
        report["files"] += 1
        report["dropped"] += drop
        report["kept"] += len(keep)
        if apply:
            p.write_text(head + ("## " + "## ".join(keep) if keep else ""),
                         encoding="utf-8")
        print("%s: dropped %d, kept %d" % (p.name, drop, len(keep)))

    if apply and survivors:
        lines = ["## " + "## ".join([])]  # placeholder replaced below
        lines = ["# BIBL - Hyperlinked Lexicon", "",
                 "> Rebuilt from the letter files after the self-index prune.",
                 "> Total entries: %d" % len(survivors), "",
                 "## All Terms (Alphabetical)", ""]
        for term in sorted(survivors):
            lines.append("- **%s** - %s" % (term, survivors[term][:80]))
        (bibl / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("INDEX.md rebuilt: %d entries" % len(survivors))

    after = sum(p.stat().st_size for p in bibl.rglob("*") if p.is_file())
    report["before_mb"] = round(before / 1e6, 2)
    report["after_mb"] = round(after / 1e6, 2) if apply else round(before / 1e6, 2)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bibl", help="BIBL directory, e.g. src/ORCHESTRATION/BIBL")
    ap.add_argument("--apply", action="store_true", help="write the prune")
    a = ap.parse_args(argv)

    bibl = Path(a.bibl)
    if not bibl.is_dir():
        print("MISSING: %s" % bibl)
        return 2
    r = prune(bibl, a.apply)
    print("\n%d file(s), %d self-referenced entries %s, %d kept"
          % (r["files"], r["dropped"], "dropped" if a.apply else "would be dropped",
             r["kept"]))
    print("BIBL %s -> %s MB" % (r["before_mb"], r["after_mb"]))
    if not a.apply:
        print("DRY RUN — re-run with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
