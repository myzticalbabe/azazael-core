#!/usr/bin/env python3
"""


__all__ = [
    "cmd_gc",
    "cmd_recent",
    "cmd_search",
    "cmd_stats",
    "cmd_status",
    "cmd_timeline",
    "main",
]

cli_memory.py — DirectMemory CLI for azazael-ctl.

Usage:
    azazael-ctl memory status               # Show memory health
    azazael-ctl memory recent <n>           # Show recent N events
    azazael-ctl memory stats                # Event type distribution
    azazael-ctl memory search <type>        # Search events by type
    azazael-ctl memory timeline <session>   # Timeline for a session
    azazael-ctl memory gc                   # Close stale sessions
"""

import sys
import sqlite3
from CORE.direct_memory import DirectMemory
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _get_db() -> str:
    """Dynamically resolve DB_PATH so temp DB overrides work in tests."""
    from CORE.direct_memory import DB_PATH
    return str(DB_PATH)


def cmd_status():
    print("=== DirectMemory Status ===")
    try:
        open_count = DirectMemory.get_open_sessions_count()
        print(f"  Open sessions: {open_count}")
    except Exception as exc:
        note_failure("cli_memory:L42", exc)
    try:
        conn = sqlite3.connect(_get_db(), timeout=2)
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM memories").fetchone()[0]
        conn.close()
        print(f"  Total events: {total}")
        print(f"  Sessions: {sessions}")
    except Exception as e:
        print(f"  Error: {e}")


def cmd_recent(n: int = 20):
    conn = sqlite3.connect(_get_db(), timeout=2)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT event_type, timestamp, session_id FROM memories "
        "ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    conn.close()
    print(f"=== Recent {len(rows)} Events ===")
    for r in rows:
        sid = r["session_id"][-20:]
        print(f"  {r['event_type']:25s}  {r['timestamp'][:19]}  ...{sid}")


def cmd_stats():
    conn = sqlite3.connect(_get_db(), timeout=2)
    types = conn.execute(
        "SELECT event_type, COUNT(*) as cnt FROM memories "
        "GROUP BY event_type ORDER BY cnt DESC"
    ).fetchall()
    total = sum(r[1] for r in types)
    conn.close()
    print(f"=== Event Type Distribution (total: {total}) ===")
    for et, count in types[:25]:
        pct = count / total * 100 if total > 0 else 0
        bar = "#" * int(pct / 2)
        print(f"  {et:30s} {count:6d} ({pct:4.1f}%) {bar}")


def cmd_search(event_type: str):
    conn = sqlite3.connect(_get_db(), timeout=2)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT event_type, timestamp, session_id, data FROM memories "
        "WHERE event_type = ? ORDER BY id DESC LIMIT 20",
        (event_type,)
    ).fetchall()
    conn.close()
    print(f"=== Events of type '{event_type}' ({len(rows)}) ===")
    for r in rows:
        sid = r["session_id"][-20:]
        data_str = str(r.get("data", ""))[:60]
        print(f"  {r['timestamp'][:19]}  ...{sid}  {data_str}")


def cmd_timeline(session_id: str):
    conn = sqlite3.connect(_get_db(), timeout=2)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT event_type, timestamp, data FROM memories "
        "WHERE session_id = ? ORDER BY id ASC LIMIT 50",
        (session_id,)
    ).fetchall()
    conn.close()
    print(f"=== Timeline for ...{session_id[-20:]} ===")
    for r in rows:
        print(f"  {r['timestamp'][:19]}  {r['event_type']}")


def cmd_gc():
    closed = DirectMemory.close_stale_sessions(max_age_hours=1)
    print(f"Closed {closed} stale session(s)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    commands = {"status": cmd_status, "stats": cmd_stats, "gc": cmd_gc}
    if cmd in commands:
        commands[cmd]()
    elif cmd == "recent":
        n = int(sys.argv[2]) if len(sys.argv) >= 3 and sys.argv[2].isdigit() else 20
        cmd_recent(n)
    elif cmd == "search" and len(sys.argv) >= 3:
        cmd_search(" ".join(sys.argv[2:]))
    elif cmd == "timeline" and len(sys.argv) >= 3:
        cmd_timeline(sys.argv[2])
    else:
        print(f"Unknown memory command: {cmd}")

if __name__ == "__main__":
    main()
