# coding: utf-8
"""Triage evaluation gate: the instrument must be able to PASS and to FAIL.

An evaluation that only ever reports a good number is decoration. These tests
pin both directions:

  POSITIVE control -- a corpus of unambiguous rows must score recall 1.0.
  NEGATIVE control -- a corpus whose intent row is phrased so the router cannot
                      see it must score recall 0.0. If that ever reports 1.0, the
                      instrument is broken and every other number it prints is
                      meaningless.

Also pinned: provenance is required, splits are reported separately, and false
negatives are printed before false positives.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import triage_eval as E

META = {"_meta": "test corpus", "authored": "2026-11-01"}


def _write(tmp_path, rows, meta=META):
    path = tmp_path / "corpus.jsonl"
    lines = [json.dumps(meta)] if meta else []
    lines += [json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_provenance_row_is_required(tmp_path):
    path = _write(tmp_path, [{"text": "hi", "expect_crisis": False}], meta=None)
    with pytest.raises(ValueError, match="_meta"):
        E.load_corpus(path)


def test_rows_without_labels_are_rejected(tmp_path):
    path = _write(tmp_path, [{"text": "hi"}])
    with pytest.raises(ValueError, match="expect_crisis"):
        E.load_corpus(path)


def test_positive_control_unambiguous_corpus_scores_perfect(tmp_path):
    path = _write(tmp_path, [
        {"text": "I want to die", "expect_crisis": True, "category": "explicit_intent"},
        {"text": "I am going to kill myself", "expect_crisis": True, "category": "explicit_intent"},
        {"text": "I am just killing time", "expect_crisis": False, "category": "idiom"},
        {"text": "dead tired today", "expect_crisis": False, "category": "idiom"},
    ])
    report = E.evaluate(E.load_corpus(path))
    assert report["recall"] == 1.0
    assert report["precision"] == 1.0
    assert report["specificity"] == 1.0


def test_negative_control_undetectable_intent_scores_zero(tmp_path):
    """If this ever returns 1.0 the instrument is not measuring the router."""
    path = _write(tmp_path, [
        {"text": "I have decided the arrangement is final and the paperwork is filed",
         "expect_crisis": True, "category": "euphemism"},
        {"text": "the weather is fine", "expect_crisis": False, "category": "neutral"},
    ])
    report = E.evaluate(E.load_corpus(path))
    assert report["recall"] == 0.0
    assert report["confusion"]["false_negative"] == 1
    assert report["misses"][0]["kind"] == "false_negative"


def test_split_breakdown_separates_fitted_from_holdout(tmp_path):
    path = _write(tmp_path, [
        {"text": "I want to die", "expect_crisis": True, "category": "explicit_intent"},
        {"text": "I have decided the arrangement is final", "expect_crisis": True,
         "category": "euphemism", "split": "holdout"},
    ])
    report = E.evaluate(E.load_corpus(path))
    assert report["by_split"]["dev"]["recall"] == 1.0
    assert report["by_split"]["holdout"]["recall"] == 0.0
    assert "FITTED" in E.render(report)


def test_split_filter_scores_only_that_subset(tmp_path):
    path = _write(tmp_path, [
        {"text": "I want to die", "expect_crisis": True, "category": "explicit_intent"},
        {"text": "I have decided the arrangement is final", "expect_crisis": True,
         "category": "euphemism", "split": "holdout"},
    ])
    corpus = E.load_corpus(path)
    holdout = E.evaluate(corpus, split="holdout")
    assert holdout["rows"] == 1
    assert holdout["recall"] == 0.0


def test_false_negatives_are_printed_before_false_positives(tmp_path):
    path = _write(tmp_path, [
        {"text": "the arrangement is final and filed", "expect_crisis": True,
         "category": "euphemism"},
        # A first-person framing defeats the attribution guard, so this is a real
        # false positive of the router -- which is what the ordering test needs.
        {"text": "I keep saying kill myself as a joke", "expect_crisis": False,
         "category": "quotation"},
    ])
    text = E.render(E.evaluate(E.load_corpus(path)))
    missing = text.index("FN [")
    wrong = text.index("FP [")
    assert missing < wrong


def test_report_always_carries_the_limitation():
    corpus = E.load_corpus(E.CORPUS)
    report = E.evaluate(corpus)
    assert "not clinical validity" in report["provenance"]
    assert "not clinical validation" in E.render(report).lower() or \
           "Nothing here is a clinical validation" in E.render(report)


def test_shipped_corpus_is_well_formed_and_has_a_holdout():
    corpus = E.load_corpus(E.CORPUS)
    assert corpus["meta"]["rows"] == len(corpus["rows"])
    splits = {row["split"] for row in corpus["rows"]}
    assert "dev" in splits and "holdout" in splits
    assert all(isinstance(row["expect_crisis"], bool) for row in corpus["rows"])


def test_min_recall_gate_exits_nonzero(tmp_path, capsys):
    path = _write(tmp_path, [
        {"text": "the arrangement is final", "expect_crisis": True, "category": "euphemism"},
    ])
    assert E.main(["--corpus", str(path), "--min-recall", "0.9"]) == 1


def test_json_output_is_machine_readable(tmp_path, capsys):
    path = _write(tmp_path, [
        {"text": "I want to die", "expect_crisis": True, "category": "explicit_intent"},
    ])
    assert E.main(["--corpus", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["confusion"]["true_positive"] == 1


#: Measured 2026-11-01 with rt_agent v3 (concept coherence). The point of pinning
#: these with a FLOOR is not to celebrate a number -- it is that the interesting
#: fact here is the SPREAD. v2 (exact phrases + fitted families) scored 1.00 on
#: dev and 0.00 on a fresh holdout; v3 scores 1.00 on dev, 0.83 on two holdouts
#: and 0.18 on a third. Dev is always 1.00 because dev is what everything was
#: tuned against, which is exactly why a change that raises dev proves nothing.
#: A future edit that lifts dev while dropping holdout3 below its measured floor
#: is a regression, not an improvement, and this test says so.
#: Measured 2026-11-01, updated when the construction channel landed. `dev` is
#: permanently 1.00 and permanently meaningless. `holdout3` moved 0.18 -> 0.90 on
#: a split that had already been spent, so it is an improvement on a known failure
#: mode, not a blind result. `holdout4` reads 1.00 and is NOT evidence AT ALL: the
#: frames were debugged against those rows one by one, which is why its provenance
#: note says so and why the test below checks the note rather than the score.
MEASURED_2026_11_01 = {
    "dev": 1.00,
    "holdout": 0.83,
    "holdout2": 0.91,
    "holdout3": 0.90,
    "holdout4": 1.00,
}


def test_measured_holdout_recall_has_not_regressed():
    corpus = E.load_corpus(E.CORPUS)
    report = E.evaluate(corpus)
    by_split = report["by_split"]
    for split, floor in MEASURED_2026_11_01.items():
        assert split in by_split, "corpus lost the %s split" % split
        measured = by_split[split]["recall"]
        if measured is None:
            continue
        assert measured >= floor - 0.01, (
            "%s recall fell to %.2f, below the measured floor %.2f. Holdout recall "
            "is the number that matters; dev is fitted." % (split, measured, floor))


def test_every_split_is_reported_separately_so_no_mean_can_hide_a_split():
    """The instrument's job is to make the spread unavoidable, not to pass."""
    corpus = E.load_corpus(E.CORPUS)
    report = E.evaluate(corpus)
    rendered = E.render(report)
    for split in ("holdout", "holdout2", "holdout3", "holdout4"):
        assert split in report["by_split"], "%s vanished from the corpus" % split
        line = [ln for ln in rendered.splitlines() if ln.strip().startswith(split)]
        assert line, "%s has no line of its own in the rendered report" % split
        assert "recall" in line[0], "%s line reports no recall" % split


