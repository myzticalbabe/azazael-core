"""Tests for tools/mutation_probe.py -- the harness that measures detectors.

Three properties are load-bearing and each gets its own test:

1. the clean control really is clean under the canonical scanner, so a detector
   that flags everything cannot earn full marks;
2. the planted house anti-patterns are caught by the rule predicted for them,
   which is what turns "the scanner ran" into "the scanner sees this defect";
3. a recorded KNOWN gap and an unexplained HOLE are different answers -- one is
   reported, the other fails the run -- because a probe that is red forever over
   an accepted gap gets switched off.

The external legs (ruff, pyflakes) are deliberately not driven from pytest: they
are subprocesses with their own rule sets and versions, and a unit test that
fails when a third-party tool renames a code would be measuring the tool, not
this tree. The end-to-end reading including both legs is
`python tools/mutation_probe.py`, recorded in docs/EXTERNAL_LINT_2026-11-04.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import mutation_probe as mp  # noqa: E402  (after the sys.path insert above)


def _scanner():
    scan_file = mp.load_scanner()
    assert scan_file is not None, "canonical scanner could not be imported"
    return scan_file


def _row(name, caught=None, known_gap=None):
    """A scored row shaped exactly like mutation_probe.score() produces."""
    caught = caught or {det: [] for det in mp.DETECTORS}
    return {
        "name": name,
        "path": Path(name + ".py"),
        "expect": {},
        "caught": caught,
        "missed": [det for det in mp.DETECTORS if not caught[det]],
        "wrong": [],
        "predicted": 0,
        "known_gap": known_gap,
    }


ALL_OK = {det: "ok" for det in mp.DETECTORS}


def test_clean_control_produces_no_findings(tmp_path):
    """The negative control: an unfaulted fixture must be invisible."""
    rows = mp.build_fixtures(tmp_path)
    control = rows[0]
    result = _scanner()(str(control["path"]))
    assert result is not None
    assert result.findings == [], (
        "clean control is dirty: %r -- every mutant score would be inflated"
        % (result.findings,)
    )


def test_house_antipatterns_are_caught_by_the_predicted_rule(tmp_path):
    """Each planted defect is found, and found for the reason predicted."""
    rows = mp.build_fixtures(tmp_path)
    scan_file = _scanner()
    failures = []
    for row in rows[1:]:
        predicted = row["expect"].get("scanner")
        if not predicted:
            continue
        result = scan_file(str(row["path"]))
        assert result is not None
        categories = [cat for _line, cat, _snippet in result.findings]
        if predicted not in categories:
            failures.append("%s: wanted %s, got %r"
                            % (row["name"], predicted, categories))
    assert failures == [], "scanner missed planted defects: " + "; ".join(failures)


def test_known_gap_is_reported_without_failing_the_run():
    """An accepted gap is named, and the run stays green."""
    scored = [_row("clean_control"),
              _row("raw_windows_path", known_gap="raw strings escape the pattern")]
    text, code = mp.render(scored, ALL_OK, 1)

    assert code == 0, text
    assert "[KNOWN   ]" in text
    assert "raw strings escape the pattern" in text
    assert "[HOLE    ]" not in text
    assert "0/1 caught" in text, "a known gap must not be counted as a catch"


def test_unexplained_gap_fails_the_run_and_is_named():
    """A gap nobody has explained is a defect in coverage, not a footnote."""
    scored = [_row("clean_control"), _row("mystery")]
    text, code = mp.render(scored, ALL_OK, 1)

    assert code == 1, text
    assert "[HOLE    ]" in text
    assert "mystery" in text
    assert "0/1 caught" in text


def test_gap_that_starts_being_caught_is_reported_closed():
    """A fixed blind spot is announced, so the annotation gets removed."""
    scored = [_row("clean_control"),
              _row("raw_windows_path",
                   caught={"scanner": ["hardcoded_win_path"], "ruff": [],
                           "pyflakes": []},
                   known_gap="raw strings escape the pattern")]
    text, code = mp.render(scored, ALL_OK, 1)

    assert code == 0, text
    assert "[CLOSED  ]" in text
    assert "drop the annotation" in text


def test_dirty_control_makes_the_whole_run_invalid():
    """If the control is dirty no score is meaningful, so the run refuses."""
    dirty = _row("clean_control",
                 caught={"scanner": ["os_system"], "ruff": [], "pyflakes": []})
    text, code = mp.render([dirty, _row("a_mutant")], ALL_OK, 1)

    assert code == 2, text
    assert "INVALID" in text
    assert "control" in text


def test_missing_detector_is_partial_not_covered():
    """A leg that could not run cannot contribute evidence of coverage."""
    states = {"scanner": "ok", "ruff": "ok", "pyflakes": "missing"}
    text, code = mp.render([_row("clean_control"),
                            _row("x", caught={"scanner": ["os_system"],
                                              "ruff": ["F401"], "pyflakes": []})],
                           states, 1)

    assert code == 2, text
    assert "PARTIAL" in text
    assert "BLIND" in text and "pyflakes" in text
