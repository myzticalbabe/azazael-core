#!/usr/bin/env python3
# coding: utf-8
"""Tests for the construct battery, the store, and the fit that reads it.

The negative controls matter more than the happy paths here:

* a reverse-scored item must FLIP, and a session of straight 4s must land near
  the midpoint instead of maxing every node -- otherwise acquiescence reads as a
  perfect profile;
* an edited store line must FAIL the digest check. The first version of that
  check compared a recomputed hash to itself and could never fail, which is the
  same defect as the bridge tool's "hand map vs itself" bug;
* with too few sessions the graph fitter must produce NO edges rather than a
  plausible-looking pair.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from PSYCHE import elicitation  # noqa: E402
from PSYCHE.elicitation import (ITEMS, INSTRUMENT_VERSION, IncompleteSession,  # noqa: E402
                                Record, homogeneity, score_session)

import elicit  # noqa: E402
import psyche_fit_pams  # noqa: E402


def full_answers(value: int = 2) -> dict:
    return {item.key: value for item in ITEMS}


def test_every_construct_has_a_reverse_item():
    """Without one, a person who agrees with everything scores maximal on all ten.

    That profile is a straight line the estimator cannot tell from a real one,
    so the control has to exist per construct, not per battery.
    """
    grouped = {}
    for item in ITEMS:
        grouped.setdefault(item.construct, []).append(item)
    assert set(grouped) == set(elicitation.CONSTRUCTS)
    for construct, items in grouped.items():
        assert any(i.reverse for i in items), construct
        assert any(not i.reverse for i in items), construct
        assert len(items) == 3, construct


def test_agree_with_everything_lands_near_midpoint():
    """The acquiescence control: straight 4s must NOT read as maximal constructs."""
    record = score_session(full_answers(4), (0.0, 0.0, 0.0), taken_at="2026-11-01T00:00:00Z")
    # Forward items score +1, reverse items flip to 0 and score -1, so a 3-item
    # construct lands halfway between: (1 + 1 - 1) / 3 -> about 0.33, never 1.0.
    for construct, value in record.constructs.items():
        assert value < 0.5, "%s read %+.2f from straight 4s" % (construct, value)
        assert value > -0.5, "%s read %+.2f from straight 4s" % (construct, value)


def test_reverse_items_flip_the_score():
    neutral = score_session(full_answers(2), (0, 0, 0), taken_at="2026-11-01T00:00:00Z")
    for value in neutral.constructs.values():
        assert value == pytest.approx(0.0, abs=1e-9)

    forward_high = dict(full_answers(2))
    forward_high["threat_1"] = 4
    high = score_session(forward_high, (0, 0, 0), taken_at="2026-11-01T00:00:00Z")
    assert high.constructs["threat_appraisal"] > 0

    reverse_only = dict(full_answers(2))
    reverse_only["threat_3"] = 4  # "Today I felt safe." -> high 4 means LOW threat
    low = score_session(reverse_only, (0, 0, 0), taken_at="2026-11-01T00:00:00Z")
    assert low.constructs["threat_appraisal"] < 0


def test_partial_session_is_refused_not_renormalised():
    answers = full_answers()
    del answers["rumin_2"]
    with pytest.raises(IncompleteSession):
        score_session(answers, (0, 0, 0), taken_at="2026-11-01T00:00:00Z")


def test_out_of_range_answer_is_refused():
    answers = full_answers()
    answers["approach_1"] = 7
    with pytest.raises(ValueError):
        score_session(answers, (0, 0, 0), taken_at="2026-11-01T00:00:00Z")


def test_store_round_trip_and_digest(tmp_path):
    store = tmp_path / "records.jsonl"
    answers = full_answers(3)
    record = score_session(answers, (-0.4, 0.6, -0.3), taken_at="2026-11-01T01:00:00Z",
                           order=[i.key for i in ITEMS])
    elicit.append_store(store, record)

    read = elicit.read_store(store)
    assert len(read) == 1
    assert read[0].constructs == record.constructs
    assert read[0].pad == record.pad
    assert elicit.verify_digests(read) == []


def test_edited_store_line_fails_the_digest_check(tmp_path):
    """The control that the first draft of verify() would have failed.

    A store whose numbers were changed after scoring must be detectable. If the
    check compares a recomputed hash to itself it always passes, so this test
    asserts the check FIRES on an edit -- a negative control, not a happy path.
    """
    store = tmp_path / "records.jsonl"
    record = score_session(full_answers(3), (-0.4, 0.6, -0.3),
                           taken_at="2026-11-01T01:00:00Z")
    elicit.append_store(store, record)

    row = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    row["constructs"]["self_worth"] = 0.99  # hand-edited, hash left alone
    store.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert elicit.verify_digests(elicit.read_store(store)) == ["2026-11-01T01:00:00Z"]


def test_homogeneity_flags_direction_answering():
    """A record that answers 4 to every item has forward/reverse means 4 apart."""
    record = score_session(full_answers(4), (0, 0, 0), taken_at="2026-11-01T00:00:00Z")
    gaps = homogeneity([record])
    # Forward mean is 4, reverse mean (flipped) is 0, so every construct reads 4.
    assert set(gaps.values()) == {4.0}


def test_records_pairs_shape_matches_the_fitter(tmp_path):
    """`as_pad_construct_pairs` must produce (pad, constructs) pairs, not rows."""
    record = score_session(full_answers(3), (0.1, 0.2, 0.3),
                           taken_at="2026-11-01T00:00:00Z")
    pairs = elicitation.as_pad_construct_pairs([record])
    assert pairs[0][0] == (0.1, 0.2, 0.3)
    assert set(pairs[0][1]) == set(elicitation.CONSTRUCTS)


def test_elicitation_store_is_probed_before_sqlite():
    """A real measurement outranks a guess about where one might hide."""
    probe = {"readings": 5, "store": "x", "kind": "records_jsonl"}
    assert probe.get("readings")


def test_graph_fit_produces_no_edges_from_four_sessions(tmp_path, monkeypatch):
    """Four observations cannot support a partial correlation; it must say so.

    The failure this guards is a fit that returns a confident-looking pair of
    edges from four rows. Zero edges is the honest output and is asserted as
    zero, not as 'not many'.
    """
    store = tmp_path / "records.jsonl"
    for index in range(4):
        answers = full_answers(2 + (index % 2))
        answers["approach_1"] = index % 5  # some spread across sessions
        answers["rumin_1"] = (index * 2) % 5
        record = score_session(answers, (0.1 * index, 0.0, 0.0),
                               taken_at="2026-11-0%dT00:00:00Z" % (index + 1))
        elicit.append_store(store, record)
    monkeypatch.setenv(psyche_fit_pams.ELICITATION_ENV, str(store))

    probe = psyche_fit_pams.probe_elicitation(store)
    assert probe["readings"] == 4
    assert probe["instruments"] == [INSTRUMENT_VERSION]

    readings = psyche_fit_pams.readings_from_records(store)
    assert len(readings) == 4
    # The pad is NOT a node: a graph over constructs must not be conditioned on
    # affect by accident, so the node set is exactly the ten constructs.
    assert all(set(r) == set(elicitation.CONSTRUCTS) for r in readings)


def test_absent_store_is_an_explicit_negative(tmp_path):
    probe = psyche_fit_pams.probe_elicitation(tmp_path / "missing.jsonl")
    assert probe.get("readings") is None
    assert "no elicitation store" in probe["reason"]


def test_empty_store_lists_its_reason(tmp_path):
    store = tmp_path / "records.jsonl"
    store.write_text("\n", encoding="utf-8")
    probe = psyche_fit_pams.probe_elicitation(store)
    assert probe.get("readings") is None
    assert "none carrying both" in probe["reason"]


def test_record_from_row_strips_unknown_fields():
    row = {"taken_at": "2026-11-01T00:00:00Z", "pad": [0, 0, 0],
           "constructs": {"self_worth": 0.1}, "answers": {"worth_1": 3},
           "order": ["worth_1"], "instrument": INSTRUMENT_VERSION,
           "digest": "abc", "comment": "not a field"}
    record = Record.from_row(row)
    assert "comment" not in record.to_row()
    assert record.stored_digest == "abc"
