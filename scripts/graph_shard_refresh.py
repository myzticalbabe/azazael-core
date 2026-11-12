#!/usr/bin/env python3
# coding: utf-8
"""graph_shard_refresh.py — parallel sharded graph refresh (GIL-safe).

Free-threading cannot reach graphify's extractor (tree-sitter grammars have
no cp314t build). Parallel PROCESSES work under any interpreter: split the
tree into top-level shards, run `graphify update` per shard concurrently,
merge the per-shard graphs, then `cluster-only` once on the merged graph.

Usage:
    python scripts/graph_shard_refresh.py --root . [--workers 8]
        [--out graphify-out] [--dry-run]
    python scripts/graph_shard_refresh.py --root . --workers 8
"""
from __future__ import annotations

import argparse
import concurrent.futures
import shutil
import subprocess
import sys
import time
from pathlib import Path
import os as _os


def _run(cmd, cwd, timeout=3600):
    # Graphify prints → and emoji; Windows consoles default to cp1252 and
    # the merge step CRASHES on its own success message without this
    # (measured 2026-09-27). UTF-8 for graphify children, always.
    env = dict(_os.environ, PYTHONUTF8="1")
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, env=env, encoding="utf-8", errors="replace")
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-1500:]
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 127, "%s: %s" % (type(e).__name__, str(e)[:200])


def discover_shards(root: Path):
    """Top-level subdirs containing code, plus a root-files shard."""
    exts = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".rs",
            ".go", ".java", ".c", ".cpp", ".h"}
    shards = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            has = any(p.suffix.lower() in exts
                      for p in child.rglob("*") if p.is_file())
        except OSError:
            continue
        if has:
            shards.append(child)
    return shards


def update_shard(python: str, shard: Path, timeout: int = 3600):
    """Run graphify update inside the shard dir. Returns (shard, rc, out)."""
    code, out = _run([python, "-m", "graphify", "update", "."],
                     cwd=shard, timeout=timeout)
    gfile = shard / "graphify-out" / "graph.json"
    ok = code == 0 and gfile.is_file()
    return (shard.name, ok, out[-500:] if not ok else "ok")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="graph_shard_refresh")
    ap.add_argument("--root", default=".")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="graphify-out")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    shards = discover_shards(root)
    print("shards: %d (%s)" % (len(shards),
                               ", ".join(s.name for s in shards[:12])))
    if args.dry_run:
        return 0
    t0 = time.perf_counter()
    oks, fails = [], []
    with concurrent.futures.ProcessPoolExecutor(
            max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(update_shard, args.python, s): s for s in shards}
        for fut in concurrent.futures.as_completed(futs):
            name, ok, out = fut.result()
            (oks if ok else fails).append(name)
            print(("  [ok] " if ok else "  [FAIL] ") + name, flush=True)
    dt = time.perf_counter() - t0
    print("shards ok=%d fail=%d in %.0fs" % (len(oks), len(fails), dt))
    gfiles = [str(root / n / "graphify-out" / "graph.json") for n in oks]
    if not gfiles:
        print("no shard graphs to merge")
        return 1
    outdir = root / args.out
    outdir.mkdir(parents=True, exist_ok=True)
    # Merge target matches the tool convention (<out>/graphify-out/graph.json),
    # which is what cluster-only reads. Flat graph.json beside it would be
    # a second, divergent copy of the same artifact.
    gdir = outdir / "graphify-out"
    gdir.mkdir(parents=True, exist_ok=True)
    code, out = _run(
        [args.python, "-m", "graphify", "merge-graphs", *gfiles,
         "--out", str(gdir / "graph.json")],
        cwd=root, timeout=1800)
    print("merge rc=%d %s" % (code, out[-300:]))
    if code != 0:
        return 1
    code, out = _run(
        [args.python, "-m", "graphify", "cluster-only", "--out", str(outdir)],
        cwd=root, timeout=1800)
    print("cluster rc=%d %s" % (code, out[-300:]))
    print("total %.0fs" % (time.perf_counter() - t0))
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
