#!/usr/bin/env python3
# coding: utf-8
"""MODULE OWNERSHIP -- which of the duplicate basenames is the real module?

Measured 2026-09-19: this tree holds 35,223 duplicate basenames. That is not a
tidiness problem, it is a correctness problem with two faces:

  * for a human or a model, "patch TELEMETRYZ.py" is ambiguous --
    src/META/TELEMETRYZ.py and src/TELEMETRYZ.py both exist, and which one loads
    depends on sys.path order
  * for local retrieval it is worse: a 4.7B model with a limited context will
    cite a retired copy as confidently as live code, and nothing in the file
    tells it which is which

So this does not delete anything. It decides canonical ownership from the only
evidence that is not a guess: **which path the rest of the tree actually
imports**. Each duplicate gets a canonical file, the shadowed copies, the import
reference counts behind the decision, and a risk grade. Resolving them is a
refactor; recording them is what stops the next patch from hitting the wrong one.

    python tools/module_ownership.py
    python tools/module_ownership.py --json logs/module_ownership.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv-3.14t", "archived",
             "archive", "archived_unified", "var", ".nox", "build", "native"}

_FROM = re.compile(r"^\s*from\s+([A-Za-z_][\w\.]*)\s+import\b", re.M)
_PLAIN = re.compile(r"^\s*import\s+([A-Za-z_][\w\.]*)", re.M)


def iter_py(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py")
            if not any(part in SKIP_DIRS for part in p.parts)]


def import_refs(files: list[Path]) -> tuple[dict[str, int], dict[str, int]]:
    dotted: dict[str, int] = defaultdict(int)
    bare: dict[str, int] = defaultdict(int)
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for rx in (_FROM, _PLAIN):
            for m in rx.finditer(text):
                mod = m.group(1)
                dotted[mod] += 1
                bare[mod.split(".")[-1]] += 1
    return dict(dotted), dict(bare)


def candidates_for(path: Path, src: Path) -> list[str]:
    """Every name this file could be imported as, most specific first."""
    rel = path.relative_to(src).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    out: list[str] = []
    for i in range(len(parts)):
        out.append(".".join(parts[i:]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default="logs/module_ownership.json")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    src_files = iter_py(SRC)
    consumers = iter_py(ROOT / "src") + iter_py(ROOT / "tools") + iter_py(ROOT / "tests")
    dotted, bare = import_refs(consumers)

    groups: dict[str, list[Path]] = defaultdict(list)
    for p in src_files:
        # `__init__.py` is a package marker: every package has one by design, so
        # counting it as a duplicate basename is a false positive that drowns the
        # real ones (it showed up 48 times and dominated the risk list). Same
        # defect class as counting `.pyc` files in the hygiene guard.
        if p.name == "__init__.py":
            continue
        groups[p.name].append(p)
    dupes = {name: paths for name, paths in groups.items() if len(paths) > 1}

    records: dict[str, dict] = {}
    ambiguous = 0
    resolved = 0
    for name, paths in sorted(dupes.items()):
        scored = []
        for p in paths:
            cands = candidates_for(p, SRC)
            score = sum(dotted.get(c, 0) for c in cands) + sum(bare.get(c, 0) for c in cands)
            scored.append({"path": str(p.relative_to(ROOT)).replace("\\", "/"),
                           "importable_as": cands, "refs": score})
        scored.sort(key=lambda d: (-d["refs"], len(d["path"])))
        top_refs = scored[0]["refs"]
        tied = [s for s in scored if s["refs"] == top_refs]
        is_ambiguous = top_refs == 0 or len(tied) > 1
        if is_ambiguous:
            ambiguous += 1
        else:
            resolved += 1
        records[name] = {
            "count": len(paths),
            "canonical": scored[0]["path"],
            "shadowed": [s["path"] for s in scored[1:]],
            "candidates": scored,
            "ambiguous": is_ambiguous,
            "risk": "HIGH" if is_ambiguous or len(paths) > 2 else "MED",
            "note": ("no import references decide this one -- ownership is unknown"
                     if top_refs == 0 else
                     "tie on reference counts" if len(tied) > 1 else
                     "decided by import references"),
        }

    report = {
        "python_files_under_src": len(src_files),
        "duplicate_basenames": len(dupes),
        "files_involved": sum(r["count"] for r in records.values()),
        "resolved_by_references": resolved,
        "ambiguous": ambiguous,
        "map": records,
    }
    out = ROOT / args.json
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")

    print("=== MODULE OWNERSHIP MAP ===")
    print("python files under src/      : %d  (__init__.py excluded: package markers)"
          % len(src_files))
    print("duplicate basenames          : %d  (%d files involved)"
          % (len(dupes), report["files_involved"]))
    print("resolved by import refs      : %d" % resolved)
    print("ambiguous (no refs / tie)    : %d" % ambiguous)
    print()
    print("highest-risk groups:")
    risky = [r for r in ({"name": n, **v} for n, v in records.items())
             if r["risk"] == "HIGH"]
    risky.sort(key=lambda r: (-r["count"], r["name"]))
    for r in risky[:args.top]:
        print("  %-34s x%-2d canonical=%s" % (r["name"], r["count"], r["canonical"]))
        for s in r["candidates"][1:3]:
            print("      shadowed: %-44s refs=%d" % (s["path"], s["refs"]))
        print("      %s" % r["note"])
    if not risky:
        print("  none")
    print()
    print("written: %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
