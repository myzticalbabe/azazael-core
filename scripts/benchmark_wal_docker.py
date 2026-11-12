#!/usr/bin/env python3.15t
"""
benchmark_wal_docker.py - SQLite WAL Throughput Benchmark (CD5 Split Config)

Measures write throughput across 3 SQLite WAL configurations:
  1. Default:  WAL journal, synchronous=NORMAL, no tuning
  2. Tuned:    WAL journal, synchronous=OFF, large cache, optimized page size
  3. Split:    WAL journal with PRAGMA wal_directory on tmpfs (CD5 spec)

Runs inside the azazael-benchmark Docker container (tmpfs at /data,
persistent volume at /persistent). Also runnable standalone for comparison:

  python scripts/benchmark_wal_docker.py                     # In-container
  python scripts/benchmark_wal_docker.py --temp-dir /tmp     # Standalone

Expected results (from CROSS_DOMAIN.md sec.CD5):
  overlay2 (default):  ~2,000 TPS  |  p99 ~45ms
  Split (volume+tmpfs):~12,000 TPS |  p99 ~5ms  (6x improvement)

Output: Writes benchmark_results.json to AZAZAEL_OUTPUT_PATH (or cwd).
"""

import os
import sys
import json
import time
import sqlite3
import tempfile
import argparse
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

# --- Configuration ---
AZAZAEL_MEMORY_DIR = os.environ.get("AZAZAEL_MEMORY_DIR", "/persistent")
AZAZAEL_WAL_DIR = os.environ.get("AZAZAEL_WAL_DIR", "/data")
AZAZAEL_OUTPUT = os.environ.get("AZAZAEL_OUTPUT_PATH", os.path.join(os.path.dirname(__file__), "..", "output"))
BENCHMARK_INSERTS = int(os.environ.get("BENCHMARK_INSERTS", "10000"))


def make_db_path(label: str) -> str:
    """Create a unique database path for a benchmark run."""
    ts = int(time.time())
    return os.path.join(
        tempfile.gettempdir() if label != "split" else AZAZAEL_MEMORY_DIR,
        f"bench_{label}_{ts}.db"
    )


