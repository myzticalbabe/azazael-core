#!/usr/bin/env python3
"""


__all__ = [
    "cmd_archive",
    "cmd_backfill",
    "cmd_correlation",
    "cmd_diff",
    "cmd_export",
    "cmd_interactive",
    "cmd_list",
    "cmd_query",
    "cmd_search",
    "cmd_summary",
    "cmd_unlinked",
    "cmd_watch",
    "main",
]

cli_crossref.py — CLI for DM ↔ AJ session cross-referencing v2.0.

Usage:
    python -m CORE.cli_crossref summary                    # Show mapping summary
    python -m CORE.cli_crossref backfill                   # Run backfill
    python -m CORE.cli_crossref list                       # List all mappings
    python -m CORE.cli_crossref unlinked                   # Show unlinked sessions
    python -m CORE.cli_crossref query <session>            # Cross-reference query
    python -m CORE.cli_crossref corr <id>                  # Lookup by correlation_id
    python -m CORE.cli_crossref export <format>            # Export mappings (csv|json)
    python -m CORE.cli_crossref watch                      # Watch for new sessions and link
    python -m CORE.cli_crossref diff                       # Diff since last backfill
    python -m CORE.cli_crossref search <event_type>        # Search mappings by event type
    python -m CORE.cli_crossref interactive                 # Interactive mode
    python -m CORE.cli_crossref archive <filepath>          # Archive/close a session
"""

import sys
import json
import csv
import time
from datetime import datetime
from CORE.direct_memory import DirectMemory
from typing import List
import shutil
from pathlib import Path

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def cmd_summary():
    s = DirectMemory.get_mapping_summary()
    print("=== Session Mapping Summary ===")
    for k, v in s.items():
        print(f"  {k}: {v}")


def cmd_backfill():
    print("=== Running Backfill ===")
    r = DirectMemory.cross_reference_ledgers()
    for k, v in r.items():
        print(f"  {k}: {v}")


def cmd_list():
    rows = DirectMemory.query_cross_reference()
    print(f"=== All Mappings ({len(rows)}) ===")
    for r in rows[:30]:
        dm = r.get("dm_session_id", r.get("session_id", "?"))[-24:]
        aj = str(r.get("audit_filepath", "?"))[-28:]
        conf = r.get("confidence", "?")
        print(f"  DM=...{dm}  ->  AJ=...{aj}  (conf={conf})")
    if len(rows) > 30:
        print(f"  ... and {len(rows) - 30} more")


def cmd_unlinked():
    report = DirectMemory.get_unlinked_report()
    print("=== Unlinked Sessions ===")
    print(f"  DM sessions unlinked: {report['unlinked_dm_count']}")
    print(f"  AJ files unlinked:    {report['unlinked_aj_count']}")
    print()
    if report['unlinked_aj']:
        print("--- Unlinked AJ files ---")
        for aj in report['unlinked_aj']:
            ts = aj.get("ts", "no ts")
            fp = aj["filepath"][-40:]
            print(f"  {fp}  ({ts})")
    if report['unlinked_dm']:
        print("--- Unlinked DM sessions (last 10) ---")
        for dm in report['unlinked_dm'][:10]:
            sid = dm['session_id'][-28:]
            print(f"  ...{sid}  started={dm.get('started_at', '?')}")


def cmd_query(session_id: str):
    rows = DirectMemory.query_cross_reference(dm_session_id=session_id)
    print(f"=== Cross-Reference for session ...{session_id[-20:]} ===")
    if not rows:
        print("  No mappings found")
        return
    for r in rows[:20]:
        et = r.get("event_type", "?")
        aj = str(r.get("audit_filepath", "?"))[-28:]
        conf = r.get("confidence", "?")
        print(f"  {et:25s} -> AJ=...{aj}  (conf={conf})")
    if len(rows) > 20:
        print(f"  ... and {len(rows) - 20} more events")


def cmd_correlation(corr_id: str):
    rows = DirectMemory.get_mapping_by_correlation_id(corr_id)
    print(f"=== Mappings for correlation_id ...{corr_id[-12:]} ===")
    if not rows:
        print("  No mappings found")
        return
    for r in rows[:10]:
        dm = r.get("dm_session_id", "?")[-24:]
        aj = str(r.get("audit_filepath", "?"))[-28:]
        print(f"  DM=...{dm}  ->  AJ=...{aj}")


