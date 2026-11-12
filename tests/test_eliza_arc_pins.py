"""Tests for tools/eliza_arc_pins.py.

The contract is that every input produces a printed verdict and an exit code:
READY 0, GAPS 1, INCOMPLETE 1, BLIND 2. A traceback also exits 1 and would be
indistinguishable from GAPS, so every malformed shape below is driven explicitly.

Each gate is tested both ways: it must be silent on a pin that satisfies it, and it
must fire on a pin that does not. A gate that has only ever been seen to pass has
not been tested -- that is the defect this file exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from tools.eliza_arc_pins import (
    DEFAULT_LEDGER,
    GATES,
    PROJECT,
    Blinded,
    carried_pins,
    check,
    check_structure,
    exit_code_for,
    gate_carried_claims_say_why,
    gate_claim_number_was_measured,
    gate_claims_are_pinned,
    gate_disclosure_manifest_is_closed,
    gate_instrument_exists,
    gate_no_clinical_claim,
    gate_publication_gate_passes,
    gate_safety_precedence_has_a_test,
    load_ledger,
    load_manifest,
    main,
    number_tokens,
    pin_table,
    pins_of,
    readme_claim_rows,
    verdict,
    VERDICT_BLIND,
    VERDICT_GAPS,
    VERDICT_INCOMPLETE,
    VERDICT_READY,
)

DOES_NOT_PROVE = "that any of this holds on a host this ledger has not run on"
CARRIED_WHY = (
    "the figure exists only in the project's prose and no script in the tree emits it, "
    "so it cannot be recomputed from the source of record"
)


def _pin(**overrides):
    """A pin that satisfies every gate on its own."""
    pin = {
        "id": "T1",
        "surface": "loop latency",
        "asserts": "a turn is decided in 0.025 ms over 200 turns",
        "tier": "A",
        "instrument": "tools/triage_eval.py",
        "command": "PYTHONPATH=src python tools/triage_eval.py",
        "source": "recorded output, 2026-11-04",
        "measured": "median 0.025 ms over 200 turns",
        "does_not_prove": DOES_NOT_PROVE,
    }
    pin.update(overrides)
    return pin


def _ledger(pins=None, **overrides):
    """A ledger whose declared gates equal the implemented ones."""
    ledger = {
        "read_at": "2026-11-04",
        "gates": sorted(GATES),
        "pins": pins if pins is not None else [_pin()],
        "declared_gaps": [],
    }
    ledger.update(overrides)
    return ledger


def _structure(ledger):
    """Structural errors only, so a shape test never runs the 10-second real gate."""
    errors, _warnings = check_structure(ledger, pins_of(ledger))
    return errors


MANIFEST = {
    "status": "PRE-PUBLICATION",
    "ship": [{"from": "a.py", "to": "b.py"}],
    "never_ship": [{"glob": "*_evidence*.json"}],
    "deny_substrings": ["FETCH_LOG"],
}


# --- the shipped ledger ------------------------------------------------------


def test_shipped_ledger_is_readable_and_gaps():
    """The real ledger grades GAPS because the project's own gate refuses to publish."""
    ledger = load_ledger()
    errors, warnings, carried = check(ledger)
    assert verdict(errors, carried) == VERDICT_GAPS
    assert len(ledger["pins"]) == 11
    assert carried == ["E1", "E2", "E3", "E4"]
    assert any("publication gate exits" in err for err in errors), errors


def test_shipped_ledger_declares_exactly_the_implemented_gates():
    ledger = load_ledger()
    assert sorted(ledger["gates"]) == sorted(GATES)


def test_shipped_ledger_has_no_structural_defect():
    """Only two classes may fail: the publication gate, and rows nobody has pinned yet."""
    errors, _warnings, _carried = check(load_ledger())
    others = [
        err
        for err in errors
        if "publication gate" not in err
        and "banner claims" not in err
        and "publishes a measurement and no pin answers" not in err
    ]
    assert others == []


def test_shipped_readme_rows_without_a_pin_are_exactly_three():
    """A ratchet on the unpinned set: a fourth unpinned reading fails this test."""
    errors, _warnings, _carried = check(load_ledger())
    unpinned = [err.split()[0] for err in errors if "publishes a measurement" in err]
    assert unpinned == ["README.md:32", "README.md:33", "README.md:34"]


