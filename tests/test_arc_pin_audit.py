"""Tests for tools/arc_pin_audit.py.

The audit answers one question per wing: does every surface the wing *declares* have
a pin answering for it? It is a second, independent reading of the same ledgers the
wings' own checkers grade, so the tests pin down three things a wrong audit would
get away with otherwise:

* a surface losing its pins is reported (not silently tolerated),
* a pin answering for a surface that is not declared is reported as an orphan,
* the control BITES -- the wing's own checker turns red when the pins are removed.
  An audit whose control has only ever been seen to pass is untested: the first
  version of this control deleted ONE pin, esquire shares its gates between pins,
  and the checker was right to stay green while the control called it SILENT.
"""

from __future__ import annotations

import json

import pytest

from tools import arc_pin_audit as audit

MINIMAL_README = (
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


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _ledger_pins(path):
    return json.loads(path.read_text(encoding="utf-8"))["pins"]


# --- esquire -----------------------------------------------------------------


def test_esquire_reports_a_gate_whose_pins_are_gone(monkeypatch, tmp_path):
    real = audit.read_json(audit.ESQUIRE_LEDGER)
    kept = [pin for pin in real["pins"] if "G1" not in (pin.get("covers") or [])]
    assert len(kept) < len(real["pins"]), "fixture assumption: some pin covers G1"
    ledger = _write(tmp_path / "pins.json", {"pins": kept})
    monkeypatch.setattr(audit, "ESQUIRE_LEDGER", ledger)

    row = audit.esquire_inventory()
    assert row["wing"] == "esquire"
    assert row["missing"], row
    assert any(entry.startswith("G1 ") for entry in row["missing"]), row["missing"]
    assert row["pinned"] < row["declared"]


def test_esquire_reports_a_pin_that_covers_nothing(monkeypatch, tmp_path):
    real = audit.read_json(audit.ESQUIRE_LEDGER)
    pins = list(real["pins"]) + [{"id": "PX", "covers": [], "asserts": "nothing"}]
    ledger = _write(tmp_path / "pins.json", {"pins": pins})
    monkeypatch.setattr(audit, "ESQUIRE_LEDGER", ledger)

    assert "PX" in audit.esquire_inventory()["orphans"]


def test_esquire_blinds_on_a_missing_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "ESQUIRE_LEDGER", tmp_path / "nope.json")
    with pytest.raises(audit.Blinded):
        audit.esquire_inventory()


# --- cain --------------------------------------------------------------------


def test_cain_reports_an_architecture_whose_pin_is_gone(monkeypatch, tmp_path):
    real = audit.read_json(audit.CAIN_LEDGER)
    victim = real["pins"][0]
    kept = [pin for pin in real["pins"] if pin is not victim]
    ledger = _write(tmp_path / "marks.json", {"pins": kept})
    monkeypatch.setattr(audit, "CAIN_LEDGER", ledger)

    row = audit.cain_inventory()
    expected = "%s / %s" % (victim["vendor"], victim["architecture"])
    assert expected in row["missing"], (expected, row["missing"])


def test_cain_reports_a_pin_for_an_architecture_the_mark_does_not_publish(
    monkeypatch, tmp_path
):
    real = audit.read_json(audit.CAIN_LEDGER)
    pins = list(real["pins"]) + [
        {"id": "CX", "vendor": "Initech", "architecture": "Tape Drive"}
    ]
    ledger = _write(tmp_path / "marks.json", {"pins": pins})
    monkeypatch.setattr(audit, "CAIN_LEDGER", ledger)

    assert any(entry.startswith("CX ") for entry in audit.cain_inventory()["orphans"])


# --- eliza -------------------------------------------------------------------


def _eliza(monkeypatch, tmp_path, readme_text, pins):
    project = tmp_path / "Project_ELIZA"
    project.mkdir(parents=True, exist_ok=True)
    (project / "README.md").write_text(readme_text, encoding="utf-8")
    ledger = _write(tmp_path / "eliza.json", {"pins": pins})
    monkeypatch.setattr(audit, "ELIZA_PROJECT", project)
    monkeypatch.setattr(audit, "ELIZA_LEDGER", ledger)
    return project


def test_eliza_counts_only_rows_of_a_measured_table(monkeypatch, tmp_path):
    _eliza(monkeypatch, tmp_path, MINIMAL_README, [])
    row = audit.eliza_inventory()
    assert row["declared"] == 2, "the status table at line 8 measures nothing"
    assert row["pinned"] == 0
    assert row["missing"] == ["README.md:5 triage", "README.md:6 safety"]


def test_eliza_reads_a_citation_range_as_every_line_it_names(monkeypatch, tmp_path):
    _eliza(monkeypatch, tmp_path, MINIMAL_README, [{"id": "E1", "source": "README.md:5-6"}])
    row = audit.eliza_inventory()
    assert row["missing"] == []
    assert row["pinned"] == 2


