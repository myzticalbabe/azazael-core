"""Shape registry: name which solid every connection rides.

Lego (a chain of blocks) is one point in a larger connectance space.
dodeca_recall.SHAPES already registers the solids behind one interface;
this module registers what each solid MEANS and refuses untyped edges,
so "all aspects of all environments, concurrently parallel" becomes a
checkable claim: every edge cites its solid, and the geometry -- fan,
funnel, ring, mesh, hub, cube -- EMERGES from the graph degrees instead
of being asserted in prose.

  complete-20  -> fan when one fans out, funnel when many converge in
  cycle-20     -> ring; a path (Lego chain, generations) is a cut ring
  dodecahedron / petersen / g40 -> mesh (peer lanes, no center)
  hypercube-32 -> cube (the parameter space evolution walks)
  icosahedron  -> hub (the Root everything cites)
"""

from __future__ import annotations

from typing import Dict, List

from .dodeca_recall import SHAPES

SOLIDS = sorted(SHAPES)

# solid -> connective roles it can play (direction decides fan vs funnel)
ROLES: Dict[str, List[str]] = {
    "complete-20": ["fan", "funnel"],
    "cycle-20": ["ring", "chain"],
    "dodecahedron": ["mesh"],
    "petersen": ["mesh"],
    "g40": ["mesh"],
    "hypercube-32": ["cube"],
    "icosahedron": ["hub"],
}


class ShapeGraph:
    """Directed edges, each citing a registered solid. Refuses the untyped."""

    def __init__(self) -> None:
        self.edges: List[Dict[str, str]] = []

    def declare(self, src: str, dst: str, solid: str, note: str = "") -> None:
        if solid not in SHAPES:
            raise ValueError("unknown solid %r; registered: %s"
                             % (solid, SOLIDS))
        self.edges.append({"src": src, "dst": dst,
                           "solid": solid, "note": note})

    def audit(self) -> Dict:
        """Geometry from degrees: fan/funnel points, hub, isolates."""
        out_deg: Dict[str, int] = {}
        in_deg: Dict[str, int] = {}
        for e in self.edges:
            out_deg[e["src"]] = out_deg.get(e["src"], 0) + 1
            in_deg[e["dst"]] = in_deg.get(e["dst"], 0) + 1
        nodes = set(out_deg) | set(in_deg)
        fans = sorted(n for n, d in out_deg.items() if d >= 2)
        funnels = sorted(n for n, d in in_deg.items() if d >= 2)
        hubs = sorted(n for n in nodes
                      if any(e["dst"] == n and e["solid"] == "icosahedron"
                             for e in self.edges))
        isolated = sorted(n for n in nodes
                          if out_deg.get(n, 0) + in_deg.get(n, 0) == 0)
        by_solid: Dict[str, int] = {}
        for e in self.edges:
            by_solid[e["solid"]] = by_solid.get(e["solid"], 0) + 1
        ok = not isolated
        return {"verdict": "PASS" if ok else "FAIL", "clean": True,
                "n_edges": len(self.edges), "n_nodes": len(nodes),
                "fans": fans, "funnels": funnels, "hubs": hubs,
                "isolated": isolated, "by_solid": by_solid}

    def to_manifest(self) -> Dict:
        return {"solids": SOLIDS, "edges": list(self.edges)}


def regime_histogram(graph: "ShapeGraph") -> Dict[str, int]:
    """Edge count per connectance regime. Complete-20 splits by
    direction: out-degree >= 2 is fan, in-degree >= 2 is funnel."""
    out_deg: Dict[str, int] = {}
    in_deg: Dict[str, int] = {}
    for e in graph.edges:
        out_deg[e["src"]] = out_deg.get(e["src"], 0) + 1
        in_deg[e["dst"]] = in_deg.get(e["dst"], 0) + 1
    hist: Dict[str, int] = {}
    for e in graph.edges:
        s = e["solid"]
        if s == "complete-20":
            role = ("funnel" if in_deg[e["dst"]] >= 2
                    else "fan" if out_deg[e["src"]] >= 2 else "fan")
        elif s == "cycle-20":
            role = "ring"
        elif s == "hypercube-32":
            role = "cube"
        elif s == "icosahedron":
            role = "hub"
        else:
            role = "mesh"
        hist[role] = hist.get(role, 0) + 1
    return hist