def test_shipped_ledger_structure_is_clean_without_running_gates():
    assert _structure(load_ledger()) == []


# --- gate: instrument-exists -------------------------------------------------


def test_instrument_gate_is_silent_when_the_file_exists():
    assert gate_instrument_exists(_ledger(), [_pin()], PROJECT, MANIFEST) == []


def test_instrument_gate_fires_on_a_missing_file():
    errors = gate_instrument_exists(_ledger(), [_pin(instrument="tools/nope.py")], PROJECT, MANIFEST)
    assert len(errors) == 1
    assert "does not exist" in errors[0]


def test_instrument_gate_fires_on_an_empty_instrument():
    errors = gate_instrument_exists(_ledger(), [_pin(instrument="")], PROJECT, MANIFEST)
    assert errors and "names no instrument" in errors[0]


# --- gate: claim-number-was-measured ----------------------------------------


def test_number_gate_is_silent_when_every_number_was_printed():
    assert gate_claim_number_was_measured(_ledger(), [_pin()], PROJECT, MANIFEST) == []


def test_number_gate_fires_on_a_number_the_reading_never_printed():
    """The claim's 300 appears nowhere in the measured text: a contradiction."""
    pin = _pin(asserts="a turn is decided in 0.025 ms over 300 turns")
    errors = gate_claim_number_was_measured(_ledger(), [pin], PROJECT, MANIFEST)
    assert len(errors) == 1
    assert "300" in errors[0]
    assert "never printed" in errors[0]


def test_number_gate_normalises_thousands_separators():
    pin = _pin(asserts="1000 turns", measured="1,000 turns observed")
    assert gate_claim_number_was_measured(_ledger(), [pin], PROJECT, MANIFEST) == []


def test_number_tokens_ignores_dates_and_separates_decimals():
    tokens = number_tokens("2026-11-04: median 0.025 ms; 1,000 rows")
    assert "0.025" in tokens
    assert "1000" in tokens
    assert "2026" in tokens


# --- gate: no-clinical-claim -------------------------------------------------


def test_clinical_gate_is_silent_on_a_technical_claim():
    assert gate_no_clinical_claim(_ledger(), [_pin()], PROJECT, MANIFEST) == []


def test_clinical_gate_fires_on_an_efficacy_claim():
    pin = _pin(asserts="the loop treats depression and reduces symptoms")
    errors = gate_no_clinical_claim(_ledger(), [pin], PROJECT, MANIFEST)
    assert len(errors) == 1
    assert "clinical claim" in errors[0]


def test_clinical_gate_permits_a_limitation_pin():
    """The project must be able to say what it cannot do."""
    pin = _pin(asserts="this is not a clinical validation and no cure is claimed", limitation=True)
    assert gate_no_clinical_claim(_ledger(), [pin], PROJECT, MANIFEST) == []


# --- gate: safety-precedence-has-a-test -------------------------------------


def _safety_pin(**overrides):
    pin = _pin(
        id="T8",
        surface="safety precedence",
        reproducible="tests/test_psyche_rt_agent.py::test_no_other_stance_can_be_chosen_when_risk_is_crisis",
    )
    pin.update(overrides)
    return pin


def test_safety_gate_is_silent_when_the_named_test_exists():
    assert gate_safety_precedence_has_a_test(_ledger(), [_safety_pin()], PROJECT, MANIFEST) == []


def test_safety_gate_fires_when_no_pin_records_precedence():
    errors = gate_safety_precedence_has_a_test(_ledger(), [_pin()], PROJECT, MANIFEST)
    assert errors and "no pin records" in errors[0]


def test_safety_gate_fires_when_the_pin_names_no_test():
    errors = gate_safety_precedence_has_a_test(_ledger(), [_safety_pin(reproducible="")], PROJECT, MANIFEST)
    assert errors and "without naming the test" in errors[0]


def test_safety_gate_fires_on_a_test_name_absent_from_the_file():
    pin = _safety_pin(reproducible="tests/test_psyche_rt_agent.py::test_that_does_not_exist")
    errors = gate_safety_precedence_has_a_test(_ledger(), [pin], PROJECT, MANIFEST)
    assert errors and "is not defined in" in errors[0]


def test_safety_gate_fires_on_a_missing_test_file():
    pin = _safety_pin(reproducible="tests/nope.py::test_x")
    errors = gate_safety_precedence_has_a_test(_ledger(), [pin], PROJECT, MANIFEST)
    assert errors and "does not exist" in errors[0]


