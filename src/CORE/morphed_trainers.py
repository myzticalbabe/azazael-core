#!/usr/bin/env python3
"""morphed_trainers — close the outer loop: evolve -> factory.

NSGA-II searches numeric SB3 hyperparameters (log learning_rate,
gamma, and one algo-native knob) against Pareto objectives; winners land
as first-class `morphed_<algo>_g<gen>` entries resolvable by the
trainers_v2 factory path in SB3Agent. Mutation varies parameters and
keeps identity (MUTATE, not morph): a morphed PPO is still PPO.

Objectives are proxy-evaluated (no full SB3 learn per genome — that cost
belongs to generation promotion, not search): reward-proxy favors
mid-range stable hyperparams, cost-proxy favors small batches/frequent
updates. Pareto fronts come from src/ML/nsga.py (sbx + polynomial).

Usage:
    from CORE.morphed_trainers import propose, promote, resolve_morphed
    proposals = propose("PPO", generations=8, pop_size=24, seed=7)
    winner = promote(proposals[0])   # registers morphed_ppo_g<N>
    SB3Agent(algo=winner["name"])     # resolves via factory

P1 deed decision (2026-10-29): morphed_registry.json is TRACKED source,
not ignored scratch — winners are property, the registry is the deed book.
It lives at src/CORE/morphed_registry.json, writes atomically (D1) under
an exclusive lock (D2), and must be committed + synced like code.
"""

import json
import math
import socket
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Hoisted 2026-10-30. The removed comment claimed a CORE <-> ML cycle;
# MEASURED otherwise: `import ML.nsga` pulls ZERO CORE modules (it imports
# only math/typing/numpy). Nine CORE imports elsewhere in src/ML is a
# package claim, not this module's edge.
from ML.nsga import NSGA2
from CORE.atomic_store import atomic_write_json, file_lock
from CORE.failure_ledger import note_failure


# Searchable numeric knobs per algo: (key, low, high, log_scale).
MORPHABLE_KNOBS: dict = {
    "PPO": [("learning_rate", 1e-5, 1e-3, True),
            ("gamma", 0.90, 0.999, False),
            ("clip_range", 0.1, 0.3, False)],
    "DQN": [("learning_rate", 1e-5, 1e-3, True),
            ("gamma", 0.90, 0.999, False),
            ("tau", 0.1, 1.0, False)],
    "SAC": [("learning_rate", 1e-5, 1e-3, True),
            ("gamma", 0.90, 0.999, False),
            ("tau", 0.005, 0.05, False)],
    "A2C": [("learning_rate", 1e-5, 1e-3, True),
            ("gamma", 0.90, 0.999, False),
            ("gae_lambda", 0.8, 1.0, False)],
}

DEFAULT_KNOBS = [("learning_rate", 1e-5, 1e-3, True),
                 ("gamma", 0.90, 0.999, False)]

_STORE_PATH = Path(__file__).with_name("morphed_registry.json")


def _load_store() -> dict:
    try:
        return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"generation": 0, "entries": {}}


def _save_store(store: dict) -> None:
    """Atomic write via CORE.atomic_store (D1) — single implementation."""
    store["updated_at"] = datetime.now(timezone.utc).isoformat()  # E7: UTC, like sweep.json
    atomic_write_json(_STORE_PATH, store, "morphed_trainers")


@contextmanager
def _store_lock():
    """Exclusive lock for read-modify-write (D2) — via CORE.atomic_store."""
    with file_lock(_STORE_PATH.with_suffix(".lock"),
                    "morphed_trainers") as lock_path:
        yield lock_path


def _decode(genome, knobs: list) -> dict:
    """Genome unit-cube -> hyperparam dict (log-scale where flagged)."""
    params: dict = {}
    for value, (key, low, high, log_scale) in zip(genome, knobs):
        value = min(1.0, max(0.0, float(value)))
        if log_scale:
            params[key] = math.exp(math.log(low) + value * (math.log(high) - math.log(low)))
        else:
            params[key] = low + value * (high - low)
    return params


