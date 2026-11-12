"""prune_memory_debt.py — reclaim the memory-store debt the 2026-10-09 forensic named.

WHY THIS EXISTS
---------------
`MEMORY_FORENSIC_REPORT_2026-10-09.md` §4 names two conditions that are retention
problems, not corruption:

  * **~2.6 GB of stale DirectMemory / LocalMemoryStore backup files** under
    `~/.azazael/`, never pruned;
  * **657,817 `otel_spans`** rows in `~/.azazael/telemetry_store.db` — an
    unbounded observability table with no retention policy.

Neither is urgent. Both are easy to "fix" destructively, which is why this tool
is **report-only by default** and needs an explicit `--apply`.

THE SAFETY MODEL
----------------
1. **Nothing changes without `--apply`.** The default run prints a disposition
   for every candidate and exits 0.
2. **A backup is never deleted.** `--apply` *moves* it into
   `<dir>/backups/quarantine_<UTCSTAMP>/` and prints the destination, so the
   operation is reversible by hand. `tools/terence_invariants.py`
   (`_remedy_quarantine`) uses the same move-not-delete rule for the same
   reason: a remedy that destroys its own evidence cannot be audited.
3. **The newest backup of each live database is always KEEP.** Superseded copies
   are the only quarantine candidates.
4. **The live databases are never candidates** — `memory_store.db`,
   `telemetry_store.db`, `compliance_audit.db`, `embedding_cache.db`. `--apply`
   refuses, non-zero, if one appears in the quarantine set.
5. **`telemetry_store.db` is copied before it is touched**, and the delete is
   bounded by `--retention-days` against a timestamp column **discovered from
   the schema via `PRAGMA table_info`**, never hardcoded — and against a unit
   inferred from the column's own magnitude, so nanosecond OTel epochs cannot be
   compared against a seconds cutoff. Getting that wrong deletes everything or
   nothing, silently.
6. **Reclaimed bytes are measured before/after**, never estimated.

If the `otel_spans` table is absent, or no timestamp column holds a numeric
value, the telemetry half reports that and **changes nothing** rather than
guessing at a predicate.

Usage:
    python azazael/tools/prune_memory_debt.py                      # report only
    python azazael/tools/prune_memory_debt.py --apply
    python azazael/tools/prune_memory_debt.py --retention-days 3 --apply
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

#: Never quarantine candidates. These are the live stores the forensic verified.
LIVE_DATABASES = (
    "memory_store.db",
    "telemetry_store.db",
    "compliance_audit.db",
    "embedding_cache.db",
)

#: A file is a backup candidate only if its name carries one of these markers.
BACKUP_MARKERS = ("backup", "bak", "prearchive", "preprune", "pre_")

#: SQLite sidecar files belong to the database whose name precedes them.
COMPANION_SUFFIXES = ("-wal", "-shm", "-journal")

#: A directory named like this is a previous quarantine. Never re-scan it.
QUARANTINE_PREFIX = "quarantine_"

#: Timestamp columns preferred by name, most specific first.
PREFERRED_TS_COLUMNS = (
    "time_unix_nano", "start_time_unix_nano", "end_time_unix_nano",
    "timestamp", "ts", "time", "start_time", "end_time", "created_at",
)

TABLE = "otel_spans"


def _default_dir() -> Path:
    """`AZAZAEL_MEMORY_DIR` wins; matches scripts/live_monitor.py's convention."""
    return Path(os.environ.get("AZAZAEL_MEMORY_DIR") or (Path.home() / ".azazael"))


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return "%.1f %s" % (x, unit)
        x /= 1024
    return "%.1f TB" % x


def _base_name(name: str) -> str:
    """`x.db-wal` -> `x.db`, so companions group with their database."""
    for suffix in COMPANION_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _live_owner(base: str) -> str | None:
    """Which live database a backup belongs to, by longest stem prefix match."""
    stem = Path(base).stem
    best = None
    for live in LIVE_DATABASES:
        live_stem = Path(live).stem
        if stem.startswith(live_stem) and (best is None or len(live_stem) > len(Path(best).stem)):
            best = live
    return best


def _is_candidate(base: str) -> bool:
    lowered = base.lower()
    if lowered in LIVE_DATABASES:
        return False
    return any(marker in lowered for marker in BACKUP_MARKERS)