def cmd_export(fmt: str):
    """Export mappings as CSV or JSON."""
    rows = DirectMemory.query_cross_reference()
    if fmt == "json":
        print(json.dumps(rows, indent=2, default=str))
    elif fmt == "csv":
        if not rows:
            print("No mappings to export")
            return
        writer = csv.DictWriter(sys.stdout, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    else:
        print(f"Unknown format: {fmt} (use 'csv' or 'json')")


def cmd_watch(interval: int = 30):
    """Watch for new sessions and auto-link them."""
    print(f"=== Watching for new sessions (polling every {interval}s) ===")
    print("  Press Ctrl+C to stop")
    known_count = DirectMemory.get_mapping_summary()["total_mappings"]
    try:
        while True:
            r = DirectMemory.cross_reference_ledgers()
            new = r.get("new_links", 0)
            if new > 0:
                total = r.get("total_mappings", known_count)
                print(f"  [{datetime.now().isoformat()}] +{new} new links (total: {total})")
                known_count = total
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Stopped")


def cmd_diff():
    """Show changes in mapping state since last call."""
    import sqlite3
    from CORE.direct_memory import DB_PATH
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    # Check for sessions that ended since last backfill
    recent = conn.execute(
        "SELECT session_id, started_at, ended_at FROM sessions "
        "WHERE ended_at IS NOT NULL AND ended_at > datetime('now', '-1 day') "
        "ORDER BY ended_at DESC LIMIT 10"
    ).fetchall()
    conn.close()
    print("=== Recent Changes (last 24h) ===")
    for r in recent:
        sid = r["session_id"][-24:]
        print(f"  DM=...{sid}  ended={r['ended_at']}")


def cmd_search(event_type: str):
    """Search mappings by event type."""
    rows = DirectMemory.query_cross_reference()
    matches = [r for r in rows if r.get("event_type") == event_type]
    print(f"=== Mappings with event_type='{event_type}' ({len(matches)}) ===")
    for r in matches[:20]:
        dm = r.get("dm_session_id", r.get("session_id", "?"))[-24:]
        aj = str(r.get("audit_filepath", "?"))[-28:]
        print(f"  DM=...{dm}  ->  AJ=...{aj}")
    if len(matches) > 20:
        print(f"  ... and {len(matches) - 20} more")


def cmd_interactive():
    """Interactive cross-reference browser."""
    cols, _ = shutil.get_terminal_size()
    print("=" * cols)
    print("  Cross-Reference Interactive Browser")
    print("=" * cols)
    print("  Commands: summary, list, backfill, unlinked, query <session>,")
    print("            corr <id>, search <type>, export <csv|json>,")
    print("            diff, links <limit>, help, quit")
    print()
    while True:
        try:
            line = input("xref> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "quit":
            break
        elif cmd == "summary":
            cmd_summary()
        elif cmd == "list":
            cmd_list()
        elif cmd == "backfill":
            cmd_backfill()
        elif cmd == "unlinked":
            cmd_unlinked()
        elif cmd == "diff":
            cmd_diff()
        elif cmd == "links":
            rows = DirectMemory.query_cross_reference()
            n = rows[:int(arg)] if arg.isdigit() else rows[:10]
            for r in n:
                dm = r.get("dm_session_id", "?")[-20:]
                aj = str(r.get("audit_filepath", "?"))[-28:]
                print(f"  DM=...{dm}  ->  AJ=...{aj}")
        elif cmd == "query" and arg:
            cmd_query(arg)
        elif cmd == "corr" and arg:
            cmd_correlation(arg)
        elif cmd == "search" and arg:
            cmd_search(arg)
        elif cmd == "export" and arg:
            cmd_export(arg)
        elif cmd == "help":
            print("  Commands: summary, list, backfill, unlinked, query <session>,")
            print("            corr <id>, search <type>, export <csv|json>,")
            print("            diff, links <limit>, help, quit")
        else:
            print(f"  Unknown command: {cmd}")


def cmd_archive(filepath: str):
    """Archive/close a session (mark as ended if open)."""
    from CORE.audit_trail import lock_audit_file
    fp = Path(filepath)
    if not fp.exists():
        print(f"  File not found: {filepath}")
        return
    # Try to close via audit trail
    try:
        lock_audit_file(str(fp))
        print(f"  Locked: {fp.name}")
    except Exception as e:
        print(f"  Lock failed: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    commands = {
        "summary": cmd_summary,
        "backfill": cmd_backfill,
        "list": cmd_list,
        "unlinked": cmd_unlinked,
        "diff": cmd_diff,
    }
    if cmd in commands:
        commands[cmd]()
    elif cmd in ("query", "corr", "archive") and len(sys.argv) >= 3:
        globals()[f"cmd_{cmd}"](sys.argv[2])
    elif cmd == "export" and len(sys.argv) >= 3:
        cmd_export(sys.argv[2])
    elif cmd == "watch":
        interval = int(sys.argv[2]) if len(sys.argv) >= 3 and sys.argv[2].isdigit() else 30
        cmd_watch(interval)
    elif cmd == "search" and len(sys.argv) >= 3:
        cmd_search(sys.argv[2])
    elif cmd == "interactive":
        cmd_interactive()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
