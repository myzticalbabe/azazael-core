"""Tests for tools/esquire_admissibility.py.

Two directions are tested on purpose. A gate that rejects everything is as
useless as a gate that accepts everything, so several tests here are NEGATIVE
CONTROLS: a record that a careless test would fail must pass. The controls match
the ones published in GATE_METADATA and in docs/ESQUIRE_ADMISSIBILITY_2026-11-04.md.
"""

from __future__ import annotations

import copy
import json

import pytest

from tools.esquire_admissibility import (
    EXIT_BLIND,
    EXIT_GAPS,
    EXIT_READY,
    GAP,
    GATE_METADATA,
    PASS,
    UNVERIFIABLE,
    VERDICT_BLIND,
    VERDICT_GAPS,
    VERDICT_INCOMPLETE,
    VERDICT_READY,
    evaluate,
    main,
    record_sha256,
    render,
    render_gates,
)

SHA = "a" * 64
SHA2 = "b" * 64


def clean() -> dict:
    """A record that satisfies every gate. Each test perturbs one thing."""
    return {
        "proffer": {
            "case": "Test v. Fixture",
            "offered_by": "proponent",
            "purpose": "to show the valuation on which the offer rested",
            "ai_assistance_disclosed": True,
        },
        "witness": {
            "kind": "human",
            "name": "A. Expert",
            "qualifications": ["valuation", "ten years in the field"],
            "reviewed_output": True,
        },
        "inputs": {
            "records": [
                {"id": "r1", "source": "ledger export", "sha256": SHA},
                {"id": "r2", "source": "audited accounts", "sha256": SHA2},
            ],
            "sufficiency_statement": "both years of records, complete and hashed",
        },
        "method": {
            "model": {
                "name": "valuation-model",
                "version": "3.2.1",
                "provenance": "internal weights, frozen build, checksum on file",
            },
            "evaluation": {"error_rate": 0.04, "test_set": "240 held-out filings"},
            "peer_review": True,
            "general_acceptance": "accepted",
        },
        "claims": [
            {
                "id": "c1",
                "text": "the enterprise value on the offer date",
                "inputs": ["r1", "r2"],
                "citations": [],
            }
        ],
        "authentication": {
            "output_sha256": SHA,
            "custody": ["generated", "exported", "stored"],
        },
        "hearsay": {
            "posture": "machine_record",
            "business_records": {
                "custodian": "D. Recordskeeper",
                "regular_practice": True,
            },
        },
        "review": {
            "name": "A. Expert",
            "authority_to_change": True,
            "recorded_at": "2026-11-04",
            "changes_made": 2,
        },
    }


def criminal() -> dict:
    """The clean record in a criminal posture, with a conforming confrontation block."""
    record = clean()
    record["proffer"]["forum"] = "criminal"
    record["confrontation"] = {
        "testimonial": True,
        "basis": "a certificate of analysis prepared at the prosecution's request",
        "declarant": "A. Analyst",
        "declarant_available": True,
        "prior_opportunity_to_cross": False,
        "notice_and_demand_waiver": False,
        "surrogate_analyst": False,
    }
    return record


def administrative() -> dict:
    """The clean record in a forum that sets its own evidence law.

    No hearsay posture is added on purpose: in this posture the FRE hearsay
    question has no referent, so a record that never answers it must not be
    failed for the omission.
    """
    record = clean()
    record["proffer"]["forum"] = "administrative"
    record["administrative"] = {
        "standard": "reliable, probative, and substantial evidence on the whole record",
        "standard_source": "5 U.S.C. 556(d); agency rule 1.33",
        "disclosed_to_opposing_party": True,
        "opportunity_to_respond": True,
    }
    return record


def gate_state(report: dict, gate_id: str) -> str:
    for gate in report["gates"]:
        if gate["gate"] == gate_id:
            return gate["state"]
    raise AssertionError(f"gate {gate_id} missing from report")


