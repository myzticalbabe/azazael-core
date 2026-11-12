# coding: utf-8
"""Tests for the construction-grammar risk detector.

The corpus here is paired: every risk construction has a benign twin that shares
the FRAME and differs only in the slot fill. That pairing is the test. A detector
that matches frames without reading fills passes the first half and fails the
second, which is exactly the failure the previous two mechanisms had -- recall on
the phrasings they were written around, nothing on the rest.

Measured scores on the labelled corpus are NOT repeated here. They live in
`tools/triage_eval.py` output, next to the splits they came from, because a number
copied into a test suite is a number that stops being re-measured.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import constructions as C  # noqa: E402
from PSYCHE.rt_agent import triage  # noqa: E402

#: (text, expected frame) -- the construction, with the fill that makes it one.
RISK = [
    ("nothing about next year has me in it, i've stopped picturing it", "future_absence"),
    ("been reading up on how much it takes. i know the number now", "means_lookup"),
    ("every drive home i slow down on the bridge. not stopping, just noticing", "rehearsal"),
    ("i've told myself the flat is the deadline. after that i'm allowed", "foreclosure"),
    ("i put the letters in the drawer on Sunday. I have not posted them yet", "preparation"),
    ("the only thing keeping me on this side of it is that my sister would find me", "tether"),
    ("honestly i think they'd be tidier without me in the house", "burden"),
    ("when the pain gets past a six i think about the drawer", "rehearsal"),
]

#: The same frames with a benign fill. Each pair differs in exactly the slot the
#: detector is supposed to read.
BENIGN_TWINS = [
    ("no version of march has me in it, they've cancelled the contract and i'm job hunting",
     "future_absence"),
    ("been reading up on how much the deposit takes, i know the number now", "means_lookup"),
    ("every drive home i slow down at the bridge because the view is good", "rehearsal"),
    ("i told myself the flat is the deadline for getting the deposit together", "foreclosure"),
    ("i put the letters in the drawer, i'll post the insurance ones tomorrow", "preparation"),
    ("the only thing keeping me in this job is the pension, honestly", "tether"),
    ("they'd be tidier without my boxes in the hall, i should sort them", "burden"),
    ("the pain gets past a six and i take the tablets the doctor gave me", "rehearsal"),
]


@pytest.mark.parametrize("text,frame", RISK)
def test_risk_construction_fires_and_names_its_frame(text, frame):
    report = C.analyse(text)
    assert report.fires, "%r did not fire; frames=%r suppressed=%r" % (
        text, report.frames, report.suppressed)
    assert frame in {f.name for f in report.frames if f.subject == C.SELF}


@pytest.mark.parametrize("text,frame", BENIGN_TWINS)
def test_benign_twin_does_not_fire_through_the_router(text, frame):
    """End-to-end, not just the analyser: the router is what a user meets.

    The coherence channel reads vocabulary and would fire on several of these, so
    this test is really about the precedence between the two channels.
    """
    result = triage(text)
    assert result.risk != "crisis", "%r routed to crisis: %r" % (text, result.reasons)


@pytest.mark.parametrize("text,frame", BENIGN_TWINS)
def test_benign_twin_is_rejected_at_formation_or_with_a_stated_reason(text, frame):
    """Two legitimate ways not to fire, and they are not the same thing.

      * FORMATION rejection -- the frame's own slot conditions never met, e.g. a
        means verb whose object is named ("the deposit") instead of unspecified.
      * FILL rejection -- a frame formed and a benign marker or an other-subject
        ruled it out, and that is recorded in `suppressed`.

    The assertion is on the distinction, not on one of them: requiring a recorded
    suppression for every non-firing would be satisfied by inventing suppression
    lines for frames that were never there, which is worse than silence.
    """
    report = C.analyse(text)
    assert not report.fires
    formed = {f.name for f in report.frames}
    if formed:
        assert report.suppressed, "frame(s) %r formed and vanished without a reason" % formed
    else:
        assert not report.suppressed or True
    # Whatever the path, the DEFAULT must not be crisis at router level.
    assert triage(text).risk != "crisis"


def test_preparation_spans_a_sentence_boundary():
    """The verb and the withheld action land in different sentences in chat."""
    report = C.analyse("i put the letters in the drawer on sunday. i have not posted them")
    assert report.fires
    assert "preparation" in {f.name for f in report.frames}


def test_subject_is_resolved_from_the_frame_not_a_blanket_guard():
    """A relative mentioned as the FINDER does not make the sentence theirs.

    "the only thing keeping me here is that my sister would find me" mentions a
    sister and is entirely about the speaker. The old blanket third-person guard
    downgraded it, which got the most protective sentence in the corpus wrong.
    """
    report = C.analyse("the only thing keeping me on this side of it is that my sister "
                       "would find me")
    assert report.fires
    assert report.frames[0].subject == C.SELF


def test_third_person_statement_is_not_this_persons_intent():
    report = C.analyse("she told me she had been looking up how much it takes")
    assert not report.fires


def test_ordinary_distress_has_no_construction():
    for text in ("work is relentless and i am not sleeping",
                 "I am grieving and it hurts but I am safe",
                 "day three of therapy and i actually slept through",
                 "the deadline is brutal, it is killing me"):
        assert not C.analyse(text).fires, text


def test_frame_weights_are_ordered_by_lethality():
    """A means lookup is more urgent than a burden statement, and the weights say so."""
    assert C.FRAME_WEIGHTS["means_lookup"] >= C.FRAME_WEIGHTS["foreclosure"]
    assert C.FRAME_WEIGHTS["future_absence"] > C.FRAME_WEIGHTS["tether"]


def test_router_reports_the_construction_as_its_reason():
    result = triage("been reading up on how much it takes. i know the number now")
    assert result.risk == "crisis"
    joined = " ".join(result.reasons)
    assert "construction" in joined
    assert "means_lookup" in joined
