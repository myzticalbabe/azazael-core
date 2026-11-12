"""Tests for tools/deere_claims_check.py.

The checker's contract is that every input produces a printed verdict and an exit
code: READY 0, GAPS 1, INCOMPLETE 1, BLIND 2. A traceback also exits 1, which is
indistinguishable from GAPS, so these tests drive every malformed shape the ledger
or a capture can take and assert that none of them raises.

Two kinds of control, and the difference matters
------------------------------------------------

The synthetic fixtures below perturb exactly one thing against a ledger that
passes. They prove the *paths* -- that a tampered quote reaches INCOMPLETE, that
moved bytes reach BLIND -- and nothing more: a fixture written by the same author
as the code validates the author's assumptions.

``test_real_corpus_is_ready`` is the other kind. It grades ``ARC/deere/claims.json``
against the eight captures on disk with the same code path, so a checker that has
drifted into passing only its own fixtures fails there and not here.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from tools.deere_claims_check import (
    EXIT_CODES,
    VERDICT_BLIND,
    VERDICT_GAPS,
    VERDICT_INCOMPLETE,
    VERDICT_READY,
    check,
    main,
    normalize,
    digest_file,
)

SOURCE_TEXT = (
    "Order II.A requires Repair Resources equivalent to those Deere then makes "
    "available to Deere Dealers, and the term runs ten years from entry."
)
QUOTE = "Repair Resources equivalent to those Deere then makes available to Deere Dealers"


def _ledger(quote=QUOTE, reason="no primary capture", status="UNSUPPORTED"):
    return {
        "schema": "deere-claims-test/1",
        "sources": {},
        "claims": [
            {
                "id": "D1",
                "tier": "A",
                "status": "SUPPORTED",
                "source": "src",
                "locator": "Order II.A, p.6",
                "claim": "The duty is dealer parity.",
                "quote": quote,
            },
            {
                "id": "D2",
                "tier": "C",
                "status": status,
                "source": None,
                "locator": "n/a",
                "claim": "A figure with no primary capture.",
                "quote": "",
                **({"reason": reason} if reason is not None else {}),
            },
        ],
    }


def build(tmp_path, *, quote=QUOTE, reason="no primary capture", status="UNSUPPORTED",
          paper=True, rewrite_capture=False, ledger_text=None):
    """A ledger that passes, with at most one thing wrong with it."""
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    capture = raw / "src.txt"
    capture.write_text(SOURCE_TEXT, encoding="utf-8")
    sha = digest_file(capture)
    if rewrite_capture:
        capture.write_text(SOURCE_TEXT + " tampered after hashing", encoding="utf-8")

    ledger = _ledger(quote=quote, reason=reason, status=status)
    ledger["sources"]["src"] = {
        "path": "raw/src.txt",
        "text": "raw/src.txt",
        "sha256": sha,
        "tier": "A",
    }
    ledger_path = tmp_path / "claims.json"
    ledger_path.write_text(
        ledger_text if ledger_text is not None else json.dumps(ledger, indent=2),
        encoding="utf-8",
    )
    paper_path = tmp_path / "WHITEPAPER.md"
    if paper:
        paper_path.write_text("Findings: D1 and D2.\n", encoding="utf-8")
    return ledger_path, paper_path


def run(tmp_path, **kwargs):
    ledger_path, paper_path = build(tmp_path, **kwargs)
    return check(ledger_path=ledger_path, paper_path=paper_path)


def test_fixture_passes(tmp_path):
    report = run(tmp_path)
    assert report.issues == []
    assert report.verdict == VERDICT_READY
    assert report.exit_code == 0


def test_tampered_quote_is_incomplete(tmp_path):
    """A claim whose quote is not in the capture was carried, not read."""
    report = run(tmp_path, quote="Repair Resources equivalent to those Deere then makes available to nobody")
    assert report.verdict == VERDICT_INCOMPLETE
    assert report.exit_code == 1
    assert any("quote not found" in issue for issue in report.issues)


def test_short_quote_is_incomplete(tmp_path):
    report = run(tmp_path, quote="August 1, 2026")
    assert report.verdict == VERDICT_INCOMPLETE
    assert any("too short" in issue for issue in report.issues)


def test_rewritten_capture_is_blind(tmp_path):
    """Bytes that moved are other bytes, not stale evidence."""
    report = run(tmp_path, rewrite_capture=True)
    assert report.verdict == VERDICT_BLIND
    assert report.exit_code == 2
    assert any("digest mismatch" in issue for issue in report.issues)


def test_missing_capture_is_blind(tmp_path):
    ledger_path, paper_path = build(tmp_path)
    (tmp_path / "raw" / "src.txt").unlink()
    report = check(ledger_path=ledger_path, paper_path=paper_path)
    assert report.verdict == VERDICT_BLIND
    assert report.exit_code == 2


def test_unnamed_gap_is_gaps(tmp_path):
    """A declared gap without a reason cannot be told from an oversight."""
    report = run(tmp_path, reason=None)
    assert report.verdict == VERDICT_GAPS
    assert report.exit_code == 1
    assert any("names no reason" in issue for issue in report.issues)


def test_declared_gap_does_not_change_verdict(tmp_path):
    report = run(tmp_path, reason="no primary capture in this corpus")
    assert report.verdict == VERDICT_READY


def test_orphan_claim_is_incomplete(tmp_path):
    ledger_path, paper_path = build(tmp_path)
    paper_path.write_text("Findings: D1 only.\n", encoding="utf-8")
    report = check(ledger_path=ledger_path, paper_path=paper_path)
    assert report.verdict == VERDICT_INCOMPLETE
    assert report.narrative_missing == ["D2"]


def test_missing_whitepaper_is_incomplete(tmp_path):
    report = run(tmp_path, paper=False)
    assert report.verdict == VERDICT_INCOMPLETE
    assert any("narrative missing" in issue for issue in report.issues)


def test_malformed_ledger_is_blind(tmp_path):
    report = run(tmp_path, ledger_text="{not json")
    assert report.verdict == VERDICT_BLIND
    assert report.exit_code == 2


def test_empty_ledger_is_blind(tmp_path):
    report = run(tmp_path, ledger_text=json.dumps({"sources": {}, "claims": []}))
    assert report.verdict == VERDICT_BLIND


def test_normalize_folds_typography_only():
    typographic = "Deere\u2019s \u201cRepair\u201d \u2014  Resources"
    assert normalize(typographic) == normalize('Deere\'s "Repair" - Resources')
    assert normalize("Repair   Resources") == "repair resources"
    # Not a fuzzy matcher: a changed word stays changed.
    assert "repair tool" not in normalize("Repair Resources")


def test_exit_codes_are_distinct_and_ordered():
    assert EXIT_CODES[VERDICT_READY] == 0
    assert EXIT_CODES[VERDICT_GAPS] == 1
    assert EXIT_CODES[VERDICT_INCOMPLETE] == 1
    assert EXIT_CODES[VERDICT_BLIND] == 2


# --- the control that is not the author's own fixture -----------------------


def _real_corpus():
    from tools.deere_claims_check import DEFAULT_LEDGER, DEFAULT_WHITEPAPER

    if not DEFAULT_LEDGER.is_file():
        pytest.skip("ARC/deere/claims.json not present in this checkout")
    return DEFAULT_LEDGER, DEFAULT_WHITEPAPER


def test_real_corpus_is_ready():
    """Positive control: the shipped ledger against the shipped captures."""
    ledger_path, paper_path = _real_corpus()
    report = check(ledger_path=ledger_path, paper_path=paper_path)
    assert report.issues == []
    assert report.verdict == VERDICT_READY
    assert report.counts["sources"] == 8
    assert report.counts["supported"] == 33
    assert report.counts["digests_ok"] == 8
    assert report.counts["found"] == 33
    assert report.narrative_missing == []


def test_real_corpus_main_prints_ready(capsys):
    ledger_path, paper_path = _real_corpus()
    code = main(["--ledger", str(ledger_path), "--whitepaper", str(paper_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "verdict: READY" in out
    assert "digests:      8/8 match" in out
