# coding: utf-8
"""PSYCHE Phase 5 gate: compiled inference that can be graded.

- A configuration seen once is NOT a prototype (min_support); seen twice it is.
- An unfamiliar pattern returns known=False rather than a low-confidence guess.
- read_or_think falls back to the graph exactly when the fast path is not
  confident, and the fast path is measurably cheaper for the same question.
- Resolving a reading does NOT edit the prototype: a wrong intuition must be
  able to be wrong twice, so the error rate stays measurable. This is the test
  that protects the whole calibration story.
- Brier score is computed from resolutions only; with none it is None, not 0.0.
- No threshold is recommended from too little evidence.
"""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import intuition as I
from PSYCHE.network import PersonNetwork

BASE = {"threat_appraisal": 0.8, "avoidance": 0.7, "rumination": 0.6,
        "arousal": 0.5, "approach": 0.05, "self_worth": -0.4}
CALM = {"threat_appraisal": 0.05, "avoidance": 0.05, "rumination": 0.1,
        "arousal": 0.1, "approach": 0.7, "self_worth": 0.5}


def _index():
    net = PersonNetwork()
    net.add_edge("threat_appraisal", "avoidance", 0.7, support=10)
    net.add_edge("rumination", "threat_appraisal", 0.5, support=10)
    net.add_edge("approach", "self_worth", 0.4, support=10)
    return I.IntuitionIndex(net)


def _learned(min_support=2, repeats=5):
    """Two prototypes, each seen `repeats` times.

    Repetition is what earns trust twice over: `support` clears min_support, and
    the evidence term 1 - exp(-support/4) clears DEFAULT_THRESHOLD. At support 2
    the confidence is 0.39, i.e. BELOW the fast-path threshold -- which is the
    intended reading (two observations is a recognition, not yet a trust).
    """
    index = _index()
    index.min_support = min_support
    for _ in range(repeats):
        index.absorb(BASE, "threat-loop")
        index.absorb(CALM, "settled")
    return index


def test_one_observation_is_not_a_pattern():
    index = _index()
    index.absorb(BASE, "threat-loop")
    reading = index.read(BASE)
    assert reading.known is False
    assert reading.prototype is None
    assert index.diagnostics()["prototypes"] == 1
    assert index.diagnostics()["trusted"] == 0


def test_second_observation_makes_it_retrievable():
    index = _index()
    index.absorb(BASE, "threat-loop")
    index.absorb(BASE, "threat-loop")
    reading = index.read(BASE)
    assert reading.known is True
    assert reading.prototype == "threat-loop"
    assert reading.support == 2


def test_unfamiliar_pattern_is_unknown_not_a_weak_guess():
    """Negative control for the match floor: near-orthogonal cues must miss."""
    index = _learned()
    odd = {"sleep_quality": 0.9, "social_support": 0.8}
    reading = index.read(odd)
    assert reading.known is False
    assert reading.confidence == 0.0
    assert reading.path == "intuition"


def test_confidence_separates_similarity_from_support():
    index = _learned()
    reading = index.read(BASE)
    assert 0.0 < reading.similarity <= 1.0
    assert 0.0 < reading.confidence <= reading.similarity


def test_partial_cues_ladder_recognise_then_trust():
    """Regression guard, 2026-11-01.

    Plain cosine over the union scored an EXACT 2-of-4 instance at 0.76, so the
    fast path was taken 0 times in 100 calls -- a decision rule that could never
    fire. Matching on the measured subset with a sqrt(coverage) discount fixes
    that, and the resulting three-way ladder is the behaviour worth pinning:

      full-ish coverage  -> recognised AND trusted, fast path taken
      half coverage      -> recognised (known=True) but NOT trusted enough to
                            skip the graph, so read_or_think still thinks
      one coordinate     -> not recognised at all
    """
    index = _index()
    for _ in range(10):
        index.absorb(BASE, "threat-loop")

    asked = {k: BASE[k] for k in
             ("threat_appraisal", "avoidance", "rumination", "arousal")}
    trusted = index.read_or_think(asked)
    assert trusted.prototype == "threat-loop"
    assert trusted.path == "intuition", trusted.to_dict()

    thin = {k: BASE[k] for k in ("threat_appraisal", "avoidance")}
    assert index.read(thin).known is True
    assert index.read_or_think(thin).path != "intuition"
    assert index.read(thin).similarity < index.read(asked).similarity

    assert index.read({"threat_appraisal": 0.8}).known is False


