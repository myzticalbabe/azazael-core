# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: ENVIRONMENT (Arena)

Arena removes the fixed-world ceiling. Every batch so far optimised against a
pool configuration that never moved: the snapshot pool, reused across cycles
until an era change reset the evidence. E9 is the bill for that — a +3.3 margin
on the snapshot bought **0/4** holdout passes, because the search had overfit a
world that could not argue back.

Arena makes the world an adversary. This is POET-style paired open-ended
co-evolution:

* An `Environment` is a concrete pool configuration — the severity mix, the
  hard-alert ratio, the era pressure. It is a *sampleable world*, not a number.
* Environments mutate toward **novelty**, subject to a **minimal criterion**
  (MC): a mutated world must be *solvable* by its paired solution, yet
  *nontrivial* for the population. Worlds nobody can solve are discarded, and
  so are worlds everybody already solves.
* Solutions **transfer**. A genome that solves world A is tested on the frontier
  of world B, and transfer success — not raw fitness — is the selection
  pressure. That is what forbids structural overfitting.

Two consequences the plan calls out explicitly:

1. **E3 becomes impossible** because open-endedness is defined by never
   converging. There is no static optimum for flat trajectories to sit on.
2. **E9 becomes structurally impossible** because there is no fixed pool left to
   overfit; the pool is an evolving adversary.

Constraint (must not be violated): never gate promotion on a world being
optimised. Arena's worlds are for *search*; the gate must read the Oracle's
imagined eras or a held-out keeper set. `keeper_split` exists so a disjoint
world set can be reserved for exactly that.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "Environment",
    "ArenaWorld",
    "Arena",
    "keeper_split",
]

# A solver is any callable `(genome, env_config) -> float`.
SolverFn = Callable[[Any, Dict[str, Any]], float]


class Environment:
    """A sampleable world: the pool configuration a cycle is run against.

    Fields mirror what the pipeline already records in `pool_fingerprint`, so no
    new instrumentation is required to persist or reload a world.
    """

    __slots__ = ("severity_mix", "hard_ratio", "era_pressure", "n_alerts", "seed")

    def __init__(self, severity_mix: Sequence[float], hard_ratio: float,
                 era_pressure: float, n_alerts: int = 1000, seed: int = 0):
        total = float(sum(severity_mix)) or 1.0
        self.severity_mix = [float(x) / total for x in severity_mix]
        self.hard_ratio = float(min(1.0, max(0.0, hard_ratio)))
        self.era_pressure = float(max(0.0, era_pressure))
        self.n_alerts = int(max(10, n_alerts))
        self.seed = int(seed)

    # -- views ------------------------------------------------------------
    def descriptor(self) -> List[float]:
        """Fixed-width novelty descriptor (POET compares in behaviour space)."""
        return list(self.severity_mix) + [self.hard_ratio,
                                          math.log1p(self.era_pressure)]

    def config(self) -> Dict[str, Any]:
        """The dict the runner consumes to actually build a pool."""
        return {
            "severity_mix": list(self.severity_mix),
            "hard_ratio": self.hard_ratio,
            "era_pressure": self.era_pressure,
            "n_alerts": self.n_alerts,
            "seed": self.seed,
        }

    def fingerprint(self) -> str:
        parts = ["%.4f" % x for x in self.severity_mix]
        parts += ["%.4f" % self.hard_ratio, "%.4f" % self.era_pressure,
                  "%d" % self.n_alerts, "%d" % self.seed]
        return "|".join(parts)

    def mutate(self, rng: random.Random, strength: float = 0.15) -> "Environment":
        mix = []
        for x in self.severity_mix:
            mix.append(max(0.0, x + rng.gauss(0.0, strength)))
        if sum(mix) <= 0:
            mix = list(self.severity_mix)
        hard = min(1.0, max(0.0, self.hard_ratio + rng.gauss(0.0, strength)))
        press = max(0.0, self.era_pressure * (1.0 + rng.gauss(0.0, strength)))
        n = max(10, int(self.n_alerts * (1.0 + rng.gauss(0.0, strength / 2.0))))
        return Environment(mix, hard, press, n, rng.randrange(1 << 30))

    def to_dict(self) -> dict:
        return self.config()

    @classmethod
    def from_dict(cls, d: dict) -> "Environment":
        mix = d.get("severity_mix") or [1.0, 1.0, 1.0]
        return cls(mix, d.get("hard_ratio", 0.0), d.get("era_pressure", 1.0),
                   d.get("n_alerts", 1000), d.get("seed", 0))

    def __repr__(self) -> str:
        return "Env(mix=%s hard=%.2f press=%.2f)" % (
            ["%.2f" % x for x in self.severity_mix], self.hard_ratio,
            self.era_pressure)


