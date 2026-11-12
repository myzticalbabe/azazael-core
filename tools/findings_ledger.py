#!/usr/bin/env python3
"""findings_ledger.py -- Aether A-3: close the loop, audit -> evolve -> re-audit.

deep_dive reports; nothing acts, and the next run cannot tell a new failure
from a year-old one. This tool keeps `logs/aether_findings.jsonl`: every
failing leg becomes an entry, and every run re-verifies it -- new (first
seen), persisting (still failing), fixed (the leg passes again; kept, not
deleted, because a fix that relapses reads as new without the history).

The ledger is advisory (exit 0 whenever it runs): a red gate on day one is a
gate someone switches off. Exit 2 only when there is no report to ledger.

Entry: {id, leg, detail, first_seen, last_seen, status}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILING = {"error", "timeout"}


def ledger(report_path: Path, ledger_path: Path) -> dict:
    """Merge one report into the ledger; return transition counts."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"ran": False}
    now = report.get("generated", "?")
    legs = report.get("legs") if isinstance(report.get("legs"), list) else []
    failing = {}
    passing = set()
    for leg in legs:
        if not isinstance(leg, dict) or not leg.get("leg"):
            continue
        if str(leg.get("status", "")).lower() in FAILING:
            failing[leg["leg"]] = str(leg.get("detail", ""))[:200]
        else:
            passing.add(leg["leg"])
    entries, skipped = {}, 0
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                skipped += 1  # malformed history is counted, never mourned silently
                continue
            if rec.get("id"):
                entries[rec["id"]] = rec
    new, persisting, fixed = 0, 0, 0
    for name, detail in failing.items():
        if name in entries and entries[name].get("status") in ("new", "persisting"):
            entries[name]["status"] = "persisting"
            entries[name]["last_seen"] = now
            entries[name]["detail"] = detail
            persisting += 1
        else:
            entries[name] = {"id": name, "leg": name, "detail": detail,
                             "first_seen": now, "last_seen": now,
                             "status": "new"}
            new += 1
    for name, rec in entries.items():
        if rec.get("status") in ("new", "persisting") and name in passing:
            rec["status"] = "fixed"
            rec["last_seen"] = now
            fixed += 1
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "w", encoding="utf-8") as fh:
        for rec in entries.values():
            fh.write(json.dumps(rec) + "\n")
    return {"ran": True, "new": new, "persisting": persisting,
            "fixed": fixed, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(ROOT / "logs" / "deep_dive.json"))
    ap.add_argument("--ledger",
                    default=str(ROOT / "logs" / "aether_findings.jsonl"))
    args = ap.parse_args(argv)
    out = ledger(Path(args.report), Path(args.ledger))
    if not out["ran"]:
        print("findings_ledger: BLIND -- no report to ledger")
        return 2
    print("findings_ledger: new=%d persisting=%d fixed=%d skipped=%d"
          % (out["new"], out["persisting"], out["fixed"], out["skipped"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
