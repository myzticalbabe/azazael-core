# coding: utf-8
"""POINDEXTER worm wiring: registry presence and execute() contract.

Production wiring for the POINDEXTER goal (register as a worm): the analysis
itself lives in UTILITIES/POINDEXTER, this covers the registry entry point.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from CORE.worm_registry import discover_worms, WORM_REGISTRY
from CORE.WORMS.POINDEXTER_WORM import POINDEXTERWORM


@pytest.fixture(scope="module", autouse=True)
def _discover():
    discover_worms()


def test_registered_in_worm_registry():
    assert "POINDEXTERWORM" in WORM_REGISTRY


def test_registry_category_and_priority():
    info = WORM_REGISTRY["POINDEXTERWORM"]
    assert info["category"] == "analysis"
    assert int(info["priority"]) in range(1, 11)


def test_execute_default_systems():
    report = POINDEXTERWORM().execute()
    assert report["metrics"]["total_systems"] == 7
    assert 0.0 <= report["metrics"]["interoperability_index"] <= 1.0
    assert report["worm"] == "POINDEXTERWORM"


def test_execute_custom_systems():
    systems = [
        {"name": "A", "inputs": ["x"], "outputs": ["y"],
         "interfaces": ["i"], "protocols": ["p"]},
        {"name": "B", "inputs": ["y"], "outputs": ["z"],
         "interfaces": ["i"], "protocols": ["p"]},
    ]
    report = POINDEXTERWORM(systems=systems).execute()
    assert report["metrics"]["total_systems"] == 2
    names = [row["system"] for row in report["centrality_ranking"]]
    assert sorted(names) == ["A", "B"]


def test_execute_report_json_serializable():
    import json

    json.dumps(POINDEXTERWORM().execute())
