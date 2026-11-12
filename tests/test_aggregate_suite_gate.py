"""Tests for `tools/aggregate_suite_gate.py` — the parsing and diff logic.

The gate exists because a per-file runner cannot see isolation defects. These
tests pin its DECISIONS (new id -> block; gone id -> report; timeout -> block)
and its parsing, without paying for a 20-minute aggregate run: the runner is
injected via monkeypatch, and the parser is exercised on real pytest output.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import aggregate_suite_gate as gate  # noqa: E402


SAMPLE = """\
................................s....................................... [ 50%]
F.........................................E............................ [100%]
=========================== short test summary info ===========================
FAILED tests/test_a.py::test_one - AssertionError: assert 1 == 2
FAILED tests/test_a.py::TestB::test_two - AttributeError: 'NoneType'
ERROR tests/test_c.py::test_three - PermissionError: CRITICAL: Unauthorized
2 failed, 71 passed, 1 error, 1 skipped in 12.34s
"""


def test_parse_extracts_failures_errors_and_counts():
    parsed = gate.parse_pytest_output(SAMPLE)
    assert parsed["failed_ids"] == ["tests/test_a.py::TestB::test_two",
                                    "tests/test_a.py::test_one"]
    assert parsed["error_ids"] == ["tests/test_c.py::test_three"]
    assert parsed["counts"] == {"failed": 2, "passed": 71, "errors": 1,
                               "skipped": 1}
    assert "2 failed" in parsed["summary_line"]


def test_ids_ignore_the_failure_message_so_only_nodeids_are_compared():
    """A changed assertion message must not read as a new failure."""
    later = SAMPLE.replace("AssertionError: assert 1 == 2",
                           "AssertionError: assert 'x' == 'y'")
    assert gate.all_ids(gate.parse_pytest_output(SAMPLE)) == \
        gate.all_ids(gate.parse_pytest_output(later))


def test_a_new_id_blocks_and_a_disappeared_id_only_reports():
    parsed = gate.parse_pytest_output(SAMPLE)
    delta = gate.diff_against_baseline(
        parsed, ["tests/test_a.py::test_one", "tests/test_gone.py::test_old"])
    assert delta["new"] == ["tests/test_a.py::TestB::test_two",
                            "tests/test_c.py::test_three"]
    assert delta["fixed"] == ["tests/test_gone.py::test_old"]
    assert delta["unchanged"] == ["tests/test_a.py::test_one"]


def test_check_fails_on_a_new_id_and_passes_when_nothing_is_new(
        tmp_path, monkeypatch, capsys):
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(gate, "BASELINE", baseline)
    monkeypatch.setattr(gate, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (1, SAMPLE))

    assert gate.main(["--baseline"]) == 0
    recorded = json.loads(baseline.read_text(encoding="utf-8"))
    assert recorded["failed_ids"] == ["tests/test_a.py::TestB::test_two",
                                      "tests/test_a.py::test_one"]

    # Same suite again: known ids, so the gate passes.
    assert gate.main(["--check"]) == 0
    assert "OK: no new aggregate failures" in capsys.readouterr().out

    # One MORE failure than the baseline: the gate blocks.
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (
        1, SAMPLE + "FAILED tests/test_z.py::test_new - AssertionError\n"))
    assert gate.main(["--check"]) == 1
    assert "NEW FAILURE: tests/test_z.py::test_new" in capsys.readouterr().out


def test_a_timeout_is_a_failure_not_a_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "baseline.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (124, "TIMEOUT"))
    assert gate.main(["--check"]) == 1
    assert "TIMEOUT" in capsys.readouterr().err


def test_missing_baseline_refuses_to_reassure(tmp_path, monkeypatch, capsys):
    """No baseline + no recorded failures must not read as 'all clear'."""
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "absent.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (0, "500 passed in 9s"))
    assert gate.main(["--check"]) == 1
    assert "no baseline" in capsys.readouterr().err


def test_baseline_filters_to_the_known_set_with_a_real_subset():
    baseline = gate.baseline_payload(gate.parse_pytest_output(SAMPLE))
    assert baseline["counts"]["failed"] == 2
    assert len(baseline["error_ids"]) == 1
    assert "recorded_at" in baseline


def test_the_isolation_profile_pins_its_own_files_and_baseline(
        tmp_path, monkeypatch):
    """CI must not compare against the full-suite baseline.

    Two different environments (no torch in CI, the full stack locally) would
    otherwise report each other's failures as regressions. The profile is the
    one place that mapping is written down.

    The mapping is asserted through `evidence_path` rather than by letting the
    run write at the default location: a test that writes a tracked artifact is
    how the isolation report came to hold `1 passed in 1s` for a real profile.
    """
    assert gate.evidence_path("baseline", "isolation", False).name == \
        "aggregate_isolation_baseline.json"
    assert gate.evidence_path("report", "isolation", False).name == \
        "aggregate_isolation_report.json"
    assert gate.evidence_path("report", "full", False).name == \
        "aggregate_suite_report.json"
    assert gate.evidence_path("report", "isolation", True).name == \
        "aggregate_isolation_report.partial.json"

    seen = {}

    def fake_run(select, *a, **k):
        seen["select"] = select
        return 0, "500 passed in 9s"

    monkeypatch.setattr(gate, "run_pytest", fake_run)
    assert gate.main(["--report", "--profile", "isolation",
                      "--report-file", str(tmp_path / "r.json"),
                      "--baseline-file", str(tmp_path / "b.json")]) == 0
    assert seen["select"] == list(gate.ISOLATION_PROFILE)
    assert gate.BASELINE == tmp_path / "b.json"
    assert gate.REPORT == tmp_path / "r.json"


def test_an_explicit_only_still_wins_over_the_profile(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(gate, "run_pytest",
                        lambda s, *a, **k: (seen.update(select=s),
                                            (0, "1 passed in 1s"))[1])
    assert gate.main(["--report", "--profile", "isolation",
                      "--only", "tests/test_x.py",
                      "--report-file", str(tmp_path / "r.json")]) == 0
    assert seen["select"] == ["tests/test_x.py"]
    assert gate.REPORT == tmp_path / "r.json"


@pytest.fixture
def profile_logs(tmp_path, monkeypatch):
    """Redirect a profile's evidence files into tmp_path.

    The gate's isolation paths are fixed names under `logs/`, and a test that
    lets a run reach them replaces a real profile's baseline with fake counts --
    which is the defect these tests exist for, so they must not re-create it.
    """
    monkeypatch.setattr(gate, "default_evidence",
                        lambda kind, profile: tmp_path / gate.DEFAULT_EVIDENCE[
                            profile][kind])
    return tmp_path


def test_a_narrowed_run_cannot_take_the_profile_report(profile_logs, monkeypatch,
                                                       capsys):
    tmp_path = profile_logs
    """A subset run must not answer for the profile it did not run.

    Measured 2026-09-22: a one-file smoke test of the stall watchdog replaced
    `logs/aggregate_isolation_report.json` with `1 passed in 1s`, seven of the
    eight profile files never executed. The next `--check` would then have read
    a baseline from a run that did not happen.
    """
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (0, "1 passed in 1s"))
    assert gate.main(["--report", "--profile", "isolation",
                      "--only", "tests/test_aggregate_suite_gate.py"]) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "aggregate_isolation_report.partial.json").exists(), \
        "a narrowed run must not write the profile's report"
    assert not (tmp_path / "aggregate_isolation_report.json").exists()
    assert not (tmp_path / "aggregate_isolation_baseline.json").exists()
    assert "a narrowed run is not" in out
    assert "subset of isolation" in out


def test_narrowing_is_membership_not_equality(profile_logs, monkeypatch):
    """Passing the profile's own files in another order is still the profile."""
    tmp_path = profile_logs
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (0, "500 passed in 9s"))
    reordered = list(reversed(gate.ISOLATION_PROFILE))
    assert gate.main(["--report", "--profile", "isolation",
                      "--only"] + reordered) == 0
    assert (tmp_path / "aggregate_isolation_report.json").exists()


