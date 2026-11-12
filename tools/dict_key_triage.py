#!/usr/bin/env python3
# coding: utf-8
"""Close dict-key findings by declaration, the way wire_or_cut already does.

Why a declaration file and not a smarter detector
-------------------------------------------------
`dict_key_contract` reports 3,464 candidate asymmetries across 309 files, and
no single file dominates -- the worst holds 3.6%. There is no hot spot to
fix and no pattern that separates the real defects from the rest: the most
common keys are `error` (64), `success` (38), `summary` (24), and each of
those is a genuine defect in one function and a correct result-dict key in
another.

So the number cannot be reduced by better detection. It can only be
*disposed of*, one at a time, by someone who knows what the function is for.
This tool provides that mechanism and, more importantly, the invariant that
keeps it honest:

  - a finding with no declaration is **undeclared** (fail)
  - a declaration with no finding is **stale** (fail)

Stale is the half that matters. Without it, a declaration file only ever
grows: fixing a key and leaving the declaration behind would let the file
quietly accumulate entries for defects that no longer exist, and a green
test over a growing file looks identical to a green test over a shrinking
problem.

The `wire_or_cut` artifact already uses exactly this shape and its test
already passes, so this is a proven pattern in this repo rather than a new
idea. Reusing it means the next reader has one rule to learn.

Disposition vocabulary
----------------------
    fix_required   a real defect; wire or correct the key. Blocks the gate.
    false_positive the census is wrong here; say why. Blocks until justified.
    accepted       a legitimate asymmetry -- a read-only config dict, a dict
                   built for a caller, a protocol boundary. Does not block.

`accepted` is the one that can rot, so it carries a mandatory `reason` and
an optional `review_after` date. An `accepted` entry past its review date is
reported as `overdue` and blocks.

Usage
-----
    python tools/dict_key_triage.py --init          # write a starter file
    python tools/dict_key_triage.py                 # report undeclared/stale
    python tools/dict_key_triage.py --check         # exit 1 if any block
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRIAGE = ROOT / "DICT_KEY_TRIAGE.json"
CONTRACT = ROOT / "tools" / "dict_key_contract.py"

DISPOSITIONS = ("fix_required", "false_positive", "accepted")
BLOCKING = ("fix_required", "false_positive", "overdue")


def today() -> str:
    return _dt.date.today().isoformat()


def key_for(f: dict[str, Any]) -> str:
    """Stable identity: file::scope::dict::key.

    Scope and dict are both in the key because the census is per
    (function, dict). Two different functions can hold a local `result` with
    genuinely different keys, and collapsing them would let one declaration
    excuse the other.
    """
    scope, _, owner = f["dict"].partition("|")
    return f"{f['file']}::{scope}::{owner}::{f['key']}"


def run_contract(scope: list[str]) -> dict[str, Any]:
    cmd = [sys.executable, str(CONTRACT), "--json", "--scope", *scope]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    if r.returncode == 2:
        raise SystemExit("dict_key_contract is INCONCLUSIVE: %s" % r.stderr)
    return json.loads(r.stdout or "{}")


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "generated_at": None, "entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def analyse(contract: dict[str, Any], triage: dict[str, Any]) -> dict[str, Any]:
    findings = {key_for(f): f for f in contract.get("findings", [])}
    entries = triage.get("entries", {})

    undeclared = sorted(set(findings) - set(entries))
    stale = sorted(set(entries) - set(findings))

    bad_status = sorted(
        k for k, v in entries.items()
        if v.get("status") not in DISPOSITIONS)
    missing_reason = sorted(
        k for k, v in entries.items()
        if v.get("status") == "accepted" and not v.get("reason", "").strip())
    overdue = sorted(
        k for k, v in entries.items()
        if v.get("status") == "accepted" and v.get("review_after")
        and v["review_after"] < today())

    blocking = sorted(set(bad_status) | set(missing_reason) | set(overdue))
    return {
        "findings": len(findings),
        "declared": len(entries),
        "undeclared": undeclared,
        "stale": stale,
        "bad_status": bad_status,
        "missing_reason": missing_reason,
        "overdue": overdue,
        "blocking": blocking,
        "closed": len(entries) - len(blocking),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Close dict-key findings by declaration.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--triage", default=str(TRIAGE))
    ap.add_argument("--scope", nargs="*", default=["src"])
    ap.add_argument("--init", action="store_true",
                    help="write a starter file with every finding undeclared")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any finding is undeclared or any entry is stale")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.triage)
    contract = run_contract(args.scope)
    triage = load(path)

    if args.init:
        entries = triage.get("entries", {})
        added = 0
        for f in contract.get("findings", []):
            k = key_for(f)
            if k not in entries:
                entries[k] = {
                    "file": f["file"], "scope": f["dict"].partition("|")[0],
                    "dict": f["dict"].partition("|")[2], "key": f["key"],
                    "line": f["line"], "kind": f["kind"],
                    "status": "accepted", "reason": "",
                    "review_after": "",
                }
                added += 1
        triage["version"] = 1
        triage["generated_at"] = today()
        triage["entries"] = entries
        save(path, triage)
        print("wrote %s: %d new entries (all `accepted`, all need a reason)"
              % (path.name, added))
        print("Every `accepted` entry without a reason blocks the gate. That is "
              "deliberate:\n3,464 is a debt count, not a defect count, and a "
              "blanket dismissal\nis the failure mode this file exists to "
              "prevent.")
        return 0

    rep = analyse(contract, triage)

    if args.json:
        print(json.dumps(rep, indent=2))
        return 1 if (rep["undeclared"] or rep["stale"]) else 0

    print("dict-key findings: %d | declared: %d | closed: %d"
          % (rep["findings"], rep["declared"], rep["closed"]))
    if rep["undeclared"]:
        print("\nUNDECLARED (%d) -- each needs a disposition:" % len(rep["undeclared"]))
        for k in rep["undeclared"][:20]:
            print("   %s" % k)
        if len(rep["undeclared"]) > 20:
            print("   ... %d more" % (len(rep["undeclared"]) - 20))
    if rep["stale"]:
        print("\nSTALE (%d) -- declared but no longer a finding. These are how "
              "a triage\nfile rots: fix the key, leave the entry, and the file "
              "grows forever."
              % len(rep["stale"]))
        for k in rep["stale"][:20]:
            print("   %s" % k)
    for label, key in (("INVALID STATUS", "bad_status"),
                       ("MISSING REASON", "missing_reason"),
                       ("OVERDUE REVIEW", "overdue")):
        rows = rep[key]
        if rows:
            print("\n%s (%d):" % (label, len(rows)))
            for k in rows[:20]:
                print("   %s" % k)

    if not args.check:
        return 0
    if rep["undeclared"] or rep["stale"] or rep["blocking"]:
        print("\nFAIL: %d undeclared, %d stale, %d blocking"
              % (len(rep["undeclared"]), len(rep["stale"]), len(rep["blocking"])))
        return 1
    print("\nOK: every finding is declared and every declaration is current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
