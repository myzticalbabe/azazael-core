"""Forensic no-dup check: confirm none of the 33 steal arXiv IDs is in the held-15 bank.

Reads batch specs (source of truth for what was fetched) and the diff file
(source of truth for held vs missing). Prints verdict. Exit 0 = clean.
"""
import json
import os
import re
import sys
from pathlib import Path


def norm(u):
    m = re.search(r"(\d{4}\.\d{4,5})", str(u or ""))
    return m.group(1) if m else None


def main():
    here = Path(__file__).resolve()
    base = str(here.parents[2] / "state" / "sota_batches")
    steals = {}
    for b in ["batch01", "batch02", "batch03", "batch04"]:
        with open(os.path.join(base, b, "specs.json"), encoding="utf-8") as fh:
            specs = json.load(fh)
        for vid, s in specs.items():
            steals[norm(s["arxiv"])] = (s["ep"], b)
    with open(here.parents[1] / "state" / "sota_diff_vs_held15.json",
              encoding="utf-8") as fh:
        diff = json.load(fh)
    held = set(diff["held"].keys())
    missing = set(e["arxiv"] and norm(e["arxiv"]) for e in diff.get("missing_all", []))
    print("steals banked:", len(steals))
    print("held bank:", len(held))
    overlap = set(steals) & held
    print("steals overlapping held-15:", sorted(overlap) if overlap else "NONE")
    not_missing = set(steals) - missing - held
    print("steals outside diff missing+held:", sorted(not_missing) if not_missing else "NONE")
    ok = not overlap
    print("VERDICT:", "CLEAN — 33 distinct, zero duplicates of held bank" if ok else "DIRTY")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
