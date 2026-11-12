#!/usr/bin/env python
"""Re-dispose `wired` ledger rows that only ever had a bare token behind them.

Why this exists. PF16's finding path used a FLAT name set, so `__init__` in 25
files or `execute` in 117 files counted as a caller. On 2026-10-31 the probe was
made consistent with its own carry path (module-qualified links), which turned
134 `wired` rows into measured PF16 findings -- a status that asserts a caller
the code cannot show.

The disposition is derived, not chosen per row:
  * the name appears as an identifier-like STRING in production code -> the row
    is `dispatched` (string-dispatched, which is exactly what that status is
    for), and the string locations are recorded as evidence;
  * no string reference anywhere                                      -> the row
    is `accepted_gap`, with the definition location and the measurement that
    produced the verdict.

Neither branch invents reachability. `--apply` writes; the default is a report.

Usage:
    python tools/redispose_bare_token_wired.py [--apply] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "NEURAL_MESH_WIRE_OR_CUT.json"
sys.path.insert(0, str(REPO / "tools"))

import mesh_function_preflight as mfp  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="only report first N")
    args = ap.parse_args()

    # A generic method name appearing as a string is NOT dispatch evidence: the
    # first dry run "found" string references for `__init__` in
    # scripts/_evo_graph_build.py, which is an AST tool that writes the literal
    # '__init__' into a graph -- the same generic-token defect one level down.
    # For those names the evidence must be the CLASS name as a string, which is
    # what a registry/dispatch table actually keys on.
    generic = {"__init__", "__repr__", "__str__", "__call__", "__enter__",
               "__exit__", "execute", "run", "main", "get", "set", "update",
               "process", "handle", "start", "stop", "close", "tick",
               "step", "reset", "validate", "load", "save"}

    funcs, _ = mfp.build(None)
    fired = {f.label: f for f in funcs if "PF16" in f.failures}
    strings = mfp.string_references()
    table = json.loads(LEDGER.read_text(encoding="utf-8"))
    declared = table.get("declared") or {}

    todo = [(k, v) for k, v in sorted(declared.items())
            if str(v.get("status")) == "wired" and k in fired]
    print(f"wired rows with no module-linked caller: {len(todo)}")
    to_dispatch, to_gap = [], []
    for key, entry in todo:
        qual = str(entry.get("qualname") or key.partition(":")[2])
        head, _, name = qual.rpartition(".")
        if name in generic and head:
            # dispatch of a whole class: the class name is the distinctive token
            classname = head.rsplit(".", 1)[-1]
            refs = [r for r in (strings.get(classname) or [])
                    if not r.startswith("tests/")]
        else:
            refs = [r for r in (strings.get(name) or [])
                    if not r.startswith("tests/")]
        if refs:
            to_dispatch.append((key, refs))
        else:
            to_gap.append(key)
    print(f"  -> dispatched (string reference exists): {len(to_dispatch)}")
    print(f"  -> accepted_gap (no reference anywhere): {len(to_gap)}")
    for key, refs in to_dispatch[:5]:
        print(f"     {key}  strings={refs[:2]}")
    for key in to_gap[:5]:
        print(f"     {key}  no string reference")

    if not args.apply:
        print("\ndry run -- re-run with --apply to write the ledger")
        return 0

    for key, refs in to_dispatch:
        entry = declared[key]
        entry["status"] = "dispatched"
        entry["reason"] = (
            "re-disposed 2026-10-31: the only production reference is a string "
            "literal, so no module-linked caller can exist; string dispatch is "
            "recorded in evidence")
        entry["evidence"] = refs[:3]
        for stale in ("link_evidence", "link_gap", "carried", "carry_evidence"):
            entry.pop(stale, None)
    for key in to_gap:
        entry = declared[key]
        fn = fired[key]
        entry["status"] = "accepted_gap"
        entry["reason"] = (
            "re-disposed 2026-10-31: measured PF16 finding with no module-linked "
            "caller and no string reference anywhere; the earlier `wired` status "
            "rested on a bare token in a file that does not import this module")
        entry["evidence"] = [f"{fn.path}:{fn.lineno}"]
        for stale in ("link_evidence", "link_gap", "carried", "carry_evidence"):
            entry.pop(stale, None)

    table["declared"] = declared
    LEDGER.write_text(json.dumps(table, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {LEDGER.name}: dispatched+{len(to_dispatch)}, accepted_gap+{len(to_gap)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
