# coding: utf-8
"""Tests for `tools/claim_guard.py` — the RULE 0B-E claim guard.

Pins the #958 Kilo requirement: negative controls (the guard must FIRE on
the guard's own CASES corpus plus the live probe-1 refusal message) and
false-positive controls (it must STAY SILENT on the CLEAN corpus plus the
live probe-2 pass message). See Engram #958 / #1134.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import claim_guard as cg

# Live probe-1 message (refused pre-commit, exit 1, held-sha b7ebf4f2).
PROBE_BLOCKED = "chore: this is done, nothing was modified, this passes"
# Live probe-2 message (committed clean as 8a88293c, then removed per scope).
PROBE_PASSED = "test: pytest.ini -p no:cacheprovider to reclaim 12s per run"


class TestNegativeControls:
    def test_corpus_cases_fire_expected_kinds(self):
        assert cg.CASES, "guard corpus must not be empty"
        for report, response, expected in cg.CASES:
            kinds = [f.kind for f in cg.scan(response, report)]
            # Same pass criterion as the guard's own selftest: expected
            # kinds as a SUBSET (CASES[4] lists SELF_ASSESSMENT twice;
            # scan emits it once — set semantics is the defined contract).
            assert set(expected) <= set(kinds), (response, kinds, expected)

    def test_live_blocked_probe_fires_three_findings(self):
        kinds = [f.kind for f in cg.scan(PROBE_BLOCKED)]
        assert kinds == ["UNQUALIFIED_ABSENCE",
                         "SELF_ASSESSMENT", "SELF_ASSESSMENT"]

    def test_empty_text_fires_nothing(self):
        assert cg.scan("") == []


class TestFalsePositiveControls:
    def test_corpus_clean_passes_silent(self):
        assert cg.CLEAN, "guard clean corpus must not be empty"
        for report, response in cg.CLEAN:
            findings = cg.scan(response, report)
            assert findings == [], (response, [str(f) for f in findings])

    def test_live_passed_probe_stays_silent(self):
        assert cg.scan(PROBE_PASSED) == []

    def test_evidence_shaped_fix_message_stays_silent(self):
        msg = ("fix: learning_grpo comment now states reward derives from "
               "success_rate alone, duration emitted not subtracted (#1037)")
        assert cg.scan(msg) == []