def test_eliza_reports_a_citation_into_a_row_that_measures_nothing(monkeypatch, tmp_path):
    _eliza(
        monkeypatch,
        tmp_path,
        MINIMAL_README,
        [{"id": "E1", "source": "README.md:5-6"}, {"id": "E2", "source": "README.md:10"}],
    )
    row = audit.eliza_inventory()
    assert row["missing"] == []
    assert row["orphans"] == ["README.md:10 (E2)"]


def test_eliza_blinds_without_a_readme(monkeypatch, tmp_path):
    project = tmp_path / "Project_ELIZA"
    project.mkdir()
    monkeypatch.setattr(audit, "ELIZA_PROJECT", project)
    monkeypatch.setattr(audit, "ELIZA_LEDGER", _write(tmp_path / "e.json", {"pins": []}))
    with pytest.raises(audit.Blinded):
        audit.eliza_inventory()


# --- verdict / exit codes ----------------------------------------------------


def test_verdict_is_clean_only_when_no_wing_has_a_defect():
    assert audit.verdict([{"missing": [], "orphans": []}]) == audit.VERDICT_CLEAN
    assert audit.verdict([{"missing": ["G1 x"], "orphans": []}]) == audit.VERDICT_MISSING


def test_an_orphan_pin_is_a_defect_not_blindness():
    """BLIND means nothing could be read; a pin answering for an undeclared surface is read fine."""
    rows = [{"missing": [], "orphans": ["P1"]}]
    assert audit.verdict(rows) == audit.VERDICT_MISSING


def test_exit_codes_keep_blind_distinct_from_missing():
    assert audit.exit_code_for(audit.VERDICT_CLEAN) == 0
    assert audit.exit_code_for(audit.VERDICT_MISSING) == 1
    assert audit.exit_code_for(audit.VERDICT_BLIND) == 2


def test_main_returns_two_when_the_declaration_cannot_be_read(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(audit, "ESQUIRE_LEDGER", tmp_path / "nope.json")
    assert audit.main(["--wing", "esquire"]) == 2
    out = capsys.readouterr().out
    assert "verdict: BLIND" in out
    assert "reason:" in out


def test_only_flag_runs_one_wing():
    assert [row["wing"] for row in audit.audit("eliza")] == ["eliza"]


# --- the control -------------------------------------------------------------


def test_control_bites_when_every_supporter_of_one_surface_is_removed(monkeypatch, tmp_path):
    """The wing's own checker must turn red -- otherwise the binding is decorative."""
    _eliza(
        monkeypatch,
        tmp_path,
        MINIMAL_README,
        [{"id": "E1", "source": "README.md:5"}, {"id": "E2", "source": "README.md:6"}],
    )
    row = audit.eliza_inventory()
    control = audit.prove(row, audit.ELIZA_LEDGER, "eliza_arc_pins")
    assert control["surface"] == "README.md:5"
    assert control["deleted"] == "E1"
    assert control["exit"] != 0, control["detail"]
    assert control["control"] == "BITES"


def test_control_blinds_when_no_surface_is_pinned():
    row = {"wing": "eliza", "_supporters": [("README.md:5", [])]}
    with pytest.raises(audit.Blinded):
        audit.prove(row, audit.ELIZA_LEDGER, "eliza_arc_pins")


def test_control_blinds_when_the_named_pin_is_absent(monkeypatch, tmp_path):
    ledger = _write(tmp_path / "e.json", {"pins": [{"id": "E9", "source": "README.md:5"}]})
    row = {"wing": "eliza", "_supporters": [("README.md:5", ["E1"])]}
    with pytest.raises(audit.Blinded):
        audit.prove(row, ledger, "eliza_arc_pins")


# --- the shipped tree --------------------------------------------------------


def test_shipped_tree_declares_a_pin_for_every_esquire_and_cain_surface():
    rows = {row["wing"]: row for row in audit.audit()}
    assert rows["esquire"]["missing"] == [], rows["esquire"]["missing"]
    assert rows["cain"]["missing"] == [], rows["cain"]["missing"]
    assert rows["esquire"]["orphans"] == [], rows["esquire"]["orphans"]
    assert rows["cain"]["orphans"] == [], rows["cain"]["orphans"]


def test_shipped_tree_eliza_rows_without_a_pin_are_exactly_three():
    """A ratchet on the eliza gap: a fourth unpinned reading fails this test."""
    rows = {row["wing"]: row for row in audit.audit()}
    assert rows["eliza"]["missing"] == [
        "README.md:32 bypass",
        "README.md:33 ultra-loose",
        "README.md:34 concurrent",
    ]
