"""Tests for tools/cain_obsolescence_pins.py.

The checker's contract is that every input produces a printed verdict and an exit
code: READY 0, GAPS 1, INCOMPLETE 1, BLIND 2. A traceback also exits 1, which is
indistinguishable from GAPS, so the tests drive every malformed shape the ledger
or the mark can take and assert that none of them raises.

Each test perturbs exactly one thing against a fixture that passes; the tests for
machine gates perturb the mark's own arithmetic instead, because those gates are
the half of the contract that no ledger can satisfy.
"""

from __future__ import annotations

import json
import re
import types

import pytest

from tools.cain_obsolescence_pins import (
    DEFAULT_LEDGER,
    Blinded,
    check,
    describe,
    exit_code_for,
    gate_table,
    load_ledger,
    load_mark_module,
    load_marks,
    machine_gates,
    main,
    pin_tier,
    staleness,
    verdict,
    VERDICT_BLIND,
    VERDICT_GAPS,
    VERDICT_INCOMPLETE,
    VERDICT_READY,
)

URL = "https://example.test/evidence"
READ_OK = "HTTP 200 at source on 2026-11-04"
READ_404 = "HTTP 404 at source on 2026-11-04"
LONG = "this figure establishes far less than the sentence it appears in claims"


def _described():
    """One mark, one architecture: the smallest shape the ledger can address."""
    return [
        {
            "mark_id": "CAIN-MARK-TEST-20261104",
            "mark_date": "2026-11-04T00:00:00",
            "system_name": "PC",
            "vendor": "Testco",
            "seal": "sha256:" + "a" * 64,
            "witness_signature": "WITNESS-2026-11-03T00:00:00Z",
            "covenant_compliance_score": 0.8,
            "architectures": [
                {
                    "name": "Test Gate",
                    "vendor": "Testco",
                    "affected_devices_count": 100,
                    "years_of_life_stolen": 2.0,
                    "financial_damage_usd": 50.0,
                    "evidence_url": URL,
                }
            ],
            "totals": {"devices": 100, "years": 200.0, "damage": 5000.0},
            "violation_count": 1,
        }
    ]


def _figures():
    return {
        "affected_devices_count": {
            "value": 100,
            "presented_as": "fact",
            "tier": "A",
            "source": URL,
            "verified": READ_OK,
            "does_not_prove": LONG,
        },
        "years_of_life_stolen": {
            "value": 2.0,
            "presented_as": "estimate",
            "tier": "C",
            "source": URL,
            "verified": READ_OK,
            "estimation_method": "assumed from a stated service life of twelve years",
            "does_not_prove": LONG,
        },
        "financial_damage_usd": {
            "value": 50.0,
            "presented_as": "estimate",
            "tier": "C",
            "source": URL,
            "verified": READ_OK,
            "estimation_method": "list price of the successor model, no trade-in value",
            "does_not_prove": LONG,
        },
    }


def _pin(**overrides):
    pin = {
        "id": "P1",
        "architecture": "Test Gate",
        "vendor": "Testco",
        "mark": "CAIN-MARK-TEST",
        "mechanism": {
            "claim": "a firmware gate stops the device on a vendor condition",
            "tier": "A",
            "source": URL,
            "verified": READ_OK,
            "does_not_prove": LONG,
        },
        "figures": _figures(),
        "does_not_prove": LONG,
    }
    pin.update(overrides)
    return pin


def _ledger(**overrides):
    ledger = {"instrument": "mark.py", "read_at": "2026-11-04", "pins": [_pin()]}
    ledger.update(overrides)
    return ledger


def _machine(ok=True, gate="M1", title="mark-computes", detail="fine"):
    return [{"gate": gate, "title": title, "ok": ok, "detail": detail}]


def _errors(ledger=None, described=None, machine=None):
    errs, _ = check(
        ledger if ledger is not None else _ledger(),
        described if described is not None else _described(),
        machine if machine is not None else _machine(),
    )
    return errs


# ── the fixture passes ──────────────────────────────────────────────────────


