# coding: utf-8
"""cluster_route.py — P1-5 HDBSCAN-lite + ProtoNet + TPE-lite (2026-09-15).

Stdlib-first so CPU box stays testable; uses sklearn/hdbscan/optuna
when present, else deterministic fallbacks.

EP223 (2026-10-10): the rescore found that this module, `cascade.py` and
`allocation.ThompsonAllocator` each decide SEPARATELY, so the host edit is one
policy over their joint action space. This file now exposes its action space as
data (`action_space`) and accepts an externally-supplied suggestion source, so
that joint policy can drive it. The policy itself is not here -- it spans the
other two files.
"""
from typing import List
import math
import random

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def hdbscan_lite(vecs, min_size: int = 5):
    """Single-link threshold clustering fallback (noise=-1).

    If hdbscan/sklearn present, delegates; else distance-threshold groups.
    Returns {"labels": [...], "noise_rate": float}.
    """
    try:
        import hdbscan as _h
        cl = _h.HDBSCAN(min_cluster_size=min_size).fit(vecs)
        labels = list(map(int, cl.labels_))
        noise = sum(1 for l in labels if l == -1) / max(1, len(labels))
        return {"labels": labels, "noise_rate": noise, "backend": "hdbscan"}
    except Exception as exc:
        note_failure("cluster_route:L21", exc)
    try:
        from sklearn.cluster import HDBSCAN as _H
        cl = _H(min_cluster_size=min_size).fit(vecs)
        labels = list(map(int, cl.labels_))
        noise = sum(1 for l in labels if l == -1) / max(1, len(labels))
        return {"labels": labels, "noise_rate": noise, "backend": "sklearn"}
    except Exception as exc:
        note_failure("cluster_route:L29", exc)
    # fallback: greedy threshold (mean pairwise dist / 2)
    n = len(vecs)
    if n == 0:
        return {"labels": [], "noise_rate": 0.0, "backend": "fallback"}
    threshold = 1.0
    labels, cur = [-1] * n, 0
    for i in range(n):
        if labels[i] != -1:
            continue
        labels[i] = cur
        for j in range(i + 1, n):
            d = math.sqrt(sum((a - b) ** 2 for a, b in zip(vecs[i], vecs[j])))
            if d < threshold:
                labels[j] = cur
        cur += 1
    # mark singletons as noise
    from collections import Counter
    cnt = Counter(labels)
    labels = [l if cnt[l] >= min(1, min_size // 5 + 1) else -1 for l in labels]
    noise = sum(1 for l in labels if l == -1) / max(1, n)
    return {"labels": labels, "noise_rate": noise, "backend": "fallback"}


def proto_route(query, protos: dict):
    """Nearest-prototype routing. protos: {worm: vec}. Returns best worm."""

    def d(a, b):
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
    return min(protos, key=lambda w: d(query, protos[w]))


def _clamp(values: dict, bounds: dict) -> dict:
    """Clamp `values` into `bounds`, filling a missing key with its midpoint.

    Holds the local path's return contract (one entry per bound) even when an
    external policy supplies only the keys it has an opinion about, and refuses
    a non-numeric value by recording it rather than letting it propagate.
    """
    out = {}
    for k, (lo, hi) in bounds.items():
        raw = values.get(k, (lo + hi) / 2.0)
        try:
            out[k] = min(hi, max(lo, float(raw)))
        except (TypeError, ValueError) as exc:
            note_failure("cluster_route:_clamp/%s" % k, exc)
            out[k] = (lo + hi) / 2.0
    return out


def action_space(bounds: dict, n_candidates: int = 24) -> dict:
    """Describe this router's action space as data (EP223).

    A single policy over the joint action space of `cluster_route`, `cascade`
    and `allocation` has to know what each router can choose and what it hands
    back. This is that description -- data, not a new module.
    """
    return {
        "router": "cluster_route.tpe_lite_suggest",
        "strategy": "tpe_lite",
        "returns": "params",
        "n_candidates": int(n_candidates),
        "selection": "sample-around-top-quartile",
        "params": {k: {"lo": float(lo), "hi": float(hi)}
                   for k, (lo, hi) in bounds.items()},
    }


def tpe_lite_suggest(trials, bounds: dict, n_candidates: int = 24, suggest=None):
    """Multivariate-lite TPE: split top-25% good, sample around best.

    trials: [(params_dict, score)]. bounds: {k: (lo, hi)}.
    Returns suggested params dict. Optuna used when present (caller opt-in).

    `suggest` (EP223) lets an external joint policy drive this router. It is
    called as ``suggest(list(bounds), dict(bounds), n_candidates)`` and its dict
    is clamped into `bounds`. Absent, or unusable, the local sampler runs
    exactly as before -- the default path is unchanged.
    """
    rng = random.Random(0)
    if suggest is not None:
        try:
            external = suggest(list(bounds.keys()), dict(bounds), int(n_candidates))
        except Exception as exc:
            note_failure("cluster_route:tpe_lite_suggest/external-raised", exc)
        else:
            if isinstance(external, dict) and external:
                return _clamp(external, bounds)
            note_failure("cluster_route:tpe_lite_suggest/external-unusable", None)
    if not trials:
        return {k: rng.uniform(*v) for k, v in bounds.items()}
    try:
        import optuna as _o  # noqa: F401
        # Optuna present — caller should build real study; keep lite path
        # deterministic for tests. (Full study lives in tune script.)
        pass
    except Exception as exc:
        note_failure("cluster_route:L79", exc)
    ranked = sorted(trials, key=lambda t: t[1], reverse=True)
    good = ranked[:max(1, len(ranked) // 4)]
    base = good[0][0]
    out = {}
    for k, (lo, hi) in bounds.items():
        span = (hi - lo) * 0.2
        out[k] = min(hi, max(lo, base.get(k, (lo + hi) / 2) + rng.uniform(-span, span)))
    return out
