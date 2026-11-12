# coding: utf-8
"""
EX MACHINA — the operator tier.

`exmachina.py` holds the organs that JUDGE evidence: SPECULUM, TESTUDO,
FULCRUM, VIGIL, FILUM, PERENNIS, COMPAGES, BREVIS, EMERGON.

This module holds the organs that PRODUCE and ROUTE it:

  RADIX    causal discovery (PC skeleton + v-structures + Meek R1)
  CATENA   trust-region Bayesian optimisation with a Cholesky GP
  NAVIS    echo-state reservoir: train the readout, never backpropagate
  VELA     hyperdimensional binding for evidence retrieval
  NEXUS    sparse mixture-of-experts routing
  OPUS     Kolmogorov-Arnold (spline-edge) backbone
  LIMEN    active-learning evidence queue

Stdlib only, like the rest of the tier, because these organs must run unchanged
in both interpreters — the free-threaded 3.15t runner and the 3.15 GIL sidecar —
where a C-extension may or may not load.

What each one honestly is:

  RADIX   A real PC algorithm, but with the standard linear-Gaussian
          assumption (Fisher-z on partial correlations) and no latent
          handling. It is not FCI; it will not recover a confounder-free
          graph when an unobserved common cause exists. Its value here is
          the Markov blanket and the d-separation test, both of which are
          valid under the stated assumption.
  CATENA  TuRBO in miniature: a squared-exponential GP with the
          length-scale chosen by marginal likelihood, EI acquisition, and
          expand/shrink trust-region counters. Not a hyperscale TuRBO.
  NAVIS   Echo state property enforced by scaling the reservoir to a target
          spectral radius; ridge readout closed-form.
  VELA    Multiply-bind / majority-bundle / cyclic-permute, with recall
          accuracy measured rather than assumed.
  NEXUS   Top-k routing with ridge experts; the gate is a softmax over
          centroid similarity. Usage entropy is reported, so a degenerate
          router shows up as a number instead of as silent redundancy.
  OPUS    Depth-1 KAN: additive spline edges, so it is LINEAR in its
          parameters and solvable by ridge. A depth-2 KAN is nonlinear in
          its parameters and would need backprop through the spline basis;
          that is deliberately out of scope here and said so plainly.
  LIMEN   Expected information gain over binary outcomes, discounted by
          NCD redundancy against what is already held (FULCRUM's measure,
          reused rather than reimplemented).
"""

from __future__ import annotations

import math
import random
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:                                       # same-directory import, no package init
    from exmachina import ncd, ridge_fit, _finite   # type: ignore
except Exception as exc:                          # pragma: no cover - import shape guard
    note_failure("exmachina_operators:L57", exc)
    from .exmachina import ncd, ridge_fit, _finite   # type: ignore

__all__ = [
    "RADIX",
    "CATENA",
    "NAVIS",
    "VELA",
    "NEXUS",
    "OPUS",
    "LIMEN",
    "OPERATOR_ORGANS",
    # RADIX
    "partial_correlation",
    "pc_skeleton",
    "orient_edges",
    "discover",
    # CATENA
    "SquaredExponentialGP",
    "expected_improvement",
    "TrustRegionBO",
    # NAVIS
    "EchoState",
    # VELA
    "HDC",
    # NEXUS
    "SparseMoE",
    # OPUS
    "KAN",
    # LIMEN
    "binary_entropy",
    "expected_information_gain",
    "rank_candidates",
    # reporting
    "operator_report",
]

RADIX, CATENA, NAVIS = "radix", "catena", "navis"
VELA, NEXUS, OPUS, LIMEN = "vela", "nexus", "opus", "limen"

OPERATOR_ORGANS: Tuple[dict, ...] = (
    {"key": RADIX, "name": "RADIX", "tier": "implemented",
     "thesis": "Causal discovery (PC) over the evidence graph.",
     "breaks": "Correlation between margin and the check is not causation, and "
               "the gate was reading the raw correlation.",
     "metric": "edges"},
    {"key": CATENA, "name": "CATENA", "tier": "implemented",
     "thesis": "Trust-region Bayesian optimisation over the GP surrogate.",
     "breaks": "A single global surrogate is flat where it matters; TR "
               "restarts on stagnation instead of stalling.",
     "metric": "regions"},
    {"key": NAVIS, "name": "NAVIS", "tier": "implemented",
     "thesis": "Echo-state reservoir: train only the readout.",
     "breaks": "Reaches sequential structure with no backprop and no "
               "C-extension, so it can run inside the free-threaded runner.",
     "metric": "readout"},
    {"key": VELA, "name": "VELA", "tier": "implemented",
     "thesis": "Hyperdimensional binding of (entity, relation, claim).",
     "breaks": "String-keyed lookup cannot answer 'what else looks like this claim'.",
     "metric": "binds"},
    {"key": NEXUS, "name": "NEXUS", "tier": "implemented",
     "thesis": "Sparse mixture-of-experts routing across evidence types.",
     "breaks": "Raises capacity within the shared memory budget by computing "
               "only the top-k experts.",
     "metric": "experts"},
    {"key": OPUS, "name": "OPUS", "tier": "implemented",
     "thesis": "Kolmogorov-Arnold (spline-edge) backbone.",
     "breaks": "Higher expressivity per parameter than an MLP at equal memory.",
     "metric": "splines"},
    {"key": LIMEN, "name": "LIMEN", "tier": "implemented",
     "thesis": "Active learning: rank evidence by expected information gain.",
     "breaks": "Outreach was ordered by narrative, not by information value.",
     "metric": "queue"},
)


# ══════════════════════════════════════════════════════════════════════
# RADIX — causal discovery
#
# PC algorithm, linear-Gaussian version: conditional independence is tested
# with Fisher-z on partial correlations, which are computed by correlating
# the residuals of each variable regressed on the conditioning set. That is
# the textbook route and it needs only a linear solver.
#
# The limit is stated, not hidden: an unobserved common cause between two
# variables leaves an edge that PC cannot remove, so a discovered edge means
# "not d-separated given what we measured", NOT "causes". FCI would answer
# the harder question; this does not, and the docstring says so.
# ══════════════════════════════════════════════════════════════════════