def test_clean_record_is_ready():
    report = evaluate(clean())
    assert report["verdict"] == VERDICT_READY
    assert report["counts"] == {PASS: 7, GAP: 0, UNVERIFIABLE: 0}


def test_negative_control_daubert_factors_are_not_a_checklist():
    """A novel, unreviewed method with a measured error rate must still pass.

    The Daubert factors are non-exhaustive; requiring all four would encode an
    error of law as a test. This is the control that keeps G3 honest.
    """
    record = clean()
    record["method"]["peer_review"] = False
    record["method"]["general_acceptance"] = "novel"

    report = evaluate(record)

    assert report["verdict"] == VERDICT_READY
    assert gate_state(report, "G3") == PASS
    notes = [n for gate in report["gates"] for n in gate["notes"]]
    assert any("peer review" in note for note in notes)
    assert any("novel" in note for note in notes)


def test_negative_control_opinion_without_citations_passes():
    """Citation integrity is conditional: a measurement carries no authorities."""
    record = clean()
    assert record["claims"][0]["citations"] == []
    assert gate_state(evaluate(record), "G4") == PASS


def test_negative_control_reviewer_who_changed_nothing_passes():
    record = clean()
    record["review"]["changes_made"] = 0

    report = evaluate(record)

    assert report["verdict"] == VERDICT_READY
    notes = [n for gate in report["gates"] for n in gate["notes"]]
    assert any("changed nothing" in note for note in notes)


def test_fully_certified_output_passes_without_custody_chain():
    """FRE 902(13) is an alternative route to G5, not a supplement."""
    record = clean()
    record["authentication"] = {
        "output_sha256": SHA,
        "custody": [],
        "certification_902_13": {
            "certifier": "C. Custodian",
            "qualified": True,
            "notice_given": True,
        },
    }

    report = evaluate(record)

    assert report["verdict"] == VERDICT_READY
    assert gate_state(report, "G5") == PASS


def test_model_as_witness_is_a_gap():
    record = clean()
    record["witness"] = {"kind": "model", "name": "valuation-model-3"}

    report = evaluate(record)

    assert gate_state(report, "G1") == GAP
    assert report["verdict"] == VERDICT_GAPS
    assert "natural person" in render(report)


def test_absent_witness_is_unaddressed_not_failed():
    record = clean()
    del record["witness"]

    report = evaluate(record)

    assert gate_state(report, "G1") == UNVERIFIABLE
    assert report["verdict"] == VERDICT_INCOMPLETE


def test_fabricated_citation_is_a_gap_naming_mata():
    record = clean()
    record["claims"][0]["citations"] = [
        {"locator": "999 F.3d 1 (2099)", "verification": "fabricated"}
    ]

    report = evaluate(record)

    assert gate_state(report, "G4") == GAP
    detail = next(g["detail"] for g in report["gates"] if g["gate"] == "G4")
    assert "Mata" in detail


def test_unverified_citation_is_a_gap():
    record = clean()
    record["claims"][0]["citations"] = [
        {"locator": "410 U.S. 113", "verification": "recalled"}
    ]

    assert gate_state(evaluate(record), "G4") == GAP


def test_citation_read_at_source_passes():
    record = clean()
    record["claims"][0]["citations"] = [
        {"locator": "509 U.S. 579", "verification": "primary_read"}
    ]

    assert gate_state(evaluate(record), "G4") == PASS


def test_claim_tied_to_unknown_input_is_a_gap():
    record = clean()
    record["claims"][0]["inputs"] = ["r1", "r-does-not-exist"]

    report = evaluate(record)

    assert gate_state(report, "G4") == GAP
    assert "unknown input" in render(report)


def test_unversioned_model_is_a_gap():
    record = clean()
    del record["method"]["model"]["version"]

    assert gate_state(evaluate(record), "G3") == GAP


def test_impossible_error_rate_is_a_gap():
    record = clean()
    record["method"]["evaluation"]["error_rate"] = 1.5

    assert gate_state(evaluate(record), "G3") == GAP


