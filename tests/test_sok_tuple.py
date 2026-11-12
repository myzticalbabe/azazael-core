# coding: utf-8
"""Tests for protocol_artifact SoK 4-tuple fields (EP135).

The tuple (applicability, termination, interface) is OPTIONAL and
validity-pure: absent fields never flip `valid` and never add to `gaps`,
so positional constructors (merge_sops) keep their verdicts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.skill_library import merge_sops, protocol_artifact

_PLAN = ["read the target", "plan the edit", "verify the result"]
_FACTS = ["worm_registry holds 200+ registered worms"]


def test_bare_call_unchanged_shape():
    """Three-positional call keeps its exact contract plus sok_complete=False."""
    out = protocol_artifact("worm-edit", _PLAN, _FACTS)
    assert out["valid"] is True
    assert out["gaps"] == []
    assert out["sok_tuple"] == {}
    assert out["sok_complete"] is False


def test_full_tuple_echoed_and_complete():
    """All three kwargs are echoed and flip sok_complete without touching valid."""
    out = protocol_artifact(
        "worm-edit", _PLAN, _FACTS,
        applicability="dispatch-family queries",
        termination="ledger citation written",
        interface={"args": ["query"], "returns": "artifact"})
    assert out["valid"] is True
    assert out["gaps"] == []
    assert out["sok_tuple"] == {
        "applicability": "dispatch-family queries",
        "termination": "ledger citation written",
        "interface": {"args": ["query"], "returns": "artifact"}}
    assert out["sok_complete"] is True


def test_partial_tuple_stays_valid():
    """One of three keeps valid=True, gaps empty, sok_complete False."""
    out = protocol_artifact("worm-edit", _PLAN, _FACTS,
                            termination="ledger citation written")
    assert out["valid"] is True
    assert out["gaps"] == []
    assert out["sok_tuple"] == {"termination": "ledger citation written"}
    assert out["sok_complete"] is False


def test_merge_sops_verdict_unaffected():
    """merge_sops constructs positionally: its ok is unchanged by the tuple."""
    a = protocol_artifact("worm-edit", _PLAN, _FACTS)
    b = protocol_artifact("worm-edit", ["audit the ledger"], _FACTS)
    res = merge_sops(a, b)
    assert res["ok"] is True
    assert res["merged"]["valid"] is True
    assert res["merged"]["gaps"] == []
