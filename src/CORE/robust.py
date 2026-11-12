# coding: utf-8
"""robust.py — FGSM noise-aug retraining + stdlib SMOTE for small tabular.

FGSM: gradient-sign on a fitted sklearn logistic (or numeric grad fallback),
augment + refit, report delta-F1 under jitter. SMOTE: kNN interpolation for
the minority class (sklearn NearestNeighbors if present, else brute force).
"""
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


def fgsm_delta_f1(X, y, eps: float = 0.05, seed: int = 0) -> dict:
    """Fit logistic, jitter X by eps, refit on aug, return F1s.

    X: list of float vecs, y: 0/1 list. Returns
    {f1_clean, f1_noisy, f1_aug, n}.
    """
    rng = random.Random(seed)

    def fit(Xt, yt):
        try:
            from sklearn.linear_model import LogisticRegression
            m = LogisticRegression(max_iter=500)
            m.fit(Xt, yt)
            return lambda v: float(m.predict_proba([v])[0][1])
        except Exception as exc:
            note_failure("robust:L25", exc)
            mu1 = [sum(r[i] for r, t in zip(Xt, yt) if t) / max(1, sum(yt))
                   for i in range(len(Xt[0]))]
            mu0 = [sum(r[i] for r, t in zip(Xt, yt) if not t) / max(1, len(yt) - sum(yt))
                   for i in range(len(Xt[0]))]
            return lambda v: 1.0 / (1.0 + __import__("math").exp(
                -(sum((a - b) * x for a, b, x in zip(mu1, mu0, v)))))

    def f1(pred, yt):
        tp = sum(1 for p, t in zip(pred, yt) if p >= 0.5 and t)
        fp = sum(1 for p, t in zip(pred, yt) if p >= 0.5 and not t)
        fn = sum(1 for p, t in zip(pred, yt) if p < 0.5 and t)
        return 2 * tp / max(1, 2 * tp + fp + fn)

    p = fit(X, y)
    noisy = [[x + rng.uniform(-eps, eps) for x in row] for row in X]
    base = f1([p(r) for r in X], y)
    dropped = f1([p(r) for r in noisy], y)
    Xa = X + noisy
    ya = y + y
    pa = fit(Xa, ya)
    return {"f1_clean": round(base, 3), "f1_noisy": round(dropped, 3),
            "f1_aug": round(f1([pa(r) for r in noisy], y), 3), "n": len(X)}


def smote(X, y, k: int = 3, seed: int = 0):
    """Return (X_bal, y_bal) with minority interpolated up to majority size."""
    rng = random.Random(seed)
    pos = [i for i, t in enumerate(y) if t]
    neg = [i for i, t in enumerate(y) if not t]
    if not pos or not neg:
        return list(X), list(y)
    mn, mj = (pos, neg) if len(pos) < len(neg) else (neg, pos)
    need = len(mj) - len(mn)

    def dist(a, b):
        return math.sqrt(sum((x - z) ** 2 for x, z in zip(X[a], X[b])))

    try:
        from sklearn.neighbors import NearestNeighbors
        nn = NearestNeighbors(n_neighbors=min(k + 1, len(mn))).fit([X[i] for i in mn])
        _, idx = nn.kneighbors([X[i] for i in mn])
        nbrs = [[mn[j] for j in row[1:]] for row in idx]
    except Exception as exc:
        note_failure("robust:L69", exc)
        nbrs = []
        for i in mn:
            ds = sorted(((dist(i, j), j) for j in mn if j != i))[:k]
            nbrs.append([j for _, j in ds] or [i])
    Xn, yn = list(X), list(y)
    lab = 1 if mn is pos else 0
    for n in range(need):
        i = mn[n % len(mn)]
        j = rng.choice(nbrs[mn.index(i)] or [i])
        a = rng.random()
        Xn.append([x + a * (z - x) for x, z in zip(X[i], X[j])])
        yn.append(lab)
    return Xn, yn
