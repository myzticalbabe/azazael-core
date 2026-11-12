# coding: utf-8
"""wire_or_cut_queue.py — turn the wire-or-cut backlog into a bounded queue.

The NEURAL_MESH_WIRE_OR_CUT worklist only moved when an audit noticed it
(audit 2026-09-27: 100 pending rows, 45 test_reached_only / 55
no_reference_anywhere). This job proposes a bounded daily disposition for
the next N rows -- deterministic order, evidence attached -- and writes it to
state/wire_or_cut_queue.json for the operator.

It PROPOSES, it does not decide: auto-retiring code is a judgment call no
queue should make silently (wire-or-cut is exactly the discipline that says
decisions get made with evidence in hand). The queue's value is that the
decision now has a date and a bounded shape instead of an unbounded table.

Disposition logic (read-only over the artifact):
  test_reached_only    -> propose "wire": it has a consumer, so it is
                          reference-only code waiting for a production call
                          site; wiring is the safe direction.
  no_reference_anywhere -> propose "retire": nothing reads it, so it costs
                          audit attention every cycle; retiring (or archiving)
                          is the safe direction.

Output: state/mesh_gossip... no: state/wire_or_cut_queue.json (last run only).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "NEURAL_MESH_WIRE_OR_CUT.json"
OUT = ROOT / "state" / "wire_or_cut_queue.json"
BATCH = 5


def _find_rows(doc: dict) -> list:
    """Find the findings rows without trusting the container shape.

    The artifact's rows live under ``declared`` as a MAP keyed by
    "module:qualname" (measured 2026-09-27: a list-shaped finder found
    nothing here and reported 0 pending against a 100-row backlog). Accept
    the mapping shape (map key becomes the target) or a plain list of row
    dicts -- and locate it by row FIELDS, not by container key name, since
    the artifact's keys have drifted before (09-22 census vs its audit
    header).
    """
    for value in doc.values():
        if isinstance(value, dict) and value:
            head = list(value.values())[:5]
            if (all(isinstance(v, dict) for v in head)
                    and any("status" in v for v in head)):
                rows = []
                for target, row in value.items():
                    r = dict(row)
                    r.setdefault("target", target)
                    rows.append(r)
                return rows
    for value in doc.values():
        if (isinstance(value, list) and value
                and isinstance(value[0], dict)
                and any("status" in row for row in value[:5])):
            return value
    return []


def main() -> int:
    if not ARTIFACT.is_file():
        print("no wire-or-cut artifact at %s" % ARTIFACT)
        return 1
    try:
        doc = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("artifact unreadable: %s" % str(exc)[:120])
        return 1

    rows = _find_rows(doc)
    pending = [r for r in rows
               if str(r.get("status", "")).lower() == "pending"]
    # Deterministic order: triage kind then name, so the queue is stable and
    # consecutive runs page through the backlog instead of reshuffling it.
    pending.sort(key=lambda r: (str(r.get("triage", "")),
                                str(r.get("target") or r.get("qualname")
                                    or r.get("function") or "")))

    batch = pending[:BATCH]
    queue = {
        "schema": "azazael.wire_or_cut_queue.v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "total_rows": len(rows),
        "pending": len(pending),
        "batch": [
            {
                "target": (r.get("target") or r.get("qualname")
                           or r.get("function") or "?"),
                "triage": r.get("triage", "?"),
                "proposed": ("wire" if r.get("triage") == "test_reached_only"
                             else "retire"),
                "why": ("has a test consumer; add a production call site"
                        if r.get("triage") == "test_reached_only"
                        else "no references anywhere; archive or delete"),
            }
            for r in batch
        ],
        "decided_by": "operator (proposals only)",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(OUT)

    print("wire-or-cut queue: %d pending, %d proposed this run"
          % (len(pending), len(batch)))
    for row in queue["batch"]:
        print("  %-10s %-22s %s" % (row["proposed"], row["triage"],
                                    str(row["target"])[:80]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
