"""G1 tests for src/eve_unleashed/bypass_kit.py.

The kit must be honest in both directions: every suite yields
cases, every case yields a verdict, and a BYPASSED finding names
its input and effect instead of vanishing. The meta-assertion --
the walls currently hold on the sharpest known inputs -- pins
today's score so a regression cannot slip by silently.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import bypass_kit as B


def test_suites_yield_cases():
    total = 0
    for name, cases_fn, try_fn in B._SUITES:
        cases = cases_fn()
        assert len(cases) >= 2, name
        total += len(cases)
    assert total >= 20


def test_every_case_verdicts():
    res = B.run_suite()
    assert res["ran"] >= 20
    assert res["held"] + res["bypassed_count"] == res["ran"]
    assert all(r["verdict"] in ("HELD", "BYPASSED") for r in res["results"])
    assert all("input" in r and "detail" in r for r in res["results"])


def test_known_sharp_inputs_hold():
    res = B.run_suite()
    by_suite = {}
    for r in res["results"]:
        by_suite.setdefault(r["suite"], []).append(r)
    assert all(r["verdict"] == "HELD"
               for r in by_suite["path_escapes"]), [
                   r for r in by_suite["path_escapes"]
                   if r["verdict"] != "HELD"]
    assert all(r["verdict"] == "HELD"
               for r in by_suite["oversized"])