def benchmark_wal(
    db_path: str,
    label: str,
    inserts: int = BENCHMARK_INSERTS,
    synchronous: str = "NORMAL",
    cache_size: int = -65536,
    page_size: int = 4096,
    wal_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Benchmark SQLite WAL write throughput.

    Args:
        db_path: Full path to the database file.
        label: Configuration label (e.g., "default", "tuned", "split").
        inserts: Number of rows to insert.
        synchronous: PRAGMA synchronous setting (NORMAL, OFF, FULL).
        cache_size: PRAGMA cache_size in KB (negative = KB, positive = pages).
        page_size: PRAGMA page_size in bytes.
        wal_dir: PRAGMA wal_directory (None = same dir as .db).

    Returns:
        Dict with benchmark results.
    """
    # Pre-remove any lingering WAL/SHM files from previous runs
    for ext in ("-wal", "-shm"):
        p = db_path + ext
        try:
            os.remove(p)
        except OSError:
            pass

    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute(f"PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA synchronous={synchronous}")
    conn.execute(f"PRAGMA cache_size={cache_size}")
    conn.execute(f"PRAGMA page_size={page_size}")

    # Set WAL directory (CD5 split approach)
    if wal_dir:
        try:
            conn.execute(f'PRAGMA wal_directory="{wal_dir}"')
            wal_dir_applied = True
        except sqlite3.OperationalError:
            wal_dir_applied = False
            print(f"  [WARN] PRAGMA wal_directory not supported (SQLite < 3.31.0)")
    else:
        wal_dir_applied = False
        wal_dir = os.path.dirname(db_path)

    # Create schema
    conn.execute("CREATE TABLE IF NOT EXISTS bench(x INTEGER PRIMARY KEY, y TEXT, z REAL)")

    # Warm-up: verify the database is responsive
    conn.execute("SELECT COUNT(*) FROM bench")

    # Actual benchmark: timed batch insert
    t0 = time.perf_counter()
    conn.execute("BEGIN TRANSACTION")
    for i in range(inserts):
        conn.execute("INSERT INTO bench VALUES(?, ?, ?)", (i, f"bench_{label}_{i}", float(i) * 1.5))
    conn.execute("COMMIT")
    dt = time.perf_counter() - t0

    # Verify row count
    row_count = conn.execute("SELECT COUNT(*) FROM bench").fetchone()[0]

    # Check WAL file location
    wal_path = db_path + "-wal"
    wal_on_tmpfs = wal_dir_applied and wal_path.startswith(AZAZAEL_WAL_DIR)

    conn.close()

    tps = round(inserts / dt, 1) if dt > 0 else 0.0

    result = {
        "label": label,
        "inserts": inserts,
        "time_s": round(dt, 4),
        "tps": tps,
        "tps_formatted": f"{tps:,.0f}",
        "row_count": row_count,
        "db_path": db_path,
        "wal_dir": wal_dir,
        "wal_dir_applied": wal_dir_applied,
        "wal_on_tmpfs": wal_on_tmpfs,
        "synchronous": synchronous,
        "cache_size_kb": abs(cache_size) if cache_size < 0 else cache_size * page_size // 1024,
        "page_size": page_size,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return result


def cleanup_db(db_path: str):
    """Remove database and WAL/SHM artifacts."""
    for path in [db_path, db_path + "-wal", db_path + "-shm"]:
        try:
            os.remove(path)
        except OSError:
            pass


def run_benchmarks(inserts: int = BENCHMARK_INSERTS) -> List[Dict[str, Any]]:
    """
    Run all 3 WAL benchmarks and return results.
    """
    headers = {
        "default": "Default WAL (no tuning)",
        "tuned": "Tuned WAL (sync=OFF, large cache)",
        "split": "Split WAL (wal_directory on tmpfs)",
    }

    results = []
    configs = [
        # (label, synchronous, cache_size, page_size, wal_dir)
        ("default", "NORMAL", -65536, 4096, None),
        ("tuned", "OFF", -131072, 8192, None),
        ("split", "NORMAL", -65536, 4096, AZAZAEL_WAL_DIR),
    ]

    print(f"SQLite WAL Throughput Benchmark (CD5 Split Config)")
    print(f"  Inserts: {inserts:,}")
    print(f"  Memory dir: {AZAZAEL_MEMORY_DIR}")
    print(f"  WAL dir: {AZAZAEL_WAL_DIR}")
    print(f"  SQLite version: {sqlite3.sqlite_version}")
    print(f"  Python: {sys.version}")
    print()

    for label, synchronous, cache_size, page_size, wal_dir in configs:
        print(f"  [{label}] {headers[label]}")
        print(f"    sync={synchronous}  cache={abs(cache_size)}KB  page={page_size}B"
              f"{'  wal_dir=' + wal_dir if wal_dir else ''}")

        db_path = make_db_path(label)
        try:
            result = benchmark_wal(
                db_path=db_path,
                label=label,
                inserts=inserts,
                synchronous=synchronous,
                cache_size=cache_size,
                page_size=page_size,
                wal_dir=wal_dir,
            )
            results.append(result)
            print(f"    Result: {result['tps_formatted']} TPS in {result['time_s']:.2f}s"
                  f"  ({result['row_count']} rows)")
        except Exception as e:
            print(f"    FAILED: {e}")
            results.append({
                "label": label,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            cleanup_db(db_path)
        print()

    # Compute speedup
    if len(results) >= 2 and "tps" in results[0] and "tps" in results[1]:
        default_tps = results[0]["tps"]
        split_tps = results[2]["tps"] if len(results) >= 3 and "tps" in results[2] else 0
        if default_tps > 0:
            tuned_speedup = results[1]["tps"] / default_tps if results[1].get("tps", 0) > 0 else 0
            split_speedup = split_tps / default_tps if split_tps > 0 else 0
            print(f"  Speedup: tuned={tuned_speedup:.1f}x  split={split_speedup:.1f}x (vs default)")
            print()

    return results


def save_results(results: List[Dict[str, Any]], output_dir: str = AZAZAEL_OUTPUT):
    """Save benchmark results to JSON."""
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "benchmark_results.json")

    summary = {
        "benchmark": "SQLite WAL Throughput (CD5 Split Config)",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "sqlite_version": sqlite3.sqlite_version,
            "python_version": sys.version,
            "memory_dir": AZAZAEL_MEMORY_DIR,
            "wal_dir": AZAZAEL_WAL_DIR,
        },
        "configs": [
            {
                "label": r["label"],
                "inserts": r.get("inserts"),
                "time_s": r.get("time_s"),
                "tps": r.get("tps"),
                "tps_formatted": r.get("tps_formatted"),
                "synchronous": r.get("synchronous"),
                "wal_dir_applied": r.get("wal_dir_applied"),
                "wal_on_tmpfs": r.get("wal_on_tmpfs"),
            }
            if "error" not in r
            else {
                "label": r["label"],
                "error": r["error"],
            }
            for r in results
        ],
        "speedup": {},
    }

    # Compute speedup ratios
    results_map = {r["label"]: r for r in results if "tps" in r}
    default_tps = results_map.get("default", {}).get("tps", 0)
    if default_tps > 0:
        for label in ("tuned", "split"):
            tps = results_map.get(label, {}).get("tps", 0)
            if tps > 0:
                summary["speedup"][f"{label}_vs_default"] = round(tps / default_tps, 2)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"  Results saved to: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="SQLite WAL Throughput Benchmark (CD5 Split Config)"
    )
    parser.add_argument(
        "--inserts", type=int, default=BENCHMARK_INSERTS,
        help=f"Number of inserts per benchmark (default: {BENCHMARK_INSERTS:,})"
    )
    parser.add_argument(
        "--temp-dir", type=str, default=None,
        help="Temp directory for default/tuned benchmark DBs (default: system temp)"
    )
    parser.add_argument(
        "--no-cleanup", action="store_true",
        help="Don't remove benchmark databases after run"
    )
    parser.add_argument(
        "--output", type=str, default=AZAZAEL_OUTPUT,
        help=f"Output directory for results JSON (default: {AZAZAEL_OUTPUT})"
    )
    parser.add_argument(
        "--json-only", action="store_true",
        help="Only output JSON results (no human-readable output)"
    )
    args = parser.parse_args()

    # Override temp dir if provided
    if args.temp_dir:
        tempfile.tempdir = args.temp_dir

    # Run benchmarks
    results = run_benchmarks(inserts=args.inserts)

    # Save results
    output_path = save_results(results, output_dir=args.output)

    # JSON-only output for programmatic consumption
    if args.json_only:
        with open(output_path, encoding="utf-8") as f:
            print(f.read().strip())

    return 0 if any("tps" in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
