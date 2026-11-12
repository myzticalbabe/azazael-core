"""
RULE 0X-7c: Scanner Findings Database

SQLite-backed persistence for scan results enabling:
- History tracking over time
- Trend analysis (increasing/decreasing findings)
- Regression detection (new findings in previously clean files)

Usage:
    from CORE.compliance.scanner_db import FindingsDB

    db = FindingsDB("compliance_history.db")
    db.store_scan(scan_data)
    history = db.get_history(days=7)
    trend = db.get_trend()
    regressions = db.get_regressions()
"""

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class FindingsDB:
    """SQLite-backed findings database for scan history and trend analysis.

    Thread-safe. Uses WAL mode for concurrent reads.
    """

    def __init__(self, db_path: (str | None) = None):
        """Initialize the database.

        Args:
            db_path: Path to SQLite database file. Defaults to
                     src/CORE/compliance/compliance_history.db
        """
        if db_path is None:
            db_path = str(
                Path(__file__).resolve().parent / "compliance_history.db"
            )
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=30)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS scans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scan_id TEXT UNIQUE NOT NULL,
                        directory TEXT NOT NULL,
                        total_files INTEGER DEFAULT 0,
                        total_findings INTEGER DEFAULT 0,
                        files_with_findings INTEGER DEFAULT 0,
                        compliance_score REAL DEFAULT 0.0,
                        scanned_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS findings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scan_id TEXT NOT NULL,
                        filepath TEXT NOT NULL,
                        line INTEGER DEFAULT 0,
                        category TEXT NOT NULL,
                        snippet TEXT DEFAULT '',
                        scanned_at TEXT NOT NULL,
                        FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_findings_scan_id
                    ON findings(scan_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_findings_category
                    ON findings(category)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_findings_filepath
                    ON findings(filepath)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_scans_scanned_at
                    ON scans(scanned_at)
                """)
                conn.commit()
            finally:
                conn.close()

    def store_scan(self, scan_data: dict[str, Any]) -> str:
        """Store a complete scan result in the database.

        Args:
            scan_data: dict with keys: scanned_at, directory, total_files,
                      total_findings, files_with_findings, compliance_score,
                      results (list of finding dicts)

        Returns:
            scan_id of the stored scan
        """
        scan_id = (
            datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        )
        scanned_at = scan_data.get("scanned_at",
            datetime.now(timezone.utc).isoformat(timespec="seconds"))

        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=30)
            try:
                conn.execute(
                    """INSERT INTO scans
                       (scan_id, directory, total_files, total_findings,
                        files_with_findings, compliance_score, scanned_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scan_id,
                        scan_data.get("directory", "."),
                        scan_data.get("total_files", 0),
                        scan_data.get("total_findings", 0),
                        scan_data.get("files_with_findings", 0),
                        scan_data.get("compliance_score", 0.0),
                        scanned_at,
                    ),
                )

                # Store individual findings
                results = scan_data.get("results", [])
                for r in results:
                    filepath = r.get("filepath", "")
                    for f in r.get("findings", []):
                        conn.execute(
                            """INSERT INTO findings
                               (scan_id, filepath, line, category, snippet, scanned_at)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                scan_id,
                                filepath,
                                f.get("line", 0),
                                f.get("category", "unknown"),
                                f.get("snippet", "")[:200],
                                scanned_at,
                            ),
                        )

                conn.commit()
            finally:
                conn.close()

        return scan_id

    def get_history(self, days: int = 7) -> list[dict[str, Any]]:
        """Get scan history for the last N days.

        Args:
            days: Number of days of history to retrieve

        Returns:
            list of scan summary dicts, newest first
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=30)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """SELECT scan_id, directory, total_files, total_findings,
                              files_with_findings, compliance_score, scanned_at
                       FROM scans
                       WHERE scanned_at >= ?
                       ORDER BY scanned_at DESC""",
                    (cutoff,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def get_trend(self, days: int = 30) -> dict[str, Any]:
        """Analyze finding trends over time.

        Compares the most recent scan to the earliest scan within the
        specified window to determine if findings are increasing or decreasing
        per category.

        Args:
            days: Number of days to analyze

        Returns:
            dict with trend analysis:
            {
                "categories": {category: {"first": N, "last": M, "change": +/-N}},
                "overall": {"first": N, "last": M, "change": +/-N, "direction": "up|down|flat"},
            }
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=30)
            try:
                # Get scan IDs in date range
                cursor = conn.execute(
                    """SELECT scan_id, scanned_at
                       FROM scans
                       WHERE scanned_at >= ?
                       ORDER BY scanned_at ASC""",
                    (cutoff,),
                )
                scan_rows = cursor.fetchall()

                if len(scan_rows) < 2:
                    return {
                        "categories": {},
                        "overall": {
                            "first": 0, "last": 0,
                            "change": 0, "direction": "flat",
                            "message": "Not enough data for trend analysis (need 2+ scans)",
                        },
                    }

                first_scan = scan_rows[0][0]
                last_scan = scan_rows[-1][0]

                # Get category counts for first and last scans
                def get_category_counts(scan_id: str) -> dict[str, int]:
                    cur = conn.execute(
                        """SELECT category, COUNT(*) as cnt
                           FROM findings WHERE scan_id = ?
                           GROUP BY category""",
                        (scan_id,),
                    )
                    return {row[0]: row[1] for row in cur.fetchall()}

                first_cats = get_category_counts(first_scan)
                last_cats = get_category_counts(last_scan)

                all_categories = set(first_cats.keys()) | set(last_cats.keys())
                categories = {}
                overall_first = sum(first_cats.values())
                overall_last = sum(last_cats.values())

                for cat in sorted(all_categories):
                    fc = first_cats.get(cat, 0)
                    lc = last_cats.get(cat, 0)
                    change = lc - fc
                    categories[cat] = {
                        "first": fc,
                        "last": lc,
                        "change": change,
                        "direction": "up" if change > 0 else "down" if change < 0 else "flat",
                    }

                overall_change = overall_last - overall_first
                return {
                    "categories": categories,
                    "overall": {
                        "first": overall_first,
                        "last": overall_last,
                        "change": overall_change,
                        "direction": (
                            "up" if overall_change > 0
                            else "down" if overall_change < 0
                            else "flat"
                        ),
                    },
                }
            finally:
                conn.close()

    def get_regressions(self, days: int = 7) -> list[dict[str, Any]]:
        """Find regressions: files with new findings that were clean before.

        A regression is a file that:
        - Had 0 findings in all prior scans within the window
        - Has 1+ findings in the most recent scan

        Args:
            days: Number of days to look back

        Returns:
            list of regression dicts with filepath, findings, scan info
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=30)
            try:
                conn.row_factory = sqlite3.Row

                # Get the most recent scan
                cursor = conn.execute(
                    """SELECT scan_id, scanned_at
                       FROM scans WHERE scanned_at >= ?
                       ORDER BY scanned_at DESC LIMIT 1""",
                    (cutoff,),
                )
                latest = cursor.fetchone()
                if not latest:
                    return []

                latest_id = latest["scan_id"]
                latest_at = latest["scanned_at"]

                # Get all prior scan IDs
                cursor = conn.execute(
                    """SELECT scan_id FROM scans
                       WHERE scanned_at >= ? AND scan_id != ?
                       ORDER BY scanned_at ASC""",
                    (cutoff, latest_id),
                )
                prior_ids = [row["scan_id"] for row in cursor.fetchall()]

                if not prior_ids:
                    return []

                # Find files that had findings in the latest scan
                cursor = conn.execute(
                    """SELECT DISTINCT filepath FROM findings
                       WHERE scan_id = ?""",
                    (latest_id,),
                )
                latest_files = {row["filepath"] for row in cursor.fetchall()}

                # Find which of those files had findings in any prior scan
                regressions = []
                placeholders = ",".join("?" for _ in prior_ids)
                for fp in sorted(latest_files):
                    cursor = conn.execute(
                        f"""SELECT COUNT(*) as cnt FROM findings
                            WHERE scan_id IN ({placeholders})
                            AND filepath = ?""",
                        list(prior_ids) + [fp],
                    )
                    prior_count = cursor.fetchone()["cnt"]

                    if prior_count == 0:
                        # Get the new findings
                        cursor = conn.execute(
                            """SELECT line, category, snippet
                               FROM findings
                               WHERE scan_id = ? AND filepath = ?""",
                            (latest_id, fp),
                        )
                        new_findings = [
                            {
                                "line": row["line"],
                                "category": row["category"],
                                "snippet": row["snippet"],
                            }
                            for row in cursor.fetchall()
                        ]
                        regressions.append({
                            "filepath": fp,
                            "findings_count": len(new_findings),
                            "findings": new_findings,
                            "detected_at": latest_at,
                        })

                return regressions
            finally:
                conn.close()

    def get_summary(self) -> dict[str, Any]:
        """Get a quick summary of the database state."""
        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=30)
            try:
                cursor = conn.execute("SELECT COUNT(*) FROM scans")
                total_scans = cursor.fetchone()[0]

                cursor = conn.execute("SELECT COUNT(*) FROM findings")
                total_findings = cursor.fetchone()[0]

                cursor = conn.execute(
                    "SELECT scanned_at FROM scans ORDER BY scanned_at DESC LIMIT 1"
                )
                last_scan = cursor.fetchone()
                last_scan_at = last_scan[0] if last_scan else None

                cursor = conn.execute(
                    "SELECT COUNT(DISTINCT filepath) FROM findings"
                )
                unique_files = cursor.fetchone()[0]

                return {
                    "db_path": self._db_path,
                    "total_scans": total_scans,
                    "total_findings_stored": total_findings,
                    "unique_files_affected": unique_files,
                    "last_scan_at": last_scan_at,
                }
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# CLI helpers for scanner integration
# ---------------------------------------------------------------------------