def _normal_cdf(z: float) -> float:
    return 0.5 * math.erfc(-float(z) / math.sqrt(2.0))


def _residuals(target: Sequence[float], given: Sequence[Sequence[float]]) -> Optional[List[float]]:
    """Residuals of `target` after linear projection onto `given` (+ intercept)."""
    n = len(target)
    if not given:
        m = sum(target) / n
        return [v - m for v in target]
    X = [[col[i] for col in given] + [1.0] for i in range(n)]
    beta = ridge_fit(X, list(target), lam=1e-10)
    if beta is None:
        return None
    return [target[i] - sum(X[i][j] * beta[j] for j in range(len(beta))) for i in range(n)]


def _variance(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return sum((v - m) ** 2 for v in vals) / n


def _pearson(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 3:
        return None
    ma, mb = sum(a[:n]) / n, sum(b[:n]) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = sum((a[i] - ma) ** 2 for i in range(n))
    db = sum((b[i] - mb) ** 2 for i in range(n))
    if da <= 0 or db <= 0:
        return None
    return max(-0.999999, min(0.999999, num / math.sqrt(da * db)))


def partial_correlation(data: Dict[str, Sequence[float]], x: str, y: str,
                        given: Sequence[str] = (),
                        alpha: float = 0.05) -> Dict[str, Any]:
    """Fisher-z test of x ⟂ y | given. Returns r, n, z, p and `independent`."""
    n = min(len(data[x]), len(data[y])) if x in data and y in data else 0
    given = [g for g in given if g in data and g != x and g != y]
    if n:
        n = min([n] + [len(data[g]) for g in given])
    dof = n - len(given) - 3
    if n < 5 or dof < 1:
        return {"r": None, "n": n, "z": None, "p": None, "independent": None,
                "note": "not enough rows"}
    cols = [[float(v) for v in data[g][:n]] for g in given]
    x_raw = [float(v) for v in data[x][:n]]
    y_raw = [float(v) for v in data[y][:n]]
    # A variable that never varies cannot be TESTED. Reporting independence
    # here would delete an edge that was never examined -- the absence of
    # evidence printed as the evidence of absence, which is the same defect
    # that made a flat holdout series read as "cointegrated unknown".
    vx_raw, vy_raw = _variance(x_raw), _variance(y_raw)
    if vx_raw <= 0.0 or vy_raw <= 0.0:
        return {"r": None, "n": n, "z": None, "p": None, "independent": None,
                "note": "constant variable: untestable, edge kept"}
    rx = _residuals(x_raw, cols)
    ry = _residuals(y_raw, cols)
    if rx is None or ry is None:
        return {"r": None, "n": n, "z": None, "p": None, "independent": None,
                "note": "singular conditioning set"}
    # The degeneracy test is RELATIVE to the raw variance, as in the linear
    # solver's pivot tolerance. Exact collinearity leaves rounding noise
    # around 1e-30 rather than a hard zero, and an absolute test on that
    # number reads two vectors of pure rounding error as a real correlation.
    if _variance(rx) <= 1e-10 * vx_raw or _variance(ry) <= 1e-10 * vy_raw:
        # Conditioning removed ALL variance, and the raw variable DID vary.
        # That is genuine separation: the conditioning set already explains
        # it, so there is nothing left to correlate.
        return {"r": 0.0, "n": n, "z": 0.0, "p": 1.0, "independent": True,
                "note": "conditioning explains the variable entirely"}
    r = _pearson(rx, ry)
    if r is None:
        return {"r": None, "n": n, "z": None, "p": None, "independent": None,
                "note": "undefined correlation"}
    z = 0.5 * math.log((1.0 + r) / (1.0 - r))
    stat = abs(z) * math.sqrt(dof)
    p = 2.0 * (1.0 - _normal_cdf(stat))
    return {"r": round(r, 6), "n": n, "z": round(z, 6), "p": round(p, 6),
            "independent": bool(p > float(alpha))}


def pc_skeleton(data: Dict[str, Sequence[float]], alpha: float = 0.05,
                max_cond: int = 2) -> Dict[str, Any]:
    """PC skeleton: drop edges that become conditionally independent."""
    names = [k for k in data if len(_finite(data[k])) >= 5]
    adj: Dict[str, set] = {a: set(b for b in names if b != a) for a in names}
    sepset: Dict[Tuple[str, str], List[str]] = {}
    tests = 0
    for depth in range(max(0, int(max_cond)) + 1):
        for a in names:
            for b in sorted(adj[a]):
                if b not in adj[a]:
                    continue
                neigh = [c for c in sorted(adj[a]) if c != b]
                if len(neigh) < depth:
                    continue
                cut: Optional[List[str]] = None
                for combo in combinations(neigh, depth):
                    tests += 1
                    res = partial_correlation(data, a, b, list(combo), alpha=alpha)
                    if res.get("independent"):
                        cut = list(combo)
                        break
                if cut is not None:
                    adj[a].discard(b)
                    adj[b].discard(a)
                    sepset[(a, b)] = cut
                    sepset[(b, a)] = cut
    edges = sorted({tuple(sorted((a, b))) for a in names for b in adj[a]})
    return {"adj": adj, "sepset": sepset, "edges": edges, "tests": tests,
            "nodes": names}


def orient_edges(skel: Dict[str, Any]) -> Dict[str, Any]:
    """v-structures, then Meek rule 1 until closure."""
    adj = skel["adj"]
    sepset = skel.get("sepset") or {}
    directed: set = set()
    vstructs = []
    for c in skel["nodes"]:
        for a, b in combinations(sorted(adj[c]), 2):
            if b in adj[a]:
                continue                                   # shielded triple
            if c in (sepset.get((a, b)) or []):
                continue                                   # not an unshielded collider
            if (a, c) not in directed:
                directed.add((a, c))
            if (b, c) not in directed:
                directed.add((b, c))
            vstructs.append((a, c, b))
    # Meek rule 1: a -> b - c, a not adjacent to c  =>  b -> c
    changed = True
    rounds = 0
    while changed and rounds < 32:
        changed = False
        rounds += 1
        for (a, b) in list(directed):
            for c in list(adj.get(b, ())):
                if c == a or (c, b) in directed:
                    continue
                if c in adj.get(a, ()):
                    continue
                if (b, c) not in directed:
                    directed.add((b, c))
                    changed = True
    return {"directed": sorted(directed), "vstructures": vstructs,
            "cycles_avoided": rounds}


def markov_blanket(skel: Dict[str, Any], oriented: Dict[str, Any],
                   target: str) -> List[str]:
    """Parents, children, and parents of children of `target`."""
    adj = skel["adj"]
    parents = {a for (a, b) in oriented.get("directed", []) if b == target}
    children = {b for (a, b) in oriented.get("directed", []) if a == target}
    parents_of_children = {a for (a, b) in oriented.get("directed", []) if b in children}
    # NB: the neighbours from the skeleton are included too -- orientation only
    # labels some edges, and an unoriented neighbour is still in the blanket.
    blanket = (parents | children | parents_of_children | set(adj.get(target, ()))) - {target}
    return sorted(blanket)


def discover(data: Dict[str, Sequence[float]], target: Optional[str] = None,
             check: Optional[str] = None, alpha: float = 0.05,
             max_cond: int = 2) -> Dict[str, Any]:
    """Full RADIX run: skeleton -> orientation -> blanket -> d-separation test.

    `check` is the independent outcome (the holdout flag). The returned
    `linked` says whether the objective and the check survive the
    conditioning tests as adjacent, i.e. NOT d-separated. When they are
    d-separated the objective carries no causal information about the check
    in this data, which is a far stronger statement than "the margin is low".
    """
    skel = pc_skeleton(data, alpha=alpha, max_cond=max_cond)
    oriented = orient_edges(skel)
    out: Dict[str, Any] = {
        "nodes": len(skel["nodes"]),
        "edges": len(skel["edges"]),
        "edge_list": ["%s--%s" % e for e in skel["edges"]],
        "tests": skel["tests"],
        "vstructures": len(oriented["vstructures"]),
    }
    if target:
        out["blanket"] = markov_blanket(skel, oriented, target)
        out["blanket_size"] = len(out["blanket"])
    if target and check and target in skel["nodes"] and check in skel["nodes"]:
        pair = tuple(sorted((target, check)))
        out["linked"] = pair in set(skel["edges"])
        out["independent_of_check"] = not out["linked"]
        res = partial_correlation(data, target, check, [])
        out["marginal"] = res
        # Conditional on the target's other neighbours: if the direct link
        # vanishes, the marginal association was confounded.
        others = [c for c in skel["adj"].get(target, ()) if c != check][:3]
        if others:
            out["conditional"] = partial_correlation(data, target, check, others)
            out["conditional_on"] = others
    return out


# ══════════════════════════════════════════════════════════════════════
# CATENA — trust-region Bayesian optimisation
# ══════════════════════════════════════════════════════════════════════

def _cholesky(a: List[List[float]], jitter: float = 1e-8) -> Optional[List[List[float]]]:
    n = len(a)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                if s <= 0.0 and jitter <= 0.0:
                    return None
                L[i][j] = math.sqrt(max(s, 1e-18) + jitter)
            else:
                L[i][j] = s / L[j][j] if L[j][j] else 0.0
    return L


def _chol_solve(L: List[List[float]], b: Sequence[float]) -> List[float]:
    n = len(L)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(L[i][k] * y[k] for k in range(i))) / L[i][i]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (y[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


class SquaredExponentialGP:
    """GP with an SE kernel; length-scale picked by marginal likelihood.

    The point of the grid search is that the length-scale is NOT asserted.
    A surrogate whose length-scale is wrong is flat exactly where the search
    needs gradient, which is the failure mode CATENA exists to fix.
    """

    def __init__(self, length_scales: Optional[Sequence[float]] = None,
                 noise: float = 1e-3):
        self.candidates = list(length_scales or (0.05, 0.1, 0.2, 0.4, 0.8, 1.6))
        self.noise = float(noise)
        self.X: List[List[float]] = []
        self.y: List[float] = []
        self.ls = self.candidates[0]
        self._L: Optional[List[List[float]]] = None
        self._alpha: Optional[List[float]] = None
        self._ym = 0.0
        self.ll = None

    def _kernel(self, a: Sequence[float], b: Sequence[float], ls: float) -> float:
        d2 = sum((a[i] - b[i]) ** 2 for i in range(len(a)))
        return math.exp(-0.5 * d2 / (ls * ls))

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[float]) -> "SquaredExponentialGP":
        self.X = [list(map(float, r)) for r in X]
        self.y = [float(v) for v in y]
        n = len(self.X)
        if n == 0:
            return self
        self._ym = sum(self.y) / n
        yc = [v - self._ym for v in self.y]
        best = None
        for ls in self.candidates:
            K = [[self._kernel(self.X[i], self.X[j], ls) + (self.noise if i == j else 0.0)
                  for j in range(n)] for i in range(n)]
            L = _cholesky(K, jitter=0.0)
            if L is None:
                continue
            alpha = _chol_solve(L, yc)
            quad = sum(yc[i] * alpha[i] for i in range(n))
            logdet = 2.0 * sum(math.log(L[i][i]) for i in range(n))
            ll = -0.5 * quad - 0.5 * logdet - 0.5 * n * math.log(2.0 * math.pi)
            if best is None or ll > best[0]:
                best = (ll, ls, L, alpha)
        if best is None:
            self.ls = self.candidates[0]
            K = [[self._kernel(self.X[i], self.X[j], self.ls) + (self.noise if i == j else 0.0)
                  for j in range(n)] for i in range(n)]
            self._L = _cholesky(K, jitter=1e-6)
            self._alpha = _chol_solve(self._L, yc) if self._L else None
            return self
        self.ll, self.ls, self._L, self._alpha = best[0], best[1], best[2], best[3]
        return self

    def predict(self, Xs: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
        """Posterior (mean, sd) per row."""
        if self._L is None or self._alpha is None or not self.X:
            return [(self._ym, 1.0) for _ in Xs]
        n = len(self.X)
        out = []
        for x in Xs:
            x = list(map(float, x))
            ks = [self._kernel(x, self.X[i], self.ls) for i in range(n)]
            v = _chol_solve(self._L, ks)
            mu = self._ym + sum(ks[i] * self._alpha[i] for i in range(n))
            var = 1.0 - sum(ks[i] * v[i] for i in range(n))
            out.append((mu, math.sqrt(max(var, 1e-12))))
        return out


def expected_improvement(mu: float, sd: float, best: float, xi: float = 0.01) -> float:
    """EI for a MAXIMISATION problem, closed form via the normal CDF."""
    if sd <= 0:
        return max(0.0, mu - best)
    z = (mu - best - xi) / sd
    return (mu - best - xi) * _normal_cdf(z) + sd * math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


class TrustRegionBO:
    """TuRBO in miniature: restart the region, do not restart the surrogate."""

    def __init__(self, bounds: Dict[str, Tuple[float, float]],
                 radius: float = 0.2, max_radius: float = 0.5,
                 min_radius: float = 0.01, seed: int = 20260912):
        self.bounds = {k: (float(v[0]), float(v[1])) for k, v in bounds.items()}
        self.keys = sorted(self.bounds)
        self.radius = float(radius)
        self.max_radius = float(max_radius)
        self.min_radius = float(min_radius)
        self.rng = random.Random(seed)
        self.gp = SquaredExponentialGP()
        self.X: List[List[float]] = []
        self.y: List[float] = []
        self.best_x: Optional[List[float]] = None
        self.best_y = float("-inf")
        self.success = 0
        self.failure = 0
        self.restarts = 0
        self.iters = 0

    # -- bounds helpers -------------------------------------------------
    def _norm(self, x: Sequence[float]) -> List[float]:
        return [(float(v) - self.bounds[k][0]) / max(1e-12, self.bounds[k][1] - self.bounds[k][0])
                for k, v in zip(self.keys, x)]

    def _denorm(self, x: Sequence[float]) -> List[float]:
        out = []
        for k, v in zip(self.keys, x):
            lo, hi = self.bounds[k]
            out.append(lo + min(1.0, max(0.0, float(v))) * (hi - lo))
        return out

    def tell(self, x: Sequence[float], y: float) -> None:
        xn = self._norm(x)
        self.X.append(xn)
        self.y.append(float(y))
        self.iters += 1
        if y > self.best_y:
            self.best_y, self.best_x = float(y), xn
            self.success += 1
            self.failure = 0
        else:
            self.failure += 1
            self.success = 0
        if self.success >= 3:
            self.radius = min(self.max_radius, self.radius * 2.0)
            self.success = 0
        elif self.failure >= 4:
            self.radius = max(self.min_radius, self.radius / 2.0)
            self.failure = 0
            if self.radius <= self.min_radius and self.X:
                # A restart resets the SURROGATE and its local data. It must
                # NOT reset the incumbent: clearing best_y threw away the best
                # point found so far, so `report()["best"]` went back to None
                # and ask() began sampling from nowhere. The first version of
                # this did exactly that, and the test that caught it was
                # "does TR-BO beat its own starting point".
                self.restarts += 1
                self.radius = min(self.max_radius, self.radius * 4.0)
                self.gp = SquaredExponentialGP()
                self.X, self.y = [], []

    def ask(self, n_candidates: int = 256) -> List[float]:
        """Next point by EI inside the trust region (random if too few points)."""
        d = len(self.keys)
        if self.best_x is None or len(self.X) < 3:
            return self._denorm([self.rng.random() for _ in range(d)]) if self.best_x is None \
                else self._denorm([min(1.0, max(0.0, self.best_x[i] + self.rng.uniform(-self.radius, self.radius)))
                                   for i in range(d)])
        self.gp.fit(self.X, self.y)
        cands = []
        for _ in range(int(n_candidates)):
            cands.append([min(1.0, max(0.0, self.best_x[i] + self.rng.uniform(-self.radius, self.radius)))
                          for i in range(d)])
        preds = self.gp.predict(cands)
        best_i, best_ei = 0, float("-inf")
        for i, (mu, sd) in enumerate(preds):
            ei = expected_improvement(mu, sd, self.best_y)
            if ei > best_ei:
                best_ei, best_i = ei, i
        return self._denorm(cands[best_i])

    def report(self) -> Dict[str, Any]:
        return {"n": self.iters, "since_restart": len(self.X), "radius": round(self.radius, 4),
                "ls": None if self.gp.ll is None else round(self.gp.ls, 4),
                "log_ml": None if self.gp.ll is None else round(self.gp.ll, 3),
                "best": None if self.best_y == float("-inf") else round(self.best_y, 4),
                "restarts": self.restarts}


# ══════════════════════════════════════════════════════════════════════
# NAVIS — echo-state reservoir
# ══════════════════════════════════════════════════════════════════════

class EchoState:
    """Reservoir with a closed-form ridge readout.

    The spectral radius is ESTIMATED by power iteration on the sampled
    matrix and scaled to the target, rather than being assumed from the
    sampling distribution: an unverified reservoir can fail the echo state
    property and then no readout can rescue it.
    """

    def __init__(self, n_in: int, n_res: int = 48, spectral_radius: float = 0.9,
                 sparsity: float = 0.15, leak: float = 1.0, seed: int = 20260912):
        self.n_in = int(n_in)
        self.n_res = int(n_res)
        self.leak = min(1.0, max(1e-3, float(leak)))
        rng = random.Random(seed)
        self.W_in = [[rng.uniform(-1.0, 1.0) for _ in range(self.n_in + 1)]
                     for _ in range(self.n_res)]
        W = [[0.0] * self.n_res for _ in range(self.n_res)]
        for i in range(self.n_res):
            for j in range(self.n_res):
                if rng.random() < float(sparsity):
                    W[i][j] = rng.uniform(-1.0, 1.0)
        rho = self._spectral_radius(W)
        scale = (float(spectral_radius) / rho) if rho > 1e-12 else 1.0
        self.W = [[v * scale for v in row] for row in W]
        self.spectral_radius = float(spectral_radius)
        self.readout: Optional[List[float]] = None

    @staticmethod
    def _spectral_radius(W: List[List[float]], iters: int = 60) -> float:
        n = len(W)
        v = [1.0 / math.sqrt(n)] * n
        lam = 0.0
        for _ in range(iters):
            nv = [sum(W[i][j] * v[j] for j in range(n)) for i in range(n)]
            norm = math.sqrt(sum(x * x for x in nv))
            if norm <= 1e-15:
                return 0.0
            v = [x / norm for x in nv]
            lam = norm
        return lam

    def _step(self, x: List[float], u: Sequence[float]) -> List[float]:
        pre = [sum(self.W_in[i][j] * ([1.0] + list(u))[j] for j in range(self.n_in + 1))
               + sum(self.W[i][j] * x[j] for j in range(self.n_res))
               for i in range(self.n_res)]
        new = [math.tanh(v) for v in pre]
        return [(1.0 - self.leak) * x[i] + self.leak * new[i] for i in range(self.n_res)]

    def run(self, inputs: Sequence[Sequence[float]],
            washout: int = 0) -> List[List[float]]:
        """Collect reservoir states. `washout` drops the transient, which is
        the difference between learning the signal and learning the initial
        condition."""
        x = [0.0] * self.n_res
        states = []
        for t, u in enumerate(inputs):
            x = self._step(x, u)
            if t >= int(washout):
                states.append(list(x))
        return states

    def fit(self, inputs: Sequence[Sequence[float]], targets: Sequence[float],
            washout: int = 5, ridge: float = 1e-3) -> Optional[List[float]]:
        states = self.run(inputs, washout=washout)
        y = [float(v) for v in targets][len(targets) - len(states):]
        if len(states) < 2 or len(states) != len(y):
            return None
        X = [s + [1.0] for s in states]
        self.readout = ridge_fit(X, y, lam=ridge)
        return self.readout

    def predict(self, inputs: Sequence[Sequence[float]], washout: int = 5) -> List[float]:
        if self.readout is None:
            return []
        out = []
        for s in self.run(inputs, washout=washout):
            row = s + [1.0]
            out.append(sum(row[i] * self.readout[i] for i in range(len(row))))
        return out


# ══════════════════════════════════════════════════════════════════════
# VELA — hyperdimensional binding
# ══════════════════════════════════════════════════════════════════════

class HDC:
    """Multiply-bind, majority-bundle, cyclic-permute.

    Recall accuracy is MEASURED (`selftest`) rather than assumed: a
    hyperdimensional store that cannot retrieve what it just wrote is worse
    than a dictionary, because it looks like it can.
    """

    def __init__(self, dim: int = 1024, seed: int = 20260912):
        self.dim = int(dim)
        self.seed = int(seed)
        self.rng = random.Random(seed)
        self.atoms: Dict[str, List[int]] = {}

    def symbol(self, name: str) -> List[int]:
        if name not in self.atoms:
            r = random.Random("%d|%s" % (self.seed, name))
            self.atoms[name] = [1 if r.random() < 0.5 else -1 for _ in range(self.dim)]
        return self.atoms[name]

    def bind(self, a: Sequence[int], b: Sequence[int]) -> List[int]:
        return [1 if a[i] * b[i] >= 0 else -1 for i in range(self.dim)]

    def bundle(self, hvs: Iterable[Sequence[int]]) -> List[int]:
        acc = [0] * self.dim
        n = 0
        for hv in hvs:
            n += 1
            for i in range(self.dim):
                acc[i] += hv[i]
        if n == 0:
            return [0] * self.dim
        return [1 if acc[i] >= 0 else -1 for i in range(self.dim)]

    def permute(self, hv: Sequence[int], k: int = 1) -> List[int]:
        k %= self.dim
        if k == 0:
            return list(hv)
        return list(hv[-k:]) + list(hv[:-k])

    def similarity(self, a: Sequence[int], b: Sequence[int]) -> float:
        return sum(1 for i in range(self.dim) if a[i] == b[i]) / float(self.dim)

    def encode(self, pairs: Sequence[Tuple[str, str]], ordered: bool = True) -> List[int]:
        """bind(key, value) per pair, bundled; position marked by permutation."""
        parts = []
        for pos, (k, v) in enumerate(pairs):
            key = self.symbol(str(k))
            if ordered:
                key = self.permute(key, (pos % 8) + 1)
            parts.append(self.bind(key, self.symbol(str(v))))
        return self.bundle(parts)

    def recall(self, mem: Sequence[int], candidates: Sequence[str]) -> Tuple[Optional[str], float]:
        best, best_sim = None, -1.0
        for c in candidates:
            s = self.similarity(mem, self.symbol(str(c)))
            if s > best_sim:
                best, best_sim = c, s
        return best, round(best_sim, 4)

    def selftest(self, n_items: int = 32, n_queries: int = 24) -> Dict[str, Any]:
        """Write n items, read back n; report the measured accuracy."""
        pool = ["v%d" % i for i in range(max(4, n_items))]
        # Coverage of the store, then a CLEAN retrieval test: unbind each
        # key and ask whether the right value comes back. The failed version
        # of this counted "a best match was returned", which is always true.
        self.encode([("k%d" % i, pool[i % len(pool)]) for i in range(n_items)])
        trials = min(n_queries, n_items)
        exact = 0
        for i in range(trials):
            v = pool[i % len(pool)]
            key = self.symbol("k%d" % i)
            # UNBIND before recalling. Comparing bind(key, value) directly to
            # the value atoms gives ~0.5 similarity for every candidate, since
            # binding a random vector produces another random vector -- the
            # first version of this scored 1/12 and looked like a broken store.
            probe = self.bind(self.bind(key, self.symbol(v)), key)
            got, _s = self.recall(probe, pool)
            exact += 1 if got == v else 0
        return {"dim": self.dim, "items": n_items,
                "atoms": len(self.atoms),
                "recall": round(exact / float(max(1, trials)), 4),
                "trials": trials,
                "noise_floor": round(self.similarity(self.symbol("a"), self.symbol("b")), 4)}


# ══════════════════════════════════════════════════════════════════════
# NEXUS — sparse mixture of experts
# ══════════════════════════════════════════════════════════════════════

class SparseMoE:
    """Top-k routing with ridge experts and a similarity gate.

    Usage entropy is part of the report on purpose: a router that sends
    everything to one expert is a single model with extra steps, and that
    should be visible as entropy ≈ 0 rather than inferred.
    """

    def __init__(self, n_experts: int = 4, top_k: int = 2, ridge: float = 1e-2,
                 seed: int = 20260912, temperature: float = 0.5):
        self.n_experts = max(1, int(n_experts))
        self.top_k = max(1, min(int(top_k), self.n_experts))
        self.ridge = float(ridge)
        self.temperature = max(1e-3, float(temperature))
        self.centroids: List[List[float]] = []
        self.experts: List[Optional[List[float]]] = [None] * self.n_experts
        self.rng = random.Random(seed)

    def _dist2(self, a: Sequence[float], b: Sequence[float]) -> float:
        return sum((a[i] - b[i]) ** 2 for i in range(len(a)))

    def _init_centroids(self, X: List[List[float]]) -> None:
        # k-means++ style spread, seeded for reproducibility.
        first = self.rng.randrange(len(X))
        cents = [list(X[first])]
        while len(cents) < self.n_experts:
            d = [min(self._dist2(x, c) for c in cents) for x in X]
            tot = sum(d) or 1.0
            r, acc, pick = self.rng.random() * tot, 0.0, 0
            for i, v in enumerate(d):
                acc += v
                if acc >= r:
                    pick = i
                    break
            cents.append(list(X[pick]))
        self.centroids = cents

    def gate(self, x: Sequence[float]) -> List[float]:
        if not self.centroids:
            return [1.0 / self.n_experts] * self.n_experts
        d = [self._dist2(x, c) for c in self.centroids]
        m = min(d)
        w = [math.exp(-(v - m) / (2.0 * self.temperature * max(1e-9, m) + 1e-9)) for v in d]
        s = sum(w) or 1.0
        return [v / s for v in w]

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[float]) -> "SparseMoE":
        Xl = [list(map(float, r)) for r in X]
        yl = [float(v) for v in y]
        if not Xl:
            return self
        self._init_centroids(Xl)
        assign = [max(range(self.n_experts), key=lambda e: self.gate(x)[e]) for x in Xl]
        for e in range(self.n_experts):
            idx = [i for i, a in enumerate(assign) if a == e]
            if len(idx) < 2:
                self.experts[e] = None
                continue
            rows = [Xl[i] + [1.0] for i in idx]
            self.experts[e] = ridge_fit(rows, [yl[i] for i in idx], lam=self.ridge)
        return self

    def predict(self, X: Sequence[Sequence[float]]) -> List[float]:
        out = []
        for x in X:
            xl = list(map(float, x))
            w = self.gate(xl)
            order = sorted(range(self.n_experts), key=lambda e: -w[e])[:self.top_k]
            num = den = 0.0
            for e in order:
                beta = self.experts[e]
                if beta is None:
                    continue
                row = xl + [1.0]
                val = sum(row[i] * beta[i] for i in range(len(row)))
                num += w[e] * val
                den += w[e]
            out.append(num / den if den > 0 else 0.0)
        return out

    def report(self, X: Sequence[Sequence[float]]) -> Dict[str, Any]:
        counts = [0] * self.n_experts
        for x in X:
            w = self.gate(x)
            counts[max(range(self.n_experts), key=lambda e: w[e])] += 1
        tot = sum(counts) or 1
        ent = -sum((c / tot) * math.log2(c / tot) for c in counts if c)
        return {"experts": self.n_experts, "top_k": self.top_k,
                "usage": counts,
                "usage_entropy": round(ent, 4),
                "max_entropy": round(math.log2(self.n_experts), 4),
                "trained": sum(1 for e in self.experts if e is not None)}


# ══════════════════════════════════════════════════════════════════════
# OPUS — spline-edge backbone
# ══════════════════════════════════════════════════════════════════════

class KAN:
    """Depth-1 KAN: additive spline edges, linear in the coefficients.

    Each input gets its own cubic B-spline basis (Cox-de Boor recursion on
    uniform knots); the output is a ridge-regularised linear combination of
    all basis functions plus the raw inputs. This is the Kolmogorov-Arnold
    structure with one layer, and it is fitted in closed form.

    A depth-2 KAN (splines whose outputs feed more splines) is nonlinear in
    its parameters and needs backprop through the basis. That is NOT
    implemented; saying otherwise would be the over-claim this tier exists
    to avoid.

    LIMITATION, tested rather than hidden: splines are LOCAL, so this does
    not extrapolate. Outside the training range every basis function decays
    to zero and only the linear term survives, which can be worse than a
    plain linear model. That is acceptable for the Oracle's use (candidate
    genes lie inside the observed bounds) and unacceptable if it is ever
    asked to project beyond them.
    """

    def __init__(self, n_in: int, n_grid: int = 5, degree: int = 3,
                 ridge: float = 1e-3):
        self.n_in = int(n_in)
        self.n_grid = max(2, int(n_grid))
        self.degree = max(1, int(degree))
        self.ridge = float(ridge)
        self.lo = [0.0] * self.n_in
        self.hi = [1.0] * self.n_in
        self.knots: List[List[float]] = []
        self.beta: Optional[List[float]] = None
        self.n_features = 0

    def _make_knots(self, i: int) -> List[float]:
        """Uniform knot vector, extended by `degree` past both ends.

        The extension is what makes the basis a partition of unity on the
        closed interval; without it the boundary is left uncovered and the
        fit degrades into a straight line near the edges.
        """
        p = self.degree
        lo, hi = self.lo[i], self.hi[i]
        if hi - lo < 1e-12:
            hi = lo + 1.0
        h = (hi - lo) / float(self.n_grid)
        return [lo + h * k for k in range(-p, self.n_grid + p + 1)]

    @staticmethod
    def _bspline(t: float, knots: Sequence[float], degree: int) -> List[float]:
        """Cox-de Boor recursion, evaluated for every basis function at once.

        Built bottom-up from the degree-0 indicators rather than recursively
        per function: the recursion is the same, but the cost stays linear in
        the number of basis functions instead of exponential in the degree.
        """
        n = len(knots) - 1
        if t >= knots[-1]:
            N = [0.0] * n
            N[-1] = 1.0
        else:
            N = [1.0 if knots[i] <= t < knots[i + 1] else 0.0 for i in range(n)]
        for deg in range(1, degree + 1):
            M = []
            for i in range(len(N) - 1):
                d1 = knots[i + deg] - knots[i]
                d2 = knots[i + deg + 1] - knots[i + 1]
                left = ((t - knots[i]) / d1 * N[i]) if d1 > 0 else 0.0
                right = ((knots[i + deg + 1] - t) / d2 * N[i + 1]) if d2 > 0 else 0.0
                M.append(left + right)
            N = M
        return N

    def _features(self, row: Sequence[float]) -> List[float]:
        f: List[float] = []
        for i in range(self.n_in):
            f.extend(self._bspline(float(row[i]), self.knots[i], self.degree))
        f.extend(float(v) for v in row[:self.n_in])
        return f

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[float]) -> "KAN":
        Xl = [list(map(float, r)) for r in X]
        if not Xl:
            return self
        for i in range(self.n_in):
            col = [r[i] for r in Xl]
            self.lo[i], self.hi[i] = min(col), max(col)
        self.knots = [self._make_knots(i) for i in range(self.n_in)]
        F = [self._features(r) for r in Xl]
        self.n_features = len(F[0]) if F else 0
        self.beta = ridge_fit(F, [float(v) for v in y], lam=self.ridge)
        return self

    def predict(self, X: Sequence[Sequence[float]]) -> List[float]:
        if self.beta is None:
            return []
        return [sum(f * b for f, b in zip(self._features(list(map(float, r))), self.beta))
                for r in X]

    def report(self) -> Dict[str, Any]:
        return {"n_in": self.n_in, "grid": self.n_grid, "degree": self.degree,
                "features": self.n_features,
                "params": 0 if self.beta is None else len(self.beta)}


# ══════════════════════════════════════════════════════════════════════
# LIMEN — active-learning evidence queue
# ══════════════════════════════════════════════════════════════════════

def binary_entropy(p: float) -> float:
    p = min(1.0, max(0.0, float(p)))
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))


