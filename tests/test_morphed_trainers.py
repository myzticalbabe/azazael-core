#!/usr/bin/env python3
"""G1 test: morphed_trainers closes the evolve->factory outer loop.

Names CORE.morphed_trainers + the sb3_agent morphed path; asserts NSGA-II
proposals are Pareto-shaped, promotion registers, and SB3Agent resolves
morphed names to base algo with merged kwargs (transsubstantiation hook).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from CORE import morphed_trainers
from CORE.morphed_trainers import list_morphed, promote, propose, resolve_morphed


def test_propose_returns_pareto_shaped_proposals():
    props = propose("PPO", generations=3, pop_size=10, seed=7)
    assert len(props) >= 1
    for p in props:
        assert p["algo"] == "PPO"
        assert set(p["params"]) == {"learning_rate", "gamma", "clip_range"}
        assert 1e-5 <= p["params"]["learning_rate"] <= 1e-3
        assert 0.90 <= p["params"]["gamma"] <= 0.999
        assert len(p["objectives"]) == 2


def test_promote_refuses_proxy_only(tmp_path, monkeypatch):
    store = tmp_path / "morphed_registry.json"
    monkeypatch.setattr(morphed_trainers, "_STORE_PATH", store)
    props = propose("DQN", generations=2, pop_size=8, seed=11)
    refused = promote(props[0])
    assert not refused["ok"] and "trained_reward" in refused["error"]


def test_promote_registers_and_resolves(tmp_path, monkeypatch):
    store = tmp_path / "morphed_registry.json"
    monkeypatch.setattr(morphed_trainers, "_STORE_PATH", store)
    props = propose("DQN", generations=2, pop_size=8, seed=11)
    props[0]["trained_reward"] = 123.4
    winner = promote(props[0])
    assert winner["ok"] and winner["name"].startswith("morphed_dqn_g")
    assert winner["trained_reward"] == 123.4
    entry = resolve_morphed(winner["name"])
    assert entry and entry["algo"] == "DQN"
    assert entry["trained_reward"] == 123.4
    assert winner["name"] in list_morphed()


def test_sb3agent_drops_unknown_registry_keys(tmp_path, monkeypatch):
    # E4: hand-edited registry keys must not reach the SB3 constructor.
    store = tmp_path / "morphed_registry.json"
    monkeypatch.setattr(morphed_trainers, "_STORE_PATH", store)
    props = propose("PPO", generations=2, pop_size=8, seed=7)
    props[0]["trained_reward"] = 42.0
    props[0]["params"] = dict(props[0]["params"])
    props[0]["params"]["not_a_knob"] = 12345
    winner = promote(props[0])
    assert winner["ok"]
    from CORE.sb3_agent import SB3Agent

    agent = SB3Agent(algo=winner["name"])
    assert agent.algo_name == "PPO"
    assert "not_a_knob" not in agent.algo_kwargs
    assert "learning_rate" in agent.algo_kwargs


def test_promote_survives_cross_host_generation_collision(tmp_path, monkeypatch):
    # E6: kilo and local both mint g<N> via git sync — no winner lost.
    from CORE.morphed_trainers import _load_store, _save_store
    store = tmp_path / "morphed_registry.json"
    monkeypatch.setattr(morphed_trainers, "_STORE_PATH", store)
    props = propose("PPO", generations=2, pop_size=8, seed=7)
    props[0]["trained_reward"] = 1.0
    first = promote(props[0])
    assert first["ok"]
    # Simulate the other host: same generation counter, different params.
    disk = _load_store()
    disk["generation"] = 0
    _save_store(disk)
    rival = dict(props[0])
    rival["params"] = dict(props[0]["params"])
    rival["params"]["gamma"] = min(0.999, float(rival["params"]["gamma"]) + 0.001)
    rival["trained_reward"] = 2.0
    second = promote(rival)
    assert second["ok"] and second["name"] != first["name"]
    entries = list_morphed()
    assert first["name"] in entries and second["name"] in entries
    assert entries[first["name"]]["trained_reward"] == 1.0
    assert entries[second["name"]]["trained_reward"] == 2.0
    assert entries[second["name"]].get("uuid")
    assert entries[second["name"]].get("origin")


def test_sb3agent_resolves_morphed_to_base(tmp_path, monkeypatch):
    store = tmp_path / "morphed_registry.json"
    monkeypatch.setattr(morphed_trainers, "_STORE_PATH", store)
    props = propose("PPO", generations=2, pop_size=8, seed=7)
    props[0]["trained_reward"] = 99.0
    winner = promote(props[0])
    from CORE.sb3_agent import SB3Agent

    agent = SB3Agent(algo=winner["name"])
    assert agent.algo_name == "PPO"
    assert agent.morphed_params["learning_rate"] == winner["params"]["learning_rate"]
    assert agent.algo_kwargs["learning_rate"] == winner["params"]["learning_rate"]
