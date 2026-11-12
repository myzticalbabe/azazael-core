# coding: utf-8
"""Controls for tools/wire_evidence_audit.py.

RULE realised here: a `wired` declaration must name the link that supports it.
The classifier has three verdicts and each gets a fixture whose contents differ
from the others by exactly the property under test -- a class registered with
`@register_worm`, a class referenced from another module, and a class described
in its own file only.

Why the negative control matters: 136 of 137 wired rows in this repo carried
`module-linked: no` in their own evidence before 2026-10-19. A classifier that
answered REGISTRY for everything would have "fixed" that by asserting a link
that may not exist, so the isolated fixture must come back NONE.
"""

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import wire_evidence_audit as audit  # noqa: E402 - after sys.path


DEAD = '''\
class LonelyWorm:
    def __init__(self):
        self.x = 1

    def execute(self):
        return self.x
'''

REGISTERED = '''\
def register_worm(**kw):
    def deco(cls):
        return cls
    return deco


@register_worm(category="test", priority=1, cooldown_seconds=1.0)
class WiredWorm:
    def __init__(self):
        self.x = 1

    def execute(self):
        return self.x
'''

CONSUMER = '''\
from core.registered import WiredWorm


def build():
    return WiredWorm()
'''


def _write(tmp_path, name, body):
    path = Path(tmp_path) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _table(*ids):
    return {"declared": {i: {"status": "wired", "reason": "fixture"}
                         for i in ids}}


def _row(rows, key):
    matches = [r for r in rows if r["id"] == key]
    assert matches, "no row for %s in %r" % (key, [r["id"] for r in rows])
    return matches[0]


# -- the three verdicts ----------------------------------------------------

def test_register_worm_decorator_is_registry_evidence(tmp_path):
    defined = _write(tmp_path, "registered.py", REGISTERED)
    rows = audit.classify(_table("t:WiredWorm.execute"), [defined])
    row = _row(rows, "t:WiredWorm.execute")
    assert row["verdict"] == "REGISTRY", row
    assert "@register_worm" in row["evidence"], row["evidence"]
    # `detail` is rendered as text (a str) so it can be JSON-serialised.
    assert Path(row["detail"]).name == "registered.py", row["detail"]


def test_a_reference_from_another_module_is_module_evidence(tmp_path):
    defined = _write(tmp_path, "plain.py", DEAD.replace("LonelyWorm", "SharedWorm"))
    _write(tmp_path, "consumer.py", "from plain import SharedWorm\n\nSharedWorm()\n")
    rows = audit.classify(_table("t:SharedWorm.execute"),
                          [defined, Path(tmp_path) / "consumer.py"])
    row = _row(rows, "t:SharedWorm.execute")
    assert row["verdict"] == "MODULE", row
    assert "consumer.py" in row["evidence"], row["evidence"]


def test_an_isolated_class_has_no_link(tmp_path):
    """The negative control: registration absent, no reference anywhere."""
    defined = _write(tmp_path, "lonely.py", DEAD)
    rows = audit.classify(_table("t:LonelyWorm.execute"), [defined])
    row = _row(rows, "t:LonelyWorm.execute")
    assert row["verdict"] == "NONE", row
    assert row["evidence"] == "", row


def test_prose_naming_the_class_is_not_a_module_link(tmp_path):
    """A docstring mention is not a caller -- the 2026-10-19 false positive.

    `NeuralPipeline.py:7` names `SchemaAwareFeatureExtractor` in its module
    docstring. A line-text matcher scored that as a link; the AST does not,
    because a string constant references nothing.
    """
    defined = _write(tmp_path, "plain.py", DEAD.replace("LonelyWorm", "ProseWorm"))
    prose = _write(tmp_path, "doc.py", '\"\"\"Mentions ProseWorm in prose only.\"\"\"\n')
    rows = audit.classify(_table("t:ProseWorm.execute"), [defined, prose])
    row = _row(rows, "t:ProseWorm.execute")
    assert row["verdict"] == "NONE", row
    assert row["evidence"] == "", row


def test_a_call_outranks_a_bare_name_reference(tmp_path):
    """Two referencing files: the call is the evidence that is reported."""
    defined = _write(tmp_path, "plain.py", DEAD.replace("LonelyWorm", "RankedWorm"))
    annotation = _write(tmp_path, "types_only.py",
                        "from plain import RankedWorm\n\nAlias = RankedWorm\n")
    caller = _write(tmp_path, "caller.py",
                    "from plain import RankedWorm\n\n\ndef go():\n    return RankedWorm()\n")
    rows = audit.classify(_table("t:RankedWorm.execute"),
                          [defined, annotation, caller])
    evidence = _row(rows, "t:RankedWorm.execute")["evidence"]
    assert "caller.py" in evidence and "calls RankedWorm" in evidence, evidence


