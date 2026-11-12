#!/usr/bin/env python
"""Archive and trim the RL step tap (`episode_complete`), then reclaim the file.

Measured 2026-10-31: 579 686 of the store's 595 212 rows were `episode_complete`
-- one row per RL step, ~23/s across processes, written in about seven hours --
and the type had NO entry in data_retention.TYPE_CAPS, so nothing ever bounded
it. The payload was only 114 MB; the file was 1834 MB because pruning had left
1 064 MB of free pages.

Order matters and is the point of this script: export -> verify the export by
reading it back -> only then delete -> VACUUM. A trim that cannot show what it
removed is indiscernible from data loss.

Usage:
    python tools/trim_episode_events.py [--keep 20000] [--apply]

Without --apply it measures and reports; nothing is deleted.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path.home() / ".azazael" / "memory_store.db"
ARCHIVE_DIR = Path("G:/MEMORY_ARCHIVE_2026-10-31")
EVENT = "episode_complete"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=20000,
                    help="rows of this event type to retain (newest)")
    ap.add_argument("--apply", action="store_true", help="delete after verifying the archive")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    db = Path(args.db)
    con = sqlite3.connect(str(db), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.row_factory = sqlite3.Row
    total = con.execute("SELECT COUNT(*) FROM memories WHERE event_type=?",
                        (EVENT,)).fetchone()[0]
    if total <= args.keep:
        print(f"{EVENT}: {total} rows <= keep={args.keep}; nothing to trim")
        return 0
    # Boundary: keep the newest `keep` rows, drop everything at or below.
    cut = con.execute(
        "SELECT timestamp FROM memories WHERE event_type=? ORDER BY timestamp DESC "
        "LIMIT 1 OFFSET ?", (EVENT, args.keep - 1)).fetchone()[0]
    drop_n = con.execute("SELECT COUNT(*) FROM memories WHERE event_type=? AND timestamp < ?",
                         (EVENT, cut)).fetchone()[0]
    print(f"{EVENT}: total={total} keep={args.keep} boundary={cut} to_drop={drop_n}")

    if not args.apply:
        print("dry run -- re-run with --apply to archive, verify, delete, vacuum")
        con.close()
        return 0

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE_DIR / f"{EVENT}-{datetime.now().strftime('%Y%m%d-%H%M')}.jsonl.gz"
    h = hashlib.sha256()
    written = 0
    with gzip.open(out, "wt", encoding="utf-8") as f:
        cur = con.execute(
            "SELECT id, timestamp, source, severity, data FROM memories "
            "WHERE event_type=? AND timestamp < ? ORDER BY timestamp", (EVENT, cut))
        for row in cur:
            line = json.dumps(dict(row), ensure_ascii=False) + "\n"
            h.update(line.encode("utf-8"))
            f.write(line)
            written += 1
    print(f"archived {written} rows -> {out} ({out.stat().st_size/1e6:.1f} MB)")

    # Verify by READING THE ARCHIVE BACK, not by trusting the write.
    read_back = 0
    h2 = hashlib.sha256()
    with gzip.open(out, "rt", encoding="utf-8") as f:
        for line in f:
            h2.update(line.encode("utf-8"))
            read_back += 1
    ok = read_back == written == drop_n and h.hexdigest() == h2.hexdigest()
    print(f"verify: read_back={read_back} expected={drop_n} sha_match={h.hexdigest() == h2.hexdigest()} "
          f"{'OK' if ok else 'MISMATCH'}")
    if not ok:
        print("NOT deleting -- archive did not verify")
        con.close()
        return 2

    deleted = con.execute("DELETE FROM memories WHERE event_type=? AND timestamp < ?",
                          (EVENT, cut)).rowcount
    con.commit()
    print(f"deleted {deleted} rows")
    before = db.stat().st_size / 1e6
    con.execute("VACUUM")
    con.close()
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    check = con.execute("PRAGMA quick_check").fetchone()[0]
    con.close()
    print(f"file: {before:.0f} MB -> {db.stat().st_size/1e6:.0f} MB, rows={rows}, integrity={check}")
    print(json.dumps({"archive": str(out), "sha256": h.hexdigest(), "deleted": deleted,
                      "file_mb": round(db.stat().st_size/1e6, 1),
                      "created_utc": datetime.now(timezone.utc).isoformat()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
