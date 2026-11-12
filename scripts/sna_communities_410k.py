#!/usr/bin/env python3
"""
sna_communities_410k.py -- igraph Louvain community detection on the REAL 410k graph

Loads the azazael graphify-out graph (18 node + 22 edge shards, ~410k nodes /
653k edges) into python-igraph and runs multilevel (Louvain) community detection
at full scale, reporting modularity and the community-size distribution.

    venv-313-tools/Scripts/python.exe azazael/scripts/sna_communities_410k.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT = Path.home() / "SNA_COMMUNITIES_410K_evidence.json"


def main() -> int:
    import igraph as ig

    from sna_scale_azazael import load as load_rx  # reuses nd*/ed* shard loader

    t0 = time.perf_counter()
    t_epoch = time.time()
    rxg = load_rx()
    edges = list(rxg.edge_list())
    print(f"rx load done ({time.perf_counter()-t0:.1f}s), converting to igraph")
    g = ig.Graph(n=rxg.num_nodes(), edges=edges)
    del rxg, edges

    t1 = time.perf_counter()
    vc = g.community_multilevel(weights=None, return_levels=False)
    modularity = vc.modularity
    memberships = vc.membership
    sizes = {}
    for m in memberships:
        sizes[m] = sizes.get(m, 0) + 1
    dist = sorted(sizes.values(), reverse=True)
    print(f"Louvain done ({time.perf_counter()-t1:.1f}s)")

    out = {
        "engine": "python-igraph 1.0.0 community_multilevel (Louvain)",
        "nodes": g.vcount(),
        "edges": g.ecount(),
        "n_communities": len(dist),
        "modularity": round(float(modularity), 4),
        "largest_community_share": round(dist[0] / g.vcount(), 4),
        "top_community_sizes": dist[:10],
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "wall_start_epoch": int(t_epoch),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