def test_a_narrowed_check_refuses_rather_than_borrowing_a_baseline(
        profile_logs, monkeypatch, capsys):
    tmp_path = profile_logs
    """The failure must be a refusal, not a vacuous pass.

    Comparing a subset against the profile's baseline would report every
    unrun file as "no longer failing" -- the gate reading green because it
    looked at less.
    """
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (0, "1 passed in 1s"))
    assert gate.main(["--check", "--profile", "isolation",
                      "--only", "tests/test_aggregate_suite_gate.py"]) == 1
    assert "no baseline" in capsys.readouterr().err
    assert not list(tmp_path.glob("aggregate_isolation_report*.json")) or \
        not (tmp_path / "aggregate_isolation_report.json").exists()


def test_the_raw_output_is_persisted_so_a_failure_can_be_diagnosed(
        tmp_path, monkeypatch, capsys):
    """The report stores nodeids; a nodeid names a failure, it does not explain it.

    Measured cost of NOT doing this: five aggregate runs whose ~50 failures could
    be counted but not read, because the only copy of the assertion message was
    in a subprocess pipe that the gate had already closed.
    """
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "b.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (1, SAMPLE))
    assert gate.main(["--report"]) == 0
    raw = tmp_path / "report.raw.txt"
    assert raw.exists() and "AssertionError: assert 1 == 2" in raw.read_text(
        encoding="utf-8")
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["raw_output"] == "report.raw.txt"
    assert payload["profile"] == "full"
    assert payload["targets"] == ["tests/"]
    assert "raw output:" in capsys.readouterr().out


