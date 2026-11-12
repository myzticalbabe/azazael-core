#!/usr/bin/env python3
"""cast_verdict.py -- Aether A-5: the verdict in every form it is owed.

A run's verdict today is prose on stdout plus an exit code -- unqueryable
history. Drift between sessions is then a feeling, not a number. This tool
reads `logs/deep_dive.json` and casts it three ways:

  machine   the report itself (already JSON, left untouched)
  human     `logs/verdict.md` -- one chronicle row per leg, rewritten each run
  history   `logs/verdict_history.jsonl` -- one row appended per run, so the
            verdict's drift across sessions is a query, not a recollection

Exit: 0 cast, 2 no report to cast (never worded as a pass).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cast(report_path: Path, md_path: Path, history_path: Path) -> dict:
    """Read the report, rewrite the chronicle, append the history row."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"cast": False}
    legs = report.get("legs") if isinstance(report.get("legs"), list) else []
    lines = ["# Verdict chronicle",
             "",
             "generated: %s   verdict: %s   elapsed_s: %s" % (
                 report.get("generated", "?"), report.get("verdict", "?"),
                 report.get("elapsed_s", "?")),
             ""]
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        lines.append("- %-8s %-18s %6ss  %s" % (
            leg.get("status", "?"), leg.get("leg", "?"),
            leg.get("seconds", "?"), (leg.get("detail") or "")[:160]))
    lines.append("")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    row = {"ts": report.get("generated"), "verdict": report.get("verdict"),
           "legs": {l.get("leg"): l.get("status") for l in legs
                    if isinstance(l, dict) and l.get("leg")}}
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return {"cast": True, "verdict": row["verdict"], "legs": len(row["legs"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(ROOT / "logs" / "deep_dive.json"))
    ap.add_argument("--md", default=str(ROOT / "logs" / "verdict.md"))
    ap.add_argument("--history",
                    default=str(ROOT / "logs" / "verdict_history.jsonl"))
    args = ap.parse_args(argv)
    out = cast(Path(args.report), Path(args.md), Path(args.history))
    if not out["cast"]:
        print("cast_verdict: BLIND -- no report to cast")
        return 2
    print("cast_verdict: %s over %d legs -> %s + history row"
          % (out["verdict"], out["legs"], args.md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
