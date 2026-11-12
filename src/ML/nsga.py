# coding: utf-8
"""NSGA-II multi-objective optimizer (Deb et al., 2002), implemented from
first principles on numpy.

Real-coded: simulated binary crossover (SBX), polynomial mutation,
non-dominated sorting, crowding-distance niching, binary tournament
selection. Stateless functions plus the stateful :class:`NSGA2` driver;
fresh RNG per instance (seedable) so concurrent runs are independent and
free-thread safe.
"""

from __future__ import annotations

import math
from typing import Callable, List

import numpy as np

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def dominates(a: np.ndarray, b: np.ndarray) -> bool:
    """True when ``a`` is no worse than ``b`` everywhere and better somewhere."""
    return bool(np.all(a <= b) and np.any(a < b))


def non_dominated_sort(objectives: np.ndarray) -> list[list[int]]:
    """Sort row indices into Pareto fronts (front 0 first)."""
    n = len(objectives)
    dominated_by: list[set[int]] = [set() for _ in range(n)]
    domination_count = [0] * n
    fronts: list[list[int]] = [[]]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if dominates(objectives[i], objectives[j]):
                dominated_by[i].add(j)
            elif dominates(objectives[j], objectives[i]):
                domination_count[i] += 1
        if domination_count[i] == 0:
            fronts[0].append(i)
    k = 0
    while fronts[k]:
        nxt: list[int] = []
        for i in fronts[k]:
            for j in dominated_by[i]:
                domination_count[j] -= 1
                if domination_count[j] == 0:
                    nxt.append(j)
        k += 1
        fronts.append(nxt)
    return [f for f in fronts if f]


def crowding_distance(front_objectives: np.ndarray) -> np.ndarray:
    """Crowding distance per row; boundary points get ``inf``."""
    n, m = front_objectives.shape
    dist = np.zeros(n)
    if n <= 2:
        dist[:] = math.inf
        return dist
    for k in range(m):
        order = np.argsort(front_objectives[:, k])
        vals = front_objectives[order, k]
        span = vals[-1] - vals[0]
        dist[order[0]] = math.inf
        dist[order[-1]] = math.inf
        if span <= 0:
            continue
        for pos in range(1, n - 1):
            dist[order[pos]] += (vals[pos + 1] - vals[pos - 1]) / span
    return dist


def sbx_crossover(
    rng: np.random.Generator,
    p1: np.ndarray,
    p2: np.ndarray,
    bounds: np.ndarray,
    eta: float = 20.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Simulated binary crossover; children clipped to ``bounds``."""
    c1, c2 = p1.copy(), p2.copy()
    for i in range(len(p1)):
        if rng.random() > 0.5 or abs(p1[i] - p2[i]) < 1e-12:
            continue
        u = rng.random()
        if u <= 0.5:
            beta = (2.0 * u) ** (1.0 / (eta + 1.0))
        else:
            beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))
        c1[i] = 0.5 * ((1 + beta) * p1[i] + (1 - beta) * p2[i])
        c2[i] = 0.5 * ((1 - beta) * p1[i] + (1 + beta) * p2[i])
    lo, hi = bounds[:, 0], bounds[:, 1]
    return np.clip(c1, lo, hi), np.clip(c2, lo, hi)


def polynomial_mutation(
    rng: np.random.Generator,
    x: np.ndarray,
    bounds: np.ndarray,
    eta: float = 20.0,
    rate: float | None = None,
) -> np.ndarray:
    """Polynomial mutation with per-gene ``rate`` (default ``1/n``)."""
    n = len(x)
    if rate is None:
        rate = 1.0 / n
    lo, hi = bounds[:, 0], bounds[:, 1]
    y = x.copy()
    for i in range(n):
        if rng.random() > rate:
            continue
        span = hi[i] - lo[i]
        if span <= 0:
            continue
        delta1 = (y[i] - lo[i]) / span
        delta2 = (hi[i] - y[i]) / span
        u = rng.random()
        if u < 0.5:
            delta = (2.0 * u + (1.0 - 2.0 * u) * (1.0 - delta1) ** (eta + 1.0)) ** (1.0 / (eta + 1.0)) - 1.0
        else:
            delta = 1.0 - (2.0 * (1.0 - u) + 2.0 * (u - 0.5) * (1.0 - delta2) ** (eta + 1.0)) ** (1.0 / (eta + 1.0))
        y[i] = np.clip(y[i] + delta * span, lo[i], hi[i])
    return y


class NSGA2:
    """NSGA-II driver for ``fns`` objectives over box ``bounds``."""

    def __init__(
        self,
        fns: list[Callable[[np.ndarray], float]],
        bounds: np.ndarray,
        pop_size: int = 60,
        seed: int = 0,
        crossover_eta: float = 20.0,
        mutation_eta: float = 20.0,
    ) -> None:
        self.fns = fns
        self.bounds = np.asarray(bounds, dtype=float)
        self.pop_size = pop_size
        self.rng = np.random.default_rng(seed)
        self.crossover_eta = crossover_eta
        self.mutation_eta = mutation_eta

    def evaluate(self, pop: np.ndarray) -> np.ndarray:
        return np.array([[f(ind) for f in self.fns] for ind in pop])

    def _tournament(self, pop: np.ndarray, rank: np.ndarray, crowd: np.ndarray) -> np.ndarray:
        i, j = self.rng.integers(0, len(pop), 2)
        if rank[i] < rank[j] or (rank[i] == rank[j] and crowd[i] > crowd[j]):
            return pop[i].copy()
        return pop[j].copy()

    def run(self, generations: int) -> tuple[np.ndarray, np.ndarray]:
        """Evolve and return ``(pareto_X, pareto_F)`` of the final front."""
        lo, hi = self.bounds[:, 0], self.bounds[:, 1]
        pop = self.rng.uniform(lo, hi, size=(self.pop_size, len(lo)))
        for _ in range(generations):
            objs = self.evaluate(pop)
            fronts = non_dominated_sort(objs)
            rank = np.empty(len(pop), dtype=int)
            crowd = np.empty(len(pop))
            for r, front in enumerate(fronts):
                rank[front] = r
                crowd[front] = crowding_distance(objs[front])
            children: list[np.ndarray] = []
            while len(children) < self.pop_size:
                p1 = self._tournament(pop, rank, crowd)
                p2 = self._tournament(pop, rank, crowd)
                c1, c2 = sbx_crossover(self.rng, p1, p2, self.bounds, self.crossover_eta)
                children.append(polynomial_mutation(self.rng, c1, self.bounds, self.mutation_eta))
                if len(children) < self.pop_size:
                    children.append(polynomial_mutation(self.rng, c2, self.bounds, self.mutation_eta))
            combined = np.vstack([pop, np.array(children)])
            objs = self.evaluate(combined)
            fronts = non_dominated_sort(objs)
            nxt: list[int] = []
            for front in fronts:
                if len(nxt) + len(front) <= self.pop_size:
                    nxt.extend(front)
                    continue
                dist = crowding_distance(objs[front])
                order = sorted(range(len(front)), key=lambda k: -dist[k])
                nxt.extend(front[k] for k in order[: self.pop_size - len(nxt)])
                break
            pop = combined[nxt]
        objs = self.evaluate(pop)
        front = non_dominated_sort(objs)[0]
        return pop[front], objs[front]
