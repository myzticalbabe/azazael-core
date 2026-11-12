"""Regime five: price survival, not just reach.

Diameter is the worst day; the spectral gap is every day; fault-stretch
is the day a lane dies. All three are measured here, on the registered
solids, with numpy doing the eigenvalue work and BFS doing the rest.
`melee` races two solids on the same keys through the real recall path;
`retune` is the dynamic hook -- workload in, solid out, thresholds
exposed so Lillith can tune them instead of a human re-deriving them.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np

from .dodeca_recall import SHAPES, ShapeStore, bfs_distances

try:
    from . import _forensic_cy as _cy
    ENGINE = "native"
except ImportError:
    _cy = None
    ENGINE = "python"


def moore_efficiency(solid: str) -> float:
    """Registered vertices over the Moore bound: perfection prices itself."""
    n, nbr = SHAPES[solid]
    home = bfs_distances(0, n, nbr)
    k = max(home.values())
    d = len(nbr(0))
    bound = (2 * k + 1) if d == 2 else 1 + d * ((d - 1) ** k - 1) / (d - 2)
    return n / bound


def fault_stats(solid: str) -> Dict:
    """Kill every vertex in turn: worst surviving diameter + disconnects."""
    n, nbr = SHAPES[solid]
    adj = {v: list(nbr(v)) for v in range(n)}
    base = max(bfs_distances(0, n, nbr).values())
    if _cy is not None:
        worst, disc = _cy.fault_worst(n, [adj[v] for v in range(n)])
        return {"fault_diameter": worst, "disconnects": disc,
                "fault_stretch": worst / base, "engine": ENGINE}
    worst, disc = base, 0
    for kill in range(n):
        keep = [v for v in range(n) if v != kill]
        reach, mx = 0, 0
        for u in keep:
            dist, q = {u: 0}, [u]
            while q:
                x = q.pop(0)
                for w in adj[x]:
                    if w != kill and w not in dist:
                        dist[w] = dist[x] + 1
                        q.append(w)
            reach += len(dist) - 1
            mx = max(mx, max(dist.values()))
        if reach < (n - 1) * (n - 2):
            disc += 1
        else:
            worst = max(worst, mx)
    return {"fault_diameter": worst, "disconnects": disc,
            "fault_stretch": worst / base, "engine": ENGINE}


def spectral_gap(solid: str) -> float:
    """Second Laplacian eigenvalue: the gossip convergence rate."""
    n, nbr = SHAPES[solid]
    lap = np.zeros((n, n))
    for v in range(n):
        vs = nbr(v)
        lap[v, v] = len(vs)
        for w in vs:
            lap[v, w] = -1.0
    return float(sorted(np.linalg.eigvalsh(lap))[1])


def regime_report(solid: str) -> Dict:
    n, nbr = SHAPES[solid]
    dists = [bfs_distances(u, n, nbr)[v]
             for u in range(n) for v in range(n) if u != v]
    fs = fault_stats(solid)
    return {"solid": solid, "n": n, "degree": len(nbr(0)),
            "edges": sum(len(nbr(v)) for v in range(n)) // 2,
            "diameter": max(dists), "mean_distance": sum(dists) / len(dists),
            "moore_efficiency": moore_efficiency(solid),
            "spectral_gap": spectral_gap(solid),
            "survival": fs,
            "engine": ENGINE,
            "price": fs["fault_stretch"] / moore_efficiency(solid)}


def melee(solids: List[str], keys: List[str], query: str) -> Dict:
    """Same keys, same query, real recall path on each solid. Fewest hops wins."""
    table = {}
    for name in solids:
        store = ShapeStore(name)
        for k in keys:
            store.put(k, k)
        r = store.recall(query)
        table[name] = {"hops": r["hops"], "visited": r["visited"]}
    winner = min(table, key=lambda s: (table[s]["hops"],
                                       table[s]["visited"]))
    return {"winner": winner, "table": table, "query": query}


# Lillith tunes these, not humans: workload features -> solid.
RETUNE_RULE = ("broadcast->complete-20; fits petersen (<=10)->petersen; "
               "fits dodeca (<=20)->dodecahedron; fits cube (<=32)->"
               "hypercube-32; else g40")


def retune(n_total: int, n_hot: int = 0, broadcast: bool = False) -> Dict:
    """Workload in, solid out. Few hot keys -> small diameter (petersen);
    many keys where order matters -> dodecahedron; broadcast with no
    nearest-first -> complete. Thresholds are data so Lillith can move them."""
    if broadcast:
        solid = "complete-20"
    elif n_total <= 10 and n_hot <= 4:
        solid = "petersen"
    elif n_total <= 20:
        solid = "dodecahedron"
    elif n_total <= 32:
        solid = "hypercube-32"
    else:
        solid = "g40"
    return {"solid": solid, "rule": RETUNE_RULE,
            "workload": {"n_total": n_total, "n_hot": n_hot,
                         "broadcast": broadcast}}
