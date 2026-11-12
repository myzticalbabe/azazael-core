# coding: utf-8
"""tests/test_rule_0b_a_edict.py — the gate-block check must actually FAIL.

A gate that has never been seen to reject anything is not a gate; it is
decoration with a green light. These tests exist to prove the sentinel's
gate-block validator rejects a missing gate, a partial gate, and acceptance
language, and accepts honest output. If someone later loosens the patterns,
this fails.

HISTORY (2026-10-04 repoint): this file pinned the RULE 0B-A five-item
recitation. MANDATES.md reverted 0B-A/0B-B/0B-C/0B-D outright (0B-E era),
so the recitation demand made programmatic writes undecidable. The
validator now checks the LIVE pre-action gate markers (AP-1 scope answer +
GATE DECISION line, RULE 0J/0S) plus the acceptance scan (now cited to
0B-E). Function names, parse keys, and result keys are STABLE identifiers
(they key the chain, governance, and verify scripts) even though the
checked content changed — see validate_rule_0b_a's docstring before
"fixing" the names.

Run:  python -m pytest tests/test_rule_0b_a_edict.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "src" / "SCRIPTS" / "sentinel_gate.py"


def _load():
    if not GATE.exists():  # pragma: no cover
        raise FileNotFoundError(GATE)
    spec = importlib.util.spec_from_file_location("sentinel_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sentinel_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


sg = _load()
validate = sg.validate_rule_0b_a


CLEAN = """
[PRE-ACTION GATE ASSERTION]
- **AP-1 (Scope Check):** user asked for this exact render
- **RULE 0H (Anti-Pattern Scan):** Passed - AST scan: 0 Unicode, 0 fstring backslash
- **GATE DECISION:** PROCEED
"""


def test_clean_block_passes():
    ok, msg = validate(CLEAN)
    assert ok, msg


def test_acceptance_language_fails():
    """Acceptance substituted for description."""
    for phrase in ("this passes", "this clears the rule",
                   "this is good enough", "this is done",
                   "that meets the bar", "i consider this complete"):
        ok, msg = validate(CLEAN + "\nVerdict: %s\n" % phrase)
        assert not ok, "acceptance language not caught: %r" % phrase
        assert "ACCEPTANCE" in msg.upper()


def test_missing_gate_block_fails():
    """A skipped check must not read as a pass."""
    ok, msg = validate("")
    assert not ok
    assert "gate-block" in msg


def test_partial_gate_block_fails():
    ok, msg = validate("[PRE-ACTION GATE ASSERTION]\n"
                       "- **AP-1 (Scope Check):** user asked for this")
    assert not ok
    assert "incomplete" in msg


def test_self_reported_fail_is_honoured():
    """If the assistant's own check says FAIL, the gate says FAIL."""
    ok, msg = validate(CLEAN + "\nGATE DECISION: HALT - redo as stated.\n")
    assert not ok
    assert "NOT" in msg


def test_not_delivery_turn_is_na():
    ok, msg = validate("N/A - read-only probe, nothing delivered")
    assert ok, msg


def test_validator_never_raises_on_junk():
    """A validator that crashes is a validator that gets disabled."""
    for junk in (None, 0, [], {}, 3.14, b"\xff\xfe", "x" * 20000):
        ok, msg = validate(junk)
        assert isinstance(ok, bool)
        assert isinstance(msg, str)


def test_real_gate_format_single_line_passes():
    """The format actually used: AP-1 and GATE DECISION on one line."""
    block = (
        "**AP-1 (Scope Check):** user asked for this exact render. "
        "**RULE 0H:** Passed - AST scan clean. **GATE DECISION:** PROCEED"
    )
    ok, msg = validate(block)
    assert ok, msg


def test_end_to_end_through_the_real_gate():
    """parse_stdin -> SentinelValidator, so the wiring cannot silently rot."""
    gate = (
        "[PRE-ACTION GATE ASSERTION]\n"
        "- **AP-1 (Scope Check):** user asked for this exact render\n"
        "- **RULE 0H (Anti-Pattern Scan):** Passed - AST scan clean\n"
        "- **GATE DECISION:** PROCEED"
    )
    data = sg.parse_stdin(gate)
    v = sg.SentinelValidator(mode="validate", silent=True)
    v.execute_validation(data)
    row = [r for r in v.results if "0B-A" in r["key"]]
    assert row, "0B-A result missing from the validator chain"
    assert row[0]["passed"], row[0]["message"]

    # ...and the same gate with a verdict appended must FAIL. The verdict is
    # parked in GATE DECISION, which is the realistic place to put it, and
    # the whole-block acceptance scan must catch it there.
    data2 = sg.parse_stdin(gate.replace("PROCEED", "PROCEED. This passes."))
    v2 = sg.SentinelValidator(mode="validate", silent=True)
    v2.execute_validation(data2)
    row2 = [r for r in v2.results if "0B-A" in r["key"]]
    assert row2 and not row2[0]["passed"], "verdict in GATE DECISION dodged the scan"
    assert not sg.validate_rule_0b_a(data2.get("rule_0b_a_edict", ""),
                                      full_text="\n".join(
                                          str(v) for v in data2.values() if v))[0]
