#!/usr/bin/env python3
"""worm_census.py -- forensic purpose audit for every worm (read-only).

For each worm class found in src/CORE/WORMS (+ registry): docstring?
registered? executed in last 30d (outcome tap)? referenced anywhere
(imports/name mentions)? Classifies:

  LIVE       executed recently AND referenced
  DORMANT    referenced but never executed (dead wiring or standby)
  ORPHAN     neither referenced nor executed (purge candidate)
  GHOST      executed but NOT in registry/file (stale tap rows, renames)
  UNDOC      no docstring (purpose unrecorded, regardless of class)

Usage: python scripts/worm_census.py [--json] [--days N]
Exit 0 always; prints report.
"""
from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

WORMS_DIR = HERE.parent / "src" / "CORE" / "WORMS"
DB = Path.home() / ".azazael" / "memory_store.db"


def _worm_classes() -> dict[str, dict]:
    """name -> {file, doc, category} via AST (no imports, no side effects)."""
    out: dict[str, dict] = {}
    for fp in sorted(WORMS_DIR.rglob("*.py")):
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            n = node.name
            if "worm" not in n.lower():
                continue
            doc = (ast.get_docstring(node) or "").strip()
            out.setdefault(n, {"file": str(fp.relative_to(HERE.parent)),
                               "doc": doc[:200],
                               "has_doc": bool(doc),
                               "category": "",
                               "dup_files": []})
            if out[n]["file"] != str(fp.relative_to(HERE.parent)):
                out[n]["dup_files"].append(str(fp.relative_to(HERE.parent)))
    return out


def _registry() -> dict[str, dict]:
    try:
        from CORE.worm_registry import WORM_REGISTRY, discover_worms
        discover_worms()
        return dict(WORM_REGISTRY)
    except Exception as e:
        return {"__error__": str(e)[:120]}


def _tap(days: int) -> tuple[dict[str, int], dict[str, str]]:
    """worm -> (count, max_ts) over recent outcome rows, both DBs."""
    counts: dict[str, int] = defaultdict(int)
    latest: dict[str, str] = {}
    dbs = [DB]
    try:
        from CORE.direct_memory import _get_telemetry_db_path
        tdb = _get_telemetry_db_path()
        if tdb != DB:
            dbs.append(Path(tdb))
    except Exception:
        pass
    for db in dbs:
        if not Path(db).is_file():
            continue
        try:
            c = sqlite3.connect(str(db), timeout=30)
            rows = c.execute(
                "SELECT json_extract(data,'$.worm'), COUNT(*), MAX(created_at)"
                " FROM memories WHERE event_type='clusterwormz_worm_executed'"
                " AND created_at > datetime('now', ?)"
                " GROUP BY 1", ("-%d days" % days,)).fetchall()
            c.close()
        except Exception:
            continue
        for worm, n, ts in rows:
            if not worm:
                continue
            counts[worm] += n
            if ts and ts > latest.get(worm, ""):
                latest[worm] = ts
    return dict(counts), latest


def _reference_index() -> dict[str, int]:
    """Rough inbound-reference counts per worm name across code (not logs)."""
    idx: dict[str, int] = defaultdict(int)
    roots = [HERE.parent / "src", HERE, HERE.parent / "tools"]
    files = []
    for r in roots:
        if r.is_dir():
            files.extend(r.rglob("*.py"))
    blobs = []
    for fp in files:
        try:
            if fp.stat().st_size > 2_000_000:
                continue
            blobs.append((str(fp), fp.read_text(encoding="utf-8",
                                                errors="replace")))
        except Exception:
            continue
    names = set()
    for _, text in blobs:
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9]*(?:Worm|WORM|worm))\b", text):
            names.add(m.group(1))
    for _, text in blobs:
        for n in names:
            c = text.count(n)
            if c:
                idx[n] += c
    return dict(idx), names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="worm_census")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)

    classes = _worm_classes()
    reg = _registry()
    reg_names = {k for k in reg if not k.startswith("__")}
    counts, latest = _tap(args.days)
    refs, _ = _reference_index()

    rows = []
    for name, info in sorted(classes.items()):
        n = counts.get(name, 0)
        ref = refs.get(name, 0) - 1  # minus own definition file hit (approx)
        in_reg = name in reg_names
        if n > 0 and ref > 0:
            cls = "LIVE"
        elif n > 0:
            cls = "LIVE-UNREF"
        elif ref > 0:
            cls = "DORMANT"
        else:
            cls = "ORPHAN"
        rows.append({"worm": name, "class": cls, "runs": n,
                     "last": latest.get(name, ""), "refs": max(ref, 0),
                     "registered": in_reg, "doc": info["has_doc"],
                     "file": info["file"]})
    ghosts = sorted(set(counts) - set(classes) - reg_names)

    summary: dict[str, int] = defaultdict(int)
    for r in rows:
        summary[r["class"]] += 1
    summary["GHOST"] = len(ghosts)
    summary["UNDOC"] = sum(1 for r in rows if not r["doc"])
    summary["UNREGISTERED"] = sum(1 for r in rows if not r["registered"])

    rep = {"summary": dict(summary), "ghosts": ghosts, "rows": rows}
    if args.json:
        print(json.dumps(rep, indent=1, default=str))
        return 0
    print("=== Worm purpose census (%d classes, %dd window) ===" % (len(rows), args.days))
    for k in ("LIVE", "LIVE-UNREF", "DORMANT", "ORPHAN"):
        print("  %-10s %d" % (k, summary.get(k, 0)))
    print("  GHOST(exec,unknown): %d  UNDOC: %d  UNREGISTERED: %d"
          % (len(ghosts), summary["UNDOC"], summary["UNREGISTERED"]))
    for bucket in ("ORPHAN", "DORMANT"):
        names = [r["worm"] for r in rows if r["class"] == bucket]
        if names:
            print("\n-- %s (%d) --" % (bucket, len(names)))
            for r in rows:
                if r["class"] == bucket:
                    print("  %s  refs=%d reg=%s doc=%s  %s"
                          % (r["worm"], r["refs"], r["registered"], r["doc"], r["file"]))
    if ghosts:
        print("\n-- GHOST (%d, top by runs) --" % len(ghosts))
        for g in sorted(ghosts, key=lambda x: -counts.get(x, 0))[:15]:
            print("  %s  runs=%d last=%s" % (g, counts.get(g, 0), latest.get(g, "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
