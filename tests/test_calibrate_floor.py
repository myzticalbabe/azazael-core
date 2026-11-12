"""calibrate_floor.py, exercised as a command line instrument.

The floors behind `apply_floor` were set from a measurement, and the canon
rule that owns them ("thresholds are a property of the store") says
RE-MEASURE, never guess. The daemon-live sweep is `floor_measure.py`; this
file is the standalone side of that pair, and it is tested here as an
instrument -- a tool either measures or it doesn't, and a wrong reading from
a calibration tool is the state that made 6 nuisance reboots out of one
number that was recorded once and quoted forever. Each check runs the CLI
the way an operator would and asserts on its EXIT CODE and its NAMED
reason, because a check that would pass on exit 0 has no upside and a check
that fails without saying what is wrong has been tried here before and was
useless at the moment of failure.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "calibrate_floor.py"
FIXTURE = REPO / "tests" / "fixtures" / "pmb_floor_fixture.json"


def run(*args, env_extra=None):
    env = dict(os.environ)
    env.pop("PMB_RECALL_MIN_BEST", None)
    env.pop("PMB_RECALL_FLOOR", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, timeout=60, env=env)
    return proc


# --------------------------------------------------------------------------- #
# PASS state: the shipped floors read back as inside the committed measurement
# --------------------------------------------------------------------------- #

def test_shipped_floors_pass_on_the_committed_measured_gap():
    # Exit 0 with both named floors passing -- the fixture's provenance
    # (measured 2026-10-30) and the shipped 0.89/0.70 must still agree.
    proc = run("--check")
    assert proc.returncode == 0, proc.stdout
    assert "CHECK: PASS" in proc.stdout
    assert "FAIL:" not in proc.stdout


def test_the_proposed_query_floor_is_the_gap_midpoint():
    # The derivation restates how the shipped floor was derived: the midpoint
    # of the committed measured gap [0.836 .. 0.949] is 0.8925.
    proc = run()
    assert proc.returncode == 0, proc.stdout
    assert "proposed PMB_RECALL_MIN_BEST : 0.8925" in proc.stdout
    # row floor from the fixture's recorded stats, p10=0.640 / p25=0.850.
    assert "proposed PMB_RECALL_FLOOR     : 0.745" in proc.stdout


# --------------------------------------------------------------------------- #
# FAIL states: a changed deployment is reported with the reason, never PASSed
# --------------------------------------------------------------------------- #

def test_a_row_floor_at_the_band_edge_fails_and_names_the_bound():
    # 0.85 is exactly the p25 tail bound: the band is half-open, so a floor ON
    # the bound cuts the p25 row -- the boundary stays strict at deployment too.
    proc = run("--check", env_extra={"PMB_RECALL_FLOOR": "0.85"})
    assert proc.returncode == 1, proc.stdout
    assert "FAIL: row floor 0.85 is OUTSIDE its measured band" in proc.stdout
    assert "PMB_RECALL_FLOOR: 0.70" in proc.stdout


def test_a_moved_query_floor_fails_and_says_a_real_query_would_be_dropped():
    # 0.90 is still under the weakest real query (0.949) but it no longer
    # matches the value committed WITH the measurement. Both placements and
    # provenance own a change: the tool reports the disagreeing one.
    proc = run("--check", env_extra={"PMB_RECALL_MIN_BEST": "0.90"})
    assert proc.returncode == 1, proc.stdout
    assert "FAIL: deployed query floor 0.90 disagrees" in proc.stdout


def test_a_missing_input_is_exit_2_never_a_silent_zero():
    # Exit 0 on an unmeasurable floor is the failure that hides: both the
    # live sweep and this tool treat "could not measure" as its own code.
    proc = run("--check", "--file", str(REPO / "no_such_fixture.json"))
    assert proc.returncode == 2
    assert "cannot calibrate" in proc.stderr


def test_a_garbage_input_is_exit_2_with_the_reason_printed():
    # A JSON file that parses to something unexpected is a distinct defect
    # from a missing file, and gets its own named reason on stderr.
    bad = REPO / "tests" / "fixtures" / "calibrate_floor_bad_input.tmp.json"
    bad.write_text("{" + "corrupt", encoding="utf-8")
    try:
        proc = run("--check", "--file", str(bad))
    finally:
        bad.unlink()
    assert proc.returncode == 2
    assert "is missing or not JSON" in proc.stderr
    # The parse error itself is carried, not generalised away: an operator
    # reads which line failed, not merely that something did.
    assert "Expecting property name" in proc.stderr
