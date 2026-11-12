#!/usr/bin/env python3
# coding: utf-8
"""db_top.py — who is writing to the memory store, right now.

Missing-tool #1 (2026-09-27): lock collisions were fought blind — retries
against unknown writers. This answers "who is hot" from the rows writers
actually commit: per-source/event counts over a trailing window, plus the
largest single-row payloads (bulk writers show up by bytes, not just rows).
Read-only. Never raises (monitoring must not break callers).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _db() -> str:
    return str(Path.home() / ".azazael" / "memory_store.db")


def top_writers(minutes: int = 15, limit: int = 12) -> dict:
    """Per (source, event) row counts + byte volume over trailing window."""
    out: dict = {"window_min": minutes, "writers": [], "error": None}
    try:
        # Read-only (mode=ro): this tool only SELECTs; an rw open would
        # take a writer-class lock on the live store for no reason.
        con = sqlite3.connect(
            "file:%s?mode=ro" % _db().replace("\\", "/"),
            uri=True, timeout=10)
        try:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(minutes=minutes)).isoformat()
            rows = con.execute(
                "SELECT source, event_type, COUNT(*), "
                "COALESCE(SUM(LENGTH(data)), 0) FROM memories "
                "WHERE timestamp > ? GROUP BY 1, 2 "
                "ORDER BY 3 DESC LIMIT ?", (cutoff, limit)).fetchall()
            for source, event, n, b in rows:
                out["writers"].append({
                    "source": source, "event": event, "rows": n,
                    "kb": round((b or 0) / 1024.0, 1)})
        finally:
            con.close()
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="db_top",
                                 description="Top memory-store writers")
    ap.add_argument("--minutes", type=int, default=15)
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args(argv)
    rep = top_writers(args.minutes, args.limit)
    if rep["error"]:
        print("error: %s" % rep["error"])
        return 1
    print("writers, trailing %dm:" % rep["window_min"])
    for w in rep["writers"]:
        print("  %6d rows %9s  %s/%s" % (
            w["rows"], "%.1fKB" % w["kb"], w["source"], w["event"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