def test_clean_fixture_has_no_errors():
    assert _errors() == []
    assert verdict([], []) == VERDICT_READY
    assert exit_code_for(VERDICT_READY) == 0


def test_gate_table_names_the_pin_and_its_tiers():
    rows = gate_table(_ledger(), _described())
    assert rows[0]["pin"] == "P1"
    assert rows[0]["mechanism_tier"] == "A"
    assert rows[0]["stale"] is False
    assert "devices:A" in rows[0]["figures"]


# ── machine gates: the mark's own arithmetic ────────────────────────────────


def test_failed_machine_gate_is_an_error_naming_the_gate():
    errs = _errors(machine=_machine(ok=False, gate="M4", title="seal-is-a-digest"))
    assert any("M4 seal-is-a-digest" in e for e in errs)


def test_machine_gate_failure_beats_a_clean_ledger():
    errs = _errors(machine=_machine(ok=False))
    assert errs
    assert verdict(errs, []) == VERDICT_GAPS


def test_aggregate_mismatch_is_detected():
    described = _described()
    described[0]["totals"]["devices"] = 99
    fake = FakeModule()
    gates = machine_gates(fake, fake.marks(), described)
    m3 = next(g for g in gates if g["gate"] == "M3")
    assert m3["ok"] is False
    assert "sum to 100" in m3["detail"]


def test_aggregate_damage_mismatch_is_detected():
    described = _described()
    described[0]["totals"]["damage"] = 4999.0
    fake = FakeModule()
    m3 = next(g for g in machine_gates(fake, fake.marks(), described) if g["gate"] == "M3")
    assert m3["ok"] is False
    assert "count x damage" in m3["detail"]


def test_label_seal_is_reported_as_a_failed_gate():
    described = _described()
    described[0]["seal"] = "SHA256:CAIN-OBSOLESCENCE-MARK-v1.0-KILO-AUTHORIZED"
    fake = FakeModule()
    m4 = next(g for g in machine_gates(fake, fake.marks(), described) if g["gate"] == "M4")
    assert m4["ok"] is False
    assert "label, not a digest" in m4["detail"]


def test_witness_dated_after_the_mark_is_reported():
    described = _described()
    described[0]["witness_signature"] = "KILO-2026-11-05T02:30:00Z"
    described[0]["mark_date"] = "2026-11-04T22:00:00"
    fake = FakeModule()
    m5 = next(g for g in machine_gates(fake, fake.marks(), described) if g["gate"] == "M5")
    assert m5["ok"] is False
    assert "after the mark it witnesses" in m5["detail"]


def test_witness_without_a_timestamp_is_reported():
    described = _described()
    described[0]["witness_signature"] = "a name, not a time"
    fake = FakeModule()
    m5 = next(g for g in machine_gates(fake, fake.marks(), described) if g["gate"] == "M5")
    assert m5["ok"] is False


def test_hand_set_compliance_score_is_reported():
    described = _described()
    described[0]["covenant_compliance_score"] = 0.99
    fake = FakeModule()
    m6 = next(g for g in machine_gates(fake, fake.marks(), described) if g["gate"] == "M6")
    assert m6["ok"] is False
    assert "own formula gives" in m6["detail"]


def test_compliance_score_outside_the_unit_interval_is_reported():
    described = _described()
    described[0]["covenant_compliance_score"] = 1.4
    fake = FakeModule()
    m6 = next(g for g in machine_gates(fake, fake.marks(), described) if g["gate"] == "M6")
    assert m6["ok"] is False
    assert "outside 0..1" in m6["detail"]


def test_repr_string_instead_of_an_object_fails_serialization():
    described = _described()
    m2 = next(
        g
        for g in machine_gates(FakeModule(repr_acc=True), [object()], described)
        if g["gate"] == "M2"
    )
    assert m2["ok"] is False
    assert "not an object" in m2["detail"]


def test_engine_absent_is_a_failed_gate_not_a_crash():
    described = _described()
    m2 = next(
        g
        for g in machine_gates(FakeModule(no_engine=True), [object()], described)
        if g["gate"] == "M2"
    )
    assert m2["ok"] is False