def test_a_streamed_run_can_be_parsed_instead_of_re_run(
        tmp_path, monkeypatch, capsys):
    """The expensive step must not have to be repeated to read its own evidence.

    A run whose progress needs watching has to stream its output somewhere other
    than the gate's pipe; without this option the only way to gate on it would be
    to pay the full wall-clock again.
    """
    streamed = tmp_path / "stream.txt"
    streamed.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "b.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "report.json")

    def must_not_run(*a, **k):
        raise AssertionError("run_pytest was called for a --parse-from run")

    monkeypatch.setattr(gate, "run_pytest", must_not_run)
    assert gate.main(["--report", "--parse-from", str(streamed)]) == 0
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["failed_ids"] == ["tests/test_a.py::TestB::test_two",
                                     "tests/test_a.py::test_one"]
    assert payload["error_ids"] == ["tests/test_c.py::test_three"]
    assert "parsed from:" in capsys.readouterr().out


def test_parse_from_a_missing_file_fails_instead_of_passing(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "b.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "report.json")
    assert gate.main(["--check", "--parse-from",
                      str(tmp_path / "absent.txt")]) == 1
    assert "cannot read raw output" in capsys.readouterr().err


def test_a_stalled_run_is_killed_and_named(tmp_path):
    """A frozen output pipe must become a NAMED failure, not a long wait.

    Measured 2026-09-22: a run sat at 9% for over ten minutes with a byte-frozen
    output file while the files in that region passed in 75 s alone. The only way
    to learn that was to watch the file and kill it by hand -- which is the reason
    this watchdog exists rather than a wall-clock cap alone. The child below is
    real: it prints one nodeid and then blocks.
    """
    raw = tmp_path / "stall.raw.txt"
    cmd = [sys.executable, "-c",
           "import sys, time; print('tests/test_x.py::test_a'); "
           "sys.stdout.flush(); time.sleep(30)"]
    rc, out = gate._stream_command(cmd, timeout=60, raw_path=raw,
                                   stall_seconds=1.5)
    assert rc == 126, "a stalled run must not report a verdict code"
    assert "STALL" in out
    assert "tests/test_x.py::test_a" in out
    written = raw.read_text(encoding="utf-8")
    assert "STALL" in written, "the reason must survive the kill, in the raw file"
    assert "test_a" in written


def test_a_hard_timeout_is_distinct_from_a_stall():
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    rc, out = gate._stream_command(cmd, timeout=1, raw_path=None,
                                   stall_seconds=0)
    assert rc == 124
    assert "TIMEOUT" in out


def test_a_stall_exits_nonzero_and_reports_the_last_named_test(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "b.json")
    monkeypatch.setattr(gate, "REPORT", tmp_path / "r.json")
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (
        126, "....\ntests/test_y.py::test_c\nSTALL: no output for 240s after "
             "tests/test_y.py::test_c\n"))
    assert gate.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert "STALL" in err
    assert "last named test: tests/test_y.py::test_c" in err


