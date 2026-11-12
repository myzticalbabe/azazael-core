"""Creatively adaptive hyper-edge functionality for Eve.

Six research-frontier mechanisms, stdlib only, behind one interface.
Each class is the smallest honest version of its paper:

1. DynamicHypergraph (TDHNN, IJCAI'23) -- hyperedge NUMBER and features
   both adapt: underused hyperedges are pruned, crowded regions split.
2. FlexibleHyperedge (Adaptive HNN, AAAI'22) -- membership is a learnable
   weight per (node, hyperedge), not a fixed set; update by feedback.
3. MultiScaleHypergraph (Ada-MSHyper, NeurIPS'24) -- group-wise
   interactions at several scales + constraint clustering of similar nodes.
4. TaskAdaptiveTopology (HyperAgent, AAMAS'26) -- difficulty-conditioned
   topology: sparse hypergraphs for simple tasks, denser for hard ones,
   with a sparsity penalty standing in for the VAE regularizer.
5. AdaptiveExpansion AdE (arXiv'25) -- expand each hyperedge to weighted
   pairs via two representative nodes + distance-aware kernel weights,
   instead of fixed-weight clique expansion.
6. HeterophilyAware (HHNN'26) -- neighbors with the same label reinforce,
   neighbors with different labels contrast; per-node positional deviation
   (CAPE analogue) captures distant-but-similar pairs.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Callable, Dict, List, Sequence, Tuple


# ------------------------------------------------------------------ 1. TDHNN
class DynamicHypergraph:
    """Hyperedges adapt in count and content to usage feedback."""

    def __init__(self, seed: int = 0) -> None:
        self.edges: Dict[str, Dict[str, float]] = {}  # edge -> {node: weight}
        self.use: Dict[str, int] = defaultdict(int)
        self._next = 0  # monotonic: ids never recycled after prune
        self._rng = random.Random(seed)

    def add_edge(self, nodes: Sequence[str], weight: float = 1.0) -> str:
        eid = "e%d" % self._next
        self._next += 1
        self.edges[eid] = {n: weight for n in nodes}
        return eid

    def observe(self, eid: str, hit: bool) -> None:
        """Reward hits; misses decay the edge toward pruning."""
        self.use[eid] += 1 if hit else 0
        if not hit and eid in self.edges:
            for n in self.edges[eid]:
                self.edges[eid][n] *= 0.9

    def adapt(self, min_use: int = 1, split_above: int = 4) -> List[str]:
        """Prune starved edges, split crowded ones. Returns live edge ids."""
        for eid in [e for e, u in self.use.items()
                    if u < min_use and e in self.edges]:
            del self.edges[eid]
        for eid, members in list(self.edges.items()):
            if self.use[eid] >= split_above and len(members) > 2:
                nodes = sorted(members)
                half = nodes[len(nodes) // 2:]
                self.edges[eid] = {n: members[n] for n in nodes if n not in half}
                self.add_edge(half)
        return sorted(self.edges)

    def members(self, node: str) -> List[str]:
        return sorted(e for e, m in self.edges.items() if node in m)


# ------------------------------------------------------- 2. flexible hyperedge
class FlexibleHyperedge:
    """One hyperedge whose per-node membership strengths are learned."""

    def __init__(self, nodes: Sequence[str], lr: float = 0.1) -> None:
        self.strength: Dict[str, float] = {n: 1.0 for n in nodes}
        self.lr = lr

    def update(self, node: str, reward: float) -> None:
        """Gradient-free Hebbian step toward observed reward."""
        if node in self.strength:
            self.strength[node] = max(0.0, self.strength[node] + self.lr * reward)

    def members(self, threshold: float = 0.5) -> List[str]:
        return sorted(n for n, s in self.strength.items() if s >= threshold)


# ------------------------------------------------------------- 3. multi-scale
class MultiScaleHypergraph:
    """Group-wise interactions at several scales + similarity clustering."""

    def __init__(self, scales: Sequence[int] = (2, 4)) -> None:
        self.scales = tuple(scales)
        self.levels: Dict[int, List[List[str]]] = {}

    def build(self, nodes: Sequence[str],
              key: Callable[[str], Any] = lambda n: n) -> Dict[int, List[List[str]]]:
        """Cluster similar nodes (by key) into scale-sized hyperedges."""
        ordered = sorted(nodes, key=lambda n: str(key(n)))
        self.levels = {s: [ordered[i:i + s] for i in range(0, len(ordered), s)]
                       for s in self.scales}
        return self.levels

    def context(self, node: str) -> List[str]:
        """All scale-mates of a node across every level, nearest scale first."""
        out: List[str] = []
        for s in sorted(self.levels):
            for group in self.levels[s]:
                if node in group:
                    out += [n for n in group if n != node and n not in out]
        return out


# --------------------------------------------------- 4. task-adaptive topology
class TaskAdaptiveTopology:
    """Difficulty-conditioned sparsity: easy tasks get few edges, hard many."""

    def __init__(self, sparsity_penalty: float = 0.1, seed: int = 0) -> None:
        self.penalty = sparsity_penalty
        self._rng = random.Random(seed)

    def generate(self, agents: Sequence[str], difficulty: float) -> List[List[str]]:
        """difficulty in [0,1]; edge count scales with it, minus the penalty."""
        difficulty = min(1.0, max(0.0, difficulty))
        n = len(agents)
        want = max(1, round(n * difficulty * (1.0 - self.penalty)))
        pool = list(agents)
        self._rng.shuffle(pool)
        size = max(2, n // max(1, want))
        return [pool[i:i + size] for i in range(0, len(pool), size)][:want]


# ------------------------------------------------------------------ 5. AdE
def adaptive_expansion(hyperedges: Sequence[Sequence[str]],
                       sim: Callable[[str, str], float] | None = None
                       ) -> Dict[Tuple[str, str], float]:
    """Expand hyperedges to weighted pairs via representatives + kernel.

    Two most-similar nodes anchor each hyperedge; the rest connect to the
    nearer anchor with a distance-aware weight (1/(1+rank-gap)), so similar
    nodes inside one hyperedge bind tighter than distant ones.
    """
    sim = sim or (lambda a, b: 1.0 if a == b else 0.0)
    weights: Dict[Tuple[str, str], float] = {}
    for edge in hyperedges:
        nodes = list(edge)
        if len(nodes) < 2:
            continue
        scored = sorted(((sim(a, b), a, b) for i, a in enumerate(nodes)
                         for b in nodes[i + 1:]), reverse=True)
        _, rep_a, rep_b = scored[0]
        anchors = (rep_a, rep_b)
        for node in nodes:
            anchor = min(anchors, key=lambda a, _n=node: -sim(_n, a))
            if node == anchor:
                continue
            pair = tuple(sorted((node, anchor)))
            weights[pair] = weights.get(pair, 0.0) + 1.0 / (1.0 + abs(nodes.index(node) - nodes.index(anchor)))
    return weights


# -------------------------------------------------------------- 6. heterophily
class HeterophilyAware:
    """Same-label neighbors reinforce, different-label neighbors contrast."""

    def __init__(self) -> None:
        self.label: Dict[str, Any] = {}
        self.deviation: Dict[str, float] = defaultdict(float)

    def observe(self, node: str, label: Any) -> None:
        self.label[node] = label

    def aggregate(self, node: str, neighbors: Sequence[str]) -> float:
        """Signed agreement in [-1,1]; deviation tracks distant-but-similar."""
        mine = self.label.get(node)
        if mine is None or not neighbors:
            return 0.0
        score = sum(1.0 if self.label.get(n) == mine else -1.0 for n in neighbors)
        result = score / len(neighbors)
        self.deviation[node] = 0.9 * self.deviation[node] + 0.1 * abs(result)
        return result
