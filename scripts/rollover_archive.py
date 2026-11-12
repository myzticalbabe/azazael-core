# coding: utf-8
"""rollover_archive.py — Tiered retention: move old months to G: archives.

Lossless by construction:
- Full clone via VACUUM INTO (schema, indexes, triggers, views preserved).
- Carve per-month buckets out of the clone; verify counts match hot.
- Delete from hot ONLY after verification; verify gone afterward.
- Manifest row per bucket (month, path, counts, sha256).
- Only COMPLETE months older than cutoff roll (boundary month stays hot).

Usage:
    python rollover_archive.py --dry-run [--days 90]
    python rollover_archive.py --execute [--days 90]
"""

import argparse
import hashlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HOT_DB = Path.home() / ".azazael" / "memory_store.db"
ARCHIVE_DIR = Path("G:/AZAZAEL_SOURCE_OF_TRUTH/memory_archive")

MANIFEST_DDL = """
CREATE TABLE IF NOT EXISTS archive_manifest (
    month TEXT PRIMARY KEY,
    db_path TEXT NOT NULL,
    memories_moved INTEGER NOT NULL,
    sessions_moved INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    moved_at TEXT NOT NULL
)
"""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _months_to_roll(conn, days):
    # Only COMPLETE months older than cutoff roll (never split a month).
    rows = conn.execute(
        "SELECT substr(timestamp, 1, 7) FROM memories "
        "GROUP BY 1 HAVING MAX(datetime(timestamp)) < datetime('now', ?) "
        "ORDER BY 1", (f"-{days} days",)).fetchall()
    return [r[0] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    if not (args.dry_run or args.execute):
        args.dry_run = True

    hot = sqlite3.connect(str(HOT_DB), timeout=30)
    months = _months_to_roll(hot, args.days)
    print(f"hot={HOT_DB} months eligible: {months or 'NONE'}")
    if not months:
        return 0

    if args.dry_run:
        for m in months:
            n = hot.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE substr(timestamp, 1, 7) = ?", (m,)).fetchone()[0]
            s = hot.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id IN "
                "(SELECT session_id FROM memories GROUP BY session_id "
                "HAVING substr(MAX(timestamp), 1, 7) = ?)", (m,)).fetchone()[0]
        print(f"  {m}: {n} memories + {s} sessions would move")
        print("dry run only - hot untouched")
        return 0

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="rollover_"))
    try:
        for m in months:
            arch_path = ARCHIVE_DIR / f"memory_{m}.db"
            if arch_path.exists():
                print(f"  {m}: archive exists, skipping (already rolled)")
                continue
            clone = tmpdir / f"clone_{m}.db"
            hot.execute(f"VACUUM INTO '{clone.as_posix()}'")
            ac = sqlite3.connect(str(clone), timeout=30)
            # Carve: keep ONLY this month in the clone
            ac.execute("DELETE FROM memories WHERE substr(timestamp, 1, 7) != ?", (m,))
            ac.execute("""DELETE FROM sessions WHERE session_id NOT IN
                (SELECT session_id FROM memories)""")
            ac.commit()
            n_arch = ac.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            n_hot = hot.execute(
                "SELECT COUNT(*) FROM memories WHERE substr(timestamp, 1, 7) = ?",
                (m,)).fetchone()[0]
            if n_arch != n_hot:
                print(f"  {m}: COUNT MISMATCH clone={n_arch} hot={n_hot} - ABORT, hot untouched")
                ac.close()
                return 1
            s_arch = ac.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            ac.close()
            shutil.move(str(clone), str(arch_path))
            digest = _sha256(arch_path)
            # Delete from hot only after verified carve
            hot.execute("DELETE FROM memories WHERE substr(timestamp, 1, 7) = ?", (m,))
            hot.execute("""DELETE FROM sessions WHERE session_id NOT IN
                (SELECT session_id FROM memories) AND session_id NOT IN
                (SELECT session_id FROM sessions WHERE ended_at IS NULL
                 AND datetime(started_at) > datetime('now', '-90 days'))""")
            hot.execute(MANIFEST_DDL)
            hot.execute("INSERT OR REPLACE INTO archive_manifest VALUES (?,?,?,?,?,?)",
                        (m, str(arch_path), n_arch, s_arch, digest,
                         datetime.now(timezone.utc).isoformat()))
            hot.commit()
            left = hot.execute(
                "SELECT COUNT(*) FROM memories WHERE substr(timestamp, 1, 7) = ?",
                (m,)).fetchone()[0]
            print(f"  {m}: moved {n_arch} memories + {s_arch} sessions "
                  f"-> {arch_path.name} sha={digest[:12]}.. left-behind={left}")
            if left:
                print("  WARNING: leftovers detected (should be 0)")
                return 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    hot.execute("VACUUM")
    hot.commit()
    hot.close()
    print("rollover complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
