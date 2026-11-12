#!/usr/bin/env python
"""pmb_capture.py -- record an insight the moment you have it.

WHY THIS EXISTS
---------------
This host (Codebuff) has no `UserPromptSubmit` hook, so PMB cannot push a lesson
into context -- retrieval is PULL-ONLY (PMB's `_AGENT_CAP` lists it `mcp-only`).
That has a cost the user named directly: an insight that is not recorded while it
is in hand is lost when the session ends. The failure is not that PMB forgets; it
is that nobody wrote it down.

`record_batch` is the underlying tool, but reaching for a Python one-liner with
the right JSON shape mid-task is friction, and friction is what loses the
knowledge. This wrapper makes it one command:

    C:/Users/clint/.pmb-venv/Scripts/python.exe tools/pmb_capture.py \
        --lesson "the insight" --fact "the durable fact" --project azazael

Any combination of --lesson/--fact/--decision/--activity/--goal may be given;
each becomes one atomic item in a single `record_batch` call.

Run it with the `.pmb-venv` interpreter (the one that has `pmb`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def build_items(args) -> list[dict]:
    items: list[dict] = []
    if args.lesson:
        item = {"type": "lesson", "content": args.lesson}
        if args.project:
            item["project"] = args.project
        items.append(item)
    if args.fact:
        items.append({"type": "fact", "content": args.fact,
                      "importance": args.importance})
    if args.decision:
        items.append({"type": "decision", "content": args.decision})
    if args.activity:
        items.append({"type": "activity", "content": args.activity,
                      "kind": "action"})
    if args.goal:
        items.append({"type": "goal", "title": args.goal})
    return items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lesson", help="a procedural rule (surfaced by find_lessons)")
    ap.add_argument("--fact", help="a durable fact")
    ap.add_argument("--decision", help="a decision made")
    ap.add_argument("--activity", help="something completed")
    ap.add_argument("--goal", help="an intention (goes to the goal ledger)")
    ap.add_argument("--project", default="azazael",
                    help="project tag for the lesson (default: azazael)")
    ap.add_argument("--importance", type=float, default=0.8)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    items = build_items(args)
    if not items:
        ap.error("give at least one of --lesson/--fact/--decision/"
                 "--activity/--goal")

    import pmb_client
    try:
        res = pmb_client.call_tool("record_batch", {"items": items})
    except Exception as exc:  # noqa: BLE001 -- a capture must not break the task
        sys.stderr.write("pmb_capture failed: %r\n" % (exc,))
        return 1
    if not args.quiet:
        print("captured %d item(s): outbox=%s"
              % (len(items), (res or {}).get("outbox_id")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