def test_all_machine_gates_pass_on_a_sound_mark():
    described = _described()
    gates = machine_gates(FakeModule(), [object()], described)
    assert [g["ok"] for g in gates] == [True] * 6


def test_seal_that_does_not_recompute_from_the_mark_is_reported():
    """A digest-shaped string the module cannot reproduce seals nothing."""
    described = _described()
    m4 = next(
        g for g in machine_gates(_WrongSeal(), [object()], described) if g["gate"] == "M4"
    )
    assert m4["ok"] is False
    assert "its own bytes hash to" in m4["detail"]


def test_two_marks_cannot_share_one_seal():
    described = _described()
    second = json.loads(json.dumps(described[0]))
    second["mark_id"] = "CAIN-MARK-TEST2-20261104"
    described.append(second)
    m4 = next(g for g in machine_gates(FakeModule(), [], described) if g["gate"] == "M4")
    assert m4["ok"] is False
    assert "cannot repeat" in m4["detail"]


def test_module_without_compute_seal_cannot_seal_anything():
    """Shape and uniqueness are satisfiable by a counter; only a recompute is not."""
    module = types.SimpleNamespace()
    m4 = next(g for g in machine_gates(module, [], _described()) if g["gate"] == "M4")
    assert m4["ok"] is False
    assert "exposes no compute_seal" in m4["detail"]


def test_compute_seal_raising_is_a_failed_gate_not_a_crash():
    described = _described()
    m4 = next(
        g for g in machine_gates(_RaisingSeal(), [object()], described) if g["gate"] == "M4"
    )
    assert m4["ok"] is False
    assert "compute_seal raised" in m4["detail"]


# ── coverage: both directions ───────────────────────────────────────────────


def test_unpinned_architecture_is_an_error():
    errs = _errors(ledger=_ledger(pins=[]))
    assert any("published and unpinned" in e for e in errs)


def test_pin_for_an_unknown_architecture_is_an_error():
    pin = _pin(architecture="A Gate The Mark Never Published")
    errs = _errors(ledger=_ledger(pins=[pin]))
    assert any("which the mark does not publish" in e for e in errs)


def test_pin_bound_to_the_wrong_mark_is_an_error():
    pin = _pin(mark="CAIN-MARK-OTHER")
    errs = _errors(ledger=_ledger(pins=[pin]))
    assert any("binds to mark" in e for e in errs)


def test_pin_missing_required_keys_is_an_error():
    pin = _pin()
    del pin["figures"]
    errs = _errors(ledger=_ledger(pins=[pin]))
    assert any("missing required keys" in e for e in errs)


def test_duplicate_pin_ids_are_an_error():
    errs = _errors(ledger=_ledger(pins=[_pin(), _pin()]))
    assert any("duplicate id" in e for e in errs)


def test_non_object_pin_is_an_error():
    errs = _errors(ledger=_ledger(pins=["not a pin"]))
    assert any("pins[0] is not an object" in e for e in errs)


# ── figures: value drift, presentation, method ──────────────────────────────


def test_changed_figure_value_is_caught_as_drift():
    ledger = _ledger()
    ledger["pins"][0]["figures"]["affected_devices_count"]["value"] = 200
    errs = _errors(ledger=ledger)
    assert any("answers for a mark that no longer exists" in e for e in errs)


def test_unknown_figure_field_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["figures"]["carbon_tons_saved"] = {"value": 1}
    errs = _errors(ledger=ledger)
    assert any("figures the mark does not carry" in e for e in errs)


def test_missing_figure_field_is_an_error():
    ledger = _ledger()
    del ledger["pins"][0]["figures"]["financial_damage_usd"]
    errs = _errors(ledger=ledger)
    assert any("pins no financial_damage_usd" in e for e in errs)


def test_fact_presented_at_below_proof_tier_is_an_error():
    ledger = _ledger()
    figure = ledger["pins"][0]["figures"]["affected_devices_count"]
    figure["tier"] = "C"
    errs = _errors(ledger=ledger)
    assert any("presented as fact at tier C" in e for e in errs)