class ArenaWorld:
    """An environment paired with the solution that currently solves it."""

    __slots__ = ("env", "genome", "fitness", "parent")

    def __init__(self, env: Environment, genome: Any, fitness: float,
                 parent: Optional[str] = None):
        self.env = env
        self.genome = genome
        self.fitness = float(fitness)
        self.parent = parent

    @property
    def key(self) -> str:
        return self.env.fingerprint()

    def to_dict(self) -> dict:
        return {"env": self.env.to_dict(), "fitness": self.fitness,
                "parent": self.parent}


def _euclid(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


class Arena:
    """POET-style paired co-evolution of worlds and solutions."""

    def __init__(self, solver: SolverFn, mc_low: float = 0.35, mc_high: float = 0.85,
                 archive_cap: int = 32, seed: int = 0):
        self.solver = solver
        self.mc_low = float(mc_low)      # below this the world is too hard
        self.mc_high = float(mc_high)    # above this everyone already solves it
        self.archive_cap = int(archive_cap)
        self.rng = random.Random(seed)
        self.worlds: List[ArenaWorld] = []
        self.history: List[dict] = []

    # -- novelty ----------------------------------------------------------
    def novelty(self, env: Environment) -> float:
        """Distance in behaviour space to the nearest archived world."""
        if not self.worlds:
            return float("inf")
        d = env.descriptor()
        return min(_euclid(d, w.env.descriptor()) for w in self.worlds)

    def novelty_pressure(self) -> float:
        """Fraction of archive slots that are stale — drives how hard we mutate."""
        if not self.worlds:
            return 1.0
        return max(0.0, 1.0 - (len(self.worlds) / float(self.archive_cap)))

    # -- minimal criterion -------------------------------------------------
    def solve(self, genome: Any, env: Environment) -> float:
        try:
            return float(self.solver(genome, env.config()))
        except Exception as exc:
            note_failure("arena:L177", exc)
            return float("-inf")

    def populate(self, seed_env: Environment, genome: Any, n: int = 8) -> List[ArenaWorld]:
        """Seed the archive with worlds that satisfy the minimal criterion.

        A candidate world is admitted only when its paired solution actually
        solves it — the MC's "solvable" half. The "nontrivial" half is enforced
        later, at admission time, against the wider population.
        """
        admitted: List[ArenaWorld] = []
        base = self.solve(genome, seed_env)
        if base == float("-inf"):
            return admitted
        for _ in range(max(1, n)):
            cand = seed_env.mutate(self.rng, strength=0.2)
            f = self.solve(genome, cand)
            if f > float("-inf") and f >= base * self.mc_low:
                admitted.append(ArenaWorld(cand, genome, f, parent=seed_env.fingerprint()))
        self.worlds.extend(admitted)
        self._trim()
        return admitted

    def _trim(self) -> None:
        if len(self.worlds) <= self.archive_cap:
            return
        # Keep novelty: drop worlds closest to their neighbour (redundant ones).
        scored = []
        for i, w in enumerate(self.worlds):
            others = [x.env.descriptor() for j, x in enumerate(self.worlds) if j != i]
            nn = min((_euclid(w.env.descriptor(), o) for o in others), default=0.0)
            scored.append((nn, i))
        scored.sort()
        drop = {i for _, i in scored[:len(self.worlds) - self.archive_cap]}
        self.worlds = [w for i, w in enumerate(self.worlds) if i not in drop]

    # -- transfer ----------------------------------------------------------
    def transfer(self, genome: Any, source: ArenaWorld, target: ArenaWorld
                 ) -> Dict[str, float]:
        """Test a genome on a world it was not evolved for.

        `transfer_ratio` is the selection pressure: 1.0 means the genome solves
        the foreign world as well as its own. This is the number that makes
        structural overfitting unprofitable, because a genome that only works on
        its own pool scores near zero here.
        """
        own = self.solve(genome, source.env)
        foreign = self.solve(genome, target.env)
        if own == float("-inf") or foreign == float("-inf"):
            ratio = 0.0
        elif own > 0:
            ratio = max(0.0, min(1.0, foreign / own))
        else:
            ratio = 1.0 if foreign >= own else 0.0
        return {"own": own, "foreign": foreign, "transfer_ratio": ratio}

    def frontier(self) -> List[ArenaWorld]:
        """The most novel worlds that satisfy the minimal criterion."""
        if not self.worlds:
            return []
        ranked = sorted(self.worlds, key=lambda w: self.novelty(w.env), reverse=True)
        return ranked[:max(1, len(ranked) // 4)]

    # -- one co-evolution step --------------------------------------------
    def step(self, propose_genome: Callable[[ArenaWorld], Any],
             n_mutations: int = 2) -> dict:
        """Mutate worlds AND solutions together, keeping the MC.

        `propose_genome(world)` returns a candidate solution for that world —
        injected so Arena never depends on a specific search engine.
        """
        admitted, rejected_hard, rejected_easy = 0, 0, 0
        frontier = self.frontier() or self.worlds[:]

        for w in list(frontier):
            for _ in range(max(1, n_mutations)):
                child_env = w.env.mutate(self.rng, strength=0.25 * (0.5 + self.novelty_pressure()))
                child_genome = propose_genome(w)
                f = self.solve(child_genome, child_env)
                if f == float("-inf"):
                    rejected_hard += 1
                    continue
                # MC against the population: too easy or too hard both fail.
                ratios = [self.transfer(child_genome, ArenaWorld(child_env, child_genome, f), o)["transfer_ratio"]
                          for o in self.worlds[:6]]
                if ratios:
                    easy = sum(1 for r in ratios if r >= self.mc_high) / len(ratios)
                    if easy >= 1.0:
                        rejected_easy += 1
                        continue
                    if sum(ratios) / len(ratios) < self.mc_low * 0.5:
                        rejected_hard += 1
                        continue
                self.worlds.append(ArenaWorld(child_env, child_genome, f, parent=w.key))
                admitted += 1

        self._trim()
        row = {"admitted": admitted, "rejected_hard": rejected_hard,
               "rejected_easy": rejected_easy, "archive": len(self.worlds),
               "novelty_pressure": self.novelty_pressure()}
        self.history.append(row)
        return row

    # -- persistence -------------------------------------------------------
    def save(self, path: str) -> None:
        payload = {"worlds": [w.to_dict() for w in self.worlds],
                   "history": self.history[-64:],
                   "mc_low": self.mc_low, "mc_high": self.mc_high}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, default=str)

    def load(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            self.worlds = [ArenaWorld(Environment.from_dict(w["env"]), None,
                                      w.get("fitness", 0.0), w.get("parent"))
                           for w in d.get("worlds", [])]
            self.history = list(d.get("history", []))
        except Exception as exc:
            note_failure("arena:L296", exc)


def keeper_split(worlds: Sequence[ArenaWorld], keeper_frac: float = 0.25
                 ) -> Tuple[List[ArenaWorld], List[ArenaWorld]]:
    """Partition an arena into `search` worlds and disjoint `keeper` worlds.

    The keeper set exists to satisfy the one constraint Arena must never break:
    a promotion gate may read keepers or imagined eras, **never** the worlds the
    search is actively optimising. Keeping them disjoint at the type level is
    cheaper than remembering the rule.
    """
    if not worlds:
        return [], []
    n_keep = max(1, int(len(worlds) * float(keeper_frac)))
    ordered = sorted(worlds, key=lambda w: w.env.fingerprint())
    return ordered[n_keep:], ordered[:n_keep]
