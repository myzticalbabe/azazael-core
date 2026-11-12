"""
Eve Unleashed — Self-Modifying Worms (Phase 3, V9.0).

Worm DNA: each worm carries a mutable configuration genome.
Fitness function: effectiveness = f(resource usage, insight quality, error rate).
Genetic algorithm: breed successful worms, cull underperformers.
Controlled mutation: MetaWormSpawner proposes a spawn/mutation,
CurativePool (HYDRA) validates, InferenceBridge approves before applying.

Fail-open: evolution degrades to static genomes if PAMS/bridge/spawner unavailable.
"""

from __future__ import annotations

import math
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .dodeca_recall import SHAPES  # recall never imports worms, so no cycle

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass


#: Cap on inherited hunches: lineage memory, not a junk drawer.
HUNCH_CAP = 8


def splice_hunches(a: List[str], b: List[str],
                   rng: Optional[random.Random] = None,
                   cap: int = HUNCH_CAP) -> List[str]:
    """Crossover of intuitions: head of a's hunches + tail of b's.

    Deduped in order, Nones dropped, capped. Pure, tested."""
    if rng is None:
        rng = random.Random()
    _ = rng  # order is positional, not random: head-meets-tail splice
    ahead = (a or [])[:(len(a or []) + 1) // 2]
    btail = (b or [])[len(b or []) // 2:]
    out: List[str] = []
    for h in list(ahead) + list(btail):
        if h and h not in out:
            out.append(h)
    return out[:cap]


#: Genome parameters every tunable worm exposes.
@dataclass
class WormDNA:
    """Mutable configuration genome for a self-modifying worm.
    
    Defaults evolved via live training (E2R-1..9) on py316t:
    - dream hit-rate 0.97 (1,758 pairs, 1,706 hits)
    - best genome: 3141d73e (gen 69, min_confidence=0.835)
    - live walls: Wikipedia (train-cap), httpbin 404 (train-cap)
    """
    worm_id: str
    worm_type: str
    scan_interval_s: float = 36.27
    aggressiveness: float = 0.297
    resource_budget_mb: float = 307.26
    mutation_rate: float = 0.5
    min_confidence: float = 0.835
    enabled_metrics: List[str] = field(default_factory=lambda: ["cpu", "mem"])
    cooldown_s: float = 6.92
    generation: int = 1
    parent_ids: List[str] = field(default_factory=list)
    hunches: List[str] = field(default_factory=lambda: ["render", "commoncrawl", "plain"])
    shape: str = "g40"

    def __post_init__(self) -> None:
        """Enforce genome invariants at construction, not just in mutate().
        Same bounds mutate() already guarantees, so a directly built genome
        can never carry a value breeding cannot produce."""
        self.scan_interval_s = max(5.0, self.scan_interval_s)
        self.aggressiveness = max(0.0, min(1.0, self.aggressiveness))
        self.resource_budget_mb = max(64.0, self.resource_budget_mb)
        self.mutation_rate = max(0.01, min(0.5, self.mutation_rate))
        self.min_confidence = max(0.5, min(0.99, self.min_confidence))
        self.cooldown_s = max(5.0, self.cooldown_s)
        if self.shape not in SHAPES:
            self.shape = "dodecahedron"

    def mutate(self, rng: Optional[random.Random] = None) -> "WormDNA":
        if rng is None:
            rng = random.Random()
        d = asdict(self)
        d["worm_id"] = str(uuid.uuid4())
        d["generation"] = self.generation + 1
        d["parent_ids"] = [self.worm_id]
        genes = ["scan_interval_s", "aggressiveness", "resource_budget_mb",
                 "min_confidence", "cooldown_s"]
        for gene in genes:
            if rng.random() < self.mutation_rate:
                cur = d[gene]
                if gene in ("scan_interval_s", "cooldown_s"):
                    d[gene] = max(5.0, cur * rng.uniform(0.5, 1.5))
                elif gene == "aggressiveness":
                    d[gene] = max(0.0, min(1.0, cur + rng.uniform(-0.2, 0.2)))
                elif gene == "resource_budget_mb":
                    d[gene] = max(64.0, cur * rng.uniform(0.7, 1.4))
                elif gene == "min_confidence":
                    d[gene] = max(0.5, min(0.99, cur + rng.uniform(-0.1, 0.1)))
        d["mutation_rate"] = max(0.01, min(0.5, self.mutation_rate *
                                           rng.uniform(0.8, 1.2)))
        if rng.random() < 0.05:  # rare morph: try another solid (H-01)
            d["shape"] = rng.choice(sorted(SHAPES))
        return WormDNA(**d)

    #: A child recalls on the fitter parent's solid (H-01): selection
    #: pressure on shapes, not just genes. Unknown fitter -> coin flip.
    @staticmethod
    def crossover(a: "WormDNA", b: "WormDNA",
                  rng: Optional[random.Random] = None,
                  fitter: Optional["WormDNA"] = None) -> "WormDNA":
        if rng is None:
            rng = random.Random()
        child = asdict(a)
        child["worm_id"] = str(uuid.uuid4())
        child["generation"] = max(a.generation, b.generation) + 1
        child["parent_ids"] = [a.worm_id, b.worm_id]
        for gene in ("scan_interval_s", "aggressiveness", "resource_budget_mb",
                     "min_confidence", "cooldown_s"):
            child[gene] = getattr(a, gene) if rng.random() < 0.5 else getattr(b, gene)
        child["mutation_rate"] = (a.mutation_rate + b.mutation_rate) / 2
        child["hunches"] = splice_hunches(a.hunches, b.hunches, rng=rng)
        if fitter is a:
            child["shape"] = a.shape
        elif fitter is b:
            child["shape"] = b.shape
        elif rng.random() < 0.5:
            child["shape"] = b.shape
        enabled = set(a.enabled_metrics) | set(b.enabled_metrics)
        child["enabled_metrics"] = sorted(enabled)
        return WormDNA(**child)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "WormDNA":
        return WormDNA(**{k: v for k, v in d.items()
                          if k in WormDNA.__dataclass_fields__})


@dataclass
class WormFitness:
    """Measured effectiveness of one worm run."""

    worm_id: str
    worm_type: str
    generation: int
    resource_usage: float = 0.0        # 0..1 (lower is better)
    insight_quality: float = 0.0       # 0..1 (higher is better)
    error_rate: float = 0.0            # 0..1 (lower is better)
    uptime_s: float = 0.0
    actions_taken: int = 0
    actions_succeeded: int = 0
    recorded_at: float = field(default_factory=time.time)

    def score(self) -> float:
        """Composite fitness 0..1; higher = more effective."""
        success_rate = (self.actions_succeeded / max(1, self.actions_taken))
        resource_penalty = min(1.0, self.resource_usage)
        return (0.4 * self.insight_quality
                + 0.3 * success_rate
                + 0.2 * (1.0 - resource_penalty)
                + 0.1 * (1.0 - self.error_rate))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GeneticAlgorithm:
    """Breed successful worm genomes, cull underperformers."""

    def __init__(self, population_size: int = 8, elite_keep: int = 2,
                 rng: Optional[random.Random] = None):
        self.population_size = max(2, population_size)
        self.elite_keep = max(1, min(elite_keep, self.population_size - 1))
        self.rng = rng or random.Random()
        self.population: List[WormDNA] = []
        self.fitness: Dict[str, WormFitness] = {}
        self.generation = 0
        self._lock = threading.Lock()

    def seed(self, dnas: List[WormDNA]) -> None:
        with self._lock:
            self.population = list(dnas[: self.population_size])
            while len(self.population) < self.population_size:
                self.population.append(WormDNA(worm_id=str(uuid.uuid4()),
                                               worm_type="generic"))
            self.generation = 1

    def record_fitness(self, fitness: WormFitness) -> None:
        with self._lock:
            self.fitness[fitness.worm_id] = fitness

    def evolve(self) -> List[WormDNA]:
        """One generation: select elites, breed children, cull weaklings.
        Returns the new population."""
        with self._lock:
            pop = list(self.population)
            fit = dict(self.fitness)

        # Score each genome; missing fitness = baseline 0.3.
        scored = [(d, f.score() if (f := fit.get(d.worm_id)) is not None else 0.3)
                  for d in pop]
        scored.sort(key=lambda x: x[1], reverse=True)

        elites = [d for d, _ in scored[: self.elite_keep]]
        cull_threshold = scored[self.elite_keep][1] if len(scored) > self.elite_keep else 0.0

        new_pop: List[WormDNA] = list(elites)
        while len(new_pop) < self.population_size:
            p1, p2 = self._select(scored), self._select(scored)
            s1 = fit[p1.worm_id].score() if fit.get(p1.worm_id) else 0.3
            s2 = fit[p2.worm_id].score() if fit.get(p2.worm_id) else 0.3
            child = WormDNA.crossover(p1, p2, rng=self.rng,
                                      fitter=p1 if s1 >= s2 else p2)
            if self.rng.random() < 0.7:
                child = child.mutate(rng=self.rng)
            new_pop.append(child)

        self.population = new_pop[: self.population_size]
        self.generation += 1
        return list(self.population)

    def _select(self, scored: List[tuple]) -> WormDNA:
        # Fitness-proportionate selection with a floor so weak genomes survive.
        total = max(0.01, sum(s for _, s in scored))
        pick = self.rng.random() * total
        acc = 0.0
        for d, s in scored:
            acc += max(0.0, s)
            if acc >= pick:
                return d
        return scored[-1][0]

    def best(self) -> Optional[WormDNA]:
        with self._lock:
            return self._best_unlocked()

    def _best_unlocked(self) -> Optional[WormDNA]:
        if not self.population:
            return None
        scored = [(d, f.score() if (f := self.fitness.get(d.worm_id)) is not None
                   else 0.3)
                  for d in self.population]
        return max(scored, key=lambda x: x[1])[0]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            best = self._best_unlocked()
            return {
                "generation": self.generation,
                "population_size": len(self.population),
                "elite_keep": self.elite_keep,
                "best_fitness": (best.worm_id if best else None),
                "genomes": [d.to_dict() for d in self.population],
            }


class EvolutionaryWormManager:
    """Orchestrates propose -> validate (CurativePool/HYDRA) -> approve (InferenceBridge)
    -> spawn via MetaWormSpawner for each evolved genome."""

    def __init__(self, spawner=None, ga: Optional[GeneticAlgorithm] = None):
        self.spawner = spawner
        self.ga = ga or GeneticAlgorithm()
        self._bridge = None
        self._governor = None
        self._recorder = None
        self.evolution_history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    # -- subsystem access (fail-open) --------------------------------------

    def _get_bridge(self):
        if self._bridge is not None:
            return self._bridge
        try:
            from META.InferenceBridge import InferenceBridge
            self._bridge = InferenceBridge()
            return self._bridge
        except Exception as exc:
            note_failure("evo_worms:bridge", exc)
            return None

    def _get_governor(self):
        if self._governor is not None:
            return self._governor
        try:
            from META.MetaWormSpawner import CurativePool
            self._governor = CurativePool()
            return self._governor
        except Exception as exc:
            note_failure("evo_worms:governor", exc)
            return None

    def _get_recorder(self):
        if self._recorder is not None:
            return self._recorder
        try:
            from CORE.PamsRecorder import get_recorder
            self._recorder = get_recorder()
            return self._recorder
        except Exception as exc:
            note_failure("evo_worms:recorder", exc)
            return None

    def _get_spawner(self):
        if self.spawner is not None:
            return self.spawner
        try:
            from META.MetaWormSpawner import MetaWormSpawner
            self.spawner = MetaWormSpawner()
            return self.spawner
        except Exception as exc:
            note_failure("evo_worms:spawner", exc)
            return None

    # -- evolution cycle ---------------------------------------------------

    def propose_mutation(self, dna: WormDNA) -> Dict[str, Any]:
        """Propose a mutated worm: validate then approve -> spawn."""
        mutated = dna.mutate(rng=self.ga.rng)
        proposal = {
            "action_type": "spawn_mutant",
            "worm_type": mutated.worm_type,
            "genome": mutated.to_dict(),
            "output_size": 200,
        }

        # 1) Validate via CurativePool (HYDRA governance).
        governor = self._get_governor()
        decision = {"decision": "allowed"}
        if governor is not None:
            try:
                decision = governor.evaluate_action(
                    {"context": f"spawn mutant {mutated.worm_type}"},
                    proposal, [], [])
            except Exception as exc:
                note_failure("evo_worms:govern_eval", exc)
                decision = {"decision": "allowed"}

        if decision.get("decision") != "allowed":
            return {"status": "blocked", "reason": "governor_rejected",
                    "genome": mutated.to_dict()}

        # 2) Approve via InferenceBridge (LLM sanity check on the genome).
        bridge = self._get_bridge()
        if bridge is not None:
            try:
                bridge_status = bridge.get_bridge_status()
                if not bridge_status.get("llama_available", False):
                    decision = {"decision": "allowed"}  # degrade, don't block
                else:
                    prompt = ("Review this proposed worm genome for safety. "
                              "Reply ONLY with 'APPROVE' or 'REJECT'.\n"
                              f"{mutated.to_dict()}\n")
                    verdict = bridge.send_to_llama(prompt).strip().upper()
                    decision = {"decision": "allowed" if "APPROVE" in verdict
                                else "rejected"}
            except Exception as exc:
                note_failure("evo_worms:bridge_approve", exc)
                decision = {"decision": "allowed"}  # fail-open

        if decision.get("decision") != "allowed":
            return {"status": "blocked", "reason": "bridge_rejected",
                    "genome": mutated.to_dict()}

        # 3) Spawn via MetaWormSpawner.
        spawner = self._get_spawner()
        entry = None
        if spawner is not None:
            try:
                entry = spawner.spawn(f"evo_{mutated.worm_type}",
                                      mutated.worm_type, priority=5,
                                      request={"context": "evolutionary spawn"},
                                      _interactive=False)
            except Exception as exc:
                note_failure("evo_worms:spawn", exc)

        result = {"status": "spawned" if entry else "spawn_failed",
                  "genome": mutated.to_dict(),
                  "worm_id": mutated.worm_id,
                  "entry_id": entry.id if entry else None,
                  "generation": mutated.generation}
        with self._lock:
            self.evolution_history.append(result)
        self._record(result)
        return result

    def run_generation(self) -> Dict[str, Any]:
        """Evolve one generation and propose top genome for spawning."""
        new_pop = self.ga.evolve()
        best = self.ga.best()
        outcome = {"status": "no_best", "generation": self.ga.generation}
        if best is not None:
            outcome = self.propose_mutation(best)
        outcome["population"] = [d.to_dict() for d in new_pop]
        return outcome

    def record_fitness(self, fitness: WormFitness) -> None:
        self.ga.record_fitness(fitness)
        self._record({"type": "fitness", "data": fitness.to_dict()})

    def _record(self, payload: Dict[str, Any]) -> None:
        rec = self._get_recorder()
        if rec is None:
            return
        try:
            rec.record_event(event_type="worm_evolution", data=payload,
                             source="evolutionary_worm_manager",
                             severity="info")
        except Exception as exc:
            note_failure("evo_worms:record", exc)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "ga": self.ga.snapshot(),
            "evolution_history": list(self.evolution_history[-50:]),
        }


__all__ = ["WormDNA", "WormFitness", "GeneticAlgorithm",
           "EvolutionaryWormManager"]
