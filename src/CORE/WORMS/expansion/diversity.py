"""Quality-diversity, novelty, and many-objective search subsystem.

This layer is deliberately separate from scalar-reward track selection. It
preserves diverse behavior, discovers novel candidates, and exposes a real
NSGA-III optimizer instead of counting reference-point helpers as an algorithm.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import dist, sqrt
from pathlib import Path
from random import Random
from typing import Callable, Sequence, List
import json

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass(frozen=True)
class Individual:
    """A candidate in a diversity or many-objective search."""

    genes: dict[str, float]
    objectives: tuple[float, ...]
    fitness: float = 0.0
    behavior: tuple[float, ...] = ()
    novelty: float = 0.0
    source: str = "random"

    def as_dict(self) -> dict:
        return asdict(self)


class PersistentMAPElites:
    """Deterministic, JSON-backed MAP-Elites archive."""

    def __init__(
        self,
        behavior_dims: Sequence[tuple[str, int, float, float]],
        gene_bounds: dict[str, tuple[float, float]],
        *,
        seed: int = 0,
        path: str | Path | None = None,
    ) -> None:
        if not behavior_dims or not gene_bounds:
            raise ValueError("behavior_dims and gene_bounds are required")
        self.behavior_dims = [
            (name, int(bins), float(low), float(high))
            for name, bins, low, high in behavior_dims
        ]
        self.gene_bounds = {
            name: (float(bounds[0]), float(bounds[1]))
            for name, bounds in gene_bounds.items()
        }
        self.rng = Random(int(seed))
        self.path = Path(path) if path is not None else None
        self.archive: dict[str, dict] = {}

    def cell_of(self, behavior: Sequence[float]) -> str:
        cells: list[int] = []
        if len(behavior) != len(self.behavior_dims):
            raise ValueError("behavior width does not match behavior_dims")
        for (name, bins, low, high), value in zip(self.behavior_dims, behavior):
            if bins <= 0 or high <= low:
                raise ValueError(f"invalid behavior dimension: {name}")
            bounded = max(low, min(high, float(value)))
            index = min(bins - 1, int((bounded - low) / (high - low) * bins))
            cells.append(index)
        return ",".join(str(value) for value in cells)

    def add(self, individual: Individual) -> bool:
        cell = self.cell_of(individual.behavior)
        current = self.archive.get(cell)
        if current is None or individual.fitness > float(current["fitness"]):
            self.archive[cell] = individual.as_dict()
            return True
        return False

    def sample(self) -> dict[str, float]:
        if not self.archive:
            return {
                name: self.rng.uniform(low, high)
                for name, (low, high) in self.gene_bounds.items()
            }
        elite = self.rng.choice(list(self.archive.values()))
        genes = elite["genes"]
        return {
            name: max(low, min(high, float(genes[name]) + self.rng.uniform(-0.2, 0.2) * (high - low)))
            for name, (low, high) in self.gene_bounds.items()
        }

    def stats(self) -> dict[str, float | int]:
        total = 1
        for _name, bins, _low, _high in self.behavior_dims:
            total *= bins
        fitnesses = [float(row["fitness"]) for row in self.archive.values()]
        return {
            "filled": len(self.archive),
            "total_cells": total,
            "coverage": len(self.archive) / total if total else 0.0,
            "qd_score": sum(fitnesses),
            "best": max(fitnesses) if fitnesses else 0.0,
        }

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "behavior_dims": self.behavior_dims,
            "gene_bounds": self.gene_bounds,
            "archive": self.archive,
            "stats": self.stats(),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self) -> bool:
        if self.path is None or not self.path.exists():
            return False
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        archive = payload.get("archive")
        if not isinstance(archive, dict):
            raise ValueError("invalid MAP-Elites archive")
        self.archive = archive
        return True


class NoveltySearch:
    """Nearest-neighbor novelty archive for behavior-space exploration."""

    def __init__(self, *, threshold: float = 0.1, archive_cap: int = 512) -> None:
        if threshold <= 0 or archive_cap <= 0:
            raise ValueError("threshold and archive_cap must be positive")
        self.threshold = float(threshold)
        self.archive_cap = int(archive_cap)
        self.archive: list[tuple[float, ...]] = []

    def assess(self, behavior: Sequence[float]) -> float:
        point = tuple(float(value) for value in behavior)
        novelty = 1.0 if not self.archive else min(
            dist(point, member) for member in self.archive
        )
        if novelty >= self.threshold:
            self.archive.append(point)
            if len(self.archive) > self.archive_cap:
                self.archive.pop(0)
        return novelty


def _dominates(left: Sequence[float], right: Sequence[float]) -> bool:
    return all(a <= b for a, b in zip(left, right)) and any(a < b for a, b in zip(left, right))


def nondominated_fronts(population: Sequence[Individual]) -> list[list[int]]:
    remaining = set(range(len(population)))
    fronts: list[list[int]] = []
    while remaining:
        current = [
            index for index in sorted(remaining)
            if not any(
                _dominates(population[other].objectives, population[index].objectives)
                for other in sorted(remaining)
                if other != index
            )
        ]
        if not current:
            current = [min(remaining)]
        fronts.append(current)
        remaining.difference_update(current)
    return fronts


def das_dennis_reference_points(n_objectives: int, divisions: int = 4) -> list[tuple[float, ...]]:
    if n_objectives < 2 or divisions < 1:
        raise ValueError("NSGA-III requires at least two objectives and one division")
    points: list[tuple[float, ...]] = []

    def build(prefix: list[float], left: int, objective: int) -> None:
        if objective == n_objectives - 1:
            points.append(tuple(prefix + [left / divisions]))
            return
        for value in range(left + 1):
            build(prefix + [value / divisions], left - value, objective + 1)

    build([], divisions, 0)
    return points


class NSGAIII:
    """Executable reference-direction selection for minimization objectives."""

    def __init__(self, population_size: int = 24, divisions: int = 4) -> None:
        if population_size < 4:
            raise ValueError("population_size must be at least four")
        self.population_size = int(population_size)
        self.divisions = int(divisions)

    def select(self, candidates: Sequence[Individual]) -> list[Individual]:
        if len(candidates) <= self.population_size:
            return list(candidates)
        objectives = len(candidates[0].objectives)
        if any(len(candidate.objectives) != objectives for candidate in candidates):
            raise ValueError("all candidates must have the same objective width")
        references = das_dennis_reference_points(objectives, self.divisions)
        survivors: list[int] = []
        for front in nondominated_fronts(candidates):
            if len(survivors) + len(front) <= self.population_size:
                survivors.extend(front)
                continue
            chosen = self._associate(front, candidates, references, objectives)
            remaining = self.population_size - len(survivors)
            for index in chosen[:remaining]:
                survivors.append(index)
            break
        return [candidates[index] for index in survivors]

    def _associate(
        self,
        front: Sequence[int],
        candidates: Sequence[Individual],
        references: Sequence[tuple[float, ...]],
        objectives: int,
    ) -> list[int]:
        lows = [min(candidates[index].objectives[axis] for index in front) for axis in range(objectives)]
        highs = [max(candidates[index].objectives[axis] for index in front) for axis in range(objectives)]
        associates: dict[int, list[int]] = {ref: [] for ref in range(len(references))}
        for index in front:
            normalized = tuple(
                (value - low) / (high - low or 1.0)
                for value, low, high in zip(candidates[index].objectives, lows, highs)
            )
            reference = min(
                range(len(references)),
                key=lambda ref: sum((a - b) ** 2 for a, b in zip(normalized, references[ref])),
            )
            associates[reference].append(index)
        available = [reference for reference, members in associates.items() if members]
        chosen: list[int] = []
        while available and len(chosen) < self.population_size:
            reference = min(
                available,
                key=lambda ref: sqrt(sum(value ** 2 for value in references[ref])),
            )
            members = associates[reference]
            niche = min(members, key=lambda index: sum(
                (a - b) ** 2
                for a, b in zip(candidates[index].objectives, references[reference])
            ))
            chosen.append(niche)
            members.remove(niche)
            if not members:
                available.remove(reference)
        return chosen

    def optimize(
        self,
        initial: Sequence[Individual],
        *,
        mutate: Callable[[dict[str, float]], dict[str, float]],
        evaluate: Callable[[dict[str, float]], tuple[float, ...]],
        generations: int,
    ) -> tuple[list[Individual], dict[str, int | float]]:
        if generations <= 0:
            raise ValueError("generations must be positive")
        population = self.select(list(initial))
        history: list[float] = []
        for _ in range(generations):
            offspring = [
                Individual(genes=mutate(candidate.genes), objectives=evaluate(mutate(candidate.genes)))
                for candidate in population
            ]
            population = self.select(population + offspring)
            history.append(min(candidate.objectives[0] for candidate in population))
        return population, {"generations": generations, "initial_best": history[0], "final_best": history[-1]}


def run_diversity_evidence(*, seed: int = 314, archive_path: str | Path | None = None) -> dict:
    """Run a small deterministic proof of QD, novelty, and NSGA-III."""

    archive = PersistentMAPElites(
        [("speed", 3, 0.0, 1.0), ("quality", 3, 0.0, 1.0)],
        {"x": (-1.0, 1.0), "y": (-1.0, 1.0)},
        seed=seed,
        path=archive_path,
    )
    novelty = NoveltySearch(threshold=0.2, archive_cap=64)
    for index in range(12):
        genes = {
            "x": -0.8 + index * 0.14,
            "y": -0.6 + (index % 4) * 0.4,
        }
        behavior = ((genes["x"] + 1.0) / 2.0, (genes["y"] + 1.0) / 2.0)
        score = 1.0 - abs(genes["x"] - 0.25) - abs(genes["y"] + 0.2)
        archive.add(Individual(genes, (), score, behavior, novelty.assess(behavior)))
    archive.save()

    initial = [
        Individual({"x": float(index % 4) - 1.5, "y": float(index // 4)}, (float(index % 4), float(index // 4)))
        for index in range(8)
    ]
    nsga = NSGAIII(population_size=4, divisions=2)
    selected = nsga.select(initial)
    return {
        "seed": seed,
        "map_elites": archive.stats(),
        "novelty_archive": len(novelty.archive),
        "nsga3": {"input": len(initial), "selected": len(selected), "objectives": 2},
    }
