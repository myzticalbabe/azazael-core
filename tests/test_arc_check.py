"""Tests for tools/arc_check.py -- the container aggregator.

The aggregator's whole value is refusing to turn an unreadable or contradictory
instrument into a pass, so these tests are mostly negative controls: each one
feeds the aggregator a wing that looks plausible and checks it is graded BLIND
rather than counted.

Nothing here spawns a checker. ``run_wing`` is the only subprocess path and it
is exercised by the live command, not by the suite: a suite that shells out to
three instruments would take longer than the instruments and would fail for
their reasons instead of its own.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "arc_check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("arc_check_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses and typing resolve the module through sys.modules while the
    # body executes; without this registration exec_module raises AttributeError
    # on the module's own __dict__.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


arc = _load_module()


def wing(name="w", verdict=arc.VERDICT_READY, **kwargs):
    base = {
        "wing": name,
        "verdict": verdict,
        "exit": arc.WING_EXIT[verdict],
        "reason": None,
        "errors": [],
        "pins": 1,
        "seconds": 0.01,
        "stdout_sha256": "0" * 64,
    }
    base.update(kwargs)
    return base


class TestPrintedVerdict:
    def test_reads_the_verdict_line(self):
        assert arc.parse_printed_verdict("ledger: 3 pins\nverdict: GAPS\n") == "GAPS"

    def test_ignores_a_verdict_word_inside_prose(self):
        text = "reason: the verdict GAPS was expected\n"
        assert arc.parse_printed_verdict(text) is None

    def test_absent_line_is_none(self):
        assert arc.parse_printed_verdict("") is None
        assert arc.parse_printed_verdict(None) is None


class TestGradeAgrees:
    def test_all_three_readings_agree(self):
        graded = arc.grade_wing(
            "cain", 1, {"verdict": "GAPS", "errors": ["e"], "pins": 8},
            "verdict: GAPS\n", 0.5,
        )
        assert graded["verdict"] == "GAPS"
        assert graded["exit"] == 1
        assert graded["errors"] == ["e"]
        assert graded["pins"] == 8
        assert len(graded["stdout_sha256"]) == 64

    def test_ready_carries_exit_zero(self):
        graded = arc.grade_wing("e", 0, {"verdict": "READY"}, "verdict: READY\n", 0.1)
        assert (graded["verdict"], graded["exit"]) == ("READY", 0)

    def test_report_alone_is_enough_when_stdout_is_empty(self):
        graded = arc.grade_wing("e", 1, {"verdict": "INCOMPLETE"}, "", 0.1)
        assert graded["verdict"] == "INCOMPLETE"


class TestGradeRefusesToGuess:
    def test_no_verdict_anywhere_is_blind(self):
        graded = arc.grade_wing("e", 0, None, "", 0.1)
        assert graded["verdict"] == "BLIND"
        assert "no verdict line" in graded["reason"]

    def test_a_zero_exit_with_no_verdict_is_not_a_pass(self):
        # The defining defect: silence read as success.
        graded = arc.grade_wing("e", 0, None, "Traceback suppressed\n", 0.1)
        assert graded["verdict"] == "BLIND"

    def test_unknown_verdict_is_blind(self):
        graded = arc.grade_wing("e", 0, None, "verdict: PROBABLY\n", 0.1)
        assert graded["verdict"] == "BLIND"
        assert "unknown verdict" in graded["reason"]

    def test_exit_code_contradicting_the_verdict_is_blind(self):
        # Printed READY, exited 1: the instrument disagrees with itself.
        graded = arc.grade_wing("e", 1, None, "verdict: READY\n", 0.1)
        assert graded["verdict"] == "BLIND"
        assert "disagrees with itself" in graded["reason"]

    def test_report_contradicting_the_print_is_blind(self):
        graded = arc.grade_wing(
            "e", 0, {"verdict": "GAPS"}, "verdict: READY\n", 0.1
        )
        assert graded["verdict"] == "BLIND"
        assert "report says GAPS" in graded["reason"]

    def test_a_crash_exit_is_blind_even_with_a_parseable_line(self):
        graded = arc.grade_wing("e", 2, None, "verdict: GAPS\n", 0.1)
        assert graded["verdict"] == "BLIND"


class TestCombine:
    def test_all_ready_is_ready(self):
        assert arc.combine([wing("a"), wing("b")]) == ("READY", 0)

    def test_gaps_outranks_incomplete(self):
        got = arc.combine([wing("a", "GAPS"), wing("b", "INCOMPLETE")])
        assert got == ("GAPS", 1)

    def test_blind_outranks_everything(self):
        got = arc.combine([wing("a", "GAPS"), wing("b", "BLIND")])
        assert got == ("BLIND", 2)

    def test_incomplete_wing_blocks_ready(self):
        assert arc.combine([wing("a"), wing("b", "INCOMPLETE")]) == ("INCOMPLETE", 1)

    def test_a_blocked_wing_caps_the_container_below_ready(self):
        got = arc.combine([wing("a"), wing("b")], blocked=["golgotha"])
        assert got == ("INCOMPLETE", 1)

    def test_an_undeclared_dir_caps_the_container_below_ready(self):
        got = arc.combine([wing("a")], undeclared=["new_wing"])
        assert got == ("INCOMPLETE", 1)

    def test_a_defect_still_sets_the_headline_over_a_blocked_wing(self):
        got = arc.combine([wing("a", "GAPS")], blocked=["golgotha"])
        assert got == ("GAPS", 1)

    def test_no_wings_at_all_is_not_ready(self):
        # An empty container passed everything vacuously; undeclared/golgotha
        # coverage is what stops it reading READY, so assert the contract.
        assert arc.combine([], blocked=["golgotha"]) == ("INCOMPLETE", 1)


class TestDeclaration:
    def test_every_declared_wing_names_a_real_checker_and_ledger(self):
        root = MODULE_PATH.parents[1]
        for spec in arc.WINGS:
            assert (root / spec["checker"]).is_file(), spec["checker"]
            assert (root / spec["ledger"]).is_file(), spec["ledger"]

    def test_declared_wings_are_unique(self):
        names = [w["wing"] for w in arc.WINGS]
        assert len(names) == len(set(names))

    def test_no_wing_is_both_graded_and_blocked(self):
        assert not ({w["wing"] for w in arc.WINGS} & set(arc.BLOCKED_WINGS))

    def test_undeclared_dirs_sees_the_real_tree(self):
        # The live tree declares every directory it holds, so this is empty.
        assert arc.undeclared_dirs() == []

    def test_non_wing_dirs_do_not_overlap_declarations(self):
        assert not (set(arc.NON_WING_DIRS) & ({w["wing"] for w in arc.WINGS} | set(arc.BLOCKED_WINGS)))


class TestStateBlock:
    """The generated README block: placeable, idempotent, and clock-free."""

    def _block(self, **kwargs):
        legs = [
            wing("esquire"),
            wing("cain", "GAPS", errors=["C3 (A) DEFECT: capture missing"]),
        ]
        return arc.render_state_block(
            legs, kwargs.get("provenance"), kwargs.get("blocked", []), []
        )

    def test_block_carries_the_verdict_and_every_leg(self):
        block = self._block()
        assert block.startswith(arc.STATE_START)
        assert block.endswith(arc.STATE_END)
        assert "container: GAPS" in block
        for name in ("esquire", "cain"):
            assert name in block

    def test_block_carries_no_clock_or_head(self):
        # A timestamp would make --check-readme fail one second after a write.
        import re

        block = self._block()
        assert not re.search(r"\d{4}-\d{2}-\d{2}", block)
        assert "HEAD" not in block

    def test_a_folded_evidence_leg_appears_as_its_own_row(self):
        provenance = wing("provenance", "INCOMPLETE", pins=39)
        block = self._block(provenance=provenance)
        assert "provenance" in block
        assert "container: GAPS" in block

    def test_extract_returns_the_block_verbatim(self):
        block = self._block()
        text = "intro\n\n%s\n\noutro\n" % block
        assert arc.extract_state_block(text) == block

    def test_extract_refuses_a_half_present_block(self):
        text = "intro\n%s\n" % arc.STATE_START
        assert arc.extract_state_block(text) is None

    def test_replace_is_a_round_trip_and_idempotent(self):
        block = self._block()
        text = "head\n\n%s\n\ntail\n" % block
        replaced, ok = arc.replace_state_block(text, block)
        assert ok is True
        assert replaced == text
        again, ok_again = arc.replace_state_block(replaced, block)
        assert ok_again is True
        assert again == replaced

    def test_replace_actually_substitutes_a_stale_block(self):
        fresh = self._block()
        stale = "%s\ncontainer: READY\n%s" % (arc.STATE_START, arc.STATE_END)
        text = "head\n%s\ntail\n" % stale
        replaced, ok = arc.replace_state_block(text, fresh)
        assert ok is True
        assert "container: READY" not in replaced
        assert arc.extract_state_block(replaced) == fresh

    def test_replace_declines_when_the_markers_are_absent(self):
        text = "no markers here\n"
        assert arc.replace_state_block(text, "x") == (text, False)
