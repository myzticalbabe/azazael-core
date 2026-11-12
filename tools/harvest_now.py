#!/usr/bin/env python3
"""harvest_now — write the error row at the moment it happens, in one command.

WHY THIS EXISTS
---------------
The ERROR CAPTURE mandate (#714/#716) says every error, near-miss and falsification
is recorded at ENCOUNTER time, not at session end, because reconstruction loses
detail and over-weights whatever is still salient. On 2026-10-17 the clause was
missing from all fourteen minted tasks; the lane sat at 7,523 rows while ~17
distinct errors accumulated and were recoverable only retrospectively.

A rule that requires the agent to remember a schema at the worst moment is a rule
that fails at the worst moment. This tool makes the write one command with three
required flags, validates the schema at the door, and appends atomically:

  state/bake_harvest_<agent>_<YYYY-MM-DD>.jsonl
  {"source", "item_id", "title", "body", "kind"}

`body` must be >= 40 characters — a row that says "broke" is not a record.

USAGE
-----
  python tools/harvest_now.py --agent freebuff --kind error_fix \
      --title "reset orphaned 15 commits" --body "..." [--source tools/x.py] [--item 47697]
  python tools/harvest_now.py --agent freebuff --validate   # check every lane today
  python tools/harvest_now.py --agent freebuff --tail 3

KINDS: error_fix | lesson | mandate | sop_rule | violation | anti_pattern

EXIT CODES
----------
  0  the row was appended (and re-read back from disk)
  1  validation found problems in an existing lane file
  2  the row was refused — nothing written
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / "state"
KINDS = ("error_fix", "lesson", "mandate", "sop_rule", "violation", "anti_pattern")
MIN_BODY = 40


def lane_path(agent: str, day: str) -> Path:
    return STATE / f"bake_harvest_{agent}_{day}.jsonl"


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def append_row(agent: str, kind: str, title: str, body: str, source: str,
               item_id: str) -> int:
    problems = []
    if kind not in KINDS:
        problems.append(f"kind '{kind}' is not one of {KINDS}")
    if len(body.strip()) < MIN_BODY:
        problems.append(f"body is {len(body.strip())} chars; the mandate requires "
                        f">= {MIN_BODY} so the row still explains itself later")
    if not title.strip():
        problems.append("a row with no title cannot be found again")
    if problems:
        for p in problems:
            print(f"harvest_now: REFUSED — {p}", file=sys.stderr)
        return 2

    day = today()
    path = lane_path(agent, day)
    row = {
        "source": source or "harvest_now",
        "item_id": item_id or datetime.now(timezone.utc).strftime("%H%M%S"),
        "title": title.strip(),
        "body": body.strip(),
        "kind": kind,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"harvest_now: could not append ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"harvest_now: appended but could not read back ({type(exc).__name__})",
              file=sys.stderr)
        return 1
    print(f"harvest_now: row {len(lines)} written to {path.name} "
          f"[{kind}] {row['title'][:70]}")
    return 0


def validate(agent: str, all_dates: bool = False) -> int:
    """Check schema compliance.

    Scoped to TODAY's lane by default, because the lane carries history: the
    2026-10-19 files hold 27 rows written before `item_id` was required, and a
    validator that fails on them fails forever, which is how a gate gets
    switched off and stops validating anything. `--all-dates` prints the
    history; the default answers for the rows a commit would actually merge.
    """
    if all_dates:
        files = sorted(STATE.glob(f"bake_harvest_{agent}_*.jsonl"))
    else:
        files = [p for p in (lane_path(agent, today()),) if p.exists()]
    if not files:
        print(f"harvest_now: no lane file for {agent} dated {today()} — nothing recorded today")
        return 0
    bad = 0
    for path in files:
        rows = 0
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  {path.name}:{number} NOT JSON ({exc})")
                bad += 1
                continue
            missing = [k for k in ("source", "item_id", "title", "body", "kind")
                       if not str(row.get(k) or "").strip()]
            if missing:
                print(f"  {path.name}:{number} missing {missing}")
                bad += 1
            elif len(str(row["body"])) < MIN_BODY:
                print(f"  {path.name}:{number} body {len(str(row['body']))} < {MIN_BODY}")
                bad += 1
            elif row["kind"] not in KINDS:
                print(f"  {path.name}:{number} kind '{row['kind']}' unknown")
                bad += 1
        print(f"  {path.name}: {rows} row(s)")
    if bad:
        scope = "all dates" if all_dates else today()
        print(f"VERDICT: {bad} defective row(s) in {scope} — merge_bake_lane.py would report drops")
        return 1
    scope = "all dates" if all_dates else today()
    print(f"VERDICT: every row in {scope} satisfies the schema")
    return 0


def tail(agent: str, count: int) -> int:
    files = sorted(STATE.glob(f"bake_harvest_{agent}_*.jsonl"))
    if not files:
        print(f"harvest_now: no lane files for {agent}")
        return 0
    lines = files[-1].read_text(encoding="utf-8").splitlines()
    for line in lines[-count:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"  UNREADABLE ({exc}): {line[:80]}")
            continue
        print(f"  [{row.get('kind')}] {row.get('title')} — {str(row.get('body'))[:90]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="harvest_now", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", required=True, help="lane name, e.g. freebuff")
    ap.add_argument("--kind", default="error_fix", help=f"one of {KINDS}")
    ap.add_argument("--title", default="")
    ap.add_argument("--body", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--item", dest="item_id", default="")
    ap.add_argument("--validate", action="store_true", help="check today's lane file")
    ap.add_argument("--all-dates", dest="all_dates", action="store_true",
                    help="validate every lane file for this agent, including historical debt")
    ap.add_argument("--tail", type=int, default=0, help="show the last N rows")
    args = ap.parse_args()

    if args.validate:
        return validate(args.agent, all_dates=args.all_dates)
    if args.tail:
        return tail(args.agent, args.tail)
    if not args.title or not args.body:
        print("harvest_now: --title and --body are required to append", file=sys.stderr)
        return 2
    return append_row(args.agent, args.kind, args.title, args.body, args.source,
                      args.item_id)


if __name__ == "__main__":
    sys.exit(main())
