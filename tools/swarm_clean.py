"""swarm_clean — free-threaded worker swarm triage of the dirty worktree.

One task per dirty path under ThreadPoolExecutor (the 3.15t interpreter gives
real parallel threads), read-only on git: it never stages, deletes or reverts.
Writes per-file verdicts to state/swarm_clean.jsonl and prints a summary.

Usage:
  python C:/Azazael/.venvs/py315t/Scripts/python.exe tools/swarm_clean.py
  python tools/swarm_clean.py --max-workers 32 --save OUT.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ARTIFACT_RE = (
    ".pyc", ".pyo", ".pyd", ".DS_Store", ".tmp", ".bak", ".orig", ".rej",
    ".appmap", ".lastos", ".stackdump", ".lock",
)
ARTIFACT_NAMES = ("__pycache__", "crash-report", "nuitka-crash", ".pytest_cache")
DATA_RE = (".jsonl", ".log")


def sh(*args: str) -> str:
    try:
        out = subprocess.run(args, capture_output=True, cwd=str(REPO),
                             encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired as exc:
        return (124, "", "timeout after 600s: %s" % type(exc).__name__)  # type: ignore
    return (out.returncode, out.stdout or "", out.stderr or "")  # type: ignore


def dirty_paths() -> list[tuple[str, str]]:
    code, out, _ = sh("git", "status", "--porcelain", "-z")
    if code != 0:
        raise SystemExit(f"git status failed: exit {code}")
    rows: list[tuple[str, str]] = []
    for entry in out.split("\0"):
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        st = status.strip()
        if path.startswith('-> '):
            continue  # renames: both sides resolve to other entries
        if st == "??":
            kind = "untracked"
        elif "D" in st:
            kind = "deleted"
        else:
            kind = "modified"
        rows.append((path, kind))
    return rows


def classify(path: str, status: str, stat: os.stat_result) -> dict:
    low = path.lower()
    name = os.path.basename(low)
    if any(name.endswith(a) for a in ARTIFACT_RE) or any(a in name for a in ARTIFACT_NAMES):
        cat = "build-artifact"
    elif any(name.endswith(d) for d in DATA_RE) or low.startswith(("state/", "out/", "logs/", "tmp/")):
        cat = "data-or-log"
    elif status == "untracked" and "/archived/" in low:
        cat = "archived-tree"
    elif name.endswith((".py", ".rs", ".go", ".ts", ".md", ".toml", ".yaml", ".yml")):
        cat = "source-or-doc"
    elif name.endswith(".json"):
        cat = "data-or-log" if ("state/" in low or "out/" in low or "/logs/" in low) else "source-or-doc"
    else:
        cat = "unknown"
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
        "bytes": stat.st_size,
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
        "added": added,
        "removed": removed,
        "category": cat,
    }


def expand_dir(path: str) -> tuple[str, list[dict]]:
    """A collapsed porcelain dir entry: list the files actually inside it."""
    code, out, _ = sh("git", "ls-files", "--others", "--exclude-standard", "-z", "--", path)
    if code != 0:
        return path, []
    files = [p for p in out.split("\0") if p]
    def one(p: str) -> dict:
        try:
            st = os.stat(os.path.join(REPO, p))
        except OSError:
            st = os.stat(REPO)
        return {"path": p, "bytes": st.st_size, "mtime": time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}
    return path, [one(p) for p in files]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-workers", type=int, default=min(32, os.cpu_count() or 8))
    ap.add_argument("--save", default=str(REPO / "state" / "swarm_clean.jsonl"))
    ns = ap.parse_args()

    gil_free = getattr(sys, "_is_gil_enabled", lambda: True)() is False
    rows = dirty_paths()
    dir_rows = [(p, k) for (p, k) in rows if p.endswith("/")]
    dir_paths = [p for p, _ in dir_rows]
    with ThreadPoolExecutor(max_workers=ns.max_workers) as ex:
        expanded: dict[str, list[dict]] = dict(ex.map(expand_dir, dir_paths))
    file_rows = []
    for fs in expanded.values():
        for f in fs:
            file_rows.append((f["path"], "untracked"))

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=ns.max_workers) as ex:
        verdicts = list(ex.map(lambda v: classify(v[0], v[1], os.stat(
            os.path.join(REPO, v[0])) if v[1] != "deleted" else os.stat(REPO)),
            rows + file_rows))
    dt = time.perf_counter() - t0

    cats: dict[str, int] = {}
    unk_bytes = 0
    for v in verdicts:
        cats[v["category"]] = cats.get(v["category"], 0) + 1
        if v["status"] == "untracked":
            unk_bytes += v["bytes"]

    with open(ns.save, "w", encoding="utf-8", newline="\n") as f:
        for v in verdicts:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **v}) + "\n")

    print(f"swarm_clean: {len(verdicts)} paths ({len(dir_rows)} dirs -> "
          f"{len(file_rows)} files), {ns.max_workers} workers, "
          f"{'gil-free' if gil_free else 'GIL'}, {dt:.2f}s, "
          f"untracked {unk_bytes / 1e6:.1f} MB -> {ns.save}")
    print("  collapsed dirs:", "{:,}".format(len(dir_rows)), "expanded",
          "{:,}".format(len(file_rows)), "files")
    for c in sorted(cats, key=cats.get, reverse=True):  # type: ignore
        print(f"  {cats[c]:4d}  {c}")
    top = sorted((v for v in verdicts if v["status"] == "untracked"),
                 key=lambda v: -v["bytes"])[:10]
    if top:
        print("  largest untracked:")
        for v in top:
            print(f"    {v['bytes'] / 1e6:8.1f} MB  {v['path']}")
    mod = sorted((v for v in verdicts if v["status"] == "modified"
                  and v["added"] >= 0), key=lambda v: -v["added"])[:10]
    if mod:
        print("  most-modified tracked:")
        for v in mod:
            print(f"    +{v['added']}/-{v['removed']}  {v['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
