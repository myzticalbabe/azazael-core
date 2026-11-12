#!/usr/bin/env python
"""pmb_outbox_pickup.py -- drain supervisor outbox rows into PMB.

Closes #1296: supervisor record_outcome() writes Engram + one JSONL row
that nobody read, so the memories diverged under a 'unified' verdict.
This is the PMB half: each row becomes one record_batch item.

  C:/Users/clint/.pmb-venv/Scripts/python.exe tools/pmb_outbox_pickup.py [--once]

--once drains state/supervisor_outbox.jsonl now. Consumed rows move to
state/supervisor_outbox.done.jsonl ONLY after PMB acknowledges; a failed
row stays for the next run (never silently dropped). Run it on a schedule
or after sessions that used record_outcome.

Must run under .pmb-venv (the interpreter with pmb_client).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTBOX = HERE.parent / "state" / "supervisor_outbox.jsonl"
DONE = HERE.parent / "state" / "supervisor_outbox.done.jsonl"

KIND_TO_TYPE = {"lesson": "lesson", "decision": "decision", "fact": "fact",
                "activity": "activity", "goal": "goal"}


def load_rows(fp: Path) -> list[dict]:
    if not fp.exists():
        return []
    rows = []
    for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true",
                    help="drain the outbox now (default action)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rows = load_rows(OUTBOX)
    if not rows:
        print("outbox empty: nothing to pick up")
        return 0
    if args.dry_run:
        print("outbox rows: %d (dry run, nothing sent)" % len(rows))
        return 0

    import pmb_client

    sent, kept = [], []
    for r in rows:
        content = "[%s] %s" % (str(r.get("title", ""))[:120],
                               str(r.get("body", ""))[:3000])
        item = {"type": KIND_TO_TYPE.get(str(r.get("kind", "")), "fact"),
                "content": content, "project": "azazael"}
        try:
            pmb_client.call_tool("record_batch", {"items": [item]})
            sent.append(r)
        except Exception as exc:  # noqa: BLE001 -- one bad row must not eat the rest
            sys.stderr.write("pickup failed for %r: %r\n"
                             % (r.get("title", "?")[:60], exc))
            kept.append(r)
    if sent:
        with open(DONE, "a", encoding="utf-8") as fh:
            for r in sent:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(OUTBOX, "w", encoding="utf-8") as fh:
            for r in kept:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("pickup: sent=%d kept=%d" % (len(sent), len(kept)))
    return 0 if not kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
