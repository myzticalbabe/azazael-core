"""EP413 CURE: the live acceptance gate must name the repair-tree resync root.

Absorption test for engram #632 (MIMO SHELFWARE ABSORB), slice 2.

The defect this locks down: `verification_gate.repair_tree` (EP413, tree half) had
callers only in the exercise layer (`technique_wiring/_l3_verify.py:368`) and in
`tests/test_backlog_hosts.py`. The production acceptance gate
(`verify_before_accept`, 10 production callers across `meta_cognition.py`,
`sentinel_enforcer.py`, `technique_runtime.py`) called `margin_check` and named the
fragile ids, but never built the repair tree -- so the resync point (the
highest-confidence non-fragile id that re-verification must restart from) was
computed nowhere on the live path.

`repair_tree`'s own docstring says resync EXECUTION is deferred ("no chain-runner
exists at this layer -- the tree names where it runs"). Naming it is therefore
exactly this seam's job, and it is what was missing.

Per this repo's standard (tests/test_simulate_vs_spawn.py): "A pure function
nothing calls is the exact shape this repo keeps producing (flaws S1/S3:
EXERCISE-ONLY by construction), so the wiring is part of the contract."
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _gate():
    from CORE import verification_gate as vg

    return vg


def _accept(vg, confidences, subject="ep413-probe"):
    """`verify_before_accept` takes `subject` positionally; supply one."""
    return vg.verify_before_accept(subject, confidences=confidences)


def test_a_fragile_margin_names_the_ep413_resync_root():
    """The gate must say WHERE re-verification restarts, not only what failed."""
    vg = _gate()
    items = [{"id": "plan", "confidence": 0.92},
             {"id": "edit", "confidence": 0.64},
             {"id": "lint", "confidence": 0.81}]

    out = _accept(vg, items)

    assert out["accepted"] is False, out
    joined = " | ".join(out["remediations"])
    assert "EP413 resync-root=" in joined, out["remediations"]
    # Highest-confidence non-fragile id is "plan" (0.92), not the fragile ones.
    assert "resync-root=plan" in joined, out["remediations"]


def test_the_resync_root_matches_the_repair_tree_itself():
    """The gate must report repair_tree's answer, not a second implementation."""
    vg = _gate()
    items = [{"id": "plan", "confidence": 0.92},
             {"id": "edit", "confidence": 0.64}]

    tree = vg.repair_tree(items)
    out = _accept(vg, items)
    joined = " | ".join(out["remediations"])

    assert f"resync-root={tree['resync']}" in joined, (tree, out["remediations"])
    assert f"{tree['n_fragile']} fragile node(s)" in joined, out["remediations"]


def test_all_fragile_falls_back_to_the_root_node():
    """repair_tree's documented fallback must reach the gate verbatim."""
    vg = _gate()
    items = [{"id": "a", "confidence": 0.1}, {"id": "b", "confidence": 0.2}]

    tree = vg.repair_tree(items)
    out = _accept(vg, items)
    joined = " | ".join(out["remediations"])

    assert tree["resync"] == "root", tree
    assert "resync-root=root" in joined, out["remediations"]


def test_a_passing_margin_adds_no_ep413_remediation():
    """Positive control: the absorb must not invent work when nothing is fragile."""
    vg = _gate()
    items = [{"id": "plan", "confidence": 0.95}, {"id": "edit", "confidence": 0.9}]

    out = _accept(vg, items)

    assert out["accepted"] is True, out
    assert not any("EP413" in r for r in out["remediations"]), out["remediations"]


def test_the_original_remediation_contract_is_preserved():
    """Additive only: the 'repair ONLY at fragile nodes' string must survive."""
    vg = _gate()
    items = [{"id": "plan", "confidence": 0.92}, {"id": "edit", "confidence": 0.64}]

    out = _accept(vg, items)

    joined = " | ".join(out["remediations"])
    assert "repair ONLY at fragile nodes" in joined, out["remediations"]
    assert "do not re-run the chain" in joined, out["remediations"]