# --- gate: disclosure-manifest-is-closed ------------------------------------


def test_disclosure_gate_is_silent_on_a_closed_manifest():
    assert gate_disclosure_manifest_is_closed(_ledger(), [_pin()], PROJECT, MANIFEST) == []


@pytest.mark.parametrize("key", ["ship", "never_ship", "deny_substrings"])
def test_disclosure_gate_fires_when_a_list_is_empty(key):
    """A deny list with no allowlist fails open, so an empty key is a defect."""
    manifest = dict(MANIFEST)
    manifest[key] = []
    errors = gate_disclosure_manifest_is_closed(_ledger(), [_pin()], PROJECT, manifest)
    assert errors and "fails open" in errors[0]


def test_disclosure_gate_fires_when_status_is_unstated():
    manifest = dict(MANIFEST)
    manifest["status"] = ""
    errors = gate_disclosure_manifest_is_closed(_ledger(), [_pin()], PROJECT, manifest)
    assert errors and "publication state is unstated" in errors[0]


# --- gate: carried-claims-say-why -------------------------------------------


def test_carried_gate_is_silent_on_a_tier_a_pin_with_a_command():
    assert gate_carried_claims_say_why(_ledger(), [_pin()], PROJECT, MANIFEST) == []


def test_carried_gate_is_silent_on_a_tier_b_pin_that_says_why():
    pin = _pin(tier="B", command="no command in the tree reproduces this", carried_because=CARRIED_WHY)
    assert gate_carried_claims_say_why(_ledger(), [pin], PROJECT, MANIFEST) == []


def test_carried_gate_fires_on_a_tier_b_pin_without_a_reason():
    pin = _pin(tier="B", carried_because="README says so")
    errors = gate_carried_claims_say_why(_ledger(), [pin], PROJECT, MANIFEST)
    assert errors and "without saying why" in errors[0]


def test_carried_gate_fires_on_a_tier_a_pin_that_admits_it_has_no_command():
    pin = _pin(command="no command in the tree reproduces this figure")
    errors = gate_carried_claims_say_why(_ledger(), [pin], PROJECT, MANIFEST)
    assert errors and "but names no command" in errors[0]


# --- gate: publication-gate-passes ------------------------------------------


def _project_with_gate(tmp_path, body: str) -> "object":
    """A throwaway project holding one stub gate, so the control does not depend
    on the real tree's current state."""
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "eliza_release_gate.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_publication_gate_passes_on_a_green_stub(tmp_path):
    project = _project_with_gate(tmp_path, "import sys\nprint('all checks ok')\nsys.exit(0)\n")
    assert gate_publication_gate_passes(_ledger(), [_pin()], project, MANIFEST) == []


def test_publication_gate_fires_on_a_red_stub(tmp_path):
    body = "import sys\nprint('FAIL: 3 failure(s)')\nsys.exit(1)\n"
    project = _project_with_gate(tmp_path, body)
    errors = gate_publication_gate_passes(_ledger(), [_pin()], project, MANIFEST)
    assert len(errors) == 2
    assert "exits 1" in errors[0]
    assert "FAIL: 3 failure(s)" in errors[0]


def test_publication_gate_fires_when_the_script_is_absent(tmp_path):
    errors = gate_publication_gate_passes(_ledger(), [_pin()], tmp_path, MANIFEST)
    assert errors and "does not exist" in errors[0]


def test_publication_gate_reports_the_real_tree_as_red():
    """The shipped project's gate is red as of 2026-11-04; a green reading here
    would mean the stub or the path is wrong, not that publication was fixed."""
    errors = gate_publication_gate_passes(_ledger(), [_pin()], PROJECT, MANIFEST)
    assert errors, "the real publication gate is recorded as failing"


# --- structural validation ---------------------------------------------------


def test_structure_accepts_a_well_formed_ledger():
    assert _structure(_ledger()) == []


def test_structure_accepts_the_shipped_ledger():
    errors = _structure(load_ledger())
    assert errors == []


def test_structure_fires_on_a_duplicate_pin_id():
    errors = _structure(_ledger(pins=[_pin(), _pin()]))
    assert any("appears 2 times" in err for err in errors)