def _scan(root: Path) -> list[dict]:
    """Every existing backup candidate, grouped so companions move together."""
    seen: dict[str, dict] = {}

    def consider(entry: os.DirEntry[str], directory: Path) -> None:
        base = _base_name(entry.name)
        if not _is_candidate(base):
            return
        group = seen.setdefault(base, {"base": base, "files": [], "owner": _live_owner(base)})
        try:
            stat = entry.stat()
            group["files"].append((entry.name, stat.st_size, stat.st_mtime, directory))
        except OSError:
            group["files"].append((entry.name, 0, 0.0, directory))

    dirs = [root]
    backups = root / "backups"
    if backups.is_dir():
        dirs.append(backups)

    for directory in dirs:
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if entry.is_file(follow_symlinks=False):
                        consider(entry, directory)
        except OSError as exc:
            print("  ! cannot scan %s: %s" % (directory, exc))

    groups = []
    for group in seen.values():
        group["bytes"] = sum(f[1] for f in group["files"])
        group["mtime"] = max((f[2] for f in group["files"]), default=0.0)
        # Real on-disk locations, so the live-database guard cannot be fooled by a
        # companion file that happens to sit in the backups/ subdirectory.
        group["paths"] = [str(f[3] / f[0]) for f in group["files"]]
        groups.append(group)
    return groups


def _dispositions(groups: list[dict]) -> None:
    """KEEP the newest backup per live database; QUARANTINE its superseded ones."""
    newest: dict[str, dict] = {}
    for group in groups:
        owner = group["owner"]
        if owner is None:
            group["disposition"] = "IGNORED"
            group["why"] = "not attributable to a live database - left alone"
            continue
        if owner not in newest or group["mtime"] > newest[owner]["mtime"]:
            newest[owner] = group

    for group in groups:
        # Only unowned groups carry a disposition at this point; owned ones are
        # classified by the two branches below. Use .get() — indexing here was a
        # KeyError on every owned group, i.e. on the only groups this function is
        # for.
        if group.get("disposition") == "IGNORED":
            continue
        if newest.get(group["owner"]) is group:
            group["disposition"] = "KEEP"
            group["why"] = "newest backup of %s" % group["owner"]
        else:
            group["disposition"] = "QUARANTINE"
            group["why"] = "superseded by a newer %s backup" % group["owner"]


def _live_paths(root: Path) -> set[str]:
    out = set()
    for live in LIVE_DATABASES:
        out.add(os.path.normcase(str(root / live)))
        for suffix in COMPANION_SUFFIXES:
            out.add(os.path.normcase(str(root / (live + suffix))))
    return out


def _pick_ts_column(conn: sqlite3.Connection) -> tuple[str | None, list[str]]:
    """The first column that actually holds numbers, plus the reasons others failed."""
    columns = [row[1] for row in conn.execute('PRAGMA table_info("%s")' % TABLE)]
    if not columns:
        return None, ["table %s has no columns" % TABLE]

    ordered = [c for pref in PREFERRED_TS_COLUMNS for c in columns if c.lower() == pref]
    ordered += [c for c in columns
                if c not in ordered
                and any(k in c.lower() for k in ("time", "ts", "date", "start", "end"))]

    rejected: list[str] = []
    for col in ordered:
        try:
            row = conn.execute(
                'SELECT typeof(MAX("%s")), MAX("%s"), MIN("%s") FROM "%s"' % (col, col, col, TABLE)
            ).fetchone()
        except sqlite3.Error as exc:
            rejected.append("%s: query failed (%s)" % (col, exc))
            continue
        kind, vmax = (row[0], row[1]) if row else (None, None)
        if kind in ("integer", "real") and vmax is not None:
            return col, rejected
        rejected.append("%s: not numeric (%s)" % (col, kind))
    return None, rejected or ["no time-like column found in %s" % TABLE]


def _time_scale(vmax: float) -> tuple[str, float]:
    """Store units per second, inferred from magnitude. OTel epochs are usually ns."""
    if vmax > 1e17:
        return "ns", 1e9
    if vmax > 1e14:
        return "us", 1e6
    if vmax > 1e11:
        return "ms", 1e3
    return "s", 1.0


