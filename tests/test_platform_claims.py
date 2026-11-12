# coding: utf-8
"""tests for tools/platform_claims.py -- the ledger behind docs/platforms.

The checker's job is to make a page's claims falsifiable, so these tests assert
the two directions: a claim that agrees is CONFIRMED, and a claim that disagrees
is STALE. A checker tested only on agreeing input cannot be told apart from one
that always returns "clean".
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("platform_claims", ROOT / "tools" / "platform_claims.py")
pc = importlib.util.module_from_spec(_spec)
sys.modules["platform_claims"] = pc
_spec.loader.exec_module(pc)


def _ledger(claims):
    return {"schema": "platform-claims/1", "claims": claims}


def _claim(**over):
    base = {
        "id": "c1",
        "doc": "docs/platforms/windows.md",
        "claim": "something",
        "kind": "path",
        "scope": "repo",
        "path": "tools/platform_claims.py",
    }
    base.update(over)
    return base


def test_self_test_passes():
    """The negative control is part of the artifact, not of the test suite's opinion."""
    assert pc.self_test() == 0


def test_real_ledger_has_no_schema_defects():
    problems = pc.validate(pc.load())
    assert problems == [], problems


def test_real_ledger_cites_every_page():
    ledger = pc.load()
    cited = {c["doc"] for c in ledger["claims"]}
    pages = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs" / "platforms").glob("*.md")}
    assert pages - cited == set(), "platform page(s) with no claim: %s" % (pages - cited)


def test_refused_claim_needs_a_reason():
    ledger = _ledger([_claim(kind="refused", path=None, refused_because=None)])
    problems = pc.validate(ledger, require_all_pages=False)
    assert any("refused_because" in p for p in problems)


def test_refused_claim_is_unverifiable_not_confirmed():
    ledger = _ledger([_claim(kind="refused", path=None, refused_because="no such hardware")])
    assert pc.validate(ledger, require_all_pages=False) == []
    result = pc.check_claim(ledger["claims"][0])
    assert result["status"] == "UNVERIFIABLE"


def test_duplicate_ids_rejected():
    problems = pc.validate(_ledger([_claim(), _claim()]), require_all_pages=False)
    assert any("duplicate id" in p for p in problems)


def test_missing_doc_rejected():
    problems = pc.validate(_ledger([_claim(doc="docs/platforms/not-a-page.md")]), require_all_pages=False)
    assert any("doc does not exist" in p for p in problems)


def test_absent_claim_fires_when_the_path_appears():
    ledger = _ledger([_claim(kind="absent", path="tools/platform_claims.py")])
    result = pc.check_claim(ledger["claims"][0])
    assert result["status"] == "STALE"


def test_command_claim_confirms_on_matching_output():
    claim = _claim(kind="command", path=None, run=["-c", "print('yes')"], expect="yes")
    assert pc.check_claim(claim)["status"] == "CONFIRMED"


def test_command_claim_goes_stale_on_wrong_output():
    claim = _claim(kind="command", path=None, run=["-c", "print('no')"], expect="yes")
    assert pc.check_claim(claim)["status"] == "STALE"


def test_command_claim_honours_expect_exit():
    claim = _claim(kind="command", path=None, run=["-c", "import sys; print('bye'); sys.exit(3)"],
                   expect="bye", expect_exit=3)
    assert pc.check_claim(claim)["status"] == "CONFIRMED"


def test_command_claim_without_exit_expectation_fails_on_a_crash():
    """Echoing the expected word from a traceback is not agreement."""
    claim = _claim(kind="command", path=None, run=["-c", "raise SystemExit('bye')"], expect="bye")
    assert pc.check_claim(claim)["status"] == "STALE"


def test_host_scope_difference_is_not_fatal():
    claim = _claim(kind="command", path=None, scope="host", run=["-c", "print('other')"], expect="token")
    result = pc.check_claim(claim)
    assert result["status"] == "HOST-DIFFERS"


def test_no_run_leaves_commands_unverified():
    claim = _claim(kind="command", path=None, run=["-c", "print('yes')"], expect="yes")
    result = pc.check_claim(claim, run=False)
    assert result["status"] == "UNVERIFIABLE"
    assert "not run" in result["detail"]


def test_report_exit_code_is_zero_when_clean_and_one_when_stale(capsys):
    # The in-memory fixtures skip the all-pages rule; the real ledger run in
    # test_real_ledger_cites_every_page is where that rule is exercised.
    clean = [_claim(kind="path")]
    result = pc.check_claim(clean[0], run=False)
    assert pc.report([], [result]) == 0

    stale = [_claim(kind="absent", path="tools/platform_claims.py")]
    result = pc.check_claim(stale[0], run=False)
    assert pc.report([], [result]) == 1


def test_ledger_json_is_what_the_docs_quote():
    ledger = pc.load()
    ids = [c["id"] for c in ledger["claims"]]
    assert "no-accelerate-resolver-module" in ids, (
        "the claim that catches the phantom src/CORE/accelerate resolver must stay"
    )
    assert "gate-registry-prints-its-count" in ids
