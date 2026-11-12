"""HYDRA Verbose Mode — wrap run_stage with per-stage reporting.

Usage:  python3 tools/hydra_verbose.py --discover --run --verbose
        (run from the azazael/ repo root)

Rewritten 2026-10-18: the old file monkey-patched HYDRACluster._execute_stage
(removed in the rewrite) and chdir'd into a dead ~/Desktop path. This wraps
the current HYDRACluster.run_stage instead and stays in the repo root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ORCHESTRATION.HYDRA import HYDRACluster

_original_run_stage = HYDRACluster.run_stage


def verbose_run_stage(self, stage):
    result = _original_run_stage(self, stage)
    if '--verbose' in sys.argv:
        print(f"\n  Stage: {stage.value}")
        print(f"  Nodes processed: {result.modules_processed}")
        print(f"  Success: {result.success} ({result.duration:.2f}s)")
        for err in result.errors:
            print(f"    ERR {err[:120]}")
    return result


HYDRACluster.run_stage = verbose_run_stage

# Run HYDRA with verbose flag
if __name__ == "__main__":
    # Remove --verbose from args for downstream parsing
    sys.argv = [a for a in sys.argv if a != '--verbose']
    cluster = HYDRACluster()
    cluster.discover_nodes()

    if '--discover' in sys.argv:
        print(f"Discovered {len(cluster.nodes)} nodes")

    if '--run' in sys.argv:
        results = cluster.run_sequential()
        print(f"\nCompleted {len(results)} stages")

    if '--status' in sys.argv:
        s = cluster.get_cluster_status()
        n, st = s['nodes'], s['stages']
        print(f"Nodes: {n['total']} | Successful: {st['successful']} | Failed: {st['failed']}")