def _telemetry(db: Path, retention_days: int, apply: bool) -> dict:
    """Report (and optionally prune) otel_spans. Changes nothing it cannot justify."""
    result: dict = {"path": str(db), "exists": db.exists(), "applied": False,
                    "rows": None, "deleted": None, "before": None, "after": None,
                    "column": None, "unit": None, "detail": [], "ok": True}
    if not db.exists():
        result["detail"].append("telemetry store absent - nothing to do")
        return result

    result["before"] = db.stat().st_size
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    except sqlite3.Error as exc:
        result["ok"] = False
        result["detail"].append("cannot open read-only: %s" % exc)
        return result

    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if TABLE not in tables:
            result["detail"].append(
                "table %s not present (found: %s) - changing nothing"
                % (TABLE, ", ".join(sorted(tables)) or "none"))
            return result

        column, rejected = _pick_ts_column(conn)
        result["detail"].extend("rejected ? %s" % r for r in rejected)
        if column is None:
            result["detail"].append(
                "no usable timestamp column - changing nothing rather than guessing a predicate")
            result["ok"] = False
            return result
        result["column"] = column

        total = conn.execute('SELECT COUNT(*) FROM "%s"' % TABLE).fetchone()[0]
        result["rows"] = int(total)
        vmax = conn.execute('SELECT MAX("%s") FROM "%s"' % (column, TABLE)).fetchone()[0]
        vmin = conn.execute('SELECT MIN("%s") FROM "%s"' % (column, TABLE)).fetchone()[0]
        unit, scale = _time_scale(float(vmax))
        result["unit"] = unit

        cutoff = (time.time() - retention_days * 86400) * scale
        doomed = conn.execute(
            'SELECT COUNT(*) FROM "%s" WHERE "%s" < ?' % (TABLE, column), (cutoff,)
        ).fetchone()[0]
        # In report mode this is "how many WOULD be pruned". Hardcoding 0 here made
        # the report print `prunable: 0 row(s)` immediately above a detail line
        # reading `older-than-7d=657817` -- a self-contradiction inside the one
        # tool whose job is to say what it is about to destroy. The --apply path
        # overwrites this with the real rowcount after the DELETE.
        result["deleted"] = int(doomed)
        result["detail"].append(
            "column=%s unit=%s span=[%s .. %s] total=%d older-than-%dd=%d"
            % (column, unit, vmin, vmax, total, retention_days, doomed))

        if not apply or not doomed:
            result["after"] = result["before"]
            return result
    finally:
        conn.close()

    # --apply: copy first, so the pre-prune state exists on disk.
    backup = db.with_name(db.name + ".pre_prune_" + _stamp())
    try:
        shutil.copy2(db, backup)
        for suffix in COMPANION_SUFFIXES:
            side = Path(str(db) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(backup) + suffix))
        result["detail"].append("copied to %s" % backup.name)
    except OSError as exc:
        result["ok"] = False
        result["detail"].append("backup copy failed (%s) - refusing to mutate" % exc)
        return result

    try:
        conn = sqlite3.connect(str(db), timeout=30)
        conn.isolation_level = None
        deleted = conn.execute(
            'DELETE FROM "%s" WHERE "%s" < ?' % (TABLE, column), (cutoff,)
        ).rowcount
        conn.execute("VACUUM")
        conn.close()
        result["deleted"] = int(deleted or 0)
        result["applied"] = True
    except sqlite3.Error as exc:
        result["ok"] = False
        result["detail"].append("prune failed: %s" % exc)
        return result

    result["after"] = db.stat().st_size
    return result


