"""Tests for tools/esquire_pins.py.

The checker's contract is that every input produces a printed verdict and an
exit code: READY 0, GAPS 1, INCOMPLETE 1, BLIND 2. A traceback also exits 1,
which is indistinguishable from GAPS, so the tests below drive every malformed
shape the ledger or the instrument can take and assert that none of them
raises. Each test perturbs exactly one thing; the unperturbed fixture passes.
"""

from __future__ import annotations

import json

import pytest

from tools.esquire_pins import (
    DEFAULT_LEDGER,
    Blinded,
    check,
    exit_code_for,
    gate_table,
    is_stale,
    load_instrument_gates,
    load_ledger,
    main,
    staleness,
    verdict,
    VERDICT_BLIND,
    VERDICT_GAPS,
    VERDICT_INCOMPLETE,
    VERDICT_READY,
)

GATE_AUTHORITY = "FRE 901(a)"
TEXT = "evidence sufficient to support a finding that the item is what it claims"


def _pin(**overrides):
    """A pin that satisfies every requirement for gate G1 at tier A."""
    pin = {
        "id": "P1",
        "demand": "the item offered must be shown to be what it claims",
        "authority": GATE_AUTHORITY,
        "cite": "Fed. R. Evid. 901(a)",
        "text": TEXT,
        "tier": "A",
        "verified": "2026-11-04 read at source, HTTP 200",
        "source": "https://example.invalid/rule_901",
        "satisfied_by": ["the process record"],
        "covers": ["G1"],
        "does_not_prove": "relevance, reliability, or weight",
    }
    pin.update(overrides)
    return pin


def _ledger(pins=None, gaps=None):
    return {
        "pins": [_pin()] if pins is None else pins,
        "declared_gaps": [] if gaps is None else gaps,
    }


def _gates(*ids):
    ids = ids or ("G1",)
    return [{"id": gid, "title": "gate " + gid, "authority": GATE_AUTHORITY} for gid in ids]


def _instrument(tmp_path, body):
    path = tmp_path / "instrument_under_test.py"
    path.write_text(body, encoding="utf-8")
    return path


# ── the real ledger ────────────────────────────────────────────────────────


def test_real_ledger_is_ready():
    ledger = load_ledger(DEFAULT_LEDGER)
    gates = load_instrument_gates()
    errors, warnings = check(ledger, gates)
    assert errors == []
    assert verdict(errors, staleness(ledger["pins"], gates)) == VERDICT_READY
    assert exit_code_for(VERDICT_READY) == 0
    # advisory notes exist in the real ledger and must not change the verdict
    assert isinstance(warnings, list)


def test_real_ledger_gives_every_implemented_gate_proof_support():
    ledger = load_ledger(DEFAULT_LEDGER)
    rows = gate_table(ledger, load_instrument_gates())
    implemented = [r for r in rows if "declared" not in r["title"]]
    assert implemented, "the instrument exposes no gates"
    assert [r["gate"] for r in implemented if not r["proof_pins"]] == []


def test_real_ledger_declares_g11_with_pins():
    ledger = load_ledger(DEFAULT_LEDGER)
    gaps = {g["gate"]: g for g in ledger["declared_gaps"]}
    assert "G11" in gaps
    assert gaps["G11"]["pins"]
    rows = gate_table(ledger, load_instrument_gates())
    row = next(r for r in rows if r["gate"] == "G11")
    assert row["title"].endswith("[declared, not implemented]")


# ── ledger defects: each makes GAPS, none raises ───────────────────────────


def test_duplicate_pin_id_is_an_error():
    errors, _ = check(_ledger([_pin(), _pin()]), _gates())
    assert any("duplicate id" in e for e in errors)
    assert verdict(errors, []) == VERDICT_GAPS


def test_gate_without_pins_is_an_error():
    errors, _ = check(_ledger(), _gates("G1", "G2"))
    assert any(e.startswith("G2") and "has no pin" in e for e in errors)


def test_tier_a_text_that_says_it_was_not_read_is_an_error():
    errors, _ = check(_ledger([_pin(text="not quoted at source; see the report")]), _gates())
    assert any("text says it was not read" in e for e in errors)


