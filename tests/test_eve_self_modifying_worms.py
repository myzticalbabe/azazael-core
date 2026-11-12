#!/usr/bin/env python3
"""Deeper tests for Eve self-modifying worms."""
import sys
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_worm_dna_mutate():
    from eve_unleashed.self_modifying_worms import WormDNA
    d = WormDNA(worm_id="a", worm_type="monitor")
    rng = random.Random(42)
    m = d.mutate(rng=rng)
    assert m.worm_id != d.worm_id
    assert m.generation == d.generation + 1
    assert m.parent_ids == [d.worm_id]
    assert 0.0 <= m.aggressiveness <= 1.0
    assert m.scan_interval_s >= 5.0


def test_worm_dna_crossover():
    from eve_unleashed.self_modifying_worms import WormDNA
    a = WormDNA(worm_id="a", worm_type="x", aggressiveness=0.1)
    b = WormDNA(worm_id="b", worm_type="y", aggressiveness=0.9)
    rng = random.Random(1)
    c = WormDNA.crossover(a, b, rng=rng)
    assert c.worm_id not in ("a", "b")
    assert c.generation == max(a.generation, b.generation) + 1
    assert set(c.parent_ids) == {"a", "b"}
    assert c.enabled_metrics == sorted(set(a.enabled_metrics) | set(b.enabled_metrics))


def test_worm_dna_roundtrip():
    from eve_unleashed.self_modifying_worms import WormDNA
    d = WormDNA(worm_id="a", worm_type="x")
    d2 = WormDNA.from_dict(d.to_dict())
    assert d2.worm_id == d.worm_id
    assert d2.to_dict() == d.to_dict()


def test_worm_fitness_score():
    from eve_unleashed.self_modifying_worms import WormFitness
    f = WormFitness(worm_id="a", worm_type="x", generation=1,
                    insight_quality=1.0, actions_taken=10, actions_succeeded=10,
                    resource_usage=0.0, error_rate=0.0)
    assert f.score() > 0.9
    f2 = WormFitness(worm_id="b", worm_type="x", generation=1)
    assert 0.0 <= f2.score() <= 1.0


def test_genetic_algorithm():
    from eve_unleashed.self_modifying_worms import GeneticAlgorithm, WormDNA, WormFitness
    ga = GeneticAlgorithm(population_size=4, elite_keep=1, rng=random.Random(0))
    dnas = [WormDNA(worm_id=f"d{i}", worm_type="t") for i in range(3)]
    ga.seed(dnas)
    assert len(ga.population) == 4
    # record fitness
    for d in ga.population:
        ga.record_fitness(WormFitness(worm_id=d.worm_id, worm_type=d.worm_type,
                                     generation=d.generation, insight_quality=0.5))
    new_pop = ga.evolve()
    assert len(new_pop) == 4
    assert ga.generation == 2
    assert ga.best() is not None
    snap = ga.snapshot()
    assert snap["generation"] == 2


def test_evolutionary_manager_propose():
    from eve_unleashed.self_modifying_worms import EvolutionaryWormManager, WormDNA
    mgr = EvolutionaryWormManager(spawner=None, ga=None)
    # no subsystems -> fail-open
    dna = WormDNA(worm_id="a", worm_type="monitor")
    mgr.ga.seed([dna])
    res = mgr.propose_mutation(dna)
    assert res["status"] in ("spawned", "spawn_failed", "blocked")
    assert "genome" in res
    snap = mgr.snapshot()
    assert "ga" in snap