def test_the_isolation_profile_names_only_files_that_exist():
    """A renamed test file must break the gate, not silently shrink it."""
    missing = [p for p in gate.ISOLATION_PROFILE if not (gate.REPO / p).exists()]
    assert not missing, "isolation profile names absent files: %s" % missing


def test_the_isolation_profile_stays_dependency_light():
    """Rule 2 of the selection rule, enforced rather than documented.

    A heavy import makes the profile's baseline environment-specific, which is
    exactly the failure the profile exists to avoid. Checked on the AST, not on
    the text: `test_mesh_wire_or_cut.py` *discusses* torch in a docstring about
    a lazy proxy because it reads `nerve.py` as data, and a substring test
    cannot tell a discussion of a module from an import of one.
    """
    heavy = {"torch", "stable_baselines3", "transformers", "sklearn",
             "tensorflow", "gymnasium"}
    offenders = {}
    for rel in gate.ISOLATION_PROFILE:
        tree = ast.parse((gate.REPO / rel).read_text(encoding="utf-8",
                                                    errors="replace"))
        hits = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hits |= {a.name.split(".")[0] for a in node.names} & heavy
            elif isinstance(node, ast.ImportFrom) and node.module:
                hits |= {node.module.split(".")[0]} & heavy
        if hits:
            offenders[rel] = sorted(hits)
    assert not offenders, (
        "isolation profile must stay installable without the ML stack: %s"
        % offenders)


def test_output_with_no_summary_line_is_not_a_verdict_and_leaves_the_report(
        tmp_path, monkeypatch, capsys):
    """A run that produced nothing must not be stored where a clean run goes.

    Measured 2026-10-15: `logs/aggregate_suite_report.json` was replaced by an
    all-zero record with `summary_line` "" -- 3698 passed / 2 failed with
    failed_ids became 0/0/0/0, and nothing in the file said the run had failed
    to produce a verdict at all. `parse_pytest_output` leaves every count at
    zero for input with no summary line, so the guard has to sit at the write
    site; this test pins both halves -- the parse that makes the zeros, and the
    refusal to store them.
    """
    parsed = gate.parse_pytest_output("")
    assert parsed["summary_line"] == ""
    assert parsed["counts"] == {"failed": 0, "passed": 0, "errors": 0,
                                "skipped": 0}

    sentinel = {"recorded_at": "2026-09-22T16:03:26+00:00",
                "counts": {"failed": 2, "passed": 3698, "errors": 0,
                           "skipped": 38}}
    report = tmp_path / "aggregate_report.json"
    report.write_text(json.dumps(sentinel), encoding="utf-8")
    monkeypatch.setattr(gate, "REPORT", report)

    # The path that actually did the damage: pytest exited 0 having printed no
    # summary line, which used to be written out as a clean-looking report.
    monkeypatch.setattr(gate, "run_pytest", lambda *a, **k: (0, ""))
    assert gate.main([]) == 1, "a run with no summary line must not report 0"
    assert "no pytest summary line" in capsys.readouterr().err
    assert json.loads(report.read_text(encoding="utf-8")) == sentinel, (
        "a run with no verdict must not overwrite recorded evidence with an "
        "all-zero report")

    # And the same refusal on the saved-output path.
    empty = tmp_path / "empty.raw.txt"
    empty.write_text("", encoding="utf-8")
    assert gate.main(["--parse-from", str(empty)]) == 1
    assert json.loads(report.read_text(encoding="utf-8")) == sentinel
