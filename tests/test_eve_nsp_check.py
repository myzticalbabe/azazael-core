"""G1 test for src/eve_unleashed/nsp_check.py.

The NSP verifier must discriminate: a valid routing trace verifies,
a routing to a known-dead provider falsifies and names no_dead_provider.
A test that cannot fail is a rumour; this one fails if the Z3 leg goes
vacuous again (the 2026-11-06 defect: every real spec falsified on
valid traces because the parser could not read SMT-LIB).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.nsp_check import verify_router_trace

COVERED = ["valid_query_features", "valid_provider_selection",
           "no_dead_provider"]


def _trace(selected_idx, dead_idx):
    return [{"event_type": "route", "symbolic_state": {
        "query_norm": 5.0, "selected_idx": selected_idx,
        "num_providers": 2, "dead_provider_idx": dead_idx}}]


def test_valid_routing_verifies():
    verdict, detail = verify_router_trace(_trace(1, 0), COVERED)
    assert verdict == "verified", detail
    assert "no_dead_provider=verified" in detail, detail


def test_routing_to_dead_provider_falsifies_by_name():
    verdict, detail = verify_router_trace(_trace(0, 0), COVERED)
    assert verdict == "falsified", detail
    assert "no_dead_provider=falsified" in detail, detail
