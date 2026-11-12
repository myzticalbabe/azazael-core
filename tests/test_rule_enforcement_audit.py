# coding: utf-8
"""Controls for tools/rule_enforcement_audit.py.

RULE realised here: a mandate only counts as enforced when something
executable can say NO. These controls pin the three verdicts and the exit-code
contract, and -- because a rule auditor that reports everything ENFORCED would
pass a naive test -- each verdict has its own fixture whose contents differ
from the others by exactly the property under test.

What is NOT covered, deliberately: the real `MANDATES.md`. These fixtures are
shaped like it (a `## RULE 0A:` heading, an em-dash `### RULE 0Z-L —` heading,
a sub-rule) but a fixture cannot prove the parser meets the live file. Run the
tool itself for that; the fixtures prove the *classification logic* is not
vacuous.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import rule_enforcement_audit as auditor  # noqa: E402 - after sys.path

# Two headings plus a duplicate and a sub-rule. The em-dash form is real: RULE
# 0Z-L in MANDATES.md uses "—", and a colon-only parser drops it.
MANDATES = """# MANDATES (fixture)

## RULE 0A: ALWAYS VERIFY (CRITICAL)

Prose that names the rule, and nothing executable.

## RULE 0Z-L — FREE-THREADED SWEEP PROTOCOL

### RULE 0H-A: RUNTIME SCANS ONLY

## RULE 0A: ALWAYS VERIFY (duplicate heading, must not duplicate the row)
"""

ENFORCING_GATE = '''\
def check_verify(flag):
    # names RULE 0A and can refuse
    if not flag:
        raise RuntimeError("0A: unverified")
    return True
'''

ALWAYS_PASS_GATE = '''\
def check_verify(flag):
    # names RULE 0A but cannot refuse
    return True
'''


def _surfaces(tmp_path, name, body):
    path = Path(tmp_path) / name
    path.write_text(body, encoding="utf-8")
    return [path]


def _mandates(tmp_path, text=MANDATES):
    """The audit reads its rule list from a PATH, so the fixture must exist.

    Passing an unwritten path made `rules` empty and the lookup raise
    StopIteration -- a passing fixture is not a passing test (2026-10-19).
    """
    path = Path(tmp_path) / "M.md"
    path.write_text(text, encoding="utf-8")
    return path


# -- parsing ---------------------------------------------------------------

def test_parser_finds_both_heading_forms_and_dedups(tmp_path):
    rules = auditor.parse_rules(MANDATES)
    ids = [rule["id"] for rule in rules]
    assert "0A" in ids, ids
    assert "0Z-L" in ids, "em-dash heading dropped: %r" % (ids,)
    assert "0H-A" in ids, "sub-rule heading dropped: %r" % (ids,)
    assert ids.count("0A") == 1, "duplicate heading produced two rows: %r" % (ids,)
    # A colon-only, `##`-only parser is the failure mode this catches.
    assert all(rule["title"] for rule in rules), rules


# -- the three verdicts ----------------------------------------------------

def test_a_raising_validator_is_enforced(tmp_path):
    surfaces = _surfaces(tmp_path, "gate.py", ENFORCING_GATE)
    report = auditor.audit(_mandates(tmp_path), surfaces=surfaces)
    row = next(r for r in report["rules"] if r["id"] == "0A")
    assert row["verdict"] == "ENFORCED", row
    assert row["references"][0]["failure_path"] is True, row["references"]


def test_a_validator_that_cannot_fail_is_not_enforced(tmp_path):
    """The faux-gate shape: it names the rule, it never says no."""
    surfaces = _surfaces(tmp_path, "gate.py", ALWAYS_PASS_GATE)
    report = auditor.audit(_mandates(tmp_path), surfaces=surfaces)
    row = next(r for r in report["rules"] if r["id"] == "0A")
    assert row["verdict"] == "WIRED-BUT-ALWAYS-PASS", row
    assert row["references"], "the mention itself must still be reported"


def test_a_prose_only_mention_is_no_validator(tmp_path):
    """Same rule id, same file shape -- only the executable mention is absent."""
    surfaces = _surfaces(tmp_path, "notes.md", "RULE 0A is discussed here.\n")
    report = auditor.audit(_mandates(tmp_path), surfaces=surfaces)
    row = next(r for r in report["rules"] if r["id"] == "0A")
    assert row["verdict"] == "NO-VALIDATOR", row
    assert row["references"] == [], row["references"]


def test_a_hook_mention_counts_as_a_failure_surface(tmp_path):
    """A hook can exit nonzero even when the naming scope is module level."""
    hooks = Path(tmp_path) / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\n# gate for RULE 0A\nexit 1\n", encoding="utf-8")
    report = auditor.audit(_mandates(tmp_path), surfaces=[hook])
    row = next(r for r in report["rules"] if r["id"] == "0A")
    assert row["verdict"] == "ENFORCED", row


# -- the CLI contract ------------------------------------------------------

def _pin_surfaces(monkeypatch, surfaces):
    """Make the CLI scan a fixture surface list, not the live repository."""
    monkeypatch.setattr(auditor, "_iter_surface_files",
                        lambda: (list(surfaces), []))


def test_check_exits_one_when_a_rule_has_no_validator(tmp_path, monkeypatch,
                                                     capsys):
    mandates = Path(tmp_path) / "M.md"
    mandates.write_text(MANDATES, encoding="utf-8")
    _pin_surfaces(monkeypatch, _surfaces(tmp_path, "gate.py", ENFORCING_GATE))
    # 0Z-L and 0H-A are not named by the gate, so the audit must fail.
    code = auditor.main(["--mandates", str(mandates), "--check"])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "FAIL:" in out, out


def test_check_exits_zero_when_every_rule_can_fail(tmp_path, monkeypatch,
                                                  capsys):
    mandates = Path(tmp_path) / "M.md"
    mandates.write_text("## RULE 0A: ALWAYS VERIFY\n", encoding="utf-8")
    _pin_surfaces(monkeypatch, _surfaces(tmp_path, "gate.py", ENFORCING_GATE))
    report = auditor.audit(mandates)
    assert [r["verdict"] for r in report["rules"]] == ["ENFORCED"], report
    assert report["missing_surfaces"] == [], report
    assert auditor.main(["--mandates", str(mandates), "--check"]) == 0
    assert "OK:" in capsys.readouterr().out


def test_plain_run_never_gates(tmp_path, monkeypatch, capsys):
    """Without --check the same gap is reported but the exit code stays 0."""
    mandates = Path(tmp_path) / "M.md"
    mandates.write_text(MANDATES, encoding="utf-8")
    _pin_surfaces(monkeypatch, _surfaces(tmp_path, "notes.md", "RULE 0A here\n"))
    assert auditor.main(["--mandates", str(mandates)]) == 0
    assert "NO-VALIDATOR" in capsys.readouterr().out
    assert auditor.main(["--mandates", str(mandates), "--json"]) == 0
    capsys.readouterr()


def test_unreadable_mandates_reports_instead_of_crashing(tmp_path, capsys):
    missing = Path(tmp_path) / "nope.md"
    report = auditor.audit(missing, surfaces=[])
    assert report["rules"] == [], report
    assert "cannot read" in report["error"], report
    assert auditor.main(["--mandates", str(missing)]) == 2
    capsys.readouterr()
