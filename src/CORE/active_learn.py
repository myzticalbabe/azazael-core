# coding: utf-8
"""active_learn.py — P1-4 FixMatch-cache + BALD-lite + core-set (2026-09-15)."""
import random
from typing import List
import math

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def fixmatch_round(texts, score_fn, tau: float = 0.9, rng=None):
    """Full FixMatch round: weak->confident pseudo-label, train on strong.

    Returns {"pseudo": [(strong_text, label)], "coverage": kept/total}.
    score_fn(text) -> class-prob list.
    """
    rng = rng or random.Random(0)
    pseudo = []
    for t in texts:
        s = score_fn(weak_aug(t))
        top = max(range(len(s)), key=lambda i: s[i])
        if s[top] >= tau:
            pseudo.append((strong_aug(t, rng), top))
    return {"pseudo": pseudo,
            "coverage": round(len(pseudo) / max(1, len(texts)), 3)}


def weak_aug(text: str) -> str:
    return " ".join(text.split())


def strong_aug(text: str, rng=None) -> str:
    rng = rng or random.Random(0)
    toks = text.split()
    # token mask 15% + swap 1 pair (cheap paraphrase proxy)
    toks = [t for t in toks if rng.random() > 0.15] or toks[:1]
    if len(toks) >= 2 and rng.random() < 0.5:
        i = rng.randrange(len(toks) - 1)
        toks[i], toks[i + 1] = toks[i + 1], toks[i]
    return " ".join(toks)


def fixmatch_filter(texts, score_fn, tau: float = 0.9):
    """Weak->confident pseudo-label; train strong. Returns [(text, pseudo)]."""
    out = []
    for t in texts:
        s = score_fn(weak_aug(t))
        if max(s) >= tau:
            out.append((strong_aug(t), s.index(max(s))))
    return out


def bald_lite(scores_list):
    """MC-dropout variance proxy: variance across T stochastic scores."""
    import statistics
    # scores_list: [T x C]; uncertainty = mean per-class variance
    t = len(scores_list)
    if t < 2:
        return [0.0] * len(scores_list[0])
    c = len(scores_list[0])
    unc = []
    for ci in range(c):
        col = [row[ci] for row in scores_list]
        unc.append(statistics.pvariance(col))
    return sum(unc) / max(1, c)


def kcenter_greedy(vecs, k: int, seed: int = 0):
    """Core-set k-center greedy on embedding vecs (euclidean)."""
    n = len(vecs)
    if n == 0 or k <= 0:
        return []
    chosen = [seed % n]
    dist = [0.0] * n

    def d(a, b):
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(vecs[a], vecs[b])))

    for _ in range(1, min(k, n)):
        best, best_d = -1, -1.0
        for i in range(n):
            if i in chosen:
                continue
            dd = min(d(i, c) for c in chosen)
            if dd > best_d:
                best, best_d = i, dd
        chosen.append(best)
    return chosen