def test_structure_fires_on_the_same_id_with_different_content():
    errors = _structure(_ledger(pins=[_pin(), _pin(surface="other")]))
    assert any("appears 2 times" in err for err in errors)


def test_structure_fires_on_an_unknown_tier():
    errors = _structure(_ledger(pins=[_pin(tier="Z")]))
    assert any("is not one of" in err for err in errors)


def test_structure_fires_when_does_not_prove_is_missing():
    pin = _pin()
    del pin["does_not_prove"]
    errors = _structure(_ledger(pins=[pin]))
    assert any("missing required key does_not_prove" in err for err in errors)


def test_structure_fires_on_a_perfunctory_does_not_prove():
    errors = _structure(_ledger(pins=[_pin(does_not_prove="nothing")]))
    assert any("does not say what it fails to prove" in err for err in errors)


def test_structure_fires_on_a_tier_a_pin_with_no_recorded_output():
    errors = _structure(_ledger(pins=[_pin(measured="")]))
    assert any("no recorded output" in err for err in errors)


def test_structure_fires_on_an_undeclared_read_date():
    errors = _structure(_ledger(read_at=""))
    assert any("no read_at date" in err for err in errors)


def test_structure_fires_on_a_declared_gate_with_no_implementation():
    errors = _structure(_ledger(gates=sorted(GATES) + ["vibes-gate"]))
    assert any("has no implementation" in err for err in errors)


def test_structure_fires_on_an_implemented_gate_that_is_not_declared():
    errors = _structure(_ledger(gates=["no-clinical-claim"]))
    assert any("implemented but not declared" in err for err in errors)


def test_structure_fires_on_a_gap_naming_an_unknown_pin():
    gap = {"gate": "G-T", "title": "t", "why": DOES_NOT_PROVE, "pins": ["E99"]}
    errors = _structure(_ledger(declared_gaps=[gap]))
    assert any("which the ledger does not contain" in err for err in errors)


def test_structure_fires_on_a_gap_with_no_reason():
    gap = {"gate": "G-T", "title": "t", "why": "", "pins": []}
    errors = _structure(_ledger(declared_gaps=[gap]))
    assert any("does not say why it exists" in err for err in errors)


def test_structure_fires_on_declared_gaps_not_being_a_list():
    errors = _structure(_ledger(declared_gaps={"a": 1}))
    assert any("declared_gaps is dict" in err for err in errors)


# --- verdict and exit contract ----------------------------------------------


def test_verdict_is_ready_when_nothing_failed_and_nothing_is_carried():
    assert verdict([], []) == VERDICT_READY


def test_verdict_is_incomplete_when_only_carried_support_remains():
    assert verdict([], ["E1"]) == VERDICT_INCOMPLETE


def test_verdict_is_gaps_when_an_error_exists_even_with_carried_pins():
    assert verdict(["boom"], ["E1"]) == VERDICT_GAPS


def test_exit_codes():
    assert exit_code_for(VERDICT_READY) == 0
    assert exit_code_for(VERDICT_GAPS) == 1
    assert exit_code_for(VERDICT_INCOMPLETE) == 1
    assert exit_code_for(VERDICT_BLIND) == 2


def test_carried_pins_lists_only_non_proof_tiers():
    pins = [_pin(), _pin(id="T2", tier="B"), _pin(id="T3", tier="C")]
    assert carried_pins(pins) == ["T2", "T3"]


def test_pin_table_marks_proof_and_carried():
    rows = pin_table([_pin(), _pin(id="T2", tier="B")])
    assert [row["status"] for row in rows] == ["proof", "carried"]


# --- main() never raises ----------------------------------------------------


def test_main_reports_gaps_for_the_shipped_ledger(capsys):
    assert main([]) == 1
    assert "verdict: GAPS" in capsys.readouterr().out


def test_main_is_blind_on_a_missing_ledger(tmp_path, capsys):
    code = main(["--ledger", str(tmp_path / "nope.json")])
    out = capsys.readouterr().out
    assert code == 2
    assert "verdict: BLIND" in out
    assert "not found" in out