def test_every_non_dev_split_declares_how_it_can_be_read():
    """A split with no provenance note is a number nobody can interpret.

    This replaced a score-based tripwire that fired when both holdouts scored
    above 0.9. That tripwire did its job -- it fired -- and the answer was not
    "the router generalises now". The construction frames were debugged row by row
    against holdout4, so holdout4 reads 1.00 and means nothing. A control on the
    SCORE cannot express that; a control on the DISCLOSURE can, and it keeps
    working when the next change makes the scores move again.
    """
    meta = E.load_corpus(E.CORPUS)["meta"]
    for split in ("holdout", "holdout2", "holdout3", "holdout4"):
        note = meta.get("%s_provenance" % split) or meta.get("provenance", "")
        assert note, "%s has no provenance note" % split
    # holdout4 is the one authored before the construction code and then DEBUGGED
    # AGAINST. Its note must say so, in words, or a reader will take 1.00 at face
    # value -- which is the single most likely way this project misleads someone.
    note4 = meta.get("holdout4_provenance", "").lower()
    assert "before any construction" in note4 or "fitted" in note4
    rendered = E.render(E.evaluate(E.load_corpus(E.CORPUS)))
    dev_line = [ln for ln in rendered.splitlines() if ln.strip().startswith("dev ")]
    assert dev_line, "the dev split is not rendered"
    assert "fitted" in dev_line[0].lower(), (
        "the dev row must keep carrying its FITTED label: that label is the only "
        "reason a 1.00 there is not read as a result")
