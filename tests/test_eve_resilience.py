"""Regime five, measured: perfection prices itself, survival is priced too.

Petersen and complete-20 sit at Moore efficiency 1.000 (mathematically
perfect, zero waste). The cube pays the most for its addressability.
Cycle-20's diameter nearly doubles when one vertex dies; the mesh does
not move. All asserted with the numbers, not the adjectives.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.resilience import (fault_stats, melee, moore_efficiency,
                                      regime_report, retune, spectral_gap)


def test_perfection_sits_at_one():
    assert moore_efficiency("petersen") == 1.0
    assert moore_efficiency("complete-20") == 1.0
    assert moore_efficiency("cycle-20") > 0.9


def test_cube_pays_most_for_addressability():
    effs = {s: moore_efficiency(s) for s in
            ("hypercube-32", "dodecahedron", "g40", "icosahedron")}
    assert min(effs, key=effs.get) == "hypercube-32"


def test_nothing_disconnects_but_the_ring_stretches():
    ring = fault_stats("cycle-20")
    mesh = fault_stats("dodecahedron")
    assert ring["disconnects"] == 0 and mesh["disconnects"] == 0
    assert ring["fault_stretch"] > mesh["fault_stretch"]
    assert mesh["fault_stretch"] == 1.0


def test_spectral_gap_orders_gossip_speed():
    gaps = {s: spectral_gap(s) for s in
            ("petersen", "dodecahedron", "cycle-20")}
    assert gaps["petersen"] > gaps["dodecahedron"] > gaps["cycle-20"]


def test_melee_is_deterministic_and_names_a_winner():
    keys = ["wikipedia", "arxiv", "github", "d_actor", "e_valid"]
    first = melee(["petersen", "dodecahedron"], keys, "wikipedia")
    second = melee(["dodecahedron", "petersen"], keys, "wikipedia")
    assert first["winner"] == second["winner"]
    assert first["winner"] in ("petersen", "dodecahedron")
    assert first["table"]["petersen"]["hops"] <= 2  # diameter bound holds
    assert first["table"]["dodecahedron"]["hops"] <= 5


def test_retune_maps_workload_to_solid():
    assert retune(8, 2)["solid"] == "petersen"       # few hot keys
    assert retune(15)["solid"] == "dodecahedron"     # order matters
    assert retune(5, broadcast=True)["solid"] == "complete-20"
    assert retune(30)["solid"] == "hypercube-32"
    assert retune(100)["solid"] == "g40"
    assert "rule" in retune(8, 2)  # thresholds are data, Lillith moves them


def test_regime_report_carries_the_price():
    r = regime_report("dodecahedron")
    assert r["n"] == 20 and r["diameter"] == 5
    assert r["survival"]["fault_stretch"] == 1.0
    assert r["price"] == r["survival"]["fault_stretch"] / r["moore_efficiency"]
