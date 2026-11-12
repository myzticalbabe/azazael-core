# coding: utf-8
"""Tests for skill_library.spine_eval — EP150 layer-by-layer spine scaffold.

Derivation, not constants: expectations come from the live
technique_runtime registry (status()), so the tests pin the SHAPE
(per-layer grouping, coverage math, run partition) rather than counts.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.skill_library import SPINE_LAYERS, spine_eval


def test_spine_eval_structure_only_matches_registry():
    """Default call groups the live registry by layer without engaging adapters."""
    from CORE import technique_runtime as rt

    reg = rt.status(record=False)
    expected = {}
    for row in reg.get("wirings", []):
        layer = str(row.get("layer", "")).upper()
        if layer in SPINE_LAYERS:
            expected.setdefault(layer, []).append(row.get("ep"))

    out = spine_eval()
    assert out["ok"] is True
    for layer in SPINE_LAYERS:
        assert out["layers"][layer]["n_wired"] == len(expected.get(layer, []))
        assert sorted(out["layers"][layer]["eps"]) == sorted(expected.get(layer, []))
    n_covered = sum(1 for layer in SPINE_LAYERS if expected.get(layer))
    assert out["coverage"] == pytest.approx(n_covered / len(SPINE_LAYERS))
    assert sorted(out["missing_layers"]) == sorted(
        [layer for layer in SPINE_LAYERS if not expected.get(layer)])


def test_spine_eval_run_partitions_engaged_per_layer():
    """run=True engages adapters once and partitions engaged/errored per layer."""
    out = spine_eval(run=True)
    assert out["ok"] is True
    per = out["per_layer"]
    assert out["engaged"] == sum(t["engaged"] for t in per.values())
    assert out["errored"] == sum(t["errored"] for t in per.values())
    for layer in SPINE_LAYERS:
        info = out["layers"][layer]
        tally = per[layer]
        assert tally["engaged"] + tally["errored"] == info["n_wired"]


def test_spine_eval_subset_of_layers():
    """Custom layer subset scopes grouping, coverage, and missing."""
    out = spine_eval(layers=("L2", "L9"))
    assert out["ok"] is True
    assert set(out["layers"]) == {"L2", "L9"}
    assert "L9" in out["missing_layers"]
    assert out["coverage"] == pytest.approx(
        0.5 if out["layers"]["L2"]["n_wired"] else 0.0)


def test_spine_eval_fail_open_without_registry():
    """Registry unavailable returns ok=False, never raises."""
    import CORE.skill_library as _sl

    with patch.object(_sl, "_rt", None):
        out = spine_eval()
    assert out["ok"] is False
    assert out["coverage"] == 0.0
    assert out["error"]
