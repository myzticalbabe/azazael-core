#!/usr/bin/env python3
"""Profile CLUSTERWORMS FreeThreadPool parallel execution vs sequential baseline.

Usage: python scripts/profile_clusterworms_parallel.py

Measures wall-clock time for sequential and parallel execution across
multiple cluster types, reporting speedup ratios and latency percentiles.
"""

import sys
import os
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
from CORE.freethreading import is_free_threaded


def profile_cluster(cluster_type: str, iterations: int = 3) -> dict:
    """Profile a single cluster type, returning timing metrics."""
    # -- Sequential --
    seq_times = []
    for i in range(iterations):
        cluster = ClusterWORMZ(cluster_type)
        cluster.set_parallel(False)
        t0 = time.perf_counter()
        results = cluster.execute_all()
        elapsed = (time.perf_counter() - t0) * 1000
        seq_times.append(elapsed)

    # -- Parallel --
    par_times = []
    for i in range(iterations):
        cluster = ClusterWORMZ(cluster_type)
        cluster.set_parallel(True)
        t0 = time.perf_counter()
        results = cluster.execute_all()
        elapsed = (time.perf_counter() - t0) * 1000
        par_times.append(elapsed)

    # Compute statistics
    def stats(times):
        times_sorted = sorted(times)
        return {
            "min_ms": round(min(times), 2),
            "max_ms": round(max(times), 2),
            "avg_ms": round(sum(times) / len(times), 2),
            "median_ms": round(times_sorted[len(times_sorted) // 2], 2),
            "p95_ms": round(times_sorted[int(len(times_sorted) * 0.95)], 2),
        }

    seq_stats = stats(seq_times)
    par_stats = stats(par_times)

    avg_seq = seq_stats["avg_ms"]
    avg_par = par_stats["avg_ms"]
    speedup = round(avg_seq / avg_par, 2) if avg_par > 0 else 1.0

    return {
        "cluster_type": cluster_type,
        "iterations": iterations,
        "free_threaded": is_free_threaded(),
        "sequential": seq_stats,
        "parallel": par_stats,
        "speedup_ratio": speedup,
        "speedup_pct": round((speedup - 1.0) * 100, 1),
    }


def main():
    cluster_types = ["system", "network", "bibl", "full"]

    print("=" * 60)
    print("  CLUSTERWORMS Parallel Execution Profile")
    print(f"  Python: {sys.version[:30]}")
    print(f"  Free-threaded: {is_free_threaded()}")
    print(f"  Iterations per cluster: 3")
    print("=" * 60)

    all_results = {}
    for ct in cluster_types:
        print(f"\n  Profiling [{ct}] cluster...")
        result = profile_cluster(ct)
        all_results[ct] = result

        s = result["sequential"]
        p = result["parallel"]
        print(f"    Sequential: avg={s['avg_ms']:.0f}ms  p95={s['p95_ms']:.0f}ms")
        print(f"    Parallel:   avg={p['avg_ms']:.0f}ms  p95={p['p95_ms']:.0f}ms")
        print(f"    Speedup:    {result['speedup_ratio']}x ({result['speedup_pct']:+.1f}%)")

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    for ct, r in all_results.items():
        spd = r["speedup_ratio"]
        label = "FASTER" if spd > 1.05 else ("SLOWER" if spd < 0.95 else "SAME")
        print(f"  [{ct:12s}] {spd:6.2f}x ({r['speedup_pct']:+.1f}%)  {label}")

    # Save JSON
    out_path = Path(__file__).resolve().parent / "profile_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n  Results saved to {out_path}")


if __name__ == "__main__":
    main()
