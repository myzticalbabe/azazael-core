#!/usr/bin/env python3
# coding: utf-8
"""bake_row.py — append ONE encounter-time row to a #714 bake lane file.

Engram #714 (the concurrent self-log lane for #712) requires every error met
while executing #712 to be written to `state/bake_deepseek_712.jsonl` AT
ENCOUNTER TIME, in the bake schema `{source, item_id, title, body, kind}`,
with the mistake AND its solution/outcome in the body. A row written hours
later from memory is dropped at merge, so this helper exists to make the write
a single cheap command instead of a remembered chore.

It is the write-side companion to `tools/merge_bake_lane.py` (the coordinator
merge gate): this tool only APPENDS valid rows, so a malformed row can never
reach the lane. It validates the same constraints the merge gate enforces —
required keys, `kind` in schema, body length floor and cap — and refuses
(non-zero) rather than writing something the gate would later drop.

`state/` is gitignored AND guarded: a bare open() is refused by the global
sentinel hook (measured — PermissionError). Writes therefore go through
`CORE.sentinel_enforcer.permit_here`, the same permit path the merge gate uses.

Usage
-----
    python tools/bake_row.py --item-id 712-P0-01 --kind error_fix \\
        --title "short searchable title" \\
        --body "**Mistake**: ... **Fix**: ... **Outcome**: ... **Proof**: file:line"

    # titles are deduped against the lane file and the bake corpus/harvest so a
    # re-log is a no-op, not a duplicate row.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANE = ROOT / "state" / "bake_deepseek_712.jsonl"
KINDS = {"error_fix", "lesson", "mandate", "sop_rule", "violation", "anti_pattern"}
MIN_BODY = 40
MAX_BODY = 4000


def norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", str(t).strip().lower())


def known_titles() -> set:
    """Normalized titles already present in the lane + corpus + harvest files.

    Dedupe here (cheap, at write time) so the lane does not accumulate rows the
    merge gate would drop as duplicates against the corpus.
    """
    titles = set()
    for fp in [LANE, ROOT / "state" / "bake_corpus.jsonl"]:
        if fp.exists():
            for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line:
                    try:
                        titles.add(norm_title(json.loads(line).get("title", "")))
                    except ValueError:
                        continue
    for fp in sorted((ROOT / "state").glob("bake_harvest_*.jsonl")):
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    titles.add(norm_title(json.loads(line).get("title", "")))
                except ValueError:
                    continue
    titles.discard("")
    return titles


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--item-id", required=True, help="e.g. 712-P0-01 (phase-slice)")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", required=True)
    ap.add_argument("--kind", required=True, choices=sorted(KINDS))
    ap.add_argument("--source", default="engram-714-lane")
    ap.add_argument("--lane", default=str(LANE))
    ap.add_argument("--force", action="store_true",
                    help="append even if a row with the same title exists")
    args = ap.parse_args()

    body = args.body
    if len(body) < MIN_BODY:
        print("REFUSING: body is %d chars (<%d); the merge gate drops these"
              % (len(body), MIN_BODY))
        return 2
    if len(body) > MAX_BODY:
        print("NOTE: body %d chars truncated to %d" % (len(body), MAX_BODY))
        body = body[:MAX_BODY]
    if args.kind not in KINDS:
        print("REFUSING: kind %r not in schema" % args.kind)
        return 2

    key = norm_title(args.title)
    if not key:
        print("REFUSING: empty title")
        return 2
    if not args.force and key in known_titles():
        print("SKIP: a row titled %r already exists (lane/corpus/harvest)" % args.title)
        return 0

    row = {"source": str(args.source)[:80], "item_id": str(args.item_id)[:80],
           "title": str(args.title)[:120], "body": body, "kind": args.kind}

    sys.path.insert(0, str(ROOT / "src"))
    try:
        from CORE.sentinel_enforcer import permit_here
    except Exception as exc:  # pragma: no cover - import guard, loud by design
        print("REFUSING: cannot import permit_here (%s: %s); state/ writes are "
              "guarded, so no fallback write is attempted"
              % (type(exc).__name__, str(exc)[:120]))
        return 2

    lane = Path(args.lane)
    lane.parent.mkdir(parents=True, exist_ok=True)
    with permit_here("engram #714 bake lane row"):
        with open(lane, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    # verify the append landed (a guarded write that silently no-ops is the
    # exact false-success shape this campaign removes).
    tail = lane.read_text(encoding="utf-8", errors="replace").splitlines()[-1]
    if json.loads(tail).get("title") != row["title"]:
        print("ERROR: append did not land as the last line")
        return 2
    print("APPENDED %s -> %s (%d rows now)"
          % (row["item_id"], lane.name, len(lane.read_text(encoding='utf-8').splitlines())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
