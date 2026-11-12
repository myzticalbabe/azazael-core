#!/usr/bin/env python3
# coding: utf-8
"""clean_windows.py — safe Windows bloat cleaner (2026-09-27 forensic).

Measured 2026-09-27 on a box with 7.8GB free of 810GB: the disk is full of
REGENERABLE weight, not data. This cleaner only touches allowlisted roots
(temp dirs, package caches, spent installers). Models, code, memory stores,
projects and the WSL VHDX are NEVER touched automatically — those need a
human call (see the --report guidance).

Usage:
    python scripts/clean_windows.py --report      # measure only (default)
    python scripts/clean_windows.py --dry-run     # list what --apply would do
    python scripts/clean_windows.py --apply       # delete allowlisted bloat
    python scripts/clean_windows.py --apply --include-downloads
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

HOME = Path.home()


def _size_tree(root: Path):
    """(bytes, files) under root. Best-effort: skips the unreadable."""
    total, n = 0, 0
    if not root.is_dir():
        return 0, 0
    for dirpath, _dirs, files in os.walk(root, onerror=lambda *a: None):
        for f in files:
            try:
                total += (Path(dirpath) / f).stat().st_size
                n += 1
            except OSError:
                continue
    return total, n


def _file_age_days(path: Path) -> float:
    try:
        return (time.time() - path.stat().st_mtime) / 86400.0
    except OSError:
        return 0.0


def targets(include_downloads: bool = False):
    """Allowlisted cleanup targets: (label, path, min_age_days, kind)."""
    local = HOME / "AppData" / "Local"
    roaming = HOME / "AppData" / "Roaming"
    out = [
        ("user-temp", Path(os.environ.get("TEMP", str(local / "Temp"))),
         1.0, "temp"),
        ("windows-temp", Path(r"C:\Windows\Temp"), 1.0, "temp"),
        ("C-temp", Path(r"C:\temp"), 7.0, "temp"),
        ("pip-cache", local / "pip" / "Cache", 0.0, "pkgcache"),
        ("npm-cache", local / "npm-cache", 0.0, "pkgcache"),
        ("pnpm-store", local / "pnpm" / "store", 0.0, "pkgcache"),
        ("uv-cache", local / "uv" / "cache", 0.0, "pkgcache"),
        ("nuget-fallback", local / "NuGet" / "v3-cache", 0.0, "pkgcache"),
    ]
    if include_downloads:
        out.append(("download-installers", HOME / "Downloads", 30.0,
                    "installers"))
    return out


INSTALLER_EXTS = {".exe", ".msi", ".iso", ".msix", ".appimage", ".sh",
                  ".zip", ".dmg", ".pkg"}


def _eligible(path: Path, min_age_days: float, kind: str):
    if kind == "installers":
        return (path.is_file()
                and path.suffix.lower() in INSTALLER_EXTS
                and _file_age_days(path) >= min_age_days)
    if kind in ("temp", "pkgcache"):
        if not path.is_file():
            return False
        try:
            # Never delete a file modified in the future-window: it may be
            # actively written (temp) or freshly seeded (cache).
            return _file_age_days(path) >= min_age_days
        except OSError:
            return False
    return False


def scan(include_downloads: bool = False):
    """Walk allowlisted roots, return per-target {bytes, files, paths}."""
    report = []
    for label, root, age, kind in targets(include_downloads):
        entry = {"label": label, "root": str(root), "bytes": 0, "files": 0,
                 "paths": [], "missing": not root.is_dir()}
        if entry["missing"]:
            report.append(entry)
            continue
        for dirpath, _dirs, files in os.walk(root, onerror=lambda *a: None):
            for f in files:
                p = Path(dirpath) / f
                if _eligible(p, age, kind):
                    try:
                        entry["bytes"] += p.stat().st_size
                        entry["files"] += 1
                        if len(entry["paths"]) < 200:
                            entry["paths"].append(str(p))
                    except OSError:
                        continue
        report.append(entry)
    return report


def clean(include_downloads: bool = False):
    """Delete eligible files. Returns per-target {deleted, failed, bytes}."""
    report = []
    for label, root, age, kind in targets(include_downloads):
        entry = {"label": label, "deleted": 0, "failed": 0, "bytes": 0}
        if not root.is_dir():
            report.append(entry)
            continue
        for dirpath, _dirs, files in os.walk(root, onerror=lambda *a: None,
                                             topdown=False):
            for f in files:
                p = Path(dirpath) / f
                if not _eligible(p, age, kind):
                    continue
                try:
                    entry["bytes"] += p.stat().st_size
                    p.unlink()
                    entry["deleted"] += 1
                except OSError:
                    entry["failed"] += 1
        # Remove newly-emptied dirs bottom-up (topdown=False already yields
        # children first); never the root itself.
        for dirpath, dirs, files in os.walk(root, onerror=lambda *a: None,
                                            topdown=False):
            for d in dirs:
                dp = Path(dirpath) / d
                if dp == root:
                    continue
                try:
                    dp.rmdir()
                except OSError:
                    pass
        report.append(entry)
    return report


def fmt_mb(b: int) -> str:
    return "%.1f MB" % (b / 1e6)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="clean_windows",
                                 description="Safe Windows bloat cleaner")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-downloads", action="store_true")
    args = ap.parse_args(argv)

    if args.apply:
        rep = clean(args.include_downloads)
        total_b = sum(r["bytes"] for r in rep)
        total_n = sum(r["deleted"] for r in rep)
        fails = sum(r["failed"] for r in rep)
        for r in rep:
            print("  %-18s freed %s in %d files (%d locked/skipped)"
                  % (r["label"], fmt_mb(r["bytes"]), r["deleted"],
                     r["failed"]))
        print("TOTAL freed %s across %d files (%d skipped)"
              % (fmt_mb(total_b), total_n, fails))
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                                   / "src"))
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            if store is not None:
                store.record_event(
                    source="cleaner", event_type="cleanup_completed",
                    data={"freed_mb": round(total_b / 1e6, 1),
                          "files": total_n, "skipped": fails,
                          "targets": [r["label"] for r in rep],
                          "include_downloads": bool(args.include_downloads)},
                    severity="info", tags=["cleaner", "windows"])
        except Exception:
            pass
        return 0

    rep = scan(args.include_downloads)
    total_b = sum(r["bytes"] for r in rep)
    total_n = sum(r["files"] for r in rep)
    for r in rep:
        if r.get("missing"):
            print("  %-18s missing (skipped)" % r["label"])
        else:
            print("  %-18s %s in %d files"
                  % (r["label"], fmt_mb(r["bytes"]), r["files"]))
    print("RECOVERABLE (safe, allowlisted): %s in %d files"
          % (fmt_mb(total_b), total_n))
    print("Untouched by design: models, code, memory stores, projects, VHDX.")
    print("Needs your call: model duplicates, Jan, Docker data, Ubuntu VHDX "
          "compaction, installer retention.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
