"""The session's own connectance, typed and audited.

Every edge below is a real relationship from this session's work:
the dream flow feeding genomes, generations chaining through time,
lineages converging on one verdict, blobs citing the manifest, the
genome walking its parameter cube. If the topology changes, this test
fails until the declaration is updated -- the shape claim stays
checkable, never recollected.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from eve_unleashed.shapes import (ROLES, SOLIDS, ShapeGraph,
                                regime_histogram)


def _session_topology() -> ShapeGraph:
    g = ShapeGraph()
    # dream flow: one log feeds many genomes (fan on complete-20)
    for i in range(3):
        g.declare("dream_log", "genome-%d" % i, "complete-20",
                  "intuition-from-memory, no live fetch")
    # generations: a chain through time (cut ring on cycle-20)
    g.declare("gen-11", "gen-12-champ", "cycle-20", "chain: cut ring")
    g.declare("gen-12-champ", "lineage-A-gen-32", "cycle-20",
              "chain: continued evolution")
    # verdict: lineages + null converge on one comparison (funnel)
    for src in ("lineage-A-gen-32", "lineage-B-gen-21", "null-budget-8"):
        g.declare(src, "verdict-tie-0.8667", "complete-20",
                  "same ruler, honest tie")
    # pipeline: freeze -> replay -> score (chain, cut ring)
    g.declare("fixture-freeze", "fixture-replay", "cycle-20",
              "chain: pin then replay")
    g.declare("fixture-replay", "score_genome", "cycle-20", "chain: one rung")
    # root: blobs cite the manifest, the audit cites the manifest (hub)
    for src in ("blobs-10", "honey.json"):
        g.declare(src, "root-audit", "icosahedron", "hub: all cite one")
    # genome walks its parameter cube
    g.declare("WormDNA", "hypercube-32-space", "hypercube-32",
              "cooldown x aggressiveness x confidence x mutation")
    # lanes: Eve, kilo, opencode pass evidence peer to peer (mesh)
    g.declare("eve-lane", "shared-index", "dodecahedron",
              "mesh: no lane commands another")
    g.declare("shared-index", "kilo-lane", "dodecahedron", "mesh: audit read")
    return g


def test_unknown_solid_refused_with_the_full_registry():
    g = ShapeGraph()
    with pytest.raises(ValueError, match="dodecahedron"):
        g.declare("a", "b", "lego")


def test_session_topology_audits_pass_with_geometry_emerged():
    v = _session_topology().audit()
    assert v["verdict"] == "PASS", v
    assert "dream_log" in v["fans"]
    assert "verdict-tie-0.8667" in v["funnels"]
    assert "root-audit" in v["hubs"]
    assert v["isolated"] == []
    assert set(v["by_solid"]) >= {"complete-20", "cycle-20",
                                  "icosahedron", "hypercube-32",
                                  "dodecahedron"}


def test_regime_histogram_counts_five_regimes_alive():
    h = regime_histogram(_session_topology())
    assert sum(h.values()) == 15
    assert h["fan"] >= 3 and h["funnel"] >= 3
    assert h["ring"] >= 4 and h["hub"] == 2
    assert h["cube"] == 1 and h["mesh"] == 2


def test_hyper_edge_audits_itself_as_a_funnel():
    # reading, settings, ledger, head: four legs, one verdict.
    g = ShapeGraph()
    for leg in ("reading_audit", "settings_shadow", "leg_ledger",
                "head_audit"):
        g.declare(leg, "hyper-edge-verdict", "complete-20",
                  "four faces of auditability")
    v = g.audit()
    assert v["verdict"] == "PASS", v
    assert v["funnels"] == ["hyper-edge-verdict"]
    assert v["isolated"] == []


def test_roles_cover_all_seven_registered_solids():
    assert sorted(ROLES) == SOLIDS
    flat = [r for rs in ROLES.values() for r in rs]
    for role in ("fan", "funnel", "ring", "chain", "mesh", "hub", "cube"):
        assert role in flat, role
