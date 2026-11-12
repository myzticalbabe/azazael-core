#!/usr/bin/env python3
# coding: utf-8
"""FS-HYGIENE -- measure the working tree, and fail on the shapes that grow quietly.

Measured 2026-09-18: 107,867 files across 56 top-level directories, 43 files over
20 MB, 7,805 __pycache__ files, 35,223 duplicate basenames, and ~4.9 GB of
near-identical graph snapshots. None of it failed anything. It was simply allowed
to accumulate -- the same silent-budget failure as a token cap that truncates a
traceback, except measured in directories instead of characters.

The guard is REGRESSION-based on purpose. A legacy 2 GB archive is a fact, not a
new defect, and a check that fails on facts gets switched off. So: baseline once,
then fail on anything new -- a new duplicate basename under src/, a new oversized
file, a new top-level directory.

    python tools/fs_hygiene.py                     # report
    python tools/fs_hygiene.py --baseline          # record current state
    python tools/fs_hygiene.py --check             # exit 1 on regression
    python tools/fs_hygiene.py --kinds pycache,graph-snapshots --execute
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "logs" / "fs_baseline.json"
MANIFEST = ROOT / "logs" / "fs_compaction_manifest.json"
COLD = ROOT / "var" / "cold"
LARGE_MB = 20
WALK_CAP = 400000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scan() -> dict:
    """One bounded walk. Everything else is derived from this single pass."""
    files = 0
    per_top_files: Counter = Counter()
    per_top_bytes: Counter = Counter()
    large: list[dict] = []
    dup: defaultdict[str, set] = defaultdict(set)
    dup_src: defaultdict[str, set] = defaultdict(set)
    cache_files = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel = os.path.relpath(dirpath, ROOT)
        top = rel.split(os.sep)[0]
        is_src = top == "src"
        is_cache = os.path.basename(dirpath) == "__pycache__"
        for name in filenames:
            files += 1
            if is_cache:
                cache_files += 1
            p = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            per_top_files[top] += 1
            per_top_bytes[top] += size
            # var/cold is the designated archive: the compactor moves dated
            # snapshots there BY DESIGN (2026-09-28: flagging them re-created
            # the regressions the move just cleared). Counted in the totals
            # above, never flagged as new large files.
            rel_p = os.path.relpath(p, ROOT).replace(os.sep, "/")
            if size > LARGE_MB * 1024 * 1024 and not rel_p.startswith("var/cold/"):
                large.append({"path": os.path.relpath(p, ROOT), "mb": round(size / 1048576, 1)})
            # __pycache__ is excluded from the duplicate check: .pyc artifacts
            # regenerate on every test run, so counting them means the guard
            # fires on a clean tree and gets switched off. It has its own
            # counter and its own compaction kind instead.
            if not is_cache:
                dup[name].add(dirpath)
                if is_src:
                    dup_src[name].add(dirpath)
    return {
        "generated": _now(),
        "files": files,
        "mb": round(sum(per_top_bytes.values()) / 1048576, 1),
        # __pycache__ is a byte-cache, not a structural path. It is (re)created
        # by any test run -- including a root-level one next to conftest.py --
        # so listing it here makes the guard fire on a clean tree for a reason
        # that says nothing about how the project is organised. It keeps its
        # own `pycache_files` counter and its own compaction kind instead.
        "top_level": sorted(t for t in per_top_files if t != "__pycache__"),
        "top_table": [[t, per_top_files[t], round(per_top_bytes[t] / 1048576, 1)]
                      for t, _ in per_top_files.most_common()],
        "large_files": sorted(large, key=lambda r: -r["mb"]),
        "duplicate_basenames": sum(1 for v in dup.values() if len(v) > 1),
        "duplicates_src": sorted(n for n, v in dup_src.items() if len(v) > 1),
        "pycache_files": cache_files,
    }


def compare(current: dict, base: dict) -> list[str]:
    """New-vs-old. Only regressions count -- see the module docstring."""
    problems: list[str] = []
    for t in current["top_level"]:
        if t not in set(base.get("top_level", [])):
            problems.append("new top-level path: %s" % t)
    known_large = {r["path"] for r in base.get("large_files", [])}
    for r in current["large_files"]:
        if r["path"] not in known_large:
            problems.append("new file over %d MB: %s (%.1f MB)" % (LARGE_MB, r["path"], r["mb"]))
    known_dups = set(base.get("duplicates_src", []))
    for name in current["duplicates_src"]:
        if name not in known_dups:
            problems.append("new duplicate basename under src/: %s" % name)
    return problems


def _move(src: Path, dest: Path, execute: bool, manifest: list, kind: str) -> tuple[int, int]:
    """Move a path into the cold store. Returns (files, bytes) affected."""
    if not src.exists():
        return 0, 0
    n = 1 if src.is_file() else sum(len(f) for _, _, f in os.walk(src))
    b = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(src)
            for f in fns) if src.is_dir() else src.stat().st_size
    manifest.append({"kind": kind, "from": str(src.relative_to(ROOT)),
                     "to": str(dest.relative_to(ROOT)), "files": n,
                     "mb": round(b / 1048576, 1), "moved": bool(execute)})
    if execute:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(dest.name + "-" + datetime.now().strftime("%H%M%S"))
        shutil.move(str(src), str(dest))
    return n, b


def compact(kinds: Sequence[str], execute: bool) -> dict:
    manifest: list[dict] = []
    killed_files = killed_mb = 0
    for kind in kinds:
        if kind == "pycache":
            for dp, dns, _ in os.walk(ROOT):
                if "__pycache__" in dns:
                    target = Path(dp) / "__pycache__"
                    n = len(list(target.rglob("*")))
                    b = sum(os.path.getsize(p) for p in target.rglob("*") if p.is_file())
                    manifest.append({"kind": kind, "from": str(target.relative_to(ROOT)),
                                     "to": "(deleted)", "files": n,
                                     "mb": round(b / 1048576, 1), "moved": bool(execute)})
                    if execute:
                        shutil.rmtree(target, ignore_errors=True)
                    killed_files += n
                    killed_mb += b
        elif kind == "graph-snapshots":
            gdir = ROOT / "graphify-out"
            if gdir.is_dir():
                for child in sorted(gdir.iterdir()):
                    dated = child.is_dir() and len(child.name) == 10 and child.name[4] == "-"
                    stale = child.is_file() and child.name.startswith("graph.stale-")
                    if dated or stale:
                        n, b = _move(child, COLD / "graph-snapshots" / child.name,
                                     execute, manifest, kind)
                        killed_files += n
                        killed_mb += b
        elif kind == "nuitka":
            for name in ("nuitka_build_backup", "build"):
                n, b = _move(ROOT / name, COLD / name, execute, manifest, kind)
                killed_files += n
                killed_mb += b
        elif kind == "archives":
            for name in ("archive", "archived_unified"):
                n, b = _move(ROOT / name, ROOT / "archived" / "legacy" / name,
                             execute, manifest, kind)
                killed_files += n
                killed_mb += b
        else:
            raise SystemExit("unknown kind: %s" % kind)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"generated": _now(), "executed": bool(execute),
                                    "entries": manifest}, indent=1), encoding="utf-8")
    return {"entries": manifest, "files": killed_files,
            "mb": round(killed_mb / 1048576, 1), "manifest": str(MANIFEST.relative_to(ROOT))}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", action="store_true", help="record the current state")
    ap.add_argument("--check", action="store_true", help="exit 1 on regression")
    ap.add_argument("--kinds", default="", help="pycache,graph-snapshots,nuitka,archives")
    ap.add_argument("--execute", action="store_true", help="actually move/delete")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    started = time.time()
    if args.kinds:
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
        result = compact(kinds, args.execute)
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        print("compaction %s: %d file(s), %.1f MB"
              % ("executed" if args.execute else "PLANNED (pass --execute)",
                 result["files"], result["mb"]))
        for e in result["entries"]:
            print("  %-8s %-46s %8d files %9.1f MB %s"
                  % (e["kind"], e["from"], e["files"], e["mb"],
                     "moved" if e["moved"] else "planned"))
        print("manifest: %s" % result["manifest"])
        return 0

    current = scan()
    base = {}
    if BASELINE.is_file():
        try:
            base = json.loads(BASELINE.read_text(encoding="utf-8"))
        except ValueError:
            base = {}
    if args.baseline:
        BASELINE.write_text(json.dumps({**current, "top_table": current["top_table"]},
                                       indent=1), encoding="utf-8")
        print("baseline recorded: %d files, %.1f MB, %d top-level path(s) -> %s"
              % (current["files"], current["mb"], len(current["top_level"]),
                 BASELINE.relative_to(ROOT)))
        return 0

    problems = compare(current, base) if base else []
    if args.json:
        print(json.dumps({"scan": current, "problems": problems,
                          "elapsed_s": round(time.time() - started, 2)}, indent=2))
        return 1 if (problems and args.check) else 0

    print("%-30s %8s %10s" % ("TOP-LEVEL", "FILES", "MB"))
    for top, n, mb in current["top_table"][:18]:
        print("%-30s %8d %10.1f" % (top, n, mb))
    print("\nfiles       : %d" % current["files"])
    print("total       : %.1f MB" % current["mb"])
    print("top-level   : %d" % len(current["top_level"]))
    print("over %2d MB  : %d" % (LARGE_MB, len(current["large_files"])))
    print("duplicate bn: %d (%d under src/)" % (current["duplicate_basenames"],
                                                len(current["duplicates_src"])))
    print("__pycache__ : %d file(s)" % current["pycache_files"])
    for r in current["large_files"][:5]:
        print("   %8.1f MB  %s" % (r["mb"], r["path"]))

    if not base:
        print("\nno baseline yet -- run --baseline to enable --check")
        return 0
    print()
    if problems:
        for p in problems:
            print("REGRESSION: %s" % p)
        print("\nFAIL: %d regression(s) vs %s"
              % (len(problems), BASELINE.relative_to(ROOT)))
        return 1 if args.check else 0
    print("OK: no regression vs %s" % BASELINE.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
