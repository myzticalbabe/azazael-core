#!/usr/bin/env python3
"""trace_intake_798.py — READ-ONLY trace for Engram #798 acceptance item 3.

Proves the end-to-end intake edge one row at a time:

    technique-link row (store)  ->  build_training_records()  ->  trainer record

For 5+ rows it prints the SOURCE row's durable identity (event_uuid / audit_hash),
its timestamp and session, the measured verdict (ok/severity), and the SHA-256 of
the trainer record the intake produced from it. Then it runs the same bounded
trainer the INTAKE track runs and reports the measured effect.

READS ONLY: it never calls a record_*/write API, so it cannot perturb the store.
Any row it cannot pair with a record is reported as a gap, never silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CORE.local_memory import get_memory_store          # noqa: E402
from CORE.training_intake import build_training_records  # noqa: E402


def sha(obj) -> str:
    """Stable SHA-256 of a record's canonical JSON."""
    blob = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def identity(row: dict) -> str:
    """The row's durable identity, in preference order."""
    for key in ("event_uuid", "audit_hash", "id", "event_id"):
        val = (row or {}).get(key)
        if val:
            return "%s=%s" % (key, val)
    return "NO-ID"


def main() -> int:
    built = build_training_records(limit=500)
    records = built.get("records") or []
    print("== intake build ==")
    print("  source        : %s" % built.get("source"))
    print("  rows seen     : %s" % built.get("n_rows"))
    print("  records built : %s" % built.get("n"))
    print("  unmeasured    : %s  (counted, never labelled)" % built.get("n_unmeasured"))
    print("  ok_rate       : %s" % built.get("ok_rate"))
    print("  by_hook       : %s" % json.dumps(built.get("by_hook")))
    if built.get("store_error") or built.get("read_error"):
        print("  ERROR         : store=%s read=%s"
              % (built.get("store_error"), built.get("read_error")))

    store = get_memory_store()
    rows = store.query_memories(source="technique-link", limit=500) or []

    # Pair each record back to its source row on (timestamp, session_id, event_type),
    # the three fields the adapter preserves verbatim.
    index = {}
    for r in rows:
        key = ((r or {}).get("timestamp"), (r or {}).get("session_id"),
               (r or {}).get("event_type"))
        index.setdefault(key, []).append(r)

    print("\n== row -> record trace (first 6) ==")
    shown = 0
    unpaired = 0
    for rec in records:
        if shown >= 6:
            break
        key = (rec.get("timestamp"), rec.get("session_id"), rec.get("event_type"))
        src = index.get(key)
        if not src:
            unpaired += 1
            continue
        row = src.pop(0)
        data = (row or {}).get("data") or {}
        shown += 1
        print("  [%d] %s" % (shown, identity(row)))
        print("      ts=%s session=%s" % (row.get("timestamp"), row.get("session_id")))
        print("      hook=%s ok=%s severity=%s (producer's own)"
              % (data.get("hook"), data.get("ok"), row.get("severity")))
        print("      -> record sha256=%s" % sha(rec))
        print("      -> record.data=%s" % json.dumps(rec.get("data"), sort_keys=True))

    print("\n  rows traced=%d  unpaired=%d" % (shown, unpaired))

    # Measured effect: same bounded trainer the INTAKE track drives, on this pool.
    from CORE.WORMS.expansion.learning import RLTrainLoop
    loop = RLTrainLoop()
    try:
        loop.load_q_table_from_store()
    except Exception as exc:                                  # noqa: BLE001
        print("  [WARN] q-table warm start skipped: %s" % exc)
    # RLTrainLoop's table is `_q_table` (a defaultdict of defaultdicts).
    before = len(getattr(loop, "_q_table", {}) or {})
    # persist=False: the orchestrator's INTAKE track persists; this harness must
    # not, or it would be the store-write it claims not to be.
    result = loop.train_from_memory(
        records=records, n_episodes=60,
        max_steps=min(50, len(records)), use_schema_features=False,
        persist=False)
    after = len(getattr(loop, "_q_table", {}) or {})
    print("\n== measured trainer effect (READ-ONLY run, no store writes) ==")
    print("  q_table states: before=%d after=%d (+%d)" % (before, after, after - before))
    if "error" in result:
        print("  ERROR: %s" % result["error"])
        return 2
    print("  eval_mean_reward=%s" % result.get("eval_mean_reward",
                                               result.get("avg_reward")))
    return 0 if shown >= 5 else 3


if __name__ == "__main__":
    raise SystemExit(main())