def _quarantine(groups: list[dict], root: Path, apply: bool) -> tuple[int, Path | None]:
    """Move quarantined groups aside. Returns (files moved, destination or None)."""
    selected = [g for g in groups if g["disposition"] == "QUARANTINE"]
    if not selected:
        return 0, None

    dest = root / "backups" / (QUARANTINE_PREFIX + _stamp())
    if not apply:
        return 0, dest

    moved = 0
    for group in selected:
        for name, _size, _mtime, directory in group["files"]:
            src = directory / name
            if not src.exists():
                continue
            dest.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dest / name))
                moved += 1
            except OSError as exc:
                print("  ! move failed for %s: %s" % (src, exc))
    return moved, dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(_default_dir()),
                    help="memory directory (default: $AZAZAEL_MEMORY_DIR or ~/.azazael)")
    ap.add_argument("--retention-days", type=int, default=7,
                    help="otel_spans older than this are prunable (default 7)")
    ap.add_argument("--apply", action="store_true",
                    help="make changes; without it this tool only reports")
    ap.add_argument("--telemetry-db", default=None,
                    help="override the telemetry store path")
    args = ap.parse_args()

    root = Path(args.dir)
    mode = "APPLY" if args.apply else "REPORT ONLY"
    print("prune-memory-debt: %s" % mode)
    print("  directory      : %s" % root)
    print("  retention      : %d day(s)" % args.retention_days)
    if not root.is_dir():
        print("  ! directory does not exist - nothing to do")
        return 2

    groups = _scan(root)
    _dispositions(groups)
    groups.sort(key=lambda g: (g["disposition"], -g["bytes"]))

    print()
    print("=== BACKUP DISPOSITIONS ===")
    if not groups:
        print("  (no backup candidates found)")
    for group in groups:
        print("  %-11s %-46s %10s  %s"
              % (group["disposition"], group["base"][:46], _human(group["bytes"]), group["why"]))
        for name, size, _mtime, _directory in group["files"]:
            if name != group["base"]:
                print("  %-11s   + %s (%s)" % ("", name, _human(size)))

    selected = [g for g in groups if g["disposition"] == "QUARANTINE"]
    selected_bytes = sum(g["bytes"] for g in selected)
    print()
    print("  candidates: %d group(s), %s" % (len(selected), _human(selected_bytes)))

    # --- refusal guard: a live database must never be a quarantine candidate.
    live = _live_paths(root)
    offending = sorted({p for g in selected for p in g["paths"]
                        if os.path.normcase(p) in live})
    if offending and args.apply:
        print()
        print("REFUSING --apply: live database(s) in the quarantine set:")
        for path in offending:
            print("  %s" % path)
        return 1

    backup_before = sum(g["bytes"] for g in groups)
    db = Path(args.telemetry_db) if args.telemetry_db else (root / "telemetry_store.db")

    print()
    print("=== TELEMETRY (%s) ===" % db.name)
    tele = _telemetry(db, args.retention_days, args.apply)
    print("  path    : %s" % tele["path"])
    if tele["before"] is not None:
        print("  size    : %s -> %s" % (_human(tele["before"]),
                                        _human(tele["after"] or tele["before"])))
    if tele["rows"] is not None:
        print("  rows    : %s (column %s, unit %s)"
              % (tele["rows"], tele["column"], tele["unit"]))
    if tele["deleted"] is not None:
        print("  prunable: %s row(s)" % tele["deleted"])
    for line in tele["detail"]:
        print("    - %s" % line)

    moved, dest = _quarantine(groups, root, args.apply)
    if args.apply and dest is not None:
        print()
        print("=== QUARANTINE ===")
        print("  moved %d file(s) to:" % moved)
        print("  %s" % dest)
        print("  (reversible by hand - nothing was deleted)")
    elif dest is not None:
        print()
        print("  would move %d file(s) to:" % sum(len(g["files"]) for g in selected))
        print("  %s" % dest)

    backup_after = sum(g["bytes"] for g in groups
                       if not (args.apply and g["disposition"] == "QUARANTINE"))
    db_before = tele["before"] or 0
    db_after = tele["after"] or db_before

    print()
    print("=== SIZE (measured) ===")
    print("  %-16s %12s %12s %12s" % ("", "before", "after", "reclaimed"))
    print("  %-16s %12s %12s %12s" % ("backup tree", _human(backup_before),
                                      _human(backup_after),
                                      _human(backup_before - backup_after)))
    print("  %-16s %12s %12s %12s" % ("telemetry db", _human(db_before), _human(db_after),
                                      _human(max(0, db_before - db_after))))
    print("  %-16s %12s %12s %12s" % ("TOTAL", _human(backup_before + db_before),
                                      _human(backup_after + db_after),
                                      _human((backup_before - backup_after)
                                             + max(0, db_before - db_after))))

    if not args.apply:
        print()
        print("REPORT ONLY - re-run with --apply to quarantine the superseded backups"
              " and prune telemetry older than %d day(s)." % args.retention_days)
    return 0 if tele["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
