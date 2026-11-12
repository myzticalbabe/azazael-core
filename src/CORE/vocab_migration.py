#!/usr/bin/env python3
"""


__all__ = [
    "cmd_map",
    "cmd_report",
    "cmd_scan",
    "get_non_vocab_types",
    "main",
]

vocab_migration.py — Tools for event type vocabulary migration and consistency.

Detects event types in DirectMemory that are not in EVENT_VOCAB,
lists them for review, and provides migration mapping.

Usage:
    python -m CORE.vocab_migration scan       # Scan DB for non-vocab types
    python -m CORE.vocab_migration report      # Detailed report with counts
    python -m CORE.vocab_migration map <old> <new>  # Create migration mapping
"""

import sys
import sqlite3
from pathlib import Path
#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    # EVENT_VOCAB lives on the CORE package (re-exported with {} fallback),
    # not in audit_trail directly -- importing from audit_trail always fails.
    from CORE import EVENT_VOCAB
except ImportError as exc:
    note_failure("vocab_migration:L29", exc)
    EVENT_VOCAB = {}
from CORE.direct_memory import DB_PATH as _DB
from typing import List



def _get_db_path():
    return Path(str(_DB))


def get_non_vocab_types() -> list:
    """Find event types in the database that are not in EVENT_VOCAB."""
    canonical = set(EVENT_VOCAB.values())
    db = _get_db_path()
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db), timeout=5)
    conn.row_factory = sqlite3.Row
    types = conn.execute(
        "SELECT event_type, COUNT(*) as cnt FROM memories "
        "GROUP BY event_type ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    results = []
    for r in types:
        if r["event_type"] not in canonical:
            results.append({
                "event_type": r["event_type"],
                "count": r["cnt"],
            })
    return results


def cmd_scan():
    types = get_non_vocab_types()
    if not types:
        print("All event types are in EVENT_VOCAB")
        return
    print(f"=== Non-Vocab Event Types ({len(types)}) ===")
    for t in types:
        mark = ""
        if t["event_type"] in ("cluster_created", "training_complete",
                                "model_registered", "nature_cluster_discovery",
                                "cross_category_route", "category_cluster_executed",
                                "cluster_execution_started", "cluster_execution_completed"):
            mark = " (legacy Worm types — not in CD2 vocab)"
        print(f"  {t['event_type']:35s}  x{t['count']:6d}{mark}")


def cmd_report():
    types = get_non_vocab_types()
    canonical = set(EVENT_VOCAB.values())
    db = _get_db_path()
    conn = sqlite3.connect(str(db), timeout=5)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    vocab_count = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE event_type IN ({','.join('?'*len(canonical))})",
        list(canonical)
    ).fetchone()[0]
    conn.close()
    print("=== Vocab Coverage Report ===")
    print(f"  Total events:      {total}")
    print(f"  In vocab:          {vocab_count} ({vocab_count/total*100:.1f}%)")
    print(f"  Non-vocab types:   {len(types)}")
    if types:
        total_non = sum(t["count"] for t in types)
        print(f"  Non-vocab events:  {total_non} ({total_non/total*100:.1f}%)")
        print()
        print("  --- Non-Vocab Types by Frequency ---")
        for t in types:
            pct = t["count"] / total * 100
            bar = "#" * int(pct)
            print(f"  {t['event_type']:35s} {t['count']:6d} ({pct:4.1f}%) {bar}")


def cmd_map(old_type: str, new_type: str):
    """Create a migration mapping (dry run)."""
    types = get_non_vocab_types()
    matches = [t for t in types if t["event_type"] == old_type]
    if not matches:
        print(f"  Type '{old_type}' not found in non-vocab types")
        return
    count = matches[0]["count"]
    print(f"  Would map: {old_type} ({count} events) -> {new_type}")
    print(f"  SQL: UPDATE memories SET event_type = '{new_type}' WHERE event_type = '{old_type}'")
    print("  Run with --execute to apply")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "scan":
        cmd_scan()
    elif cmd == "report":
        cmd_report()
    elif cmd == "map" and len(sys.argv) >= 4:
        cmd_map(sys.argv[2], sys.argv[3])
    elif cmd == "map" and "--execute" in sys.argv and len(sys.argv) >= 4:
        old_t = sys.argv[2]
        new_t = sys.argv[3]
        db = _get_db_path()
        conn = sqlite3.connect(str(db), timeout=5)
        cursor = conn.execute("UPDATE memories SET event_type = ? WHERE event_type = ?",
                              (new_t, old_t))
        conn.commit()
        print(f"  Applied: {old_t} -> {new_t} ({cursor.rowcount} rows)")
        conn.close()
    else:
        print(f"Unknown command: {cmd}")

if __name__ == "__main__":
    main()
