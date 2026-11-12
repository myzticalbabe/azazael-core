#!/usr/bin/env python3
"""EP352 NO-HOST pin: hidden-state failure prediction has no host here.

CORE.layerwise ships optimizer-memory utilities (LISA sampling, BlockAdam
blocks, MeZO perturbation, GaLore projection, outlier-weighted sampling)
— no round-one activation probes and no recall-calibrated abort cascade.
This test pins the NO-HOST verdict: if a probe-like symbol ever appears,
the verdict must be re-graded, not silently doubled up.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_PROBE_KEYS = ("probe", "activation", "abort", "cascade", "hidden",
               "internals", "interven", "recall_calibrat")


def test_ep352_layerwise_has_no_probe_symbols():
    import CORE.layerwise as lw
    found = [n for n in dir(lw)
             if any(k in n.lower() for k in _PROBE_KEYS)]
    assert found == []


def test_ep352_adapter_reports_module_granularity_absence():
    from CORE.technique_wiring import _l3_verify as l3
    out = l3._ep352()
    assert out["host_imported"] is True
    assert out["host_symbol_found"] is False
    assert out["import_error"] == ""
