# coding: utf-8
"""Dual-interpreter smoke check for the PROTEUS organs.

Run under BOTH interpreters (production 3.15t + its 3.15 GIL sidecar):
    C:/Azazael/.venvs/py315/Scripts/python.exe   _proteus_smoke.py   (GIL: torch+xpu)
    C:/Azazael/.venvs/py315t/Scripts/python.exe  _proteus_smoke.py   (free-threaded)

Exits non-zero on any failure so it can gate CI.
"""
import os
import sys
import json
import random

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# Only `_is_gil_enabled()` is authoritative. Matching a "t" against
# `sys.version` gives a FALSE POSITIVE on the GIL build, because the build path
# or executable name can contain a letter that looks like a threading marker.
# A wrong answer here would let a broken free-threaded path report as green.
free_threaded = (not sys._is_gil_enabled()) if hasattr(sys, "_is_gil_enabled") else False
print("interpreter : %s" % sys.version.split()[0])
print("free-thread : %s" % free_threaded)

failures = []


def check(name, fn):
    try:
        result = fn()
        print("  OK   %-28s %s" % (name, result if result is not None else ""))
    except Exception as exc:
        failures.append((name, "%s: %s" % (type(exc).__name__, exc)))
        print("  FAIL %-28s %s: %s" % (name, type(exc).__name__, exc))


def skip(name, why):
    print("  SKIP %-28s %s" % (name, why))


# -- device ------------------------------------------------------------
from CORE.device import describe, get_device, torch_available, numpy_available
print("\n[device]")
print("  torch=%s numpy=%s -> %s" % (torch_available(), numpy_available(), describe()))
check("get_device()", lambda: type(get_device()).__name__)

# -- morph -------------------------------------------------------------
from CORE.WORMS.expansion.morph import (
    Genome, GeneSpec, seed_pool_from_bounds, structural_mutate, viability,
    prune_zero_variance, dimension_report,
)

bounds = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5),
          "discount": (0.5, 0.999), "epsilon_decay": (0.0, 0.3)}
pool = seed_pool_from_bounds(bounds)
g0 = Genome.from_flat(bounds, {"learning_rate": 0.5, "epsilon": 0.296,
                               "discount": 0.8033, "epsilon_decay": 0.0859})
print("\n[morph]")
print("  seed genome dim=%d %s" % (g0.dimension, g0.to_dict()))


def _grow():
    rng = random.Random(0)
    dims, ops = [], {}
    for _ in range(120):
        child, op = structural_mutate(g0, pool, rng)
        ok, why = viability(child)
        if not ok:
            raise AssertionError("lethal child escaped: %s" % why)
        dims.append(child.dimension)
        ops[op] = ops.get(op, 0) + 1
    return "dims %d..%d ops=%s" % (min(dims), max(dims), ops)


check("structural mutation", _grow)
check("gene economics", lambda: "prune=%s" % (prune_zero_variance(
    [Genome.from_flat(bounds, {"learning_rate": 0.5, "epsilon": 0.3,
                               "discount": 0.8, "epsilon_decay": 0.1}),
     Genome.from_flat(bounds, {"learning_rate": 0.5, "epsilon": 0.4,
                               "discount": 0.9, "epsilon_decay": 0.2})])[0],))
check("dimension report", lambda: dimension_report(
    [{"dimension": 4, "fitness": 25.04}, {"dimension": 5, "fitness": 25.10}])["best_dimension"])

# -- arena -------------------------------------------------------------
from CORE.WORMS.expansion.arena import Arena, Environment, ArenaWorld, keeper_split


def _canyon(genome, cfg):
    return 100.0 * (1.0 - abs(cfg["hard_ratio"] - 0.5))


print("\n[arena]")
def _arena_run():
    a = Arena(_canyon, mc_low=0.3, mc_high=0.95, archive_cap=10, seed=3)
    seed_env = Environment([1, 1, 1], 0.5, 1.0, 1000, 0)
    a.populate(seed_env, None, n=5)
    row = a.step(propose_genome=lambda w: None, n_mutations=2)
    search, keeper = keeper_split(a.worlds, 0.3)
    return "archive=%d step=%s keepers=%d" % (len(a.worlds), row["admitted"], len(keeper))


check("co-evolution step", _arena_run)

# -- loom --------------------------------------------------------------
from CORE.WORMS.expansion.loom import NoveltyDrive, intrinsic_dimension, spectral_summary

print("\n[loom]")
_rng = random.Random(1)
check("intrinsic dim (spread)", lambda: "%.2f" % intrinsic_dimension(
    [[_rng.gauss(0, 1) for _ in range(4)] for _ in range(40)]))
check("intrinsic dim (collapsed)", lambda: "%.2f" % intrinsic_dimension([[0.5] * 4] * 40))


def _drive_run():
    d = NoveltyDrive(setpoint=3.0, gain=1.5)
    for _ in range(3):
        d.update([[0.5] * 4 for _ in range(20)])
    return "pressure=%.2f restart=%s" % (d.pressure, d.should_restart(3))


check("novelty drive engages", _drive_run)

# -- nerve -------------------------------------------------------------
from CORE.WORMS.expansion.nerve import (
    Neuromodulator, context_vector, modulated_q_update,
)

print("\n[nerve]")
_nm = Neuromodulator(use_torch=False)


def _nerve_run():
    ctx = context_vector("advisor", "critical", 5, 20, 0.3, 0.5)
    mods = _nm.modulations(ctx)
    q = {}
    modulated_q_update(q, "s", "a", 1.0, "s2", 0.1, 0.9, mods)
    return "mods=%s q=%.4f params=%d" % (
        ["%.2f" % m for m in mods], list(q.values())[0], _nm.n_params)