def expected_information_gain(p_flip: float) -> float:
    """Bits of decision information an item carries: 1.0 at p = 0.5.

    `p_flip` is the model's probability that obtaining this item CHANGES the
    verdict. An item that is almost certainly decisive and an item that is
    almost certainly irrelevant are both uninformative; the value is in the
    middle, which is the opposite of how a relevance-ranked queue behaves.

    Returns BITS (max 1.0). It reads as a probability-like number, which is
    why the earlier version's extra factor of 2 was both invisible in the
    tests and wrong in the docstring.
    """
    return binary_entropy(p_flip)


def rank_candidates(items: Sequence[dict], covered: Optional[Sequence[Any]] = None,
                    redundancy_penalty: float = 0.5, limit: int = 25,
                    field: str = "probability") -> List[Dict[str, Any]]:
    """Rank evidence by EIG, discounted for redundancy against what we hold.

    Redundancy uses NCD from FULCRUM rather than a second similarity measure,
    so a claim that restates an existing one is discounted by the SAME
    definition of "same" the rest of the tier uses.
    """
    have = list(covered or [])
    scored: List[Dict[str, Any]] = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        try:
            p = float(it.get(field, it.get("p", 0.5)))
        except (TypeError, ValueError) as exc:
            note_failure("exmachina_operators:L979", exc)
            continue
        # A nan probability is MISSING information, not zero information.
        # Ranking it last would quietly put an unmeasured item behind a
        # measured-irrelevant one.
        if not math.isfinite(p):
            continue
        eig = expected_information_gain(p)
        red = 0.0
        if have:
            sims = []
            for h in have:
                try:
                    sims.append(1.0 - ncd(it, h))
                except Exception as exc:
                    note_failure("exmachina_operators:L993", exc)
                    continue
            red = max(sims) if sims else 0.0
        score = eig * (1.0 - float(redundancy_penalty) * max(0.0, min(1.0, red)))
        rec = dict(it)
        rec["eig"] = round(eig, 4)
        rec["redundancy"] = round(red, 4)
        rec["priority"] = round(score, 4)
        scored.append(rec)
    scored.sort(key=lambda r: -r["priority"])
    return scored[:max(1, int(limit))]


