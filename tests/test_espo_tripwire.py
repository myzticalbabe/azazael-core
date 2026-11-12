#!/usr/bin/env python3
"""EP259 ESPO tripwire: host accumulator plus live depo_filter frontier."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS.expansion.policy_optimization import depo_filter, espo_tripwire


def test_spike_trips_absorbing():
    out = espo_tripwire((0.1, 0.15, 0.2, 0.9))
    assert out["state"] == "absorbing-failure"
    assert out["budget"] == 4.0
    assert out["regret"] == 0.65


def test_calm_stream_stays_running():
    out = espo_tripwire((0.1, 0.15, 0.2))
    assert out["state"] == "running"
    assert out["regret"] == 0.0


def test_empty_stream_never_trips():
    out = espo_tripwire(())
    assert out["state"] == "running"
    assert out["budget"] == 0.0


def test_frontier_band_keeps_observed_steps():
    kept = depo_filter({"a": 0.1, "b": 0.9, "c": 0.0, "d": 1.0})
    assert kept == ["a", "b"]


def test_ep259_adapter_drives_live_hosts():
    from CORE.technique_wiring import _l5_dispatch as l5
    out = l5._ep259()
    assert out["engaged"] is True
    assert out["accepted"] is True
    assert out["state"] == "absorbing-failure"
    assert out["calm_state"] == "running"
    assert len(out["frontier_kept"]) == 4
    assert out["where"].endswith("espo_tripwire+depo_filter")
