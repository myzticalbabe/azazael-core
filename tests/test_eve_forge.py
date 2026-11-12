"""Eve forge -- contracts for the named gaps, staged, never autonomous."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import pytest

from eve_unleashed import cognition as _c
from eve_unleashed import forge as _f


def test_every_gap_has_a_spec():
    named = {g[0] for g in _c.FORGE_GAPS}
    forged = {g["gap"] for g in _f.gaps()}
    assert named == forged
    for g in _f.gaps():
        assert g["module"] and g["function"] and g["assertion"]


def test_unknown_gap_rejected():
    with pytest.raises(ValueError):
        _f.contract("telepathy")


def test_contract_names_the_property_not_a_number():
    c = _f.contract("inhibition")
    assert "NotImplementedError" in c["module_text"]
    assert "three blocked attempts" in c["test_text"]
    assert "MOVE:" in c["test_text"]


def test_materialize_lands_red_only_in_the_staging_root(tmp_path):
    out = _f.materialize("salience", root=str(tmp_path))
    assert out["state"] in ("scaffold-ok", "scaffold-broken")
    assert out["capability"] == "absent"
    assert os.path.dirname(out["module"]) == str(tmp_path)
    assert os.path.dirname(out["test"]) == str(tmp_path)
    assert "capability is absent" in out["note"]
    rep = _f.report(root=str(tmp_path))
    assert rep["staged"] == 1 and rep["total"] == len(_f.gaps())


def test_forge_cannot_write_into_the_tree(tmp_path):
    """The staging root is the only place a forged pair can appear."""
    staged = _f.materialize("incubation", root=str(tmp_path / "stage"))
    assert str(tmp_path) in staged["module"]
    assert "src" + os.sep + "eve_unleashed" not in staged["module"]