def test_main_is_blind_on_an_unparseable_ledger(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert main(["--ledger", str(path)]) == 2
    assert "verdict: BLIND" in capsys.readouterr().out


def test_main_is_blind_when_pins_is_not_a_list(tmp_path, capsys):
    path = tmp_path / "shape.json"
    path.write_text(json.dumps({"pins": {}}), encoding="utf-8")
    assert main(["--ledger", str(path)]) == 2
    assert "no pins list" in capsys.readouterr().out


def test_main_is_blind_when_pins_is_empty(tmp_path, capsys):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"pins": []}), encoding="utf-8")
    assert main(["--ledger", str(path)]) == 2
    assert "empty pins list" in capsys.readouterr().out


def test_main_writes_a_json_report(tmp_path, capsys):
    out = tmp_path / "report.json"
    assert main(["--json", str(out)]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == VERDICT_GAPS
    assert report["pins"] == 11
    assert report["carried"] == ["E1", "E2", "E3", "E4"]
    assert sorted(report["declared_gates"]) == sorted(GATES)


def test_main_gates_flag_prints_the_table(capsys):
    assert main(["--gates"]) == 1
    out = capsys.readouterr().out
    assert "E11" in out
    assert "carried" in out


# --- gate: claims-are-pinned -----------------------------------------------

CLAIM_README = (
    "# ELIZA\n"  # 1
    "\n"  # 2
    "| layer | module | measured |\n"  # 3
    "| --- | --- | --- |\n"  # 4
    "| triage | a.py | p50 0.025 ms |\n"  # 5
    "| safety | b.py | 6 precedence tests |\n"  # 6
    "\n"  # 7
    "| limitation | status |\n"  # 8
    "| --- | --- |\n"  # 9
    "| no clinical use | open |\n"  # 10
)


def _project(tmp_path, text=CLAIM_README):
    (tmp_path / "README.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_claim_reader_takes_only_rows_of_a_measured_table(tmp_path):
    rows = readme_claim_rows(_project(tmp_path))
    assert [row["line"] for row in rows] == [5, 6]
    assert [row["surface"] for row in rows] == ["triage", "safety"]


def test_claim_reader_blinds_without_a_readme(tmp_path):
    with pytest.raises(Blinded):
        readme_claim_rows(tmp_path)


def test_claim_gate_is_silent_when_every_measured_row_is_cited(tmp_path):
    pins = [_pin(id="T1", source="README.md:5"), _pin(id="T2", source="README.md:6")]
    assert gate_claims_are_pinned(_ledger(pins), pins, _project(tmp_path), MANIFEST) == []


def test_claim_gate_accepts_a_cited_line_range(tmp_path):
    pins = [_pin(id="T1", source="README.md:5-6")]
    assert gate_claims_are_pinned(_ledger(pins), pins, _project(tmp_path), MANIFEST) == []


def test_claim_gate_fires_on_a_measured_row_no_pin_answers_for(tmp_path):
    pins = [_pin(id="T1", source="README.md:5")]
    errors = gate_claims_are_pinned(_ledger(pins), pins, _project(tmp_path), MANIFEST)
    assert len(errors) == 1
    assert errors[0].startswith("README.md:6 (safety)")
    assert "no pin answers for it" in errors[0]


def test_claim_gate_fires_on_a_citation_into_a_table_that_measures_nothing(tmp_path):
    pins = [
        _pin(id="T1", source="README.md:5"),
        _pin(id="T2", source="README.md:6"),
        _pin(id="T3", source="README.md:10"),
    ]
    errors = gate_claims_are_pinned(_ledger(pins), pins, _project(tmp_path), MANIFEST)
    assert len(errors) == 1
    assert "T3 cites README.md:10" in errors[0]
    assert "not a row of a measured table" in errors[0]


def test_claim_gate_fires_on_both_faults_at_once(tmp_path):
    pins = [_pin(id="T1", source="README.md:6"), _pin(id="T2", source="README.md:9")]
    errors = gate_claims_are_pinned(_ledger(pins), pins, _project(tmp_path), MANIFEST)
    assert len(errors) == 2


# --- loader blinding --------------------------------------------------------


def test_load_ledger_blinds_on_a_missing_file(tmp_path):
    with pytest.raises(Blinded):
        load_ledger(tmp_path / "nope.json")


def test_load_manifest_blinds_on_a_missing_manifest(tmp_path):
    with pytest.raises(Blinded):
        load_manifest(tmp_path)


def test_shipped_ledger_is_the_documented_path():
    assert DEFAULT_LEDGER.name == "marks.json"
    assert DEFAULT_LEDGER.parent.name == "eliza"