def test_tier_a_text_under_forty_characters_is_an_error():
    errors, _ = check(_ledger([_pin(text="too short")]), _gates())
    assert any("under 40 characters" in e for e in errors)


def test_tier_a_without_source_is_an_error():
    errors, _ = check(_ledger([_pin(source="   ")]), _gates())
    assert any("names no source" in e for e in errors)


def test_tier_a_without_authority_is_an_error():
    errors, _ = check(_ledger([_pin(authority="")]), _gates())
    assert any("names no authority" in e for e in errors)


def test_pin_covering_an_unknown_gate_is_an_error():
    errors, _ = check(_ledger([_pin(covers=["G9"])]), _gates())
    assert any("covers unknown gate" in e for e in errors)


def test_missing_required_key_is_an_error_not_a_crash():
    pin = _pin()
    del pin["does_not_prove"]
    errors, _ = check(_ledger([pin]), _gates())
    assert any("missing required keys" in e and "does_not_prove" in e for e in errors)


def test_unknown_tier_is_an_error():
    errors, _ = check(_ledger([_pin(tier="Z")]), _gates())
    assert any("unknown tier" in e for e in errors)


def test_pin_answering_no_gate_is_an_error():
    errors, _ = check(_ledger([_pin(covers=[])]), _gates())
    assert any("answers no gate" in e for e in errors)


def test_pin_answering_no_gate_may_be_context():
    context = _pin(id="P2", covers=[], context="background only")
    errors, _ = check(_ledger([_pin(), context]), _gates())
    assert errors == []


def test_gate_supported_only_by_a_lower_tier_is_an_error():
    errors, _ = check(_ledger([_pin(tier="C")]), _gates())
    assert any("supported only by tiers C" in e for e in errors)


def test_non_dict_pin_is_an_error_not_a_crash():
    errors, _ = check(_ledger([_pin(), "not a pin"]), _gates())
    assert any("pins[1] is not an object" in e for e in errors)


def test_non_list_declared_gaps_is_an_error_not_a_crash():
    errors, _ = check({"pins": [_pin()], "declared_gaps": {"G11": {}}}, _gates())
    assert any("declared_gaps is dict, not a list" in e for e in errors)


def test_declared_gap_naming_no_pins_is_an_error():
    errors, _ = check(_ledger(gaps=[{"gate": "G11", "title": "t", "pins": []}]), _gates())
    assert any("G11 names no pins" in e for e in errors)


def test_declared_gap_naming_a_missing_pin_is_an_error():
    errors, _ = check(_ledger(gaps=[{"gate": "G11", "title": "t", "pins": ["P99"]}]), _gates())
    assert any("names missing pin P99" in e for e in errors)


def test_declared_gap_pin_below_tier_c_is_an_error():
    pin = _pin(covers=[], tier="UNVERIFIABLE-HERE", pending_gate="G11")
    gaps = [{"gate": "G11", "title": "t", "pins": ["P1"]}]
    errors, _ = check(_ledger([pin], gaps), _gates())
    assert any("below C" in e for e in errors)


def test_pending_gate_and_covers_together_is_an_error():
    pin = _pin(pending_gate="G11")
    gaps = [{"gate": "G11", "title": "t", "pins": ["P1"]}]
    errors, _ = check(_ledger([pin], gaps), _gates())
    assert any("also claims covers" in e for e in errors)


def test_pending_gate_must_be_a_declared_gap():
    errors, _ = check(_ledger([_pin(covers=[], pending_gate="G99")]), _gates())
    assert any("not a declared gap" in e for e in errors)


def test_gates_entry_without_id_is_reported_not_raised():
    errors, _ = check(_ledger(), [{"title": "anonymous", "authority": GATE_AUTHORITY}])
    assert any("is not a named gate" in e for e in errors)


def test_advisory_citation_warning_does_not_change_the_verdict():
    errors, warnings = check(_ledger([_pin(authority="FRE 1001(d)")]), _gates())
    assert errors == []
    assert warnings
    assert verdict(errors, []) == VERDICT_READY


