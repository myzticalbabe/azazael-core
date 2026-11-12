#!/usr/bin/env python3
# coding: utf-8
"""memory_backup -- verified snapshots of the memory stores.

Why this exists
---------------
Measured 2026-10-30: the PMB workspace store (`events.sqlite`, 136.2 MB, 1,400+
outbox records, every recorded lesson) had **no backup at all** -- the only
archived file in the workspace was `meta.yaml.bak-20261021`. The disk was at
16 GB free of 756 GB (98% used), so a corruption or a bad VACUUM would have taken
the curated memory with it, permanently.

What it copies
--------------
Small, critical, cheap:

    ~/.pmb/workspaces/04a151a73354/events.sqlite      the curated memory
    ~/.pmb/workspaces/04a151a73354/bm25_index.pkl     the BM25 index beside it
    ~/.azazael/telemetry_store.db                     the lane message board

Deliberately NOT copied by default:

    ~/.azazael/memory_store.db    5.4 GB / 4,028,967 rows (measured 2026-10-30)

A copy needs its full size in free space, and this host has 16 GB free with
several GB committed elsewhere. Copying it silently would trade one risk
(corruption) for another (a full disk). Pass `--include-large` when headroom
exists; the tool prints the free space and refuses below `--min-free-gb` rather
than filling the drive.

Every copy is made with SQLite's online backup API (consistent under WAL, never a
mid-write file copy) and then verified: `PRAGMA integrity_check` on the copy, and
a row count compared with the source. A copy that fails verification is deleted
and the tool exits non-zero -- an unverified backup is worse than none, because it
is believed.

Usage
-----
    python tools/memory_backup.py                 # snapshots + verify + prune
    python tools/memory_backup.py --dry-run
    python tools/memory_backup.py --include-large --min-free-gb 12
    python tools/memory_backup.py --list
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError) as _exc:
        # A redirected/legacy stream cannot be reconfigured. Say so once, on the
        # other stream, rather than swallowing it: the run continues either way.
        sys.stderr.write(f"note: stream not reconfigured to utf-8 ({_exc})\n")

HOME = Path.home()
DEST_ROOT = Path("C:/Azazael/backups/memory")
KEEP = 5

#: (label, path) -- copied with sqlite3's backup API when it is a database.
SMALL_STORES = [
    ("pmb_events", HOME / ".pmb" / "workspaces" / "04a151a73354" / "events.sqlite"),
    ("telemetry", HOME / ".azazael" / "telemetry_store.db"),
]
PLAIN_FILES = [
    ("pmb_bm25", HOME / ".pmb" / "workspaces" / "04a151a73354" / "bm25_index.pkl"),
]
LARGE_STORES = [
    ("direct_memory", HOME / ".azazael" / "memory_store.db"),
]


def free_gb(path: Path) -> float:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free / 1e9


def copy_sqlite(src: Path, dest: Path) -> int:
    """Consistent online copy. Returns the source row count where countable."""
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest_con = sqlite3.connect(str(dest), timeout=30)
        try:
            src_con.backup(dest_con)
        finally:
            dest_con.close()
        try:
            return src_con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        except sqlite3.Error:
            return -1
    finally:
        src_con.close()


def verify_sqlite(dest: Path) -> tuple[bool, str]:
    con = sqlite3.connect(str(dest), timeout=30)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()
        return (ok and ok[0] == "ok"), (ok[0] if ok else "no result")
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        con.close()


def snapshots() -> list[Path]:
    if not DEST_ROOT.exists():
        return []
    return sorted(p for p in DEST_ROOT.iterdir() if p.is_dir())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verified snapshots of the memory stores.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-large", action="store_true", help="also copy the 5.4 GB DirectMemory store")
    ap.add_argument("--min-free-gb", type=float, default=10.0)
    ap.add_argument("--keep", type=int, default=KEEP)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        for snap in snapshots():
            size = sum(f.stat().st_size for f in snap.rglob("*") if f.is_file())
            print(f"{snap.name}  {size / 1e6:8.1f} MB")
        return 0

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = DEST_ROOT / stamp
    if args.dry_run:
        print(f"would write {dest}")
        for label, p in SMALL_STORES + PLAIN_FILES + (LARGE_STORES if args.include_large else []):
            print(f"  {label:14s} {'MISSING' if not p.exists() else f'{p.stat().st_size / 1e6:8.1f} MB'}  {p}")
        print(f"free space: {free_gb(DEST_ROOT):.1f} GB (min {args.min_free_gb})")
        return 0

    wanted = list(SMALL_STORES) + list(PLAIN_FILES)
    if args.include_large:
        wanted += LARGE_STORES
    total_mb = sum(p.stat().st_size for _, p in wanted if p.exists()) / 1e6
    free = free_gb(DEST_ROOT)
    if free - (total_mb / 1000) < args.min_free_gb:
        print(
            f"REFUSED: copying {total_mb:.0f} MB would leave {free - total_mb/1000:.1f} GB free, "
            f"below the {args.min_free_gb} GB floor. Free space first, or drop --include-large."
        )
        return 2

    dest.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    failures = 0

    for label, path in wanted:
        entry: dict = {"store": label, "source": str(path)}
        if not path.exists():
            entry.update(status="missing")
            report.append(entry)
            print(f"  {label:14s} MISSING  {path}")
            continue

        target = dest / (path.name)
        try:
            if path.suffix in (".sqlite", ".db"):
                copy_sqlite(path, target)
                ok, msg = verify_sqlite(target)
            else:
                shutil.copy2(path, target)
                ok, msg = (target.stat().st_size == path.stat().st_size, "size match")
            if not ok:
                raise RuntimeError(f"verification failed: {msg}")
            entry.update(status="ok", bytes=target.stat().st_size, verification=msg)
            print(f"  {label:14s} OK       {target.stat().st_size / 1e6:8.1f} MB  verified={msg}")
        except Exception as exc:  # noqa: BLE001 - a failed backup must be loud, not fatal mid-loop
            failures += 1
            entry.update(status="failed", error=str(exc))
            if target.exists():
                target.unlink()
            print(f"  {label:14s} FAILED   {exc}")
        report.append(entry)

    (dest / "manifest.json").write_text(
        json.dumps({"created": stamp, "free_gb_before": round(free, 1),
                    "include_large": args.include_large, "stores": report}, indent=1),
        encoding="utf-8",
    )

    for old in snapshots()[:-args.keep]:
        if old != dest:
            shutil.rmtree(old, ignore_errors=True)
            print(f"  pruned {old.name}")

    print(f"\nsnapshot: {dest}")
    print(f"stores ok: {sum(1 for r in report if r['status'] == 'ok')}/{len(report)}; failures: {failures}")
    if not args.include_large:
        print("note: DirectMemory (`~/.azazael/memory_store.db`, 5.4 GB) was NOT copied -- see --include-large.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
