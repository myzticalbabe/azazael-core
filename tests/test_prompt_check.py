#!/usr/bin/env python3
# coding: utf-8
"""Controls for tools/prompt_check.py -- the gate on the instruction files.

The gate itself has three failure modes worth testing, and each has cost someone a
session somewhere in this tree:

  1. it passes everything (a sentence-shaped check, no better than the prose it
     audits) -- so every rule is exercised on a fixture it MUST reject;
  2. it fails everything (a gate switched off within a day) -- so every rule is
     also exercised on a fixture it MUST accept;
  3. it checks the wrong thing -- a dated stale count reading as "verified",
     which is exactly how 5,380 tests in a prompt sat three days behind a 6,277
     collection under a heading that said "measured 2026-10-30".

Run: python -m pytest tests/test_prompt_check.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import prompt_check  # noqa: E402  (after the sys.path insert, by design)

HEAD = prompt_check.START_MARKER + "\n\n"
READING = {"collected_tests": 6277, "skills": 204}


def judge(text, **kwargs):
    kwargs.setdefault("measured", READING)
    return prompt_check.check_text(text, repo=REPO, **kwargs)


# --------------------------------------------------------------------------- #
# P1 -- the contract is present and near the top
# --------------------------------------------------------------------------- #

def test_clean_prompt_is_accepted():
    assert judge(HEAD + "python tools/golden_arrow.py\n") == []


def test_missing_contract_is_refused():
    problems = judge("python tools/golden_arrow.py\n")
    assert problems and problems[0].startswith("P1")


def test_contract_below_the_window_is_refused():
    buried = ("x" * (prompt_check.TOP_WINDOW + 1)) + prompt_check.START_MARKER
    assert any(p.startswith("P1") for p in judge(buried))


def test_contract_at_the_very_top_is_accepted():
    assert judge(HEAD + "nothing to check\n") == []


# --------------------------------------------------------------------------- #
# P2 -- backticked addresses resolve
# --------------------------------------------------------------------------- #

def test_dead_path_is_refused():
    problems = judge(HEAD + "run `tools/no_such_tool.py` first\n")
    assert [p for p in problems if p.startswith("P2")]


def test_live_path_is_accepted():
    assert judge(HEAD + "run `tools/golden_arrow.py`\n") == []


def test_path_relative_to_the_repo_parent_is_accepted():
    # The Kilo prompt addresses the tree as `azazael/...` because its clients run
    # with cwd=C:/Azazael. A resolver that only knew the repo root reported dozens
    # of working commands as dead on the first run of this gate.
    assert judge(HEAD + "see `azazael/MANDATES.md`\n") == []


def test_bare_filename_is_a_mention_not_an_address():
    # "a skill is a directory holding `SKILL.md`" names no path; resolving it by
    # basename search would pass a wrong path as easily as a right one.
    assert judge(HEAD + "a skill holds `SKILL.md`\n") == []


def test_output_path_may_not_exist_yet():
    # `logs/` holds artefacts a command writes; absence before the first run is
    # correct, not a defect.
    assert judge(HEAD + "writes `logs/prompt_check_never_written.json`\n") == []


def test_remote_lane_path_is_not_called_dead():
    # /root/... and BCC-REINVESTIGATION/ live on the Kali lane; a Windows run
    # cannot stat them and must say so rather than fail the beam prompt.
    assert judge(HEAD + "owned by `/root/BCC-REINVESTIGATION/AGENTS.md`\n"
                        "and `BCC-REINVESTIGATION/AGENTS.md`\n") == []


# --------------------------------------------------------------------------- #
# P3 -- counts are current, or explicitly historical
# --------------------------------------------------------------------------- #

def test_stale_count_is_refused():
    problems = judge(HEAD + "5,380 tests exist.\n")
    assert any(p.startswith("P3") for p in problems)


def test_current_count_is_accepted():
    assert judge(HEAD + "6,277 tests exist.\n") == []


def test_count_within_tolerance_is_accepted():
    # One parametrised test moves a collection number; a gate that failed on ±1
    # would be switched off within a day.
    assert judge(HEAD + "6,100 tests exist.\n") == []


def test_dated_stale_count_is_still_refused():
    # THE 2026-11-02 DEFECT: dated, defensible, 16% low, read by a fresh session
    # as the size of the suite it was about to trust. A date is not a licence.
    problems = judge(HEAD + "5,380 tests exist (measured 2026-10-30).\n")
    assert any(p.startswith("P3") for p in problems)


def test_historical_count_is_accepted():
    assert judge(HEAD + "5,380 tests existed (historical: the suite has grown).\n") == []


def test_section_caption_dates_its_table():
    # A caption reading "measured 2026-10-31" over forty rows is one dating act.
    text = HEAD + "## Table (measured 2026-10-31)\n\n| 12,000 shards |\n"
    assert judge(text) == []


def test_undated_number_with_no_reading_is_refused():
    problems = judge(HEAD + "the estate holds 1,395 shards.\n")
    assert any(p.startswith("P3") for p in problems)


def test_thousands_separator_is_parsed_whole():
    # The first version read "6,277" as 277 and failed every honest count while
    # passing a stale one that ended in the recorded digits.
    problems = judge(HEAD + "6,277 tests exist.\n")
    assert problems == []
    assert prompt_check._COUNT.search("6,277 tests exist").group(1) == "6,277"


# --------------------------------------------------------------------------- #
# P4 -- the newest handoff's commit resolves
# --------------------------------------------------------------------------- #

def test_handoff_with_unresolvable_commit_is_refused(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "HANDOFF_x.md").write_text("HEAD 0123456 stands.\n", encoding="utf-8")
    problems = prompt_check.check_text(HEAD, repo=tmp_path, measured=READING,
                                       sha_resolves=lambda sha: False)
    assert any(p.startswith("P4") for p in problems)


def test_handoff_with_resolving_commit_is_accepted(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "HANDOFF_x.md").write_text("HEAD 0123456 stands.\n", encoding="utf-8")
    problems = prompt_check.check_text(HEAD, repo=tmp_path, measured=READING,
                                       sha_resolves=lambda sha: True)
    assert problems == []


def test_handoff_naming_no_commit_is_refused(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "HANDOFF_x.md").write_text("we did some things\n", encoding="utf-8")
    problems = prompt_check.check_text(HEAD, repo=tmp_path, measured=READING,
                                       sha_resolves=lambda sha: True)
    assert any(p.startswith("P4") for p in problems)


# --------------------------------------------------------------------------- #
# The gate's own controls, as the registry runs them
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# The fresh-host path: a reading is RECORDED, never assumed and never waived
# --------------------------------------------------------------------------- #

FAKE_TOOL = '''\nimport json, pathlib, sys\nroot = pathlib.Path(__file__).resolve().parents[1]\nstate = root / "state"\nstate.mkdir(exist_ok=True)\nif %r:\n    print("BLIND: collection could not run", file=sys.stderr)\n    sys.exit(2)\n(state / "head.json").write_text(json.dumps({"schema": "head-state/1",\n    "collected_tests": %d, "skills": %d, "head": "deadbee", "short": "deadbee"}),\n    encoding="utf-8")\nprint("wrote state/head.json")\n'''


def _scratch_repo(tmp_path, *, fail: bool, tests: int = 4242, skills: int = 7):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "head_state.py").write_text(
        FAKE_TOOL % (fail, tests, skills), encoding="utf-8")
    return tmp_path


def test_missing_reading_is_recorded_then_used(tmp_path):
    # THE 2026-11-02 finding: state/head.json is gitignored, so a fresh host was
    # Blind at exit 2 and the commit stanza passed it loud. The gate now records a
    # reading through the tool that owns the format.
    repo = _scratch_repo(tmp_path, fail=False)
    measured = prompt_check.readings(repo)
    assert measured["collected_tests"] == 4242
    assert (repo / "state" / "head.json").is_file()


def test_a_recorded_reading_is_judged_against_immediately(tmp_path):
    repo = _scratch_repo(tmp_path, fail=False, tests=4242)
    measured = prompt_check.readings(repo)
    assert prompt_check.check_text(HEAD + "4,242 tests exist.\n", repo=repo,
                                   measured=measured) == []
    assert prompt_check.check_text(HEAD + "5,380 tests exist.\n", repo=repo,
                                   measured=measured)


def test_recording_failure_is_blind_not_a_pass(tmp_path):
    repo = _scratch_repo(tmp_path, fail=True)
    with pytest.raises(prompt_check.Blind):
        prompt_check.readings(repo)


def test_no_recorder_and_no_reading_is_blind(tmp_path):
    (tmp_path / "tools").mkdir()
    with pytest.raises(prompt_check.Blind):
        prompt_check.readings(tmp_path)


def test_unreadable_reading_is_blind_not_empty(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "head.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(prompt_check.Blind):
        prompt_check.readings(tmp_path)


def test_built_in_controls_pass():
    """`prompt-check-controls`: the registry executes this via preflight_all."""
    assert prompt_check.selftest() == 0


def test_live_gate_runs_and_is_clean_when_prompts_exist():
    """The live verdict must be reachable, and must be reachable as a VERDICT.

    A Blind exit is not a pass: on a host with no lane prompt this test says so by
    skipping, and the pre-commit stanza treats exit 2 the same way.
    """
    if not prompt_check.lane_prompts():
        pytest.skip("no lane prompt on this host -- the gate has nothing to judge")
    assert prompt_check.live(json_out=False) == 0
