"""Native kernel agreement: byte-identical to Python, or it does not ship.

The .pyx travels, the .pyd is built on the box (gitignored, like
_replay_cy). When the kernel is present, every number must equal the
Python path exactly; the fallback path is verified by forcing it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

cy = pytest.importorskip("eve_unleashed._forensic_cy",
                         reason="native kernel not built on this box")

from eve_unleashed.dodeca_recall import SHAPES, bfs_distances
from eve_unleashed.resilience import fault_stats


def test_native_bfs_all_equals_python_on_every_solid():
    for name in SHAPES:
        n, nbr = SHAPES[name]
        adj = [nbr(v) for v in range(n)]
        dmax, total, count = cy.bfs_all(n, adj)
        live = [bfs_distances(u, n, nbr)[v]
                for u in range(n) for v in range(n) if u != v]
        assert (dmax, total, count) == (max(live), sum(live), len(live)), name


def test_native_fault_worst_equals_python_on_every_solid():
    for name in SHAPES:
        n, nbr = SHAPES[name]
        assert tuple(cy.fault_worst(n, [nbr(v) for v in range(n)])) == (
            fault_stats(name)["fault_diameter"],
            fault_stats(name)["disconnects"]), name


def test_fallback_path_reports_python_engine(monkeypatch):
    import eve_unleashed.resilience as r
    monkeypatch.setattr(r, "_cy", None)
    monkeypatch.setattr(r, "ENGINE", "python")
    fs = r.fault_stats("petersen")
    assert fs["engine"] == "python"
    assert (fs["fault_diameter"], fs["disconnects"]) == (3, 0)