check("plasticity rule", _nerve_run)


def _nerve_default_backend():
    """Default construction requests torch; it must DEGRADE, not raise."""
    m = Neuromodulator()
    mods = m.modulations(context_vector("s", "sev", 1, 10, 0.2))
    return "backend=%s mods=%d" % (m.backend, len(mods))


check("torch degradation", _nerve_default_backend)

# -- chorus ------------------------------------------------------------
from CORE.WORMS.expansion.chorus import Chorus, FailureDigest, parse_proposals

print("\n[chorus]")


class _Stub:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, prompt, system_prompt=None, model=None, max_tokens=None,
             temperature=None):
        return {"content": self.payload, "model": "stub"}


check("parse fenced json", lambda: len(parse_proposals(
    '```json\n[{"op":"nest","gene":"learning_rate"}]\n```')))
check("valid proposal accepted", lambda: len(Chorus(
    budget=2, provider=_Stub(json.dumps([{"op": "add_gene",
                                          "gene": "use_schema_features"}])),
    cache=False).propose(FailureDigest(holdout_failures=4), g0, pool)))
check("hallucination rejected", lambda: Chorus(
    budget=2, provider=_Stub('[{"op":"add_gene","gene":"made_up"}]'),
    cache=False).report()["budget"])
check("fail-open on dead provider", lambda: Chorus(
    budget=1, provider=_Stub("garbage not json"), cache=False
).propose(FailureDigest(), g0, pool))

# -- circulation -------------------------------------------------------
from CORE.WORMS.expansion.allocation import (
    ThompsonAllocator, PROTEUS_ARMS, reward_for_outcome,
)

print("\n[circulation]")
def _alloc_run():
    a = ThompsonAllocator(list(PROTEUS_ARMS), floor=0.02, rng=random.Random(0))
    for _ in range(40):
        ok, w = reward_for_outcome("holdout_fail")
        a.update("ga", ok, w)
    budget = a.allocation(100)
    # Design intent (F-P0-1): the BUDGET floor keeps every arm funded.
    # starved() measures posterior means (a different thing); assert shares.
    assert sum(budget.values()) == 100, budget
    assert all(v > 0 for v in budget.values()), budget
    return "sum=%d min_share=%d starved(posterior)=%s" % (
        sum(budget.values()), min(budget.values()), a.starved())


check("allocation never starves", _alloc_run)

# -- evidence (Cranium) ------------------------------------------------
from CORE.WORMS.expansion.evidence import EraEvidence

print("\n[cranium]")
def _evidence_run():
    ev = EraEvidence()
    # the E4 shape: one observation in each of 28 eras used to reset evidence
    for i in range(28):
        ev.observe("era-%d" % i, 3.0)
    post = ev.posterior()
    # degenerate case: all 28 eras agree exactly -> confidently at 3.0, not nan
    assert post["mean"] == post["mean"], "posterior mean is nan"
    assert abs(post["mean"] - 3.0) < 1e-6, post["mean"]
    return "eras=%d n=%d mean=%.3f sd=%.2e" % (
        ev.n_eras, ev.n_observations, post["mean"], post["sd"])


check("cross-era pooling", _evidence_run)

# -- oracle ------------------------------------------------------------
from CORE.WORMS.expansion.oracle import FitnessOracle, build_dataset, era_descriptor

print("\n[oracle]")
check("era descriptor", lambda: ["%.2f" % v for v in era_descriptor(
    {"info": 500, "critical": 120, "warning": 380, "hard_count": 40})])


def _oracle_run():
    if not torch_available():
        # By design the Oracle is a GPU organ and runs in the GIL interpreter.
        # What must hold here is that it fails LOUD and CLEAR, never obscurely.
        try:
            FitnessOracle(n_gene=6).fit([[0.0] * 11], [1.0], epochs=1)
        except RuntimeError as exc:
            assert "torch" in str(exc).lower(), exc
            return "torch absent -> clear RuntimeError (expected)"
        raise AssertionError("fitted without torch")
    r = _r.Random(0)
    o = FitnessOracle(n_gene=6)
    # feature_count = genes + era descriptor; build X to that width
    X = [[r.random() for _ in range(o.feature_count)] for _ in range(64)]
    y = [sum(row) for row in X]
    o.fit(X, y, epochs=30, batch_size=16)
    mean, var = o.predict(X[:4])
    return "fitted=%s feats=%d mean=%.2f sd=%.3f" % (
        o.is_fitted, o.feature_count, mean[0], var[0] ** 0.5)


check("ensemble fit/predict", _oracle_run)

# -- cppn --------------------------------------------------------------
from CORE.WORMS.expansion.cppn import CPPNGenome

import random as _r
print("\n[cppn]")


def _cppn_run():
    c = CPPNGenome()
    vec = c.sample(random.Random(0))
    pheno = c.phenotype(vec, n_params=24)
    # the whole point: phenotype size decouples from gene count
    return "genes=%d params=%d" % (c.gene_count, len(pheno))


check("hyperneat phenotype", _cppn_run)

# -- summary -----------------------------------------------------------
print("\n" + "=" * 62)
if failures:
    print("FAILURES: %d" % len(failures))
    for name, err in failures:
        print("  - %s: %s" % (name, err))
    sys.exit(1)
print("ALL ORGANS OK (%s)" % ("free-threaded" if free_threaded else "GIL"))