def print_history(history: list[dict[str, Any]]):
    """Pretty-print scan history."""
    if not history:
        print("  No scan history found.")
        return

    print(f"  {'Scan ID':20} {'Files':>6} {'Findings':>9} {'Score':>6}  Date")
    print(f"  {'-'*20} {'-'*6} {'-'*9} {'-'*6}  {'-'*20}")
    for h in history:
        score = h.get("compliance_score", 0)
        print(
            f"  {h['scan_id']:20} {h['total_files']:>6} "
            f"{h['total_findings']:>9} {score:>5.0f}  {h['scanned_at']}"
        )


def print_trend(trend: dict[str, Any]):
    """Pretty-print trend analysis."""
    overall = trend.get("overall", {})
    arrow = {"up": "▲", "down": "▼", "flat": "→"}
    dir_arrow = arrow.get(overall.get("direction", "flat"), "→")

    print(f"  Overall: {overall.get('first', 0)} → {overall.get('last', 0)} "
          f"({overall.get('change', 0):+d}) {dir_arrow}")

    if overall.get("message"):
        print(f"  {overall['message']}")
        return

    cats = trend.get("categories", {})
    if cats:
        print()
        print(f"  {'Category':25} {'First':>6} {'Last':>6} {'Change':>7}  Trend")
        print(f"  {'-'*25} {'-'*6} {'-'*6} {'-'*7}  {'-'*6}")
        for cat, data in cats.items():
            ch = data["change"]
            a = arrow.get(data["direction"], "→")
            print(f"  {cat:25} {data['first']:>6} {data['last']:>6} "
                  f"{ch:+>7d}  {a}")


def print_regressions(regressions: list[dict[str, Any]]):
    """Pretty-print regression analysis."""
    if not regressions:
        print("  No regressions detected.")
        return

    print(f"  {len(regressions)} regression(s) detected:")
    print()
    for r in regressions:
        print(f"  FILE: {r['filepath']}")
        print(f"  Findings: {r['findings_count']}")
        print(f"  Detected: {r['detected_at']}")
        for f in r.get("findings", [])[:5]:
            print(f"    L{f['line']} [{f['category']}] {f['snippet'][:60]}")
        if r["findings_count"] > 5:
            print(f"    ... +{r['findings_count'] - 5} more")
        print()