# ── staleness: INCOMPLETE, exit 1 ──────────────────────────────────────────


def test_carried_citation_marks_the_gate_incomplete():
    pin = _pin(verified="carried from the 2026-11-03 report, not re-read")
    assert is_stale(pin) is True
    assert staleness([pin], _gates()) == ["G1"]
    assert verdict([], ["G1"]) == VERDICT_INCOMPLETE
    assert exit_code_for(VERDICT_INCOMPLETE) == 1


def test_read_at_source_is_not_stale():
    assert is_stale(_pin()) is False


# ── blinded readings: BLIND, exit 2, never a pass ──────────────────────────


def test_missing_ledger_is_blind(tmp_path):
    with pytest.raises(Blinded):
        load_ledger(tmp_path / "absent.json")


def test_invalid_json_ledger_is_blind(tmp_path):
    path = tmp_path / "pins.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(Blinded):
        load_ledger(path)


def test_json_list_ledger_is_blind(tmp_path):
    path = tmp_path / "pins.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(Blinded):
        load_ledger(path)


def test_ledger_without_pins_list_is_blind(tmp_path):
    path = tmp_path / "pins.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(Blinded):
        load_ledger(path)


def test_missing_instrument_is_blind(tmp_path):
    with pytest.raises(Blinded):
        load_instrument_gates(tmp_path / "absent.py")


def test_instrument_that_raises_on_load_is_blind(tmp_path):
    path = _instrument(tmp_path, "raise RuntimeError('broken on import')\n")
    with pytest.raises(Blinded):
        load_instrument_gates(path)


def test_instrument_without_gate_metadata_is_blind(tmp_path):
    path = _instrument(tmp_path, "OTHER = []\n")
    with pytest.raises(Blinded):
        load_instrument_gates(path)


def test_instrument_with_non_list_metadata_is_blind(tmp_path):
    path = _instrument(tmp_path, "GATE_METADATA = {'G1': {}}\n")
    with pytest.raises(Blinded):
        load_instrument_gates(path)


def test_instrument_with_non_object_gate_is_blind(tmp_path):
    path = _instrument(tmp_path, "GATE_METADATA = ['G1']\n")
    with pytest.raises(Blinded):
        load_instrument_gates(path)


def test_instrument_with_unnamed_gate_is_blind(tmp_path):
    path = _instrument(tmp_path, "GATE_METADATA = [{'title': 'anonymous'}]\n")
    with pytest.raises(Blinded):
        load_instrument_gates(path)


# ── the CLI: exit codes and printed verdicts ───────────────────────────────


def test_cli_real_ledger_exits_zero(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "verdict: %s" % VERDICT_READY in out


def test_cli_missing_ledger_exits_two_with_a_verdict(tmp_path, capsys):
    code = main(["--ledger", str(tmp_path / "absent.json")])
    assert code == 2
    out = capsys.readouterr().out
    assert "verdict: %s" % VERDICT_BLIND in out
    assert "reason:" in out


def test_cli_gate_table_names_the_declared_gap(capsys):
    assert main(["--gates"]) == 0
    out = capsys.readouterr().out
    assert "G11" in out
    assert "declared, not implemented" in out


def test_cli_writes_json_report(tmp_path, capsys):
    target = tmp_path / "report" / "pins.json"
    assert main(["--json", str(target)]) == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["verdict"] == VERDICT_READY
    assert payload["pins"] == 20
    assert any(row["gate"] == "G11" for row in payload["gates"])


def test_cli_unwritable_report_is_not_a_pass(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    code = main(["--json", str(blocker / "out.json")])
    assert code == 2
    out = capsys.readouterr().out
    assert "verdict: %s" % VERDICT_BLIND in out
    assert "could not be written" in out


def test_exit_codes_are_the_contract():
    assert exit_code_for(VERDICT_READY) == 0
    assert exit_code_for(VERDICT_GAPS) == 1
    assert exit_code_for(VERDICT_INCOMPLETE) == 1
    assert exit_code_for(VERDICT_BLIND) == 2