def test_read_or_think_falls_back_exactly_when_unsure():
    index = _learned()
    confident = index.read_or_think(BASE)
    assert confident.path == "intuition"
    unsure = index.read_or_think({"sleep_quality": 0.9, "social_support": 0.8})
    assert unsure.path == "deliberate-after-miss"


def test_fast_path_is_cheaper_for_the_same_question():
    """The speed claim, measured here rather than asserted in a docstring."""
    index = _learned()
    for _ in range(20):
        index.agreement(BASE)          # warm, then time the aggregate
    fast_total = 0.0
    slow_total = 0.0
    for _ in range(100):
        fast_total += index.read(BASE).latency_us
        slow_total += index.deliberate(BASE).latency_us
    assert fast_total < slow_total, (fast_total, slow_total)
    report = index.agreement(BASE)
    assert report["agree"] is True
    assert report["ratio"] > 1.0


def test_resolving_does_not_rewrite_the_prototype():
    """The property the ledger depends on: a wrong guess stays wrong, and stays."""
    index = _learned()
    before = dict(index.read(BASE).profile)
    reading = index.read(BASE)
    index.resolve(reading, "actually fine", correct=False)
    after = dict(index._prototypes["threat-loop"].profile)
    assert after == pytest.approx(before)
    assert index._prototypes["threat-loop"].wrong == 1
    # and it can be wrong again: the same error is counted twice, not hidden
    index.resolve(index.read(BASE), "fine again", correct=False)
    assert index._prototypes["threat-loop"].wrong == 2


def test_brier_score_discriminates_and_reports_its_n():
    good = _learned()
    for _ in range(4):
        good.resolve(good.read(BASE), "was right", correct=True)
    report = good.calibration()
    assert report["resolved"] == 4
    assert report["accuracy"] == 1.0
    assert report["brier"] < 0.2

    bad = _learned()
    for _ in range(4):
        bad.resolve(bad.read(BASE), "was wrong", correct=False)
    assert bad.calibration()["brier"] > good.calibration()["brier"]
    assert bad.calibration()["accuracy"] == 0.0


def test_calibration_without_resolutions_is_unknown_not_perfect():
    report = _learned().calibration()
    assert report["resolved"] == 0
    assert report["brier"] is None
    assert "unknown" in report["note"]


def test_no_threshold_is_recommended_from_too_little_data():
    index = _learned()
    index.resolve(index.read(BASE), "x", correct=True)
    assert index.recommend_threshold()["threshold"] is None


def test_threshold_is_recommended_once_the_data_supports_it():
    index = _learned()
    for _ in range(10):
        index.resolve(index.read(BASE), "right", correct=True)
    index.resolve(index.read({"sleep_quality": 0.95, "social_support": 0.9}),
                  "wrong", correct=False)
    report = index.recommend_threshold()
    assert report["resolved"] == 11
    assert report["threshold"] is not None


def test_artifact_round_trips():
    index = _learned()
    index.resolve(index.read(BASE), "right", correct=True)
    clone = I.IntuitionIndex.from_dict(index.to_dict(), index.network)
    assert [p.name for p in clone.prototypes()] == \
           [p.name for p in index.prototypes()]
    assert clone.diagnostics()["resolved"] == index.diagnostics()["resolved"]


def test_resolution_ledger_is_bounded():
    index = _learned()
    for i in range(I.MAX_RESOLUTIONS + 40):
        index.resolve(index.read(BASE), "evt-%d" % i, correct=True)
    assert len(index._resolutions) == I.MAX_RESOLUTIONS


def test_no_demographic_inputs_exist():
    banned = ("sex", "gender", "race", "ethnic", "age", "income", "religion",
              "nationality", "diagnosis", "disorder")
    for name, fn in vars(I).items():
        if not inspect.isfunction(fn) or fn.__module__ != I.__name__:
            continue
        for param in inspect.signature(fn).parameters:
            assert not any(word in param.lower() for word in banned), (name, param)
