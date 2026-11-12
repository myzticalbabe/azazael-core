#!/usr/bin/env python3
"""technique_evidence.py -- import-binding evidence for technique rows.

Word-match counting fabricates adoption (HTML/JS observers, prose, phase
names, other-class methods, same-word-different-API like tpe.observe vs
structured_action.observe). This tool gathers EVIDENCE with binding proof
and leaves the verdict to a human: for each symbol it reports import lines,
call lines, def lines, and excluded hits (tests, technique_wiring exercise
harness, prose/docstring, non-py, backups) separately -- never one number.

Alias-aware: case-insensitive substring search (catches `as _X` aliases and
guarded/dispatcher indirection that word-rg misses); each hit is then
classified so aliases are visible, not silently counted or dropped.

Usage:
    python tools/technique_evidence.py --ep EP44,EP122
    python tools/technique_evidence.py --all-partial [--census PATH]
    python tools/technique_evidence.py --ep EP44 --json OUT.json

Stdlib-only. All subprocess reads use encoding="utf-8", errors="replace".
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CENSUS = ROOT / "state" / "adoption_census.json"


def _rg(pat):
    r = subprocess.run(
        ["rg", "--no-heading", "-n", "-i", pat, "src/", "tools/",
         "scripts/", "tests/"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=300)
    return r.stdout.splitlines()


def _classify(path, text):
    """Bucket one hit line. Returns (bucket, detail)."""
    p = path.replace("\\", "/")
    base = p.split("/")[-1]
    if "/tests/" in p or base.startswith("test_"):
        return "tests", None
    if "technique_wiring/" in p:
        return "wiring", None
    if p.endswith((".html", ".md", ".pre_gate_backup")):
        return "nonpy", None
    if not p.endswith(".py"):
        return "nonpy", None
    t = text.strip()
    if t.startswith("#") or t.startswith(('"""', "'''", "*", "//")):
        return "prose", None
    if re.match(r"(async\s+)?def\s", t):
        return "defline", None
    if t.startswith("import ") or t.startswith("from "):
        return "import", None
    return "call", None


def evidence_for(symbol):
    """All hits for one symbol, bucketed. Alias-aware (case-insensitive)."""
    buckets = {"import": [], "call": [], "defline": [], "tests": [],
               "wiring": [], "prose": [], "nonpy": []}
    for line in _rg(re.escape(symbol)):
        m = re.match(r"(.+?):(\d+):(.*)", line)
        if not m:
            continue
        bucket, _ = _classify(m.group(1), m.group(3))
        loc = m.group(1).replace("\\", "/") + ":" + m.group(2)
        buckets[bucket].append((loc, m.group(3).strip()[:90]))
    return buckets


def _p(*parts):
    """Console-safe print: the evidence corpus contains non-ASCII (emoji,
    box-drawing) that crashes cp1252 consoles mid-report -- a silent
    truncation of the very evidence being gathered. Fall back to the
    CONSOLE encoding with replacement (never UTF-8 round-trip: that
    preserves the unprintable character and fails again)."""
    text = " ".join(str(p) for p in parts)
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, "replace").decode(enc))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ep", default="")
    ap.add_argument("--all-partial", action="store_true")
    ap.add_argument("--census", default=str(DEFAULT_CENSUS))
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    census = json.loads(Path(args.census).read_text(encoding="utf-8"))
    rows = {r.get("ep"): r for r in census["results"]}
    if args.all_partial:
        eps = [ep for ep, r in rows.items()
               if r.get("coverage") == "PARTIAL"]
    else:
        eps = [e for e in args.ep.split(",") if e.strip()]
    report = {}
    for ep in eps:
        row = rows.get(ep)
        if row is None:
            _p("! unknown EP: %s" % ep)
            continue
        syms = row.get("symbols") or ([row.get("symbol")]
                                      if row.get("symbol") else [])
        entry = {"name": row.get("name"), "coverage": row.get("coverage"),
                 "verdict": row.get("verdict"),
                 "n_production": row.get("n_production"),
                 "symbols": {}}
        for sym in syms:
            ident = str(sym).split("::")[-1].strip().split()
            ident = ident[-1] if ident else ""
            if not ident:
                continue
            entry["symbols"][ident] = evidence_for(ident)
        report[ep] = entry
    for ep, entry in report.items():
        _p("===== %s | %s | cov:%s verdict:%s census-n:%s" % (
            ep, entry["name"], entry["coverage"], entry["verdict"],
            entry["n_production"]))
        for sym, buckets in entry["symbols"].items():
            _p("  [%s] import:%d call:%d def:%d tests:%d wiring:%d "
               "prose:%d nonpy:%d" % (
                      sym, len(buckets["import"]), len(buckets["call"]),
                      len(buckets["defline"]), len(buckets["tests"]),
                      len(buckets["wiring"]), len(buckets["prose"]),
                      len(buckets["nonpy"])))
            for loc, text in (buckets["import"] + buckets["call"])[:6]:
                _p("    ", loc, "|", text)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1),
                                   encoding="utf-8")
        _p("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
