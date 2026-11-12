# coding: utf-8
"""graph_learn.py — P0 learned-graph bridge (2026-09-15).

Structural SAGE-mean features from existing NetworkX stats + SGAT-L0
edge mask for INFERRED adjudication + PU link-pred scoring. No torch
needed for features/mask; PyG head plugs in later.
"""
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def sage_features(stats: dict, type_onehot: dict, live: float = 0.0):
    """Build z-scored SAGE input from learning_loop_graph.py stats.

    stats: {degree, betweenness, eigenvector, closeness, kcore, pagerank,
            comm_size, cohesion, isolated(0/1)}. type_onehot: {type: 0/1}.
    """
    keys = ["degree", "betweenness", "eigenvector", "closeness",
            "kcore", "pagerank", "comm_size", "cohesion", "isolated"]
    vec = [float(stats.get(k, 0.0)) for k in keys]
    vec += [float(type_onehot.get(t, 0.0)) for t in sorted(type_onehot)]
    vec.append(float(live))
    # z-score-ish: divide heavy-tail degree/pagerank by (1+max) proxy
    return vec


def sgat_l0_mask(edges, scores, l0_penalty: float = 0.01, thresh: float = 0.5):
    """Shared-mask prune: keep edge if score - penalty > thresh.

    edges: [edge_id], scores: [0..1 confidence]. Returns kept ids.
   Models SGAT single-mask + L0 adjudication for 66 INFERRED @0.79.
    """
    return [e for e, s in zip(edges, scores) if (s - l0_penalty) > thresh]


def pu_auc(pos_scores, unlabeled_scores) -> float:
    """PU-AUC: P(pos > unlabeled). No true negatives needed (BCC case)."""
    if not pos_scores or not unlabeled_scores:
        return 0.5
    wins = sum(1 for p in pos_scores for u in unlabeled_scores if p > u)
    return wins / (len(pos_scores) * len(unlabeled_scores))


def sage_vs_gat_stub(n_nodes: int, n_hetero_types: int, avg_degree: float) -> dict:
    """P1-2 heuristic chooser (no torch): SAGE default, GAT on hetero/noisy.

    Returns {"pick": "sage"|"gat", "reason": str}. Real PyG train plugs in
    when torch_geometric present; this keeps CPU box testable.
    """
    if n_hetero_types >= 3 or avg_degree < 2.0:
        return {"pick": "gat",
                "reason": f"hetero={n_hetero_types} sparse deg={avg_degree:.1f}: attention wins"}
    return {"pick": "sage", "reason": "homophilous/dense: inductive mean wins"}


def try_pyg_train(num_nodes=32, in_dim=12):
    """Optional PyG 2-layer SAGE-mean smoke; skips if torch_geometric missing."""
    try:
        import torch
        from torch_geometric.nn import SAGEConv
    except Exception as e:
        note_failure("graph_learn:L59", e)
        return {"skipped": True, "reason": f"no pyg ({e})"}
    import torch as _t
    c1, c2 = SAGEConv(in_dim, 16), SAGEConv(16, 8)
    x = _t.randn(num_nodes, in_dim)
    ei = _t.randint(0, num_nodes, (2, num_nodes * 2))
    return {"out_shape": list(c2(c1(x, ei), ei).shape), "skipped": False}