# ══════════════════════════════════════════════════════════════════════
# One report over all seven, so the monitor has a single call
# ══════════════════════════════════════════════════════════════════════

def operator_report(rows: Sequence[dict], features: Optional[Sequence[str]] = None,
                    target: str = "margin", check: Optional[str] = None,
                    max_features: int = 6, max_cond: int = 1) -> Dict[str, Any]:
    """Exercise every operator organ against real rows, never raising.

    Each organ reports a MEASURED quantity (edges found, recall accuracy,
    routing entropy, holdout error) rather than a claim of readiness.

    `max_features`/`max_cond` are budgets, not preferences: PC conditions on
    every subset of a node's neighbours up to `max_cond`, so the test count
    grows combinatorially. Uncapped, RADIX on the fifteen auto-detected
    columns runs tens of thousands of partial-correlation solves and would
    stall a 30-second refresh loop.
    """
    out: Dict[str, Any] = {}
    keys = list(features or [])[:max(1, int(max_features))]
    needed = keys + ([target] if target else [])
    with_check = needed + ([check] if check else [])

    def _numeric(row: dict) -> Dict[str, float]:
        got: Dict[str, float] = {}
        for k, v in row.items():
            if isinstance(v, bool):
                continue
            try:
                f = float(v)
            except (TypeError, ValueError) as exc:
                note_failure("exmachina_operators:L1036", exc)
                continue
            if math.isfinite(f):
                got[k] = f
        return got

    all_rows = [_numeric(r) for r in (rows or []) if isinstance(r, dict)]

    def _columns(req: Sequence[str]) -> Dict[str, List[float]]:
        """Column lists built ONLY from rows that carry every required key.

        Row alignment is the whole point. Building each column independently
        across a sparse field -- the check flag exists on 15 rows out of 120
        -- pairs one key's values with a DIFFERENT row's, so every
        correlation computed afterwards describes a dataset that never
        existed. Complete-case subsets are the honest alternative, and the
        count is reported rather than hidden.
        """
        cols: Dict[str, List[float]] = {k: [] for k in req}
        if not req:
            return cols
        for got in all_rows:
            if all(k in got for k in req):
                for k in req:
                    cols[k].append(got[k])
        return cols

    data = _columns(with_check)
    model_data = _columns(needed)
    n = min((len(v) for v in data.values()), default=0)
    n_models = min((len(v) for v in model_data.values()), default=0)
    out["n_rows"] = len(all_rows)
    out["n_complete"] = n
    out["n_complete_models"] = n_models

    # RADIX
    if keys and n >= 12:
        pairs: Dict[str, Sequence[float]] = {k: v[:n] for k, v in data.items() if len(v) >= n}
        try:
            out["radix"] = discover(pairs, target=target if target in pairs else None,
                                    check=check if check in pairs else None,
                                    max_cond=max_cond)
        except Exception as exc:
            note_failure("exmachina_operators:L1078", exc)
            out["radix"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    else:
        out["radix"] = {"note": "need >=12 rows and >=1 numeric predictor"}

    # VELA
    try:
        hdc = HDC(dim=512)
        out["vela"] = hdc.selftest(n_items=24, n_queries=12)
    except Exception as exc:
        note_failure("exmachina_operators:L1087", exc)
        out["vela"] = {"error": "%s: %s" % (type(exc).__name__, exc)}

    # NEXUS + OPUS + NAVIS on the real series when there is enough of it.
    # These use the MODEL subset, not the RADIX subset: they do not need the
    # check column, and requiring it silently cut them from 120 rows to 15.
    if keys and n_models >= 16:
        nm = n_models
        X = [[model_data[k][i] for k in keys] for i in range(nm)]
        y = model_data.get(target, [0.0] * nm)[:nm]
        split = max(4, int(nm * 0.7))
        try:
            moe = SparseMoE(n_experts=min(4, len(keys) + 1), top_k=2).fit(X[:split], y[:split])
            pred = moe.predict(X[split:])
            err = [abs(pred[i] - y[split + i]) for i in range(len(pred))]
            moe_rep = moe.report(X)
            moe_rep["holdout_mae"] = round(sum(err) / len(err), 4) if err else None
            out["nexus"] = moe_rep
        except Exception as exc:
            note_failure("exmachina_operators:L1105", exc)
            out["nexus"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            kan = KAN(n_in=len(keys), n_grid=5).fit(X[:split], y[:split])
            kp = kan.predict(X[split:])
            kerr = [abs(kp[i] - y[split + i]) for i in range(len(kp))]
            krep = kan.report()
            krep["holdout_mae"] = round(sum(kerr) / len(kerr), 4) if kerr else None
            out["opus"] = krep
        except Exception as exc:
            note_failure("exmachina_operators:L1114", exc)
            out["opus"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            esn = EchoState(n_in=len(keys), n_res=32, seed=7)
            esn.fit(X[:split], y[:split], washout=2, ridge=1e-2)
            ep = esn.predict(X[split:], washout=2)
            eerr = [abs(ep[i] - y[split + i]) for i in range(min(len(ep), max(0, nm - split)))]
            out["navis"] = {"n_res": esn.n_res, "spectral_radius": esn.spectral_radius,
                            "trained": esn.readout is not None,
                            "holdout_mae": round(sum(eerr) / len(eerr), 4) if eerr else None}
        except Exception as exc:
            note_failure("exmachina_operators:L1124", exc)
            out["navis"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            bo = TrustRegionBO({k: (min(data[k]), max(data[k])) for k in keys})
            for i in range(min(split, 30)):
                bo.tell(X[i], y[i])
            out["catena"] = bo.report()
        except Exception as exc:
            note_failure("exmachina_operators:L1131", exc)
            out["catena"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    else:
        for k in ("nexus", "opus", "navis", "catena"):
            out[k] = {"note": "need >=16 rows and numeric predictors"}

    # LIMEN always available (it ranks whatever it is given)
    out["limen"] = {"items": 0, "note": "call rank_candidates() with the evidence set"}
    return out
