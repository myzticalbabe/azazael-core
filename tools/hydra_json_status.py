"""HYDRA --json status output. Run: python3 tools/hydra_json_status.py

Adds --json flag for machine-readable cluster status.
(run from the azazael/ repo root)
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ORCHESTRATION.HYDRA import HYDRACluster

cluster = HYDRACluster()
cluster.discover_nodes()

if '--json' in sys.argv:
    print(json.dumps(cluster.get_cluster_status(), indent=2))
else:
    s = cluster.get_cluster_status()
    n, st = s['nodes'], s['stages']
    print(f"Nodes: {n['total']} | Successful: {st['successful']} | Failed: {st['failed']}")