def test_a_missing_definition_is_reported_not_guessed(tmp_path):
    rows = audit.classify(_table("t:Absent.execute"), [])
    row = _row(rows, "t:Absent.execute")
    assert row["verdict"] == "NONE", row
    assert "not found" in row["detail"], row


# -- apply / check ---------------------------------------------------------

def test_apply_writes_link_evidence_and_leaves_status_alone(tmp_path):
    defined = _write(tmp_path, "registered.py", REGISTERED)
    table = _table("t:WiredWorm.execute")
    rows = audit.classify(table, [defined])
    touched = audit.apply(table, rows)
    assert touched == 1, touched
    rec = table["declared"]["t:WiredWorm.execute"]
    assert rec["status"] == "wired", rec
    assert rec["link_kind"] == "registry", rec
    assert "@register_worm" in rec["link_evidence"], rec
    assert "link_gap" not in rec, rec


def test_apply_records_a_named_gap_instead_of_a_link(tmp_path):
    defined = _write(tmp_path, "lonely.py", DEAD)
    table = _table("t:LonelyWorm.execute")
    audit.apply(table, audit.classify(table, [defined]))
    rec = table["declared"]["t:LonelyWorm.execute"]
    assert rec["status"] == "wired", rec
    assert "link_evidence" not in rec, rec
    assert "PF16 silence alone" in rec["link_gap"], rec


def test_apply_retracts_a_link_that_no_longer_holds(tmp_path):
    """A stale claim must be withdrawn, not left beside the new gap."""
    defined = _write(tmp_path, "lonely.py", DEAD)
    table = _table("t:LonelyWorm.execute")
    rec = table["declared"]["t:LonelyWorm.execute"]
    rec["link_evidence"] = "stale.py:1 calls LonelyWorm"
    rec["link_kind"] = "module"
    audit.apply(table, audit.classify(table, [defined]))
    assert "link_evidence" not in rec, rec
    assert "link_kind" not in rec, rec
    assert "PF16 silence alone" in rec["link_gap"], rec


def test_apply_is_idempotent(tmp_path):
    defined = _write(tmp_path, "registered.py", REGISTERED)
    table = _table("t:WiredWorm.execute")
    rows = audit.classify(table, [defined])
    assert audit.apply(table, rows) == 1
    assert audit.apply(table, audit.classify(table, [defined])) == 0


def test_check_fails_on_a_wired_row_with_no_link_record(tmp_path, monkeypatch,
                                                       capsys):
    """A wired row added without evidence is what --check exists to catch."""
    defined = _write(tmp_path, "registered.py", REGISTERED)
    ledger = Path(tmp_path) / "ledger.json"
    ledger.write_text(json.dumps(_table("t:WiredWorm.execute")), encoding="utf-8")
    monkeypatch.setattr(audit, "LEDGER", ledger)
    monkeypatch.setattr(audit, "_py_files", lambda: [defined])
    assert audit.main(["--check"]) == 1
    assert "neither a link nor a gap" in capsys.readouterr().out


def test_check_fails_when_a_row_has_neither_link_nor_gap(tmp_path, monkeypatch,
                                                         capsys):
    """The probe shape that slipped through: no defining module, no record.

    A row keyed to a class that exists nowhere classified as NONE and passed
    `--check`, because the check only inspected rows a link had been FOUND
    for. The gate's stated contract is "evidence or a named gap"; this pins
    it to that, for every wired row.
    """
    defined = _write(tmp_path, "registered.py", REGISTERED)
    ledger = Path(tmp_path) / "ledger.json"
    ledger.write_text(json.dumps(_table("t:AbsentWorm.execute")), encoding="utf-8")
    monkeypatch.setattr(audit, "LEDGER", ledger)
    monkeypatch.setattr(audit, "_py_files", lambda: [defined])
    assert audit.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "neither a link nor a gap" in out, out
    assert "t:AbsentWorm.execute" in out, out


def test_check_passes_after_apply(tmp_path, monkeypatch, capsys):
    defined = _write(tmp_path, "registered.py", REGISTERED)
    ledger = Path(tmp_path) / "ledger.json"
    ledger.write_text(json.dumps(_table("t:WiredWorm.execute")), encoding="utf-8")
    monkeypatch.setattr(audit, "LEDGER", ledger)
    monkeypatch.setattr(audit, "_py_files", lambda: [defined])
    assert audit.main(["--apply"]) == 0
    capsys.readouterr()
    assert audit.main(["--check"]) == 0
    assert "OK:" in capsys.readouterr().out