def test_fact_at_proof_tier_with_a_dead_reading_is_an_error():
    ledger = _ledger()
    figure = ledger["pins"][0]["figures"]["affected_devices_count"]
    figure["verified"] = READ_404
    errs = _errors(ledger=ledger)
    assert any("no successful source read" in e for e in errs)


def test_estimate_without_a_method_is_an_error():
    ledger = _ledger()
    del ledger["pins"][0]["figures"]["years_of_life_stolen"]["estimation_method"]
    errs = _errors(ledger=ledger)
    assert any("without a method" in e for e in errs)


def test_unknown_presentation_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["figures"]["affected_devices_count"]["presented_as"] = "very likely"
    errs = _errors(ledger=ledger)
    assert any("is presented as 'very likely'" in e for e in errs)


def test_figure_without_a_read_date_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["figures"]["affected_devices_count"]["verified"] = READ_OK.replace(
        " on 2026-11-04", ""
    )
    errs = _errors(ledger=ledger)
    assert any("records no read date" in e for e in errs)


def test_figure_that_does_not_say_what_it_fails_to_prove_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["figures"]["affected_devices_count"]["does_not_prove"] = "no"
    errs = _errors(ledger=ledger)
    assert any("does not say what the figure fails to prove" in e for e in errs)


def test_non_numeric_figure_value_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["figures"]["affected_devices_count"]["value"] = "one hundred"
    errs = _errors(ledger=ledger)
    assert any("non-numeric value" in e for e in errs)


# ── mechanism: the pillar's premise ─────────────────────────────────────────


def test_mechanism_below_proof_tier_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"]["tier"] = "UNVERIFIABLE-HERE"
    ledger["pins"][0]["mechanism"]["verified"] = READ_404
    errs = _errors(ledger=ledger)
    assert any("premise is unproven" in e for e in errs)


def test_mechanism_claiming_proof_tier_on_a_404_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"]["verified"] = READ_404
    errs = _errors(ledger=ledger)
    assert any("claims tier A but its reading records no successful source read" in e for e in errs)


def test_mechanism_without_a_claim_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"]["claim"] = "it is bad"
    errs = _errors(ledger=ledger)
    assert any("states no testable claim" in e for e in errs)


def test_mechanism_without_a_source_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"]["source"] = ""
    errs = _errors(ledger=ledger)
    assert any("mechanism names no source" in e for e in errs)


def test_mechanism_missing_keys_is_an_error():
    ledger = _ledger()
    del ledger["pins"][0]["mechanism"]["does_not_prove"]
    errs = _errors(ledger=ledger)
    assert any("mechanism missing keys" in e for e in errs)


def test_non_object_mechanism_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"] = "trust me"
    errs = _errors(ledger=ledger)
    assert any("mechanism is str" in e for e in errs)


# ── citation substitution must be visible ───────────────────────────────────


def test_silent_source_substitution_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"]["source"] = "https://elsewhere.test/report"
    errs = _errors(ledger=ledger)
    assert any("substitution is invisible" in e for e in errs)


def test_recorded_substitution_must_match_the_mark_citation():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"]["source"] = "https://elsewhere.test/report"
    ledger["pins"][0]["mark_evidence_url"] = "https://stale.test/old"
    ledger["pins"][0]["mark_evidence_status"] = "HTTP 404 on 2026-11-04"
    errs = _errors(ledger=ledger)
    assert any("but the mark cites" in e for e in errs)


def test_recorded_substitution_without_a_dated_status_is_an_error():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"]["source"] = "https://elsewhere.test/report"
    ledger["pins"][0]["mark_evidence_url"] = URL
    ledger["pins"][0]["mark_evidence_status"] = "gone"
    errs = _errors(ledger=ledger)
    assert any("records no dated status" in e for e in errs)


def test_recorded_substitution_satisfies_the_rule():
    ledger = _ledger()
    ledger["pins"][0]["mechanism"]["source"] = "https://elsewhere.test/report"
    ledger["pins"][0]["mark_evidence_url"] = URL
    ledger["pins"][0]["mark_evidence_status"] = "HTTP 404 at source on 2026-11-04"
    assert _errors(ledger=ledger) == []


