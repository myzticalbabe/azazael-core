#!/usr/bin/env python3
# coding: utf-8
"""Shared census logic for SpitFire GIL and free-threaded implementations.

Eliminates 90% code duplication between impl_gil.py and impl_free_threaded.py.
Single source of truth for: git dirty detection, file classification, subprocess wrapper.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[3]


# ═══════════════════════════════════════════════════════════════════════════
# Subprocess Helper
# ═══════════════════════════════════════════════════════════════════════════

def sh(*args: str) -> tuple[int, str, str]:
    """Run subprocess with consistent encoding, timeout, error handling.

    Returns: (returncode, stdout, stderr)
    """
    try:
        out = subprocess.run(
            args,
            capture_output=True,
            cwd=str(REPO),
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        return (124, "", f"timeout after 600s: {type(exc).__name__}")
    return (out.returncode, out.stdout or "", out.stderr or "")


# ═══════════════════════════════════════════════════════════════════════════
# Git Dirty Detection
# ═══════════════════════════════════════════════════════════════════════════

def dirty_paths() -> list[tuple[str, str]]:
    """Return list of (path, status) for all dirty files in repo.

    Status: 'untracked', 'modified', 'deleted'
    Uses git status --porcelain -z for null-delimited, parsing-safe output.
    """
    code, out, _ = sh("git", "status", "--porcelain", "-z")
    if code != 0:
        raise SystemExit(f"git status failed: exit {code}")
    rows: list[tuple[str, str]] = []
    for entry in out.split("\0"):
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        st = status.strip()
        if path.startswith("-> "):
            continue  # renames: both sides resolve to other entries
        if st == "??":
            kind = "untracked"
        elif "D" in st:
            kind = "deleted"
        else:
            kind = "modified"
        rows.append((path, kind))
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# File Classification
# ═══════════════════════════════════════════════════════════════════════════

# Extensions that indicate build artifacts
ARTIFACT_EXTS = (".pyc", ".pyo", ".pyd", ".DS_Store", ".tmp", ".bak", ".orig", ".rej")
ARTIFACT_NAMES = ("__pycache__", "crash-report", "nuitka-crash", ".pytest_cache")

# Extensions for data/log files
DATA_EXTS = (".jsonl", ".log")

# Source/doc extensions
SOURCE_EXTS = (".py", ".rs", ".go", ".ts", ".md", ".toml", ".yaml", ".yml")


def classify(path: str, status: str) -> dict[str, Any]:
    """Classify a file by path, status, and filesystem metadata.

    Returns dict with: path, status, bytes, mtime, added, removed, category
    """
    low = path.lower()
    name = os.path.basename(low)
    # A path can vanish between git status and this stat (another lane lands a
    # delete); that is a reading, not a reason to lose the whole census.
    stat = None
    try:
        stat = os.stat(os.path.join(REPO, path)) if status != "deleted" else os.stat(REPO)
    except OSError:
        stat = None

    # Category detection (order matters: specific before general)
    if any(name.endswith(a) for a in ARTIFACT_EXTS) or any(a in name for a in ARTIFACT_NAMES):
        cat = "build-artifact"
    elif any(name.endswith(d) for d in DATA_EXTS) or low.startswith(("state/", "out/", "logs/", "tmp/")):
        cat = "data-or-log"
    elif status == "untracked" and "/archived/" in low:
        cat = "archived-tree"
    elif name.endswith(SOURCE_EXTS):
        cat = "source-or-doc"
    elif name.endswith(".json"):
        cat = (
            "data-or-log"
            if ("state/" in low or "out/" in low or "/logs/" in low)
            else "source-or-doc"
        )
    else:
        cat = "unknown"

    # Git diff stats for modified files
    added = removed = -1
    if status == "modified":
        code, out, _ = sh("git", "diff", "--numstat", "HEAD", "--", path)
        first = out.splitlines()[0] if out.strip() else ""
        parts = first.split("\t")
        if len(parts) >= 2 and parts[0].isdigit():
            added, removed = int(parts[0]), int(parts[1])

    return {
        "path": path,
        "status": status,
        "bytes": stat.st_size if stat else -1,
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)) if stat else "missing",
        "added": added,
        "removed": removed,
        "category": cat,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Census Execution (parameterized by GIL status)
# ═══════════════════════════════════════════════════════════════════════════

def census_base(
    args: argparse.Namespace,
    max_workers: int,
    gil_free: bool,
) -> int:
    """Core census logic shared by GIL and free-threaded implementations.

    Args:
        args: Parsed argparse.Namespace with max_workers, save
        max_workers: Thread pool size
        gil_free: True if running on free-threaded Python (GIL disabled)

    Returns: Exit code (0 = success)
    """
    rows = dirty_paths()
    t0 = time.perf_counter()

    # Parallel classification (free-threaded = real parallel; GIL = concurrent)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        verdicts = list(ex.map(lambda v: classify(v[0], v[1]), rows))

    dt = time.perf_counter() - t0

    # Aggregate categories
    cats: dict[str, int] = {}
    for v in verdicts:
        cats[v["category"]] = cats.get(v["category"], 0) + 1

    # Save JSONL output
    save_path = Path(args.save) if getattr(args, "save", None) else REPO / "state" / "spitfire_census.jsonl"
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8", newline="\n") as f:
            for v in verdicts:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **v}) + "\n")
    except OSError as exc:
        # An unwritable report means the census did not happen; 2 = could not run.
        print(f"spitfire census: could not write {save_path}: {exc}", file=sys.stderr)
        return 2

    # Output summary
    print(
        f"spitfire census: {len(verdicts)} paths, {max_workers} workers "
        f"({'free-threaded' if gil_free else 'GIL'}), {dt:.2f}s -> {save_path}"
    )
    for c in sorted(cats, key=cats.get, reverse=True):
        print(f"  {cats[c]:4d}  {c}")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Fix Command Stub (shared)
# ═══════════════════════════════════════════════════════════════════════════

def fix_stub(args: argparse.Namespace, impl_name: str) -> int:
    """Shared fix command stub."""
    print(f"[spitfire fix] {impl_name} implementation - stub", file=sys.stderr)
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Run Dispatcher (shared)
# ═══════════════════════════════════════════════════════════════════════════

def run_dispatcher(
    args: argparse.Namespace,
    max_workers: int,
    gil_free: bool,
    impl_name: str,
) -> int:
    """Dispatch to census/fix/all using shared logic."""
    if args.cmd == "census":
        return census_base(args, max_workers, gil_free)
    if args.cmd == "fix":
        return fix_stub(args, impl_name)
    if args.cmd == "all":
        rc = census_base(args, max_workers, gil_free)
        if rc != 0:
            return rc
        return fix_stub(args, impl_name)
    return 2


# ═══════════════════════════════════════════════════════════════════════════
# GIL Detection Helper
# ═══════════════════════════════════════════════════════════════════════════

def is_free_threaded() -> bool:
    """Detect if running on free-threaded Python (GIL disabled).

    Per Python 3.13+ docs: sys._is_gil_enabled() returns False when GIL is disabled.
    """
    return getattr(sys, "_is_gil_enabled", lambda: True)() is False