# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: NEURAL ADVANCEMENT (2 worms)
EvolutionEngine — Population-based optimization beyond HYDRAMETA genetic algorithms.
GeneticOptimizer — Per-worm parameter optimization with selection/crossover/mutation.

Extends HYDRAMETA's genetic algorithm with:
- Population-based multi-generational optimization
- Tournament selection with elitism
- Adaptive crossover and mutation rates
- Fitness tracking across generations
- CurativePool-governed evolution gates
- PAMS/LocalMemoryStore integration for evolution history

Part of GOSP DoctoralThesisEngine — Tier 4: HYDRAMETA

Population-based optimization mechanics; not the improvement policy
(meta_evolution.py) or the hyperparameter tuner (ML.nsga_tuner).
"""

import json
import time
import random
import threading
from datetime import datetime
from typing import Any, Callable, List
from dataclasses import dataclass, field

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Module-level imports for 3.14t thread safety (Lesson #29) --
try:
    from CORE.local_memory import get_memory_store as _get_memory_store_global
    _LOCAL_MEMORY_OK = True
except ImportError as exc:
    note_failure("evolution:L30", exc)
    _LOCAL_MEMORY_OK = False
    _get_memory_store_global = None

try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("evolution:L37", exc)
    _REGISTRY = False
    def _null_register_worm(*args: Any, **kw: Any) -> Any:
        return lambda cls: cls
    register_worm = _null_register_worm

try:
    from CORE.WORMS.expansion.fencing import fence_for
    _FENCING_OK = True
except ImportError as exc:
    note_failure("evolution:L45", exc)
    _FENCING_OK = False
    def _null_fence_for(name: str) -> Any:
        """Fallback fence that never rejects, for a partially-imported tree."""
        class _NoFence:
            def claim(self):
                return 0

            def commit(self, token, apply):
                return True, apply()
        return _NoFence()
    fence_for = _null_fence_for


import uuid as _uuid
import hashlib as _hl
import math as _math
import math as _m



# ===========================================================================
# WIRE-OR-CUT VERDICT (ROADMAP 3.2/3.3)
# ===========================================================================
# An engine that LOOKS wired but is reached by nothing is exercised by nothing:
# it cannot regress visibly and it cannot be trusted in production. The verdict
# is recorded as DATA so it is enforced rather than documented, and so that a
# half-wiring fails a test instead of passing unnoticed.
#
# Derivation: a name is SERVED when a production driver under `scripts/`
# actually reaches it (import + call), not merely when it exists. Everything
# else is declared UNSERVED with the reason it stays.
#
# `tests/test_v2_engines.py::TestWireOrCutVerdict` asserts that no UNSERVED
# name is referenced by `scripts/evolution_cycle.py` — so the honest ways to
# silence a failure are to wire the engine and move it to SERVED_ENGINES, or to
# stop referencing it. Adding a name to SERVED_ENGINES without wiring it just
# moves the lie.
SERVED_ENGINES: tuple[str, ...] = (
    "Genome",              # scripts/evolution_cycle.py + run_training_orchestrator.py
    "EvolutionEngine",     # scripts/evolution_cycle.py (the GA arm + the checkpoint drill)
    "GeneticOptimizer",    # scripts/run_training_orchestrator.py (cma/de wrappers)
    "SepCMAES",            # scripts/evolution_cycle.py (second arm)
    "DifferentialEvolution",  # scripts/evolution_cycle.py (third arm)
    "TPESurrogate",        # scripts/evolution_cycle.py (the surrogate/seed arm)
)

UNSERVED_ENGINES: dict[str, str] = {
    # Retired 2026-10-11 (trainer retirement wave 2B): MAPElites and
    # IslandModel had zero production/scheduled callers (tests only).
    # Entries removed with the classes; the dict stays as the registry
    # the wire-or-cut verdict tests iterate.
}

# The NSGA-III / MOEA-D helpers are unreferenced too, and saying so keeps the
# audit honest instead of quietly counting them as part of a wired engine.
UNSERVED_HELPERS: dict[str, str] = {
    "generate_reference_points": "NSGA-III reference directions; now served via algorithm_lab_cycle._diversity_contract (L3→L4).",
    "hypervolume_2d": "Pareto hypervolume metric; only exercised by tests.",
    "moead_weights": "MOEA/D weight vectors; no production caller and no test.",
    "tchebycheff": "MOEA/D scalarisation; no production caller and no test.",
}


@dataclass
class Genome:
    """An individual in the evolutionary population."""
    genes: dict[str, float]
    fitness: float = 0.0
    generation: int = 0
    parent_ids: list[str] = field(default_factory=list)
    # UUID4 id: unique per genome instance. The old timestamp+random scheme
    # collided within the same millisecond (all genomes in a generation),
    # which poisoned the fitness cache — a fresh child could inherit a
    # cached fitness from a different genome and never be evaluated.
    id: str = field(default_factory=lambda: f"g_{_uuid.uuid4().hex}")
    _crowding_distance: float = field(default=0.0, repr=False)  # NSGA-II crowding distance for diversity preservation


@register_worm(
    category="learning", priority=6, cooldown_seconds=120.0,
    description="Evolution engine — population-based optimization with selection, crossover, mutation, and CurativePool governance"
)
class EvolutionEngine:
    """Population-based evolutionary optimization engine.

    Extends HYDRAMETA genetic algorithms with full evolutionary loop:
    INITIALIZE -> EVALUATE -> SELECT -> CROSSOVER -> MUTATE -> REPLACE

    Features:
    - Configurable population size and generations
    - Tournament selection with elitism preservation
    - Adaptive crossover and mutation rates
    - CurativePool-governed evolution gates
    - Full evolution audit trail to LocalMemoryStore

    Usage:
        evo = EvolutionEngine()
        evo.initialize_population({"learning_rate": (0.0001, 0.1),
                                     "batch_size": (8, 256),
                                     "gamma": (0.8, 0.999)})
        for gen in range(10):
            evo.evaluate_population(lambda genome: evaluate_model(genome))
            evo.evolve()
        best = evo.get_best_genome()
    """

    VERSION = "2.0.0"

    def __init__(self, population_size: int = 50, elitism_count: int = 5,
                 crossover_rate: float = 0.7, mutation_rate: float = 0.1,
                 tournament_size: int = 3, early_stop_patience: int = 5,
                 early_stop_tolerance: float = 0.01, memory_store=None,
                  crossover_operator: str = "sbx",
                  mutation_operator: str = "polynomial",
                  eta_c: float = 15.0, eta_m: float = 20.0,
                  levy_alpha: float = 1.5,
                  adaptive_levy: bool = False):
        self._lock = threading.Lock()
        self._memory_store = memory_store  # Injected store (None = use global, "none" = disabled)
        # Why the store could not be resolved, so an unpersisted checkpoint can
        # say why instead of returning a success-shaped dict. See _get_store.
        self._store_error = ""
        self.population_size = population_size
        self.elitism_count = elitism_count
        self.crossover_rate = crossover_rate
        self.mutation_rate = mutation_rate
        self.tournament_size = tournament_size
        self.early_stop_patience = early_stop_patience
        self.early_stop_tolerance = early_stop_tolerance
        # -- v2.0 real-coded operators (audit item 1: SBX + poly mutation) --
        # crossover_operator: "uniform" (legacy) | "sbx"
        # mutation_operator: "uniform" (legacy) | "polynomial" | "gaussian" | "levy"
        self.crossover_operator = crossover_operator
        self.mutation_operator = mutation_operator
        self.eta_c = float(eta_c)
        self.eta_m = float(eta_m)
        # -- adaptive Levy flight (2026-09-28): heavy-tailed jumps engage
        # only after patience//2 stagnant gens; counted, never silent.
        self.levy_alpha = float(levy_alpha)
        self.adaptive_levy = bool(adaptive_levy)
        self._levy_gens = 0
        # -- v2.0 mixed-type genome specs (audit item 3) --
        # {name: ("float"|"int"|"bool"|"choice", lo, hi, choices)}
        # Empty = legacy float-only inferred from bounds tuples.
        self._gene_specs: dict[str, dict[str, Any]] = {}

        # -- Adaptive mutation rate --
        self.base_mutation_rate = mutation_rate
        self.max_mutation_rate = min(mutation_rate * 4.0, 0.5)
        self.min_mutation_rate = mutation_rate * 0.2
        self._diversity_threshold_low = 0.05   # Below this: increase mutation
        self._diversity_threshold_high = 0.15  # Above this: decrease mutation
        self._mutation_adaptation_rate = 0.1   # How fast the rate adjusts per generation

        self._population: list[Genome] = []
        self._generation = 0
        self._gene_bounds: dict[str, tuple[float, float]] = {}
        self._generation_history: list[dict[str, Any]] = []
        self._best_genome: (Genome | None) = None
        self._fitness_cache: dict[str, float] = {}
        self._fitness_hash_cache: dict[str, float] = {}  # v2.0: gene-hash -> fitness (cross-cycle reuse)
        self._curative_pool = None  # Lazy CurativePool singleton

    # -- v2.0 mixed-type genome specs ----------------------------------
    # gene_specs entries: {name: {"type": float|int|bool|choice, "lo":.., "hi":..,
    #   "choices": [...]}}. Unifies the old HYDRAMETA fork (bool/categorical
    #   params) into EvolutionEngine so H-4 can be retired.

    def set_gene_specs(self, specs: dict[str, dict]) -> None:
        """Define mixed-type genes. Example:
        {"interval": {"type": "float", "lo": 0.1, "hi": 5.0},
         "parallel": {"type": "bool"},
         "priority": {"type": "choice", "choices": ["low","normal","high"]},
         "workers":  {"type": "int", "lo": 1, "hi": 8}}
        """
        with self._lock:
            self._gene_specs = dict(specs or {})
            # Mirror continuous bounds for legacy paths
            for name, spec in self._gene_specs.items():
                t = spec.get("type", "float")
                if t in ("float", "int"):
                    self._gene_bounds[name] = (spec["lo"], spec["hi"])

    def _sample_gene(self, name: str):
        spec = self._gene_specs.get(name)
        if spec is None:
            bounds = self._gene_bounds.get(name)
            if bounds is None:
                return 0.0
            lo, hi = bounds
            if isinstance(lo, int) and isinstance(hi, int):
                return float(random.randint(lo, hi))
            if isinstance(lo, bool) or isinstance(hi, bool):
                return bool(random.random() < 0.5)
            return random.uniform(float(lo), float(hi))
        t = spec.get("type", "float")
        if t == "bool":
            return bool(random.random() < 0.5)
        if t == "choice":
            import random as _r
            return _r.choice(list(spec["choices"]))
        if t == "int":
            return int(random.randint(int(spec["lo"]), int(spec["hi"])))
        return random.uniform(float(spec["lo"]), float(spec["hi"]))

    @staticmethod
    def gene_hash(genes: dict) -> str:
        """Stable cross-cycle fitness key (gene values rounded, sorted)."""
        parts = []
        for k in sorted(genes):
            v = genes[k]
            if isinstance(v, float):
                parts.append("%s=%.6f" % (k, float(v)))
            else:
                parts.append("%s=%r" % (k, v))
        return _hl.sha256("|".join(parts).encode()).hexdigest()[:16]

    def initialize_population(self, gene_bounds: (dict | None) = None,
                              seed_genomes: (list[dict] | None) = None,
                              gene_specs: (dict | None) = None) -> list[Genome]:
        """Initialize a random population within gene bounds.

        Args:
            gene_bounds: {gene_name: (min_val, max_val)} legacy float/int bounds.
                Also accepts mixed specs {name: {"type":..}} (auto-detected).
            seed_genomes: Optional pre-existing genomes to seed the population
            gene_specs: Optional explicit mixed-type specs (see set_gene_specs)

        Returns:
            list of initialized Genome objects
        """
        # Auto-detect mixed-spec dicts passed as gene_bounds
        if gene_bounds and any(isinstance(v, dict) for v in gene_bounds.values()):
            gene_specs = gene_bounds
            gene_bounds = None
        with self._lock:
            if gene_specs:
                self._gene_specs = dict(gene_specs)
                for name, spec in self._gene_specs.items():
                    t = spec.get("type", "float")
                    if t in ("float", "int"):
                        self._gene_bounds[name] = (spec["lo"], spec["hi"])
            elif gene_bounds:
                self._gene_bounds = dict(gene_bounds)
                # Infer bool/choice pass-through: keep legacy float/int sampling
            self._population = []
            self._generation = 0
            self._fitness_cache.clear()

            # Seed with provided genomes first, normalized to the current
            # gene bounds. Stale seeds from older genome shapes (e.g. a
            # 3-gene hall-of-famer after a 4th gene is added) get missing
            # genes filled with uniform draws — otherwise _crossover (which
            # iterates parent1's keys) silently drops the new dimension
            # from every child of that seed (2026-09-08 fix).
            bounds = self._gene_bounds
            if self._gene_specs:
                # full key universe = specs + legacy bounds
                bounds = dict(self._gene_bounds)
            if seed_genomes:
                for genes in seed_genomes:
                    full = dict(genes)
                    for name in list(bounds) + [k for k in self._gene_specs if k not in bounds]:
                        if name not in full:
                            full[name] = self._sample_gene(name)
                    genome = Genome(genes=full, generation=0)
                    self._population.append(genome)

            # Fill remainder with random genomes
            all_keys = list(bounds) + [k for k in self._gene_specs if k not in bounds]
            if not all_keys and gene_bounds:
                all_keys = list(gene_bounds)
            remaining = self.population_size - len(self._population)
            for _ in range(remaining):
                genes = {name: self._sample_gene(name) for name in all_keys}
                self._population.append(Genome(genes=genes, generation=0))

            return list(self._population)

    def evaluate_population(self, fitness_fn, use_curative: bool = True,
                            workers: int = 1) -> dict[str, Any]:
        """Evaluate fitness for all genomes in the population.

        Args:
            fitness_fn: Callable(genome: Genome) -> float fitness score
            use_curative: Govern evaluation through CurativePool
            workers: Thread-pool width for fitness evals (2026-09-09).
                workers=1 preserves the exact sequential behavior.
                workers>1 requires a thread-safe fitness_fn with isolated
                RNG (no global-random traffic) — true parallelism under
                free-threading. Results are assigned back in population
                order, so stats are identical to the serial path.

        Returns:
            dict with generation stats
        """
        if use_curative:
            self._govern_evolution_gate("evaluate", {
                "generation": self._generation,
                "population_size": len(self._population),
            })

        scores = []
        eval_failures = 0
        cache_hits = 0
        todo = []
        for g in self._population:
            if g.id in self._fitness_cache:
                g.fitness = self._fitness_cache[g.id]
                continue
            # v2.0 cross-cycle hash cache: same genes -> same fitness, any UUID
            h = self.gene_hash(g.genes)
            hit = self._fitness_hash_cache.get(h)
            if hit is not None:
                g.fitness = hit
                cache_hits += 1
                with self._lock:
                    self._fitness_cache[g.id] = hit
            else:
                todo.append(g)

        def _eval_one(genome):
            try:
                return (genome.id, fitness_fn(genome), None)
            except Exception as e:  # noqa: BLE001 — surfaced, not silent
                note_failure("evolution:L375", e)
                return (genome.id, 0.0, str(e)[:160])

        if workers > 1 and todo:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="evo-eval") as pool:
                results = list(pool.map(_eval_one, todo))
        else:
            results = [_eval_one(g) for g in todo]

        by_id = {g.id: g for g in self._population}
        for genome_id, fitness, err in results:
            if err is None:
                by_id[genome_id].fitness = fitness
            else:
                by_id[genome_id].fitness = 0.0
                eval_failures += 1
                if eval_failures <= 3:
                    print('[EvolutionEngine] fitness eval failed for %s: %s' % (
                        genome_id[:12], err))
            with self._lock:
                self._fitness_cache[genome_id] = by_id[genome_id].fitness
                try:
                    self._fitness_hash_cache[self.gene_hash(by_id[genome_id].genes)] = by_id[genome_id].fitness
                except Exception as exc:
                    note_failure("evolution:L400", exc)
        for genome in self._population:
            scores.append(genome.fitness)

        # Sort population by fitness
        with self._lock:
            self._population.sort(key=lambda g: -g.fitness)

        # Track best
        best = self._population[0]
        self._best_genome = best

        stats = {
            "generation": self._generation,
            "best_fitness": best.fitness,
            "avg_fitness": sum(scores) / len(scores) if scores else 0.0,
            "min_fitness": min(scores) if scores else 0.0,
            "population_size": len(self._population),
            "eval_failures": eval_failures,
            "cache_hits": cache_hits,
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            self._generation_history.append(stats)

        return stats

    def evolve(self) -> dict[str, Any]:
        """Perform one generation of evolution: SELECT -> CROSSOVER -> MUTATE -> REPLACE.

        Returns:
            dict with evolution stats for this generation
        """
        with self._lock:
            if len(self._population) < 2:
                return {"error": "Population too small", "generation": self._generation}

            current_pop = list(self._population)
            gene_bounds = dict(self._gene_bounds)

        # Mint a fencing token BEFORE doing any work. The write below presents
        # this token; a round that lost the race cannot commit it. Two engines
        # with separate locks still cannot clobber each other, because the fence
        # is process-wide rather than per-instance.
        fence = fence_for("evolution.population")
        token = fence.claim()

        # -- Elitism: keep best N genomes unchanged --
        new_population = current_pop[:self.elitism_count]

        # -- Adapt mutation rate before selection --
        self._adapt_mutation_rate(current_pop, gene_bounds)

        # -- Adaptive Levy flight: engage heavy tails only when stuck --
        # A flat search gets jumps; an improving one keeps its operator.
        # The effective operator is reported (never the configured one when
        # they differ), and levy children are counted, so the choice is
        # auditable per generation instead of folklore.
        _prev_operator = self.mutation_operator
        _levy_engaged = bool(
            self.adaptive_levy
            and self._stagnant_gens() >= max(1, self.early_stop_patience // 2))
        _levy_children = 0
        if _levy_engaged:
            self.mutation_operator = "levy"
            self._levy_gens += 1
        # -- Selection + Crossover + Mutation --
        while len(new_population) < self.population_size:
            # Tournament selection
            parent1 = self._tournament_select(current_pop)
            parent2 = self._tournament_select(current_pop)

            # Crossover (SBX dispatch passes bounds for clamping)
            if random.random() < self.crossover_rate:
                child_genes = self._crossover(parent1.genes, parent2.genes, gene_bounds)
            else:
                child_genes = dict(parent1.genes)

            # Mutation (polynomial dispatch)
            child_genes = self._mutate(child_genes, gene_bounds)
            if _levy_engaged:
                _levy_children += 1

            child = Genome(
                genes=child_genes,
                generation=self._generation + 1,
                parent_ids=[parent1.id, parent2.id],
            )
            new_population.append(child)

        # Replace old population.
        #
        # The snapshot above is taken outside this lock, so a concurrent evolve()
        # can finish first. A hand-written re-check would fix that case but would
        # have to be remembered by every future author; the token makes the stale
        # write unrepresentable instead. See CORE.WORMS.expansion.fencing.
        def _publish() -> int:
            self._population = new_population[:self.population_size]
            self._generation += 1
            return self._generation

        with self._lock:
            committed, _gen = fence.commit(token, _publish)
        if not committed:
            return {"error": "Generation superseded by a concurrent evolve",
                    "generation": self._generation}

        # Persist generation
        self._persist_generation()

        effective_operator = self.mutation_operator
        self.mutation_operator = _prev_operator
        return {
            "generation": self._generation,
            "population_size": len(self._population),
            "elites_preserved": self.elitism_count,
            "timestamp": datetime.now().isoformat(),
            "mutation_operator": effective_operator,
            "levy_children": _levy_children,
        }

    def _tournament_select(self, population: list[Genome]) -> Genome:
        """Tournament selection: pick N random, return fittest."""
        tournament = random.sample(
            population,
            min(self.tournament_size, len(population))
        )
        return max(tournament, key=lambda g: g.fitness)

    def _crossover(self, genes1: dict, genes2: dict,
                   gene_bounds: (dict | None) = None) -> dict:
        """Dispatch: SBX (default, real-coded) or uniform (legacy)."""
        if self.crossover_operator == "sbx":
            return self._sbx_crossover(genes1, genes2, gene_bounds or self._gene_bounds)
        child = {}
        for key in genes1:
            child[key] = genes1[key] if random.random() < 0.5 else genes2.get(key, genes1[key])
        # carry keys only in parent2 (mixed-type shape drift)
        for key in genes2:
            if key not in child:
                child[key] = genes2[key]
        return child

    def _sbx_crossover(self, genes1: dict, genes2: dict, gene_bounds: dict) -> dict:
        """Simulated Binary Crossover (Deb & Agrawal). One child, per-gene blend.

        Numeric genes blend with distribution index eta_c; bool/choice genes
        fall back to uniform pick (SBX undefined for discrete).
        """
        child = {}
        eta = max(1.0, float(self.eta_c))
        for key in list(genes1) + [k for k in genes2 if k not in genes1]:
            v1: Any = genes1.get(key, genes2.get(key))
            v2: Any = genes2.get(key, v1)
            spec = self._gene_specs.get(key)
            is_discrete = (
                (spec is not None and spec.get("type") in ("bool", "choice"))
                or isinstance(v1, bool) or isinstance(v2, bool)
                or isinstance(v1, str) or isinstance(v2, str)
            )
            if is_discrete:
                child[key] = v1 if random.random() < 0.5 else v2
                continue
            try:
                x1, x2 = float(v1), float(v2)
            except (TypeError, ValueError) as exc:
                note_failure("evolution:L567", exc)
                child[key] = v1 if random.random() < 0.5 else v2
                continue
            if random.random() > 0.5 or abs(x1 - x2) < 1e-14:
                child[key] = x1 if random.random() < 0.5 else x2
                continue
            lo, hi = (gene_bounds.get(key, (min(x1, x2), max(x1, x2)))
                      if isinstance(gene_bounds.get(key), (list, tuple)) else (min(x1, x2), max(x1, x2)))
            lo, hi = float(lo), float(hi)
            if hi <= lo:
                child[key] = x1
                continue
            u = random.random()
            if u <= 0.5:
                beta = (2.0 * u) ** (1.0 / (eta + 1.0))
            else:
                beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))
            c1 = 0.5 * ((1.0 + beta) * x1 + (1.0 - beta) * x2)
            c2 = 0.5 * ((1.0 - beta) * x1 + (1.0 + beta) * x2)
            c = c1 if random.random() < 0.5 else c2
            c = max(lo, min(hi, c))
            if spec is not None and spec.get("type") == "int":
                c = int(round(c))
            child[key] = c
        return child

    def _mutate(self, genes: dict,
                gene_bounds: dict[str, tuple[float, float]]) -> dict:
        """Dispatch: polynomial (default) / gaussian / levy / uniform (legacy)."""
        if self.mutation_operator == "polynomial":
            return self._polynomial_mutate(genes, gene_bounds)
        if self.mutation_operator == "gaussian":
            return self._gaussian_mutate(genes, gene_bounds)
        if self.mutation_operator == "levy":
            return self._levy_mutate(genes, gene_bounds)
        for key, (min_val, max_val) in gene_bounds.items():
            if random.random() < self.mutation_rate:
                perturbation = random.uniform(-0.1, 0.1) * (max_val - min_val)
                genes[key] = max(min_val, min(max_val, genes.get(key, (min_val + max_val) / 2) + perturbation))
        # discrete genes under legacy uniform: resample
        for key, spec in self._gene_specs.items():
            if spec.get("type") in ("bool", "choice") and random.random() < self.mutation_rate:
                genes[key] = self._sample_gene(key)
        return genes

    def _polynomial_mutate(self, genes: dict, gene_bounds: dict) -> dict:
        """Polynomial mutation (Deb). Bounded, distribution index eta_m."""
        eta = max(1.0, float(self.eta_m))
        for key, bounds in gene_bounds.items():
            if random.random() >= self.mutation_rate:
                continue
            if key in self._gene_specs and self._gene_specs[key].get("type") in ("bool", "choice"):
                genes[key] = self._sample_gene(key)
                continue
            lo, hi = float(bounds[0]), float(bounds[1])
            if hi <= lo:
                continue
            try:
                x = float(genes.get(key, (lo + hi) / 2.0))
            except (TypeError, ValueError) as exc:
                note_failure("evolution:L626", exc)
                continue
            x = max(lo, min(hi, x))
            d1 = (x - lo) / (hi - lo)
            d2 = (hi - x) / (hi - lo)
            mut_pow = 1.0 / (eta + 1.0)
            u = random.random()
            if u < 0.5:
                delta = (2.0 * u + (1.0 - 2.0 * u) * (1.0 - d1) ** (eta + 1.0)) ** mut_pow - 1.0
            else:
                delta = 1.0 - (2.0 * (1.0 - u) + 2.0 * (u - 0.5) * (1.0 - d2) ** (eta + 1.0)) ** mut_pow
            x_new = max(lo, min(hi, x + delta * (hi - lo)))
            if key in self._gene_specs and self._gene_specs[key].get("type") == "int":
                genes[key] = int(round(x_new))
            else:
                genes[key] = x_new
        for key, spec in self._gene_specs.items():
            if key not in gene_bounds and spec.get("type") in ("bool", "choice"):
                if random.random() < self.mutation_rate:
                    genes[key] = self._sample_gene(key)
        return genes

    def _gaussian_mutate(self, genes: dict, gene_bounds: dict, sigma_frac: float = 0.1) -> dict:
        for key, bounds in gene_bounds.items():
            if random.random() >= self.mutation_rate:
                continue
            lo, hi = float(bounds[0]), float(bounds[1])
            try:
                x = float(genes.get(key, (lo + hi) / 2.0))
            except (TypeError, ValueError) as exc:
                note_failure("evolution:L655", exc)
                continue
            genes[key] = max(lo, min(hi, random.gauss(x, sigma_frac * (hi - lo))))
        return genes

    def _stagnant_gens(self) -> int:
        """Trailing generations without improvement (2026-09-28).

        Counts gens after the last strict improvement of best_fitness, so
        [1.0, 1.0, 1.2, 1.2, 1.2] reports 2 and a fresh improvement
        reports 0. Empty history reports 0 (nothing to be stagnant about).
        """
        hist = self._generation_history or []
        best = None
        last_improved = -1
        for i, row in enumerate(hist):
            try:
                cell: Any = (row or {}).get("best_fitness")
                v = float(cell)
            except (TypeError, ValueError) as exc:
                note_failure("evolution:L673", exc)
                continue
            if best is None or v > best:
                best = v
                last_improved = i
        if last_improved < 0:
            return 0
        return max(0, len(hist) - 1 - last_improved)

    def _levy_mutate(self, genes: dict, gene_bounds: dict) -> dict:
        """Heavy-tailed Levy-flight mutation (Mantegna's algorithm).

        Occasional long jumps escape local optima that Gaussian steps orbit;
        clamping keeps every step in bounds and discrete genes resample like
        the sibling operators. alpha in (0, 2]; smaller alpha, heavier tail.
        """
        beta = min(1.99, max(0.3, float(self.levy_alpha)))
        try:
            sigma = (_math.gamma(1.0 + beta)
                     * _math.sin(_math.pi * beta / 2.0)
                     / (_math.gamma((1.0 + beta) / 2.0) * beta
                        * (2.0 ** ((beta - 1.0) / 2.0)))) ** (1.0 / beta)
        except (ValueError, ZeroDivisionError) as exc:
            note_failure("evolution:L696", exc)
            sigma = 1.0
        for key, bounds in gene_bounds.items():
            if random.random() >= self.mutation_rate:
                continue
            if key in self._gene_specs and self._gene_specs[key].get("type") in ("bool", "choice"):
                genes[key] = self._sample_gene(key)
                continue
            lo, hi = float(bounds[0]), float(bounds[1])
            if hi <= lo:
                continue
            try:
                x = float(genes.get(key, (lo + hi) / 2.0))
            except (TypeError, ValueError) as exc:
                note_failure("evolution:L709", exc)
                continue
            x = max(lo, min(hi, x))
            try:
                u = random.gauss(0.0, sigma)
                v = random.gauss(0.0, 1.0)
                step = u / (abs(v) ** (1.0 / beta)) if v != 0 else 0.0
            except (ValueError, ZeroDivisionError) as exc:
                note_failure("evolution:L716", exc)
                step = 0.0
            span = hi - lo
            x_new = max(lo, min(hi, x + step * 0.1 * span))
            if key in self._gene_specs and self._gene_specs[key].get("type") == "int":
                genes[key] = int(round(x_new))
            else:
                genes[key] = x_new
        for key, spec in self._gene_specs.items():
            if key not in gene_bounds and spec.get("type") in ("bool", "choice"):
                if random.random() < self.mutation_rate:
                    genes[key] = self._sample_gene(key)
        return genes

    def _govern_evolution_gate(self, phase: str, context: dict) -> None:
        """Govern evolution operations through CurativePool — double-checked locking for 3.14t."""
        try:
            if self._curative_pool is None:
                with self._lock:
                    if self._curative_pool is None:
                        from META.MetaWormSpawner import CurativePool
                        self._curative_pool = CurativePool()
            result = self._curative_pool.evaluate_action(
                {"context": f"evolution_{phase}", "params": context},
                {"action_type": "execute", "output_size": 100, "feature": f"evolution_{phase}"},
                [{"role": "user", "content": f"run evolution phase: {phase}"}],
                [{"id": "evo_analyst", "archetype": "The Analyst"}],
            )
            if result["decision"] == "blocked":
                print(f"[EvolutionEngine] CurativePool blocked {phase}: {result['block_reasons']}")
        except ImportError as exc:
            note_failure("evolution:L746", exc)

    def _get_store(self):
        """Resolve memory store: injected -> global -> None.

        Thread-safe under 3.14t free-threading — all imports at module level.
        Uses sys.modules lookup pattern from Lesson #29 to avoid import deadlocks.

        RETRIES a transient failure. A single swallowed exception used to be
        indistinguishable from "there is no store": `save_checkpoint` returned a
        success-shaped dict while persisting nothing, and the only symptom was a
        restore drill that silently kept its OLD state. One transient SQLite
        lock meant a permanently missing checkpoint. On success `_store_error`
        is cleared; on final failure it records WHY, so the caller can report it.
        """
        self._store_error = ""
        if self._memory_store == "none":
            return None
        if self._memory_store is not None:
            return self._memory_store
        if _LOCAL_MEMORY_OK and _get_memory_store_global is not None:
            last_exc = None
            for attempt in range(3):
                try:
                    return _get_memory_store_global()
                except Exception as exc:      # transient lock / busy timeout
                    note_failure("evolution:L772", exc)
                    last_exc = exc
                    if attempt < 2:
                        time.sleep(0.05 * (attempt + 1))
            self._store_error = ("memory store unavailable after 3 attempts: "
                                 "%s" % last_exc)
            return None
        return None

    def _persist_generation(self) -> None:
        """Persist generation stats to LocalMemoryStore."""
        store = self._get_store()
        if store is None:
            return
        try:
            store.record_event(
                source="EvolutionEngine",
                event_type="generation_complete",
                data={
                    "generation": self._generation,
                    "best_fitness": self._best_genome.fitness if self._best_genome else 0.0,
                    "population_size": len(self._population),
                    "timestamp": datetime.now().isoformat(),
                },
                severity="info",
                tags=["evolution", f"gen_{self._generation}"],
            )
        except Exception as exc:
            note_failure("evolution:L799", exc)

    # -- Adaptive Mutation Rate --------------------------------------

    def _compute_population_diversity(self, population: (list[Genome] | None) = None,
                                      gene_bounds: (dict[str, tuple[float, float]] | None) = None) -> float:
        """Compute normalized gene diversity across the population.

        Returns a float 0.0–1.0 where higher = more diverse.
        Uses coefficient of variation (stddev / mean) averaged across genes.

        Args:
            population: Population to analyze (defaults to self._population)
            gene_bounds: Gene bounds to normalize by (defaults to self._gene_bounds)
        """
        pop = population if population is not None else self._population
        bounds = gene_bounds if gene_bounds is not None else self._gene_bounds

        if len(pop) < 2 or not bounds:
            return 0.0

        diversity_scores = []
        for gene_name, (min_val, max_val) in bounds.items():
            # v2.0: skip discrete genes (bool/choice/str) — stddev undefined.
            # Categorical diversity is tracked by the MAP-Elites archive instead.
            try:
                values = [float(g.genes.get(gene_name, 0.0)) for g in pop]
            except (TypeError, ValueError) as exc:
                note_failure("evolution:L827", exc)
                continue
            mean_val = sum(values) / len(values)
            if mean_val == 0:
                diversity_scores.append(0.0)
                continue
            variance = sum((v - mean_val) ** 2 for v in values) / len(values)
            stddev = variance ** 0.5
            # Normalize by gene range so different-scale genes are comparable
            try:
                gene_range = float(max_val) - float(min_val)
            except (TypeError, ValueError) as exc:
                note_failure("evolution:L838", exc)
                continue
            if gene_range > 0:
                diversity_scores.append(min(stddev / gene_range, 1.0))
            else:
                diversity_scores.append(0.0)

        return sum(diversity_scores) / len(diversity_scores) if diversity_scores else 0.0

    def _adapt_mutation_rate(self, population: (list[Genome] | None) = None,
                             gene_bounds: (dict[str, tuple[float, float]] | None) = None) -> float:
        """Dynamically adjust mutation rate based on population diversity.

        - Low diversity -> increase mutation (prevent premature convergence)
        - High diversity -> decrease mutation (exploit good solutions)

        Args:
            population: Population snapshot (from evolve()'s copy)
            gene_bounds: Gene bounds snapshot (from evolve()'s copy)

        Returns the new mutation rate.
        """
        diversity = self._compute_population_diversity(population, gene_bounds)

        if diversity < self._diversity_threshold_low:
            # Premature convergence risk — boost mutation
            target = self.mutation_rate * (1.0 + self._mutation_adaptation_rate * 3.0)
            new_rate = min(target, self.max_mutation_rate)
        elif diversity > self._diversity_threshold_high:
            # Healthy diversity — decay toward base rate
            target = self.mutation_rate * (1.0 - self._mutation_adaptation_rate)
            new_rate = max(target, self.base_mutation_rate)
        else:
            # Moderate diversity — slight decay toward base
            target = self.mutation_rate + (self.base_mutation_rate - self.mutation_rate) * 0.2
            new_rate = max(target, self.min_mutation_rate)

        self.mutation_rate = round(new_rate, 5)
        return self.mutation_rate

    # -- Checkpoint / Restore ----------------------------------------

    def save_checkpoint(self, label: str = "") -> dict[str, Any]:
        """Serialize the full evolutionary state and persist to LocalMemoryStore.

        Saves: population, generation, gene bounds, history, best genome, config.

        Args:
            label: Optional human-readable label for the checkpoint

        Returns:
            dict with checkpoint metadata
        """
        with self._lock:
            checkpoint: dict[str, Any] = {
                "version": self.VERSION,
                "label": label or f"gen_{self._generation}",
                "generation": self._generation,
                "population_size": self.population_size,
                "gene_bounds": {k: list(v) for k, v in self._gene_bounds.items()},
                "crossover_rate": self.crossover_rate,
                "mutation_rate": self.mutation_rate,
                "tournament_size": self.tournament_size,
                "population": [
                    {
                        "id": g.id,
                        "genes": dict(g.genes),
                        "fitness": g.fitness,
                        "generation": g.generation,
                        "parent_ids": list(g.parent_ids),
                    }
                    for g in self._population
                ],
                "generation_history": list(self._generation_history[-50:]),
                "best_genome": (
                    {
                        "id": self._best_genome.id,
                        "genes": dict(self._best_genome.genes),
                        "fitness": self._best_genome.fitness,
                        "generation": self._best_genome.generation,
                    }
                    if self._best_genome else None
                ),
                "saved_at": datetime.now().isoformat(),
                "free_threaded": True,
            }

        # Persist to LocalMemoryStore. `persisted` is reported honestly: a
        # checkpoint that lives only in this return value is not a checkpoint,
        # and a caller running a restore drill needs to be told so.
        store = self._get_store()
        persisted = False
        persist_error = ""
        if store is None:
            persist_error = (self._store_error
                             or "no memory store available (state not persisted)")
        else:
            try:
                store.record_event(
                    source="EvolutionEngine",
                    event_type="checkpoint_saved",
                    data=checkpoint,
                    severity="info",
                    tags=["evolution", "checkpoint", f"gen_{self._generation}"],
                )
                persisted = True
            except Exception as exc:
                note_failure("evolution:L944", exc)
                persist_error = "record_event failed: %s" % exc

        return {
            "checkpoint_label": checkpoint["label"],
            "generation": checkpoint["generation"],
            "population_count": len(checkpoint["population"]),
            "best_fitness": checkpoint["best_genome"]["fitness"] if checkpoint["best_genome"] else 0.0,
            "saved_at": checkpoint["saved_at"],
            "persisted": persisted,
            "persist_error": persist_error,
        }

    def load_checkpoint(self, label: (str | None) = None) -> bool:
        """Restore evolutionary state from the most recent checkpoint in LocalMemoryStore.

        Args:
            label: Optional label filter to load a specific checkpoint

        Returns:
            True if checkpoint was loaded, False otherwise
        """
        store = self._get_store()
        if store is None:
            return False
        try:
            # `query_memories` filters by source+event_type and returns NEWEST
            # FIRST. `search_memories` ranks by full-text relevance, so on a
            # store holding 59 stale `test_checkpoint` rows the checkpoint that
            # was just written could fall outside the window and a STALE one
            # would win — a restore drill that silently kept the old state.
            if hasattr(store, "query_memories"):
                events = store.query_memories(
                    source="EvolutionEngine", event_type="checkpoint_saved",
                    limit=200,
                )
            else:
                events = store.search_memories(
                    "checkpoint_saved", source="EvolutionEngine", limit=200
                )
            if not events:
                return False

            # Find matching checkpoint — newest by saved_at wins, so reused
            # labels across test runs can't resurrect a stale checkpoint.
            best = None
            best_ts = ""
            for ev in events:
                data = ev.get("data", {})
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except (json.JSONDecodeError, TypeError) as exc:
                        note_failure("evolution:L996", exc)
                        continue
                if not isinstance(data, dict) or "population" not in data:
                    continue
                if label and data.get("label") != label:
                    continue
                ts = str(data.get("saved_at", "")) or str(ev.get("timestamp", ""))
                if best is None or ts >= best_ts:
                    best = data
                    best_ts = ts
            checkpoint = best

            if checkpoint is None:
                return False

            # Restore state
            with self._lock:
                self._generation = checkpoint.get("generation", 0)
                self.population_size = checkpoint.get("population_size", self.population_size)
                self._gene_bounds = {
                    k: tuple(v) for k, v in checkpoint.get("gene_bounds", {}).items()
                }
                self.crossover_rate = checkpoint.get("crossover_rate", self.crossover_rate)
                self.mutation_rate = checkpoint.get("mutation_rate", self.mutation_rate)
                self.tournament_size = checkpoint.get("tournament_size", self.tournament_size)
                self._generation_history = checkpoint.get("generation_history", [])
                self._fitness_cache.clear()

                # Rebuild population
                self._population = []
                for gdata in checkpoint.get("population", []):
                    genome = Genome(
                        genes=gdata.get("genes", {}),
                        fitness=gdata.get("fitness", 0.0),
                        generation=gdata.get("generation", 0),
                        parent_ids=gdata.get("parent_ids", []),
                        id=gdata.get("id") or f"g_{_uuid.uuid4().hex}",
                    )
                    self._population.append(genome)
                    self._fitness_cache[genome.id] = genome.fitness

                # Restore best genome
                best_data = checkpoint.get("best_genome")
                if best_data:
                    self._best_genome = Genome(
                        genes=best_data.get("genes", {}),
                        fitness=best_data.get("fitness", 0.0),
                        generation=best_data.get("generation", 0),
                        id=best_data.get("id", ""),
                    )
                else:
                    self._best_genome = None

            return True
        except Exception as exc:
            note_failure("evolution:L1050", exc)
            return False

    def has_converged(self) -> bool:
        """Check if the population has converged (no meaningful improvement).

        Returns True if best_fitness hasn't improved by more than
        early_stop_tolerance over the last early_stop_patience generations.
        """
        if self._generation < self.early_stop_patience:
            return False

        recent_bests = [
            h.get("best_fitness", 0.0)
            for h in self._generation_history[-self.early_stop_patience:]
        ]
        if not recent_bests:
            return False

        first_best = recent_bests[0]
        last_best = recent_bests[-1]

        if first_best <= 0:
            return False

        improvement = (last_best - first_best) / abs(first_best)
        return improvement < self.early_stop_tolerance

    # -- Multi-Objective Optimization (Pareto-front) -----------------

    def _non_dominated_sort(self, population: list[Genome],
                            objective_fns: list[Callable]) -> list[list[Genome]]:
        """NSGA-II non-dominated sorting of population into Pareto fronts.

        Each objective_fn should return a float where LOWER is better (minimization).
        Fronts[0] = Pareto-optimal individuals (not dominated by any other).

        Args:
            population: Genomes to sort
            objective_fns: list of fitness functions, each returning float

        Returns:
            list of fronts (each front is a list of genomes)
        """
        n = len(population)
        domination_counts = [0] * n
        dominated_by = [[] for _ in range(n)]
        objectives = []

        for genome in population:
            obj_values = [fn(genome) for fn in objective_fns]
            objectives.append(obj_values)

        # Build domination graph
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                # i dominates j if all objectives of i are <= j and at least one is <
                i_dominates_j = all(
                    objectives[i][k] <= objectives[j][k] for k in range(len(objective_fns))
                ) and any(
                    objectives[i][k] < objectives[j][k] for k in range(len(objective_fns))
                )
                if i_dominates_j:
                    dominated_by[i].append(j)
                    domination_counts[j] += 1

        # Build fronts
        fronts = []
        current_front = [i for i, count in enumerate(domination_counts) if count == 0]

        while current_front:
            fronts.append([population[i] for i in current_front])
            next_front = []
            for i in current_front:
                for j in dominated_by[i]:
                    domination_counts[j] -= 1
                    if domination_counts[j] == 0:
                        next_front.append(j)
            current_front = next_front

        return fronts

    def _compute_crowding_distance(self, front: list[Genome],
                                   objective_fns: list[Callable]) -> list[float]:
        """Compute crowding distance for diversity preservation.

        Higher crowding distance = more isolated = higher diversity value.
        Used to select among individuals in the same Pareto front.

        Args:
            front: Non-dominated individuals in a single front
            objective_fns: Objective functions (lower is better)

        Returns:
            list of crowding distances aligned with front
        """
        m = len(front)
        if m <= 2:
            return [float("inf")] * m

        distances = [0.0] * m
        n_objectives = len(objective_fns)

        for obj_idx in range(n_objectives):
            # Sort by this objective
            obj_values = [(i, objective_fns[obj_idx](front[i])) for i in range(m)]
            obj_values.sort(key=lambda x: x[1])

            f_min = obj_values[0][1]
            f_max = obj_values[-1][1]
            f_range = f_max - f_min if f_max != f_min else 1.0

            # Boundary points get infinite distance
            distances[obj_values[0][0]] = float("inf")
            distances[obj_values[-1][0]] = float("inf")

            # Interior points
            for k in range(1, m - 1):
                idx = obj_values[k][0]
                prev_val = obj_values[k - 1][1]
                next_val = obj_values[k + 1][1]
                distances[idx] += (next_val - prev_val) / f_range

        return distances

    def optimize_multi_objective(self, objective_fns: list[Callable],
                                 generations: int = 20,
                                 use_curative: bool = True) -> dict[str, Any]:
        """Run multi-objective evolutionary optimization (NSGA-II style).

        Optimizes for multiple competing objectives simultaneously.
        Returns the Pareto front of non-dominated solutions.

        Each objective_fn should map Genome -> float where LOWER is better.

        Args:
            objective_fns: list of objective functions to minimize
            generations: Number of generations to evolve
            use_curative: Govern through CurativePool

        Returns:
            dict with pareto_front, generation history, and convergence info
        """
        if len(objective_fns) < 2:
            return {"error": "Need at least 2 objective functions for multi-objective optimization"}

        if use_curative:
            self._govern_evolution_gate("multi_objective", {
                "generations": generations,
                "objectives": len(objective_fns),
            })

        history = []
        for gen in range(generations):
            # Non-dominated sort
            fronts = self._non_dominated_sort(self._population, objective_fns)

            # Compute crowding distance for each front
            for front in fronts:
                distances = self._compute_crowding_distance(front, objective_fns)
                for genome, dist in zip(front, distances):
                    genome._crowding_distance = dist

            # Build new population using NSGA-II selection
            with self._lock:
                new_population = []
                for front in fronts:
                    if len(new_population) + len(front) <= self.population_size:
                        new_population.extend(front)
                    else:
                        # Fill remaining slots from best of this front
                        remaining = self.population_size - len(new_population)
                        sorted_front = sorted(
                            front,
                            key=lambda g: getattr(g, "_crowding_distance", 0.0),
                            reverse=True,
                        )
                        new_population.extend(sorted_front[:remaining])
                        break

                # Elitism: ensure best from each objective survives
                elite_slots = set()
                for obj_idx, obj_fn in enumerate(objective_fns):
                    if self._population:
                        best_in_obj = min(self._population, key=obj_fn)
                        if best_in_obj not in new_population:
                            # Place in a unique slot, working backwards from end
                            slot = -(obj_idx + 1)
                            # A negative slot needs at least obj_idx + 1 members.
                            # With more objectives than survivors this used to
                            # raise IndexError out of the whole optimization.
                            if slot not in elite_slots and len(new_population) > obj_idx:
                                new_population[slot] = best_in_obj
                                elite_slots.add(slot)

                # Crossover and mutation on survivors (v2.0 SBX/poly dispatch)
                offspring = []
                while len(new_population) + len(offspring) < self.population_size * 2:
                    p1 = self._tournament_select(new_population)
                    p2 = self._tournament_select(new_population)
                    if random.random() < self.crossover_rate:
                        child_genes = self._crossover(p1.genes, p2.genes, self._gene_bounds)
                    else:
                        child_genes = dict(p1.genes)
                    child_genes = self._mutate(child_genes, self._gene_bounds)
                    offspring.append(Genome(
                        genes=child_genes,
                        generation=self._generation + 1,
                        parent_ids=[p1.id, p2.id],
                    ))

                # Combine and select next generation via non-dominated sort
                combined = new_population + offspring
                fronts_combined = self._non_dominated_sort(combined, objective_fns)
                next_pop = []
                for front in fronts_combined:
                    if len(next_pop) + len(front) <= self.population_size:
                        next_pop.extend(front)
                    else:
                        remaining = self.population_size - len(next_pop)
                        distances = self._compute_crowding_distance(front, objective_fns)
                        for g, d in zip(front, distances):
                            g._crowding_distance = d
                        sorted_front = sorted(
                            front,
                            key=lambda g: getattr(g, "_crowding_distance", 0.0),
                            reverse=True,
                        )
                        next_pop.extend(sorted_front[:remaining])
                        break

                self._population = next_pop[:self.population_size]
                self._generation += 1

            # Track generation stats
            gen_stats = {
                "generation": self._generation,
                "pareto_front_size": len(fronts[0]) if fronts else 0,
                "total_fronts": len(fronts),
                "population_size": len(self._population),
                "timestamp": datetime.now().isoformat(),
            }
            with self._lock:
                self._generation_history.append(gen_stats)

            # Early stopping: if Pareto front size hasn't changed in patience gens
            if gen >= self.early_stop_patience:
                recent_sizes = [
                    h.get("pareto_front_size", 0)
                    for h in history[-self.early_stop_patience:]
                ]
                if len(set(recent_sizes)) == 1:
                    break

            # Adapt mutation rate using current population snapshot
            with self._lock:
                pop_snapshot = list(self._population)
                bounds_snapshot = dict(self._gene_bounds)
            self._adapt_mutation_rate(pop_snapshot, bounds_snapshot)

        # Extract final Pareto front
        final_fronts = self._non_dominated_sort(self._population, objective_fns)
        pareto_front = final_fronts[0] if final_fronts else []

        return {
            "pareto_front": [
                {
                    "id": g.id,
                    "genes": g.genes,
                    "objectives": [round(fn(g), 6) for fn in objective_fns],
                    "generation": g.generation,
                }
                for g in pareto_front
            ],
            "pareto_front_size": len(pareto_front),
            "generations": self._generation,
            "history": history,
            "timestamp": datetime.now().isoformat(),
        }

    def get_best_genome(self) -> (Genome | None):
        """Get the fittest genome across all generations."""
        return self._best_genome

    def get_generation_history(self, limit: int = 20) -> list[dict]:
        """Get recent generation fitness history."""
        with self._lock:
            return self._generation_history[-limit:]

    def execute(self) -> dict[str, Any]:
        """Standard worm execute — returns current evolution status and auto-saves checkpoint."""
        with self._lock:
            status = {
                "version": self.VERSION,
                "generation": self._generation,
                "population_size": len(self._population),
                "best_fitness": self._best_genome.fitness if self._best_genome else 0.0,
                "best_genes": self._best_genome.genes if self._best_genome else {},
                "gene_bounds": self._gene_bounds,
                "generations_history": len(self._generation_history),
            }
        # Auto-save checkpoint for session-to-session persistence
        if self._population:
            self.save_checkpoint()
        return status


@register_worm(
    category="learning", priority=5, cooldown_seconds=180.0,
    description="Genetic optimizer — per-worm parameter optimization using genetic algorithms with fitness tracking"
)
class GeneticOptimizer:
    """Per-worm genetic parameter optimizer.

    Optimizes execution parameters for individual worm types using
    genetic algorithms. Tracks fitness across generations and
    automatically selects optimal parameter configurations.

    Usage:
        opt = GeneticOptimizer()
        opt.initialize("SystemZ", {"scan_depth": (1, 10), "timeout_ms": (100, 5000)})
        best_params = opt.optimize(generations=10)
    """

    VERSION = "2.0.0"

    def __init__(self):
        self._lock = threading.Lock()
        self._engines: dict[str, EvolutionEngine] = {}
        self._optimization_history: dict[str, list[dict]] = {}

    def initialize(self, worm_type: str, param_bounds: dict,
                   population_size: int = 30, **engine_kwargs) -> EvolutionEngine:
        """Initialize an evolution engine for a specific worm type.

        param_bounds accepts legacy {(lo,hi)} tuples OR mixed-type specs
        ({name: {"type": ...}}), so bool/categorical worm params no longer
        need the HYDRAMETA fork.
        """
        engine = EvolutionEngine(population_size=population_size, **engine_kwargs)
        engine.initialize_population(param_bounds)
        with self._lock:
            self._engines[worm_type] = engine
            self._optimization_history[worm_type] = []
        return engine

    def optimize_cma(self, worm_type: str, gene_bounds: dict[str, tuple[float, float]],
                     fitness_fn, generations: int = 20,
                     workers: int = 1) -> dict[str, Any]:
        """Continuous-param optimization via sep-CMA-ES (preferred over GA
        for float-only worm params). fitness_fn(genes_dict) -> float."""
        cma = SepCMAES(dict(gene_bounds))
        result = cma.optimize(fitness_fn, generations=generations, workers=workers)
        with self._lock:
            self._optimization_history.setdefault(worm_type, []).append({
                "type": "cma-es", "best_fitness": result["best_fitness"],
                "timestamp": datetime.now().isoformat()})
        return {"worm_type": worm_type, "method": "sep-cma-es", **result}

    def optimize_de(self, worm_type: str, gene_bounds: dict[str, tuple[float, float]],
                    fitness_fn, generations: int = 30) -> dict[str, Any]:
        """Continuous-param optimization via Differential Evolution."""
        de = DifferentialEvolution(dict(gene_bounds))
        result = de.optimize(fitness_fn, generations=generations)
        with self._lock:
            self._optimization_history.setdefault(worm_type, []).append({
                "type": "de", "best_fitness": result["best_fitness"],
                "timestamp": datetime.now().isoformat()})
        return {"worm_type": worm_type, "method": "de", **result}

    def optimize(self, worm_type: str, generations: int = 10,
                 fitness_fn=None, backend: str = "ga") -> dict[str, Any]:
        """Run evolutionary optimization for a worm type.

        Args:
            worm_type: Worm type to optimize
            generations: Number of generations to evolve
            fitness_fn: Optional custom fitness function
            backend: "ga" (default, in-place loop below), "cma" (sep-CMA-ES,
                preferred for float-only params), or "de" (differential
                evolution). The cma/de backends (optimize_cma/optimize_de)
                previously had no caller — this dispatch is their production
                entry point; existing callers pass nothing and keep GA.

        Returns:
            dict with best parameters and optimization stats
        """
        if backend in ("cma", "de"):
            # CMA-ES/DE need float (lo, hi) bounds; mixed-type specs stay on GA.
            try:
                bounds = dict(self._engines[worm_type]._gene_bounds)
            except (KeyError, AttributeError) as exc:
                note_failure("evolution:L1443", exc)
                bounds = {}
            if (bounds and all(isinstance(v, tuple) and len(v) == 2
                               and all(isinstance(x, (int, float)) for x in v)
                               for v in bounds.values())):
                if backend == "cma":
                    return self.optimize_cma(worm_type, bounds, fitness_fn,
                                             generations=generations)
                return self.optimize_de(worm_type, bounds, fitness_fn,
                                        generations=generations)
            note_failure("evolution:backend-bounds", None)
        engine = self._engines.get(worm_type)
        if not engine:
            return {"error": f"No engine initialized for {worm_type}"}

        # Fail loud without an explicit fitness (2026-09-09 fix): the old
        # placeholder (mean of gene values) silently "optimized" worms
        # toward MAXIMUM parameter values — the opposite of tuning.
        if fitness_fn is None:
            return {"error": "fitness_fn is required (no safe default: higher "
                             "is not better for timeouts/scales/depths)"}

        history = []
        for gen in range(generations):
            stats = engine.evaluate_population(fitness_fn)
            engine.evolve()
            history.append(stats)

            # Early stopping check
            if engine.has_converged():
                break

        best = engine.get_best_genome()

        result = {
            "worm_type": worm_type,
            "generations": generations,
            "best_parameters": best.genes if best else {},
            "best_fitness": best.fitness if best else 0.0,
            "history": history,
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            self._optimization_history[worm_type].append(result)

        # Persist using module-level import (3.14t safe per Lesson #29)
        store = None
        if _LOCAL_MEMORY_OK and _get_memory_store_global:
            try:
                store = _get_memory_store_global()
            except Exception as exc:
                note_failure("evolution:L1474", exc)
        if store is not None:
            store.record_event(
                source="GeneticOptimizer",
                event_type="optimization_complete",
                data=result,
                severity="info",
                tags=["genetic", "optimization", worm_type],
            )

        return result

    def optimize_multi(self, worm_type: str, objective_fns: list[Callable],
                       generations: int = 20) -> dict[str, Any]:
        """Run multi-objective evolutionary optimization for a worm type.

        Args:
            worm_type: Worm type to optimize
            objective_fns: list of objective functions (each Genome -> float, lower=better)
            generations: Number of generations

        Returns:
            dict with Pareto front and optimization stats
        """
        engine = self._engines.get(worm_type)
        if not engine:
            return {"error": f"No engine initialized for {worm_type}"}

        if len(objective_fns) < 2:
            return {"error": "Need at least 2 objectives for multi-objective optimization"}

        result = engine.optimize_multi_objective(
            objective_fns, generations=generations
        )

        with self._lock:
            self._optimization_history[worm_type].append({
                "type": "multi_objective",
                "pareto_front_size": result.get("pareto_front_size", 0),
                "generations": result.get("generations", 0),
                "timestamp": datetime.now().isoformat(),
            })

        return result

    def execute(self) -> dict[str, Any]:
        """Standard worm execute — returns optimization status."""
        with self._lock:
            return {
                "version": self.VERSION,
                "optimized_worms": list(self._engines.keys()),
                "total_optimizations": sum(len(h) for h in self._optimization_history.values()),
            }


# ═══════════════════════════════════════════════════════════════════════
#  v2.0 new engines (audit 2026-09-11: CMA-ES, DE, MAP-Elites, TPE,
#  NSGA-III/MOEA-D, Island model). All stdlib-only, thread-safe.
# ═══════════════════════════════════════════════════════════════════════

@register_worm(
    category="learning", priority=6, cooldown_seconds=120.0,
    description="sep-CMA-ES — diagonal-covariance evolution strategy for continuous hyperparameters"
)
class SepCMAES:
    """Separable CMA-ES (diagonal covariance) — the right tool for H-1's
    continuous 4-D RL genes where isotropic ±10% mutation wastes evals.

    Maximizes fitness_fn(genome_dict) -> float. Box-bounded by clamping.
    Ask/tell interface plus one-shot optimize().
    """

    VERSION = "2.0.0"

    def __init__(self, gene_bounds: (dict | None) = None,
                 sigma0_frac: float = 0.3, popsize: int = 0,
                 seed_genes: (dict | None) = None,
                 rng: (Any | None) = None):
        # Standby default (bare instantiation by the cluster runner): the
        # canonical 4-D RL genes. Real optimizations pass explicit bounds
        # via optimize_cma(); optimize() still requires a fitness_fn.
        gene_bounds = gene_bounds or {
            "learning_rate": (1e-5, 1e-1),
            "epsilon": (0.0, 0.5),
            "discount": (0.9, 0.999),
            "clip_range": (0.05, 0.4),
        }
        self._lock = threading.Lock()
        self.names = list(gene_bounds)
        self.bounds = {k: (float(v[0]), float(v[1])) for k, v in gene_bounds.items()}
        self.n = len(self.names)
        if self.n == 0:
            raise ValueError("SepCMAES needs at least 1 gene")
        self.rng = rng or random
        lam = 4 + int(3 * _m.log(self.n + 1)) if not popsize else popsize
        self.lam = max(4, lam)
        self.mu = max(1, self.lam // 2)
        # log-rank weights (higher weight = better rank)
        w = [_m.log(self.mu + 0.5) - _m.log(i + 1) for i in range(self.mu)]
        s = sum(w)
        self.weights = [x / s for x in w]
        self.mueff = sum(w) ** 2 / sum(x * x for x in w)
        # strategy params (Hansen 2016 defaults, diagonal variant)
        self.cc = (4.0 + self.mueff / self.n) / (self.n + 4.0 + 2.0 * self.mueff / self.n)
        self.cs = (self.mueff + 2.0) / (self.n + self.mueff + 5.0)
        self.c1 = 2.0 / ((self.n + 1.3) ** 2 + self.mueff)
        self.cmu = min(1.0 - self.c1, 2.0 * (self.mueff - 2.0 + 1.0 / self.mueff)
                       / ((self.n + 2.0) ** 2 + self.mueff))
        self.damps = 1.0 + 2.0 * max(0.0, ((self.mueff - 1.0) / (self.n + 1.0)) ** 0.5 - 1.0) + self.cs
        # state
        self.mean = {}
        for k in self.names:
            lo, hi = self.bounds[k]
            self.mean[k] = float(seed_genes[k]) if seed_genes and k in seed_genes else (lo + hi) / 2.0
        self.sigma = sum((hi - lo) for lo, hi in self.bounds.values()) / self.n * sigma0_frac
        self.sigma = max(self.sigma, 1e-12)
        self.pc = {k: 0.0 for k in self.names}
        self.ps = {k: 0.0 for k in self.names}
        self.diagC = {k: 1.0 for k in self.names}  # normalized diagonal
        self.counteval = 0
        self.best_genes: (dict | None) = None
        self.best_fitness = float("-inf")
        self._pop: list[dict] = []

    def ask(self) -> list[dict]:
        """Sample one generation of candidate gene dicts."""
        pop = []
        for _ in range(self.lam):
            g = {}
            for k in self.names:
                lo, hi = self.bounds[k]
                step = self.sigma * (self.diagC[k] ** 0.5) * self.rng.gauss(0.0, 1.0)
                # scale step by range so sigma is range-relative
                step *= (hi - lo)
                g[k] = max(lo, min(hi, self.mean[k] + step))
            pop.append(g)
        with self._lock:
            self._pop = pop
        return list(pop)

    def tell(self, fitnesses: list[float]) -> dict[str, Any]:
        """Update distribution from fitnesses aligned with last ask() (higher = better)."""
        with self._lock:
            pop = list(self._pop)
        if len(fitnesses) != len(pop):
            return {"error": "fitnesses length must match ask() population"}
        order = sorted(range(len(pop)), key=lambda i: fitnesses[i], reverse=True)
        # track best
        if fitnesses[order[0]] > self.best_fitness:
            self.best_fitness = fitnesses[order[0]]
            self.best_genes = dict(pop[order[0]])
        # weighted mean shift (normalized coords)
        old_mean = dict(self.mean)
        for k in self.names:
            lo, hi = self.bounds[k]
            rng = (hi - lo) or 1.0
            shift = sum(self.weights[r] * (pop[order[r]][k] - old_mean[k]) / rng
                        for r in range(self.mu))
            self.mean[k] = old_mean[k] + shift
            lo2, hi2 = self.bounds[k]
            self.mean[k] = max(lo2, min(hi2, self.mean[k]))
        # evolution paths + diagonal covariance + step-size (CSA)
        for k in self.names:
            lo, hi = self.bounds[k]
            rng = (hi - lo) or 1.0
            yw = sum(self.weights[r] * (pop[order[r]][k] - old_mean[k]) / rng
                     for r in range(self.mu)) / max(self.sigma, 1e-300)
            self.ps[k] = (1.0 - self.cs) * self.ps[k] + ((self.cs * (2.0 - self.cs) * self.mueff) ** 0.5) * yw
            hs = 1.0  # diagonal variant skips hsig stall logic
            self.pc[k] = (1.0 - self.cc) * self.pc[k] + hs * ((self.cc * (2.0 - self.cc) * self.mueff) ** 0.5) * yw
            # rank-one + rank-mu on the diagonal only
            rank1 = self.pc[k] ** 2
            rankmu = sum(self.weights[r] * ((pop[order[r]][k] - old_mean[k]) / rng / max(self.sigma, 1e-300)) ** 2
                         for r in range(self.mu))
            self.diagC[k] = max(1e-8, (1.0 - self.c1 - self.cmu) * self.diagC[k]
                                + self.c1 * rank1 + self.cmu * rankmu)
        ps_norm = sum(v * v for v in self.ps.values()) ** 0.5
        chiN = self.n ** 0.5 * (1.0 - 1.0 / (4.0 * self.n) + 1.0 / (21.0 * self.n ** 2))
        self.sigma *= _m.exp((self.cs / self.damps) * (ps_norm / max(chiN, 1e-12) - 1.0))
        # clamp sigma to 1e-4..1.0 of mean range
        mean_range = sum(hi - lo for lo, hi in self.bounds.values()) / self.n or 1.0
        self.sigma = max(mean_range * 1e-6, min(mean_range * 1.0, self.sigma))
        self.counteval += self.lam
        return {"best_fitness": self.best_fitness, "sigma": self.sigma,
                "evals": self.counteval, "generation_best": fitnesses[order[0]]}

    def optimize(self, fitness_fn, generations: int = 20,
                 workers: int = 1) -> dict[str, Any]:
        """Maximize fitness_fn(genes_dict). Returns best + history."""
        import concurrent.futures as _cf
        history = []
        for _ in range(generations):
            pop = self.ask()

            def _one(g):
                try:
                    return float(fitness_fn(g))
                except Exception as exc:
                    note_failure("evolution:L1674", exc)
                    return 0.0

            if workers > 1:
                with _cf.ThreadPoolExecutor(max_workers=workers) as pool:
                    fits = list(pool.map(_one, pop))
            else:
                fits = [_one(g) for g in pop]
            stat = self.tell(fits)
            history.append({"best": self.best_fitness, **stat})
        return {"best_genes": dict(self.best_genes or self.mean),
                "best_fitness": self.best_fitness,
                "evals": self.counteval, "history": history}


class DifferentialEvolution:
    """DE/rand/1/bin for continuous bounded spaces. Simpler than CMA-ES,
    consistently beats vanilla GA on continuous benchmarks (audit item P0)."""

    VERSION = "2.0.0"

    def __init__(self, gene_bounds: dict[str, tuple[float, float]],
                 popsize: int = 15, F: float = 0.7, CR: float = 0.9,
                 seed_genes: (dict | None) = None):
        self._lock = threading.Lock()
        self.names = list(gene_bounds)
        self.bounds = {k: (float(v[0]), float(v[1])) for k, v in gene_bounds.items()}
        self.popsize = max(4, popsize)
        self.F = F
        self.CR = CR
        self.pop: list[dict] = []
        self.fit: list[float] = []
        for _ in range(self.popsize):
            g = {}
            for k in self.names:
                lo, hi = self.bounds[k]
                g[k] = float(seed_genes[k]) if seed_genes and k in seed_genes and random.random() < 0.2 \
                    else random.uniform(lo, hi)
            self.pop.append(g)
            self.fit.append(float("-inf"))
        self.best_idx = 0

    def _mutate_cross(self, i: int) -> dict:
        idxs = list(range(len(self.pop)))
        idxs.remove(i)
        a, b, c = random.sample(idxs, 3)
        j_rand = random.randrange(len(self.names))
        trial = {}
        for j, k in enumerate(self.names):
            lo, hi = self.bounds[k]
            if random.random() < self.CR or j == j_rand:
                v = self.pop[a][k] + self.F * (self.pop[b][k] - self.pop[c][k])
                trial[k] = max(lo, min(hi, v))
            else:
                trial[k] = self.pop[i][k]
        return trial

    def optimize(self, fitness_fn, generations: int = 30) -> dict[str, Any]:
        def _one(g):
            try:
                return float(fitness_fn(g))
            except Exception as exc:
                note_failure("evolution:L1735", exc)
                return 0.0
        for i, g in enumerate(self.pop):
            if self.fit[i] == float("-inf"):
                self.fit[i] = _one(g)
        history = []
        for _ in range(generations):
            for i in range(len(self.pop)):
                trial = self._mutate_cross(i)
                f = _one(trial)
                if f >= self.fit[i]:
                    with self._lock:
                        self.pop[i] = trial
                        self.fit[i] = f
            self.best_idx = max(range(len(self.fit)), key=lambda i: self.fit[i])
            history.append({"best": self.fit[self.best_idx]})
        return {"best_genes": dict(self.pop[self.best_idx]),
                "best_fitness": self.fit[self.best_idx], "history": history}


class TPESurrogate:
    """Tree-structured Parzen Estimator lite (audit item P2, sample efficiency).

    Stdlib-only EI proxy: split observations into good/bad by gamma
    quantile, sample candidates around good points, score by
    (dist-to-bad − dist-to-good) in normalized gene space. Suggests where
    a full 80-ep × 3-salt eval is worth spending.
    """

    VERSION = "2.0.0"

    def __init__(self, gene_bounds: dict[str, tuple[float, float]],
                 gamma: float = 0.25, candidates: int = 24):
        self._lock = threading.Lock()
        self.bounds = {k: (float(v[0]), float(v[1])) for k, v in gene_bounds.items()}
        self.names = list(self.bounds.keys())
        self.gamma = gamma
        self.candidates = candidates
        self.obs: list[tuple[dict, float]] = []

    def observe(self, genes: dict, fitness: float) -> None:
        with self._lock:
            self.obs.append((dict(genes), float(fitness)))

    def _norm(self, genes: dict) -> list[float]:
        v = []
        for k in self.names:
            lo, hi = self.bounds[k]
            rng = (hi - lo) or 1.0
            try:
                v.append((float(genes.get(k, lo)) - lo) / rng)
            except (TypeError, ValueError) as exc:
                note_failure("evolution:L1864", exc)
                v.append(0.0)
        return v

    def suggest(self) -> dict:
        with self._lock:
            obs = list(self.obs)
        if len(obs) < 8:
            return {k: random.uniform(lo, hi) for k, (lo, hi) in self.bounds.items()}
        ranked = sorted(obs, key=lambda o: o[1], reverse=True)
        n_good = max(2, int(len(ranked) * self.gamma))
        good = [self._norm(g) for g, _f in ranked[:n_good]]
        bad = [self._norm(g) for g, _f in ranked[n_good:]] or good
        best: Any = None
        best_s: float | None = None
        for _ in range(self.candidates):
            if random.random() < 0.8 and good:
                base = random.choice(good)
                cand = [min(1.0, max(0.0, x + random.gauss(0, 0.15))) for x in base]
            else:
                cand = [random.random() for _ in self.names]
            dg = min(sum((c - g) ** 2 for c, g in zip(cand, gp)) ** 0.5 for gp in good)
            db = min(sum((c - b) ** 2 for c, b in zip(cand, bp)) ** 0.5 for bp in bad)
            score = db - dg
            if best_s is None or score > best_s:
                best_s, best = score, cand
        return {k: lo + c * (hi - lo) for k, c, (lo, hi) in
                zip(self.names, best, [self.bounds[k] for k in self.names])}


# -- v2.0 many-objective + metrics + islands (audit items P1/P2) --------

def generate_reference_points(n_obj: int, p: int = 4) -> list[tuple]:
    """Das-Dennis reference points on the unit simplex (NSGA-III)."""
    pts: list[tuple] = []

    def _rec(lo: int, left: int, cur: list):
        if lo == n_obj - 1:
            pts.append(tuple(cur + [left / p]))
            return
        for v in range(left + 1):
            _rec(lo + 1, left - v, cur + [v / p])

    _rec(0, p, [])
    return pts


def hypervolume_2d(pareto_pts: list[tuple[float, float]],
                   ref: tuple[float, float]) -> float:
    """Hypervolume for 2-obj minimization front. Larger = better."""
    pts = sorted(((min(x, ref[0]), min(y, ref[1])) for x, y in pareto_pts))
    hv, prev_y = 0.0, ref[1]
    for x, y in pts:
        if y < prev_y:
            hv += (ref[0] - x) * (prev_y - y)
            prev_y = y
    return max(0.0, hv)


def moead_weights(pop_size: int, n_obj: int) -> list[tuple]:
    """Uniform simplex weights for MOEA/D (Tchebycheff). Falls back to
    Dirichlet-ish random weights when Das-Dennis under/overshoots."""
    p = 1
    while True:
        pts = generate_reference_points(n_obj, p)
        if len(pts) >= pop_size:
            return pts[:pop_size]
        if p > 12:
            # One draw per pair. `(a, 1 - a)` sums to 1, which is exactly what the
            # Tchebycheff scalarization assumes; drawing twice made the weights sum
            # to something arbitrary, silently mis-scaling every comparison. The
            # old `[:n_obj]` also sliced a 2-tuple on a path only reached for
            # n_obj == 2, which hid the mismatch.
            if n_obj == 2:
                return [(a, 1.0 - a)
                        for a in (random.random() for _ in range(pop_size))]
            return [tuple(random.random() for _ in range(n_obj))
                    for _ in range(pop_size)]
        p += 1


def tchebycheff(values: list[float], weight: tuple, ideal: list[float]) -> float:
    """Tchebycheff scalarization (minimize)."""
    return max(w * abs(v - z) for w, v, z in zip(weight, values, ideal))