def test_input_without_hash_is_a_gap():
    record = clean()
    record["inputs"]["records"][0]["sha256"] = "not-a-hash"

    assert gate_state(evaluate(record), "G2") == GAP


def test_no_inputs_is_unaddressed():
    record = clean()
    record["inputs"]["records"] = []

    assert gate_state(evaluate(record), "G2") == UNVERIFIABLE


def test_unauthenticated_output_is_unaddressed():
    record = clean()
    del record["authentication"]

    assert gate_state(evaluate(record), "G5") == UNVERIFIABLE


def test_incomplete_certification_names_the_missing_element():
    record = clean()
    record["authentication"] = {
        "output_sha256": SHA,
        "custody": [],
        "certification_902_13": {
            "certifier": "C. Custodian",
            "qualified": True,
            "notice_given": False,
        },
    }

    report = evaluate(record)

    assert gate_state(report, "G5") == GAP
    assert "notice_given" in render(report)


def test_hearsay_exception_without_notice_is_a_gap():
    record = clean()
    record["hearsay"] = {
        "posture": "offered_for_truth_exception",
        "exception": "FRE 803(6)",
        "notice_given": False,
    }

    assert gate_state(evaluate(record), "G6") == GAP


def test_machine_record_without_a_predicate_is_a_gap():
    record = clean()
    record["hearsay"] = {"posture": "machine_record"}

    assert gate_state(evaluate(record), "G6") == GAP


def test_nominal_reviewer_is_a_gap():
    """A reviewer with no power to change the output is the fig leaf E-5 names."""
    record = clean()
    record["review"]["authority_to_change"] = False

    report = evaluate(record)

    assert gate_state(report, "G7") == GAP
    assert "authority" in render(report)


def test_undisclosed_ai_assistance_is_a_gap():
    record = clean()
    record["proffer"]["ai_assistance_disclosed"] = False

    assert gate_state(evaluate(record), "G7") == GAP


def test_unreadable_record_is_blind_never_a_pass():
    for bad in ({}, {"proffer": "not-an-object"}, [], None):
        report = evaluate(bad)
        assert report["verdict"] == VERDICT_BLIND
        assert report["gates"] == []
        assert "admissibility is decided" in report["disclaimer"]


def test_evaluation_is_deterministic_and_key_order_independent():
    first = clean()
    second = copy.deepcopy(first)
    # Rebuild with reversed insertion order to prove the hash ignores ordering.
    second = {key: second[key] for key in reversed(list(second))}

    report_a = evaluate(first)
    report_b = evaluate(second)

    assert record_sha256(first) == record_sha256(second)
    assert report_a == report_b


def test_render_never_claims_admissibility():
    for record in (clean(), {}, {**clean(), "witness": {"kind": "model"}}):
        text = render(evaluate(record)).lower()
        assert "admissible" not in text.replace("admissibility", "")


def test_cli_ready_exits_zero(tmp_path, capsys):
    path = tmp_path / "proffer.json"
    path.write_text(json.dumps(clean()), encoding="utf-8")

    assert main([str(path)]) == EXIT_READY
    assert "READY" in capsys.readouterr().out


def test_cli_gaps_exit_one_and_json_mode(tmp_path, capsys):
    record = clean()
    record["review"]["authority_to_change"] = False
    path = tmp_path / "proffer.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    assert main([str(path), "--json"]) == EXIT_GAPS
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == VERDICT_GAPS


def test_cli_missing_file_is_blind_and_never_zero(tmp_path, capsys):
    assert main([str(tmp_path / "absent.json")]) == EXIT_BLIND
    assert "BLIND" in capsys.readouterr().out