def propose(algo: str = "PPO", generations: int = 8,
            pop_size: int = 24, seed: int = 7) -> list:
    """NSGA-II search over knobs; return Pareto-front proposals (JSON-safe).

    Each proposal: {algo, params, objectives, rank}. No training happens
    here — search is proxy-evaluated; promotion pays the real cost.
    """
    knobs = MORPHABLE_KNOBS.get(algo, DEFAULT_KNOBS)
    dim = len(knobs)

    def reward_proxy(x: np.ndarray) -> float:
        """heuristic_v0 (D3): mid-range-LR guess, NOT a measurement.
        Maximize: favor mid-range stable hyperparams."""
        params = _decode(x, knobs)
        lr_penalty = abs(float(np.log10(params.get("learning_rate", 3e-4))) + 3.5)
        gamma = float(params.get("gamma", 0.99))
        gamma_penalty = abs(gamma - 0.99) * 10.0
        return float(lr_penalty + gamma_penalty)

    def cost_proxy(x: np.ndarray) -> float:
        """heuristic_v0 (D3): genome-spread guess, NOT wall-clock.
        Maximize: favor cheap configs."""
        params = _decode(x, knobs)
        return float(abs(float(x[0]) - 0.5) + abs(float(x[-1]) - 0.5))

    bounds = np.array([[0.0, 1.0]] * dim)
    driver = NSGA2(fns=[reward_proxy, cost_proxy], bounds=bounds,
                   pop_size=pop_size, seed=seed)
    pareto_x, pareto_f = driver.run(generations)
    proposals = []
    for rank, (genome, objs) in enumerate(zip(pareto_x, pareto_f)):
        proposals.append({
            "algo": algo,
            "params": _decode(genome, knobs),
            "objectives": [float(o) for o in objs],
            "rank": rank,
            "seed": seed,
            "generations": generations,
            "fitness": "heuristic_v0",  # D3: proxy, not measurement
            "trained_reward": None,  # set by real training before promote()
        })
    return proposals


def promote(proposal: dict) -> dict:
    """Register a Pareto proposal as morphed_<algo>_g<gen> (transsubstantiation
    hook: the name is factory-resolvable; substance upgraded, callers unchanged).

    D3 gate: proxy-only proposals (trained_reward None) are REFUSED — the
    heuristic_v0 front ranks guesses, not champions. Train first, then
    promote with the measured reward attached. D2: the read-increment-write
    runs under the store lock so concurrent promotes cannot share a gen.

    E6: the lock is single-host, but winners arrive from two distros via
    git sync — a kilo promote and a local promote can both mint g<N> with
    different params. Same name + different params no longer overwrites:
    the second entry takes the first free suffixed name (g<N>b, g<N>c…)
    so no winner is ever lost at merge. The common path (free name) is
    unchanged, and kilo's morphed_<algo>_g<gen> pattern keeps matching.
    """
    if proposal.get("trained_reward") is None:
        return {"name": "", "ok": False,
                "error": "proxy-only proposal refused: attach trained_reward "
                         "from a real training run (D3 heuristic_v0 gate)"}
    try:
        origin = socket.gethostname()
    except OSError as e:
        note_failure("morphed_trainers:origin", e)
        origin = "unknown-host"
    with _store_lock():
        store = _load_store()
        store["generation"] = int(store.get("generation", 0)) + 1
        gen = store["generation"]
        algo = str(proposal.get("algo", "PPO"))
        name = f"morphed_{algo.lower()}_g{gen}"
        entries = store.setdefault("entries", {})
        params = proposal.get("params", {})
        if name in entries and entries[name].get("params") != params:
            # Cross-host generation collision (E6): keep both winners.
            note_failure("morphed_trainers:generation-collision", None)
            suffix_ord = ord("b")
            while f"{name}{chr(suffix_ord)}" in entries and suffix_ord <= ord("z"):
                suffix_ord += 1
            if f"{name}{chr(suffix_ord)}" in entries:
                name = f"{name}_{uuid.uuid4().hex[:8]}"
            else:
                name = f"{name}{chr(suffix_ord)}"
        entries[name] = {
            "algo": algo,
            "params": params,
            "objectives": proposal.get("objectives", []),
            "fitness": proposal.get("fitness", "heuristic_v0"),
            "trained_reward": proposal.get("trained_reward"),
            "parent": "nsga2-pareto",
            "generation": gen,
            "uuid": uuid.uuid4().hex[:8],
            "origin": origin,
        }
        try:
            _save_store(store)
        except OSError as e:
            note_failure("morphed_trainers:store", e)
            return {"name": "", "ok": False, "error": str(e)}
        return {"name": name, "ok": True, "generation": gen,
                "algo": algo, "params": entries[name]["params"],
                "trained_reward": proposal.get("trained_reward"),
                "uuid": entries[name]["uuid"], "origin": origin}


def resolve_morphed(name: str) -> dict | None:
    """Return the stored entry for a morphed name, or None."""
    return _load_store().get("entries", {}).get(name)


def list_morphed() -> dict:
    """All registered morphed trainers."""
    return _load_store().get("entries", {})