# ── declared gaps and context pins ──────────────────────────────────────────


def _context_pin(**overrides):
    pin = {
        "id": "X1",
        "context": True,
        "tier": "C",
        "authority": "an internal declaration",
        "source": "src/ARC/package/__init__.py",
        "verified": "read at source on 2026-11-04",
        "does_not_prove": LONG,
    }
    pin.update(overrides)
    return pin


def test_context_pin_alone_is_not_an_error_for_the_pin():
    ledger = _ledger(pins=[_pin(), _context_pin()])
    assert _errors(ledger=ledger) == []


def test_context_pin_naming_an_architecture_is_an_error():
    ledger = _ledger(pins=[_pin(), _context_pin(architecture="Test Gate")])
    errs = _errors(ledger=ledger)
    assert any("a pin is one or the other" in e for e in errs)


def test_context_pin_missing_keys_is_an_error():
    pin = _context_pin()
    del pin["source"]
    errs = _errors(ledger=_ledger(pins=[_pin(), pin]))
    assert any("is context but missing keys" in e for e in errs)


def test_declared_gap_with_no_pins_is_an_error():
    ledger = _ledger(declared_gaps=[{"gate": "G1", "why": LONG}])
    errs = _errors(ledger=ledger)
    assert any("names no pins" in e for e in errs)


def test_declared_gap_naming_a_missing_pin_is_an_error():
    ledger = _ledger(
        pins=[_pin(), _context_pin()],
        declared_gaps=[{"gate": "G1", "why": LONG, "pins": ["X9"]}],
    )
    errs = _errors(ledger=ledger)
    assert any("names missing pin X9" in e for e in errs)


def test_declared_gap_pinned_below_tier_c_is_an_error():
    ledger = _ledger(
        pins=[_pin(), _context_pin(tier="UNVERIFIABLE-HERE")],
        declared_gaps=[{"gate": "G1", "why": LONG, "pins": ["X1"]}],
    )
    errs = _errors(ledger=ledger)
    assert any("below C" in e for e in errs)


def test_pin_tier_reads_the_parts_of_an_architecture_pin():
    assert pin_tier(_pin()) == "A"
    at_c = _pin(mechanism=dict(_pin()["mechanism"], tier="C"), figures=[])
    assert pin_tier(at_c) == "C"
    assert pin_tier({"id": "X1"}) is None


def test_declared_gap_on_an_architecture_pin_at_tier_c_is_not_a_tier_defect():
    # An architecture pin records a tier per claim; before the fix the loop read
    # only a top-level ``tier`` and so reported every such pin at ``None``.
    ledger = _ledger(
        pins=[_pin(mechanism=dict(_pin()["mechanism"], tier="C"), figures=[])],
        declared_gaps=[{"gate": "G1", "why": LONG, "pins": ["P1"]}],
    )
    errs = _errors(ledger=ledger)
    assert not [e for e in errs if "below C" in e]


def test_declared_gap_without_a_reason_is_an_error():
    ledger = _ledger(
        pins=[_pin(), _context_pin()],
        declared_gaps=[{"gate": "G1", "why": "not done", "pins": ["X1"]}],
    )
    errs = _errors(ledger=ledger)
    assert any("does not say why" in e for e in errs)


def test_declared_gaps_that_is_not_a_list_is_an_error():
    errs = _errors(ledger=_ledger(declared_gaps="none"))
    assert any("declared_gaps is str" in e for e in errs)


# ── ledger date and verdict precedence ──────────────────────────────────────


def test_ledger_without_a_dated_read_at_is_an_error():
    errs = _errors(ledger=_ledger(read_at="recently"))
    assert any("cannot be aged" in e for e in errs)


def test_errors_outrank_staleness():
    assert verdict(["something"], ["P1"]) == VERDICT_GAPS


def test_staleness_alone_is_incomplete():
    assert verdict([], ["P1"]) == VERDICT_INCOMPLETE


