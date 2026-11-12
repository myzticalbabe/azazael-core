#!/usr/bin/env python3
"""backtrack_metric.py — Backtracking rate as QD search-health signal.

Per pool era, counts champion re-eval flips (argmax change between consecutive
`evolution_result` best rows) + promotion-streak reversals + archive
displacement direction. Pure measurement over existing rows — no inference
required. Emits state/backtrack_report.json.

Design: pure functions operate on row dicts (hermetically testable); main()
queries the live store and writes the report. Exit 0 on live history.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REPORT_PATH = os.path.join(_ROOT, "state", "backtrack_report.json")
MAX_QUERY_LIMIT = 500


# --- Pure metric functions (hermetically testable) ---------------------------

def _row_field(row: dict, *keys, default=None):
    """First present value among keys from row['data'], else default."""
    data = (row or {}).get("data") or {}
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
    return default


def pool_id(row: dict) -> Optional[Any]:
    return _row_field(row, "pool_id")


def best_genes(row: dict) -> Optional[dict]:
    v = _row_field(row, "best_genes", "champion_genes", "seed_genes")
    return v if isinstance(v, dict) else None


def promoted(row: dict) -> Optional[bool]:
    return _row_field(row, "promoted")


def qd_archive(row: dict) -> Optional[dict]:
    v = _row_field(row, "qd_archive")
    return v if isinstance(v, dict) else None


def row_ts(row: dict) -> str:
    return (row or {}).get("timestamp") or ""


def row_id(row: dict) -> Any:
    return (row or {}).get("id")


def argmax_genes(genes: dict) -> Optional[str]:
    """Gene key with the max value (first max wins on ties)."""
    if not genes:
        return None
    best_key = None
    best_val = None
    for k, v in genes.items():
        try:
            nv = float(v)
        except (TypeError, ValueError):
            continue
        if best_val is None or nv > best_val:
            best_val = nv
            best_key = k
    return best_key


def is_flip(prev: dict, cur: dict) -> Optional[bool]:
    """True if argmax(best_genes) changed between consecutive best rows."""
    pa = argmax_genes(best_genes(prev))
    ca = argmax_genes(best_genes(cur))
    if pa is None or ca is None:
        return None
    return pa != ca


def is_reversal(prev: dict, cur: dict) -> Optional[bool]:
    """True if the promoted flag toggled between consecutive rows."""
    pp = promoted(prev)
    cp = promoted(cur)
    if pp is None or cp is None:
        return None
    return bool(pp) != bool(cp)


def archive_direction(prev: dict, cur: dict) -> str:
    """Direction of archive filled-cell displacement between two rows."""
    pa = qd_archive(prev)
    ca = qd_archive(cur)
    pf = pa.get("filled") if pa else None
    cf = ca.get("filled") if ca else None
    try:
        pf = int(pf) if pf is not None else None
    except (TypeError, ValueError):
        pf = None
    try:
        cf = int(cf) if cf is not None else None
    except (TypeError, ValueError):
        cf = None
    if pf is None or cf is None:
        return "stable"
    if cf > pf:
        return "up"
    if cf < pf:
        return "down"
    return "stable"


def era_metric(rows: List[dict]) -> dict:
    """Backtrack metric for one pool era (rows already ordered chronologically)."""
    n = len(rows)
    transitions = max(n - 1, 0)
    flips = 0
    reversals = 0
    direction = {"up": 0, "down": 0, "stable": 0}
    flip_evalable = 0
    reversal_evalable = 0

    for i in range(1, n):
        prev, cur = rows[i - 1], rows[i]
        f = is_flip(prev, cur)
        if f is not None:
            flip_evalable += 1
            if f:
                flips += 1
        r = is_reversal(prev, cur)
        if r is not None:
            reversal_evalable += 1
            if r:
                reversals += 1
        direction[archive_direction(prev, cur)] += 1

    flip_rate = round(flips / flip_evalable, 6) if flip_evalable else 0.0
    reversal_rate = round(reversals / reversal_evalable, 6) if reversal_evalable else 0.0
    backtrack_rate = (
        round((flips + reversals) / (2 * max(flip_evalable + reversal_evalable, 1)), 6)
        if (flip_evalable + reversal_evalable)
        else 0.0
    )

    added_total = 0
    filled_start = None
    filled_end = None
    for r in rows:
        a = qd_archive(r)
        if a:
            av = a.get("added")
            if isinstance(av, int) and not isinstance(av, bool):
                added_total += av
            fv = a.get("filled")
            if fv is not None:
                try:
                    fv = int(fv)
                except (TypeError, ValueError):
                    fv = None
                if filled_start is None:
                    filled_start = fv
                filled_end = fv

    return {
        "n_rows": n,
        "transitions": transitions,
        "flips": flips,
        "flip_evalable": flip_evalable,
        "flip_rate": flip_rate,
        "reversals": reversals,
        "reversal_evalable": reversal_evalable,
        "reversal_rate": reversal_rate,
        "backtrack_rate": backtrack_rate,
        "archive": {
            "added_total": added_total,
            "filled_start": filled_start,
            "filled_end": filled_end,
            "direction": direction,
        },
    }


def backtrack_metric(rows: List[dict]) -> dict:
    """Full backtrack report over evolution_result rows, grouped by pool era."""
    by_era: Dict[Any, List[dict]] = defaultdict(list)
    degenerate = 0
    for r in rows:
        pid = pool_id(r)
        if pid is None:
            degenerate += 1
            continue
        by_era[pid].append(r)

    eras = {}
    for pid, erows in by_era.items():
        erows_sorted = sorted(erows, key=lambda r: (row_ts(r), str(row_id(r))))
        eras[str(pid)] = era_metric(erows_sorted)

    n_rows = len(rows)
    n_eras = len(eras)
    total_transitions = sum(e["transitions"] for e in eras.values())
    total_flips = sum(e["flips"] for e in eras.values())
    total_reversals = sum(e["reversals"] for e in eras.values())
    total_eval = sum(e["flip_evalable"] + e["reversal_evalable"] for e in eras.values())
    overall_flip_rate = round(total_flips / sum(e["flip_evalable"] for e in eras.values()), 6) if sum(e["flip_evalable"] for e in eras.values()) else 0.0
    overall_reversal_rate = round(total_reversals / sum(e["reversal_evalable"] for e in eras.values()), 6) if sum(e["reversal_evalable"] for e in eras.values()) else 0.0
    overall_backtrack = round((total_flips + total_reversals) / (2 * max(total_eval, 1)), 6) if total_eval else 0.0

    return {
        "schema": "backtrack_metric_v1",
        "n_rows": n_rows,
        "n_eras": n_eras,
        "degenerate_rows": degenerate,
        "overall": {
            "transitions": total_transitions,
            "flips": total_flips,
            "flip_rate": overall_flip_rate,
            "reversals": total_reversals,
            "reversal_rate": overall_reversal_rate,
            "backtrack_rate": overall_backtrack,
        },
        "eras": eras,
    }


# --- Live store ingestion ----------------------------------------------------

def query_all_evolution_results(store) -> List[dict]:
    """Paginate all evolution_result rows from the store (respects MAX_QUERY_LIMIT)."""
    rows = []
    offset = 0
    while True:
        batch = store.query_memories(event_type="evolution_result", limit=MAX_QUERY_LIMIT, offset=offset) or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < MAX_QUERY_LIMIT:
            break
        offset += len(batch)
    return rows


def main() -> int:
    try:
        from CORE.local_memory import get_memory_store  # noqa: E402
        store = get_memory_store()
    except Exception as exc:  # noqa: BLE001 - store unavailable is a real condition
        report = {
            "schema": "backtrack_metric_v1",
            "error": "store_unavailable",
            "error_detail": str(exc)[:200],
            "n_rows": 0,
            "n_eras": 0,
            "overall": {"transitions": 0, "flips": 0, "flip_rate": 0.0,
                        "reversals": 0, "reversal_rate": 0.0, "backtrack_rate": 0.0},
            "eras": {},
        }
        with open(REPORT_PATH, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print("STORE UNAVAILABLE: %s WROTE=%s" % (exc, REPORT_PATH))
        return 1

    rows = query_all_evolution_results(store)
    report = backtrack_metric(rows)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    ov = report["overall"]
    print("N_ROWS=%d N_ERAS=%d TRANSITIONS=%d FLIPS=%d REVERSALS=%d BACKTRACK_RATE=%.6f WROTE=%s"
          % (report["n_rows"], report["n_eras"], ov["transitions"], ov["flips"],
             ov["reversals"], ov["backtrack_rate"], REPORT_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