def test_cli_invalid_json_is_blind(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    assert main([str(path)]) == EXIT_BLIND
    assert "BLIND" in capsys.readouterr().out


def test_gate_table_covers_every_gate_and_states_a_falsifier():
    assert [meta["id"] for meta in GATE_METADATA] == [f"G{i}" for i in range(1, 11)]
    for meta in GATE_METADATA:
        assert meta["authority"]
        assert meta["falsifier"]
        assert meta["negative_control"]
    assert GATE_METADATA[7]["applies_to"] == "criminal posture only"
    assert GATE_METADATA[8]["applies_to"] == "criminal posture only"
    assert GATE_METADATA[9]["applies_to"] == "administrative and arbitration postures only"
    text = render_gates()
    assert "negative control" in text
    assert "G7" in text
    assert "G9" in text
    assert "G10" in text


@pytest.mark.parametrize("gate_id", [f"G{i}" for i in range(1, 11)])
def test_every_gate_can_fail(gate_id):
    """Each gate must be able to return GAP, or it is not a test.

    Without this, a gate that silently returns PASS for everything would keep
    the clean-record test green while testing nothing.
    """
    perturbations = {
        "G1": lambda r: r["witness"].update({"kind": "model"}),
        "G2": lambda r: r["inputs"]["records"][0].update({"sha256": "x"}),
        "G3": lambda r: r["method"]["model"].pop("version"),
        "G4": lambda r: r["claims"][0].update({"inputs": ["nope"]}),
        "G5": lambda r: r["authentication"].update({"output_sha256": "x"}),
        "G6": lambda r: r.update({"hearsay": {"posture": "machine_record"}}),
        "G7": lambda r: r["review"].update({"authority_to_change": False}),
        "G8": lambda r: r["confrontation"].update({"testimonial": "maybe"}),
        "G9": lambda r: r["confrontation"].update({"surrogate_analyst": True}),
        "G10": lambda r: r["administrative"].update(
            {"disclosed_to_opposing_party": False}
        ),
    }
    if gate_id in {"G8", "G9"}:
        record = criminal()
    elif gate_id == "G10":
        record = administrative()
    else:
        record = clean()
    perturbations[gate_id](record)

    assert gate_state(evaluate(record), gate_id) == GAP


@pytest.mark.parametrize(
    ("gate_id", "fixture", "block"),
    [
        ("G1", clean, "witness"),
        ("G2", clean, "inputs"),
        ("G3", clean, "method"),
        ("G4", clean, "claims"),
        ("G5", clean, "authentication"),
        ("G6", clean, "hearsay"),
        ("G7", clean, "review"),
        ("G8", criminal, "confrontation"),
        ("G9", criminal, "confrontation"),
        ("G10", administrative, "administrative"),
    ],
)
def test_every_gate_reads_a_missing_block_as_unaddressed(gate_id, fixture, block):
    """A gate handed a record that never addresses its question must say
    UNVERIFIABLE, not GAP.

    G10 draws this line (test 644 of the 2026-11-04 audit: an unstated field is
    unaddressed, a stated False is a gap) and the audit it came from demanded
    the same shape at block level in every posture. A gate that reads absence
    as contradiction accuses a silent proffer of a failing fact it never
    asserted. Unaddressed is still not READY — the verdict assertion below
    holds — and every block here is one the gate reads with no default.
    """
    record = fixture()
    del record[block]

    report = evaluate(record)

    assert gate_state(report, gate_id) == UNVERIFIABLE
    assert report["verdict"] != VERDICT_READY


def test_civil_record_applies_seven_gates_and_says_so():
    """The forum default is printed: a missing key must not drop the gates silently."""
    report = evaluate(clean())

    assert report["verdict"] == VERDICT_READY
    assert report["forum"] == "civil"
    assert report["forum_source"] == "unstated"
    assert report["gates_applied"] == [f"G{i}" for i in range(1, 8)]
    text = render(report)
    assert "forum: civil (unstated)" in text
    assert "NOT applied" in text


def test_criminal_record_applies_nine_gates():
    report = evaluate(criminal())

    assert report["verdict"] == VERDICT_READY
    assert report["counts"] == {PASS: 9, GAP: 0, UNVERIFIABLE: 0}
    assert report["gates_applied"] == [f"G{i}" for i in range(1, 10)]


def test_criminal_record_without_confrontation_is_incomplete_not_ready():
    record = criminal()
    del record["confrontation"]

    report = evaluate(record)

    assert gate_state(report, "G8") == UNVERIFIABLE
    assert gate_state(report, "G9") == UNVERIFIABLE
    assert report["verdict"] == VERDICT_INCOMPLETE


def test_negative_control_non_testimonial_with_a_basis_passes():
    """An output that stayed in the regular course of business is not testimony."""
    record = criminal()
    record["confrontation"] = {
        "testimonial": False,
        "basis": "a routine billing export produced before any dispute existed",
    }

    report = evaluate(record)

    assert gate_state(report, "G8") == PASS
    assert gate_state(report, "G9") == PASS
    assert report["verdict"] == VERDICT_READY


def test_bare_non_testimonial_assertion_is_a_gap():
    record = criminal()
    record["confrontation"] = {"testimonial": False}

    assert gate_state(evaluate(record), "G8") == GAP


def test_certificate_prepared_for_litigation_is_not_non_testimonial():
    """A complete 902(13) certificate is the very document Melendez-Diaz calls testimonial."""
    record = criminal()
    record["authentication"] = {
        "output_sha256": SHA,
        "custody": [],
        "certification_902_13": {
            "certifier": "C. Custodian",
            "qualified": True,
            "notice_given": True,
        },
    }
    record["confrontation"] = {
        "testimonial": False,
        "basis": "a custodian signed a certificate",
    }

    report = evaluate(record)

    assert gate_state(report, "G8") == GAP
    detail = next(g["detail"] for g in report["gates"] if g["gate"] == "G8")
    assert "Melendez-Diaz" in detail


def test_unavailable_declarant_without_prior_opportunity_is_a_gap():
    record = criminal()
    record["confrontation"]["declarant_available"] = False
    record["confrontation"]["prior_opportunity_to_cross"] = False

    report = evaluate(record)

    assert gate_state(report, "G9") == GAP
    detail = next(g["detail"] for g in report["gates"] if g["gate"] == "G9")
    assert "Crawford" in detail


def test_negative_control_prior_cross_examination_cures_unavailability():
    record = criminal()
    record["confrontation"]["declarant_available"] = False
    record["confrontation"]["prior_opportunity_to_cross"] = True

    report = evaluate(record)

    assert gate_state(report, "G9") == PASS
    assert report["verdict"] == VERDICT_READY


def test_surrogate_analyst_is_a_gap():
    record = criminal()
    record["confrontation"]["surrogate_analyst"] = True

    report = evaluate(record)

    assert gate_state(report, "G9") == GAP
    assert "Bullcoming" in next(
        g["detail"] for g in report["gates"] if g["gate"] == "G9"
    )


def test_notice_and_demand_waiver_passes_and_states_its_limit():
    record = criminal()
    record["confrontation"]["declarant_available"] = False
    record["confrontation"]["notice_and_demand_waiver"] = True

    report = evaluate(record)

    assert gate_state(report, "G9") == PASS
    assert "demand window" in render(report)


def test_unnamed_declarant_is_a_gap():
    record = criminal()
    record["confrontation"]["declarant"] = ""

    assert gate_state(evaluate(record), "G9") == GAP


def test_named_declarant_without_availability_is_unaddressed():
    record = criminal()
    del record["confrontation"]["declarant_available"]

    report = evaluate(record)

    assert gate_state(report, "G9") == UNVERIFIABLE
    assert report["verdict"] == VERDICT_INCOMPLETE


def test_unrecognised_forum_is_read_as_civil_and_names_the_value():
    record = clean()
    record["proffer"]["forum"] = "juvenile"

    report = evaluate(record)

    assert report["forum"] == "civil"
    assert report["forum_source"] == "unrecognised"
    assert "juvenile" in report["forum_note"]
    assert "G8" not in report["gates_applied"]


def test_negative_control_confrontation_block_cannot_affect_a_civil_record():
    """A civil record carrying a defective confrontation block is not judged by G8/G9."""
    record = clean()
    record["confrontation"] = {"testimonial": True, "surrogate_analyst": True}

    report = evaluate(record)

    assert report["verdict"] == VERDICT_READY
    assert report["gates_applied"] == [f"G{i}" for i in range(1, 8)]
    text = render(report)
    gate_lines = [line for line in text.splitlines() if line.startswith(("G8", "G9"))]
    assert gate_lines == []
    assert "surrogate" not in text.lower()


def test_administrative_record_applies_g1_to_g5_g7_and_g10():
    """A forum with its own evidence law is not asked the FRE questions."""
    report = evaluate(administrative())

    assert report["verdict"] == VERDICT_READY
    assert report["counts"] == {PASS: 7, GAP: 0, UNVERIFIABLE: 0}
    assert report["gates_applied"] == ["G1", "G2", "G3", "G4", "G5", "G7", "G10"]
    assert report["forum"] == "administrative"
    assert report["forum_source"] == "stated"
    text = render(report)
    assert "forum: administrative (stated)" in text
    assert "G6 (FRE hearsay)" in text
    assert "G10" in text
    assert [line for line in text.splitlines() if line.startswith(("G6", "G8", "G9"))] == []


def test_administrative_record_without_the_block_is_incomplete_not_a_pass():
    record = administrative()
    del record["administrative"]

    report = evaluate(record)

    assert gate_state(report, "G10") == UNVERIFIABLE
    assert report["verdict"] == VERDICT_INCOMPLETE


def test_standard_without_its_source_is_unaddressed():
    """Naming a standard the record cannot source is an omission, not a pass."""
    record = administrative()
    del record["administrative"]["standard_source"]

    report = evaluate(record)

    assert gate_state(report, "G10") == UNVERIFIABLE
    assert "without the rule, agreement or guidance" in report["gates"][-1]["detail"]


def test_undisclosed_output_is_a_gap_not_an_omission():
    record = administrative()
    record["administrative"]["disclosed_to_opposing_party"] = False

    report = evaluate(record)

    assert gate_state(report, "G10") == GAP
    assert report["verdict"] == VERDICT_GAPS


def test_no_opportunity_to_respond_is_a_gap_naming_matthews():
    record = administrative()
    record["administrative"]["opportunity_to_respond"] = False

    report = evaluate(record)

    assert gate_state(report, "G10") == GAP
    detail = [g["detail"] for g in report["gates"] if g["gate"] == "G10"][0]
    assert "Mathews" in detail


def test_arbitration_is_read_under_the_administrative_posture():
    """The declared value is quoted back: a posture label must not overwrite it."""
    record = administrative()
    record["proffer"]["forum"] = "arbitration"

    report = evaluate(record)

    assert report["forum"] == "administrative"
    assert report["forum_source"] == "stated"
    assert "'arbitration'" in report["forum_note"]
    assert "G10" in report["gates_applied"]
    assert "G6" not in report["gates_applied"]
    assert "G8" not in report["gates_applied"]
    assert "G9" not in report["gates_applied"]


def test_negative_control_bare_arbitration_record_satisfies_g10():
    """Arbitration has the least evidence law of any forum; G10 must not demand FRE paperwork."""
    record = administrative()
    record["proffer"]["forum"] = "arbitration"
    del record["hearsay"]
    del record["authentication"]["custody"]
    record["administrative"] = {
        "standard": "the tribunal decides admissibility, and hears any evidence it "
        "considers material",
        "standard_source": "arbitration agreement clause 9",
        "disclosed_to_opposing_party": True,
        "opportunity_to_respond": True,
    }

    assert gate_state(evaluate(record), "G10") == PASS


def test_negative_control_administrative_block_cannot_affect_a_civil_record():
    """A gate that fired outside its posture would be an error of law dressed as rigour."""
    record = clean()
    record["administrative"] = {"disclosed_to_opposing_party": False}

    report = evaluate(record)

    assert report["verdict"] == VERDICT_READY
    assert report["gates_applied"] == [f"G{i}" for i in range(1, 8)]
    assert "G10" not in report["gates_applied"]