def test_carried_context_pin_is_stale_not_an_error():
    pin = _context_pin(verified="not fetched; carried from an earlier session 2026-11-04")
    ledger = _ledger(pins=[_pin(), pin])
    assert _errors(ledger=ledger) == []
    assert staleness(ledger["pins"]) == ["X1"]
    assert verdict([], staleness(ledger["pins"])) == VERDICT_INCOMPLETE


def test_exit_codes_are_the_contract():
    assert exit_code_for(VERDICT_READY) == 0
    assert exit_code_for(VERDICT_GAPS) == 1
    assert exit_code_for(VERDICT_INCOMPLETE) == 1
    assert exit_code_for(VERDICT_BLIND) == 2


# ── blinding: the reading cannot be performed ───────────────────────────────


def test_missing_ledger_is_blind(tmp_path):
    with pytest.raises(Blinded):
        load_ledger(tmp_path / "absent.json")


def test_unparseable_ledger_is_blind(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(Blinded):
        load_ledger(path)


def test_ledger_without_pins_is_blind(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"instrument": "x", "read_at": "2026-11-04"}), encoding="utf-8")
    with pytest.raises(Blinded):
        load_ledger(path)


def test_ledger_that_is_not_an_object_is_blind(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(Blinded):
        load_ledger(path)


def test_missing_mark_module_is_blind(tmp_path):
    with pytest.raises(Blinded):
        load_mark_module(tmp_path / "absent.py")


def test_module_without_compute_all_marks_is_blind(tmp_path):
    path = tmp_path / "silent.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(Blinded):
        load_mark_module(path)


def test_module_is_registered_before_it_executes(tmp_path):
    # ``dataclasses`` resolves a field's string annotation through
    # ``sys.modules[cls.__module__]``.  Executing an unregistered module makes
    # that lookup ``None`` and the load dies inside the standard library, which
    # the checker then reported as BLIND -- a loader bug blamed on the artifact.
    path = tmp_path / "registered.py"
    path.write_text(
        "import sys\nSEEN = sys.modules.get(__name__) is not None\n"
        "def compute_all_marks():\n    return [SEEN]\n",
        encoding="utf-8",
    )
    module = load_mark_module(path)
    assert module.SEEN is True


def test_module_with_string_annotations_loads(tmp_path):
    # The exact shape that broke: postponed annotations plus a dataclass.
    path = tmp_path / "annotated.py"
    path.write_text(
        "from __future__ import annotations\n\n"
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\nclass Mark:\n    label: str = ''\n\n\n"
        "def compute_all_marks():\n    return [Mark()]\n",
        encoding="utf-8",
    )
    module = load_mark_module(path)
    assert module.compute_all_marks()[0].label == ""


def test_compute_raising_is_blind_not_empty():
    class Exploding:
        @staticmethod
        def compute_all_marks():
            raise RuntimeError("engine died")

    with pytest.raises(Blinded) as excinfo:
        load_marks(Exploding)
    assert "engine died" in str(excinfo.value)


def test_compute_returning_nothing_is_blind():
    class Empty:
        @staticmethod
        def compute_all_marks():
            return []

    with pytest.raises(Blinded):
        load_marks(Empty)


def test_mark_without_an_accountability_record_is_blind():
    class Bare:
        pass

    with pytest.raises(Blinded):
        describe(Bare())


# ── CLI: verdict printed, exit code carried ─────────────────────────────────


def test_main_blind_on_a_missing_ledger(tmp_path, capsys):
    code = main(["--ledger", str(tmp_path / "absent.json")])
    assert code == 2
    out = capsys.readouterr().out
    assert "verdict: BLIND" in out
    assert "reason:" in out


def test_main_blind_when_the_report_cannot_be_written(tmp_path):
    # A directory is not a writable JSON path; a run that cannot report is BLIND.
    code = main(["--ledger", str(DEFAULT_LEDGER), "--json", str(tmp_path)])
    assert code == 2


def test_main_on_the_shipped_ledger_is_a_reading_not_a_blind(capsys):
    code = main(["--ledger", str(DEFAULT_LEDGER)])
    out = capsys.readouterr().out
    assert code in (0, 1), "the shipped ledger must produce a verdict, never BLIND"
    assert any(
        "verdict: %s" % name in out
        for name in (VERDICT_READY, VERDICT_GAPS, VERDICT_INCOMPLETE)
    )
    assert "marks," in out


def test_main_gates_table_prints_one_row_per_architecture(capsys):
    main(["--ledger", str(DEFAULT_LEDGER), "--gates"])
    out = capsys.readouterr().out
    assert "architecture" in out
    assert "TPM 2.0 / Secure Boot Gate" in out


def test_main_writes_a_machine_readable_report(tmp_path, capsys):
    out_path = tmp_path / "report.json"
    main(["--ledger", str(DEFAULT_LEDGER), "--json", str(out_path)])
    capsys.readouterr()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["verdict"] in (VERDICT_READY, VERDICT_GAPS, VERDICT_INCOMPLETE)
    assert len(data["machine_gates"]) == 6
    assert data["marks"] == 7


# ── the shipped ledger's structure ──────────────────────────────────────────


def test_shipped_ledger_pins_all_seven_architectures():
    ledger = load_ledger(DEFAULT_LEDGER)
    pins = [p for p in ledger["pins"] if not p.get("context")]
    assert len(pins) == 7
    assert len({p["architecture"] for p in pins}) == 7
    assert all(len(p["figures"]) == 3 for p in pins)


def test_shipped_ledger_declares_the_emotional_layer_as_a_gap():
    ledger = load_ledger(DEFAULT_LEDGER)
    context = [p for p in ledger["pins"] if p.get("context")]
    assert len(context) == 1
    gap = ledger["declared_gaps"][0]
    assert gap["pins"] == [context[0]["id"]]
    assert gap["gate"] == "G-CAIN-1"


READ_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _assert_read_date(text, what):
    """A read date must exist, be calendar-shaped, and not postdate the ledger.

    Asserting one typed date proved nothing: the requirement is that every
    assertion carries a read date, and that no recorded read is later than the
    ledger's own read_at. A typed constant also fails on every legitimate re-read.
    """
    match = READ_DATE.search(text or "")
    assert match, "%s records no read date" % what
    return match.group(0)


def test_shipped_ledger_records_a_read_for_every_pin():
    ledger = load_ledger(DEFAULT_LEDGER)
    read_at = ledger["read_at"]
    assert READ_DATE.fullmatch(read_at), "ledger records no read date"
    for pin in ledger["pins"]:
        if pin.get("context"):
            assert _assert_read_date(pin["verified"], pin["id"]) <= read_at
            continue
        assert _assert_read_date(pin["mechanism"]["verified"], pin["id"]) == read_at
        for field_name, figure in pin["figures"].items():
            assert _assert_read_date(
                figure["verified"], "%s.%s" % (pin["id"], field_name)
            ) <= read_at, "%s.%s has no read" % (pin["id"], field_name)


class FakeModule:
    """A mark engine sound enough to pass M1-M6 unless a flag breaks one gate."""

    CainObsolescenceAccountability = None

    def __init__(self, repr_acc=False, no_engine=False):
        self.repr_acc = repr_acc
        if no_engine and not repr_acc:
            self.CainObsolescenceAccountability = None
            return
        self.CainObsolescenceAccountability = self._engine

    def _engine(self):
        repr_acc = self.repr_acc

        class Engine:
            def format_mark_report(self, mark, fmt):
                if repr_acc:
                    return json.dumps({"vendor_accountability": "VendorAccountability(...)"})
                return json.dumps(
                    {"vendor_accountability": {"architectures": [{"name": "Test Gate"}]}}
                )

        return Engine()

    def compute_all_marks(self):
        return []

    def marks(self):
        return []

    def compute_seal(self, mark):
        """Reproduce the ``_described()`` seal, as the real module reproduces its own."""
        return "sha256:" + "a" * 64


class _WrongSeal(FakeModule):
    """A module whose seal does not match the digest it hands back."""

    def compute_seal(self, mark):
        return "sha256:" + "b" * 64


class _RaisingSeal(FakeModule):
    """A module whose seal routine dies on its own subject."""

    def compute_seal(self, mark):
        raise ValueError("payload is not canonical")

