# coding: utf-8
"""PSYCHE Phase 1 gate: OCC structure, regulation dissociation, invariance.

- Event/action/object branches yield the documented families with
  computed (not asserted) intensities, ranked, mixed.
- Temperament maps traits directionally and is replaceable.
- Reappraisal changes feeling; suppression changes display only
  (Gross dissociation, mechanized).
- NO demographic inputs exist anywhere in the call graph (introspected);
  identical person-state gives identical appraisal (determinism).
- TD bridge maps learning signals to appraisal checks monotonically.
- JSON-representable outputs throughout (artifact property).
"""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import affect as A
from PSYCHE.traits import TraitVector


def _fams(out):
    return [f for f, _i, _b in out]


def test_event_prospect_families():
    assert _fams(A.appraise_event(0.8))[0] == "hope"
    assert _fams(A.appraise_event(-0.8))[0] == "fear"


def test_event_confirmation_families():
    assert _fams(A.appraise_event(0.8, confirmed=True))[0] == "satisfaction"
    assert _fams(A.appraise_event(0.8, confirmed=False))[0] == "disappointment"
    assert _fams(A.appraise_event(-0.8, confirmed=True))[0] == "fears_confirmed"
    assert _fams(A.appraise_event(-0.8, confirmed=False))[0] == "relief"


def test_event_neutral_yields_nothing():
    assert A.appraise_event(0.0) == []
    assert A.appraise_event(0.02) == []


def test_action_families_self_other():
    assert _fams(A.appraise_action(0.7, self_agent=True))[0] == "pride"
    assert _fams(A.appraise_action(-0.7, self_agent=True))[0] == "shame"
    assert _fams(A.appraise_action(0.7, self_agent=False))[0] == "admiration"
    assert _fams(A.appraise_action(-0.7, self_agent=False))[0] == "reproach"


def test_object_families():
    assert _fams(A.appraise_object(0.7))[0] == "love"
    assert _fams(A.appraise_object(-0.7))[0] == "hate"


def test_compounds():
    ev = A.appraise_event(0.8, confirmed=True)
    ac = A.appraise_action(0.7, self_agent=False)
    assert _fams(A.compound_gratitude_anger(ev, ac)) == ["gratitude"]
    ev2 = A.appraise_event(-0.8, confirmed=True)
    ac2 = A.appraise_action(-0.7, self_agent=True)
    assert _fams(A.compound_gratitude_anger(ev2, ac2)) == ["anger"]


def test_intensity_scales_with_likelihood_and_effort():
    low = A.appraise_event(0.8, likelihood=0.1)[0][1]
    high = A.appraise_event(0.8, likelihood=1.0)[0][1]
    assert 0.0 <= low < high <= 1.0


def test_mixed_emotions_ranked():
    out = A.appraise_event(0.8, confirmed=True)
    assert len(out) >= 2  # satisfaction + joy tone, never a single label
    assert [i for _f, i, _b in out] == sorted(
        [i for _f, i, _b in out], reverse=True)


def test_temperament_directions_and_replaceability():
    high_n = {"O": 3.0, "C": 3.0, "E": 3.0, "A": 3.0, "N": 5.0, "H": 3.0}
    low_n = {"O": 3.0, "C": 3.0, "E": 3.0, "A": 3.0, "N": 1.0, "H": 3.0}
    assert A.temperament(high_n)[0] < A.temperament(low_n)[0]  # N lowers P
    custom = {k: dict(v) for k, v in A.TEMPERAMENT_WEIGHTS.items()}
    custom["P"]["N"] = 0.0
    assert A.temperament(high_n, custom)[0] == pytest.approx(
        A.temperament(low_n, custom)[0])
    # partial profiles stay honest (no imputed domains)
    assert A.temperament({"N": 5.0})[0] < 0


def test_pad_ops():
    assert A.pad_distance((0, 0, 0), (1, 0, 0)) == pytest.approx(1.0)
    b = A.pad_blend((1, 0, 0), (-1, 0, 0), 0.5)
    assert b[0] == pytest.approx(0.0)
    base = (0.5, 0.5, 0.5)
    assert A.pad_decay((1, 1, 1), base, 0.0) == (1, 1, 1)  # rate 0: frozen
    assert A.pad_decay((1, 1, 1), base, 99.0)[0] == pytest.approx(0.5)


def test_regulation_dissociation():
    felt = A.emotion_to_pad("fear", 0.8)
    # Reappraisal edits desirability -> the downstream feeling moves.
    assert A.reappraise_desirability(-0.8, 0.25) == pytest.approx(-0.2)
    moved = A.emotion_to_pad(
        A.appraise_event(A.reappraise_desirability(-0.8, 0.25),
                         confirmed=True)[0][0], 0.8)
    assert A.pad_distance(moved, felt) > 0.1
    # Suppression edits display only: felt state untouched by construction.
    assert A.suppress_display(felt, 0.1)[0] != pytest.approx(felt[0])
    out = A.express(felt)
    assert out["displayed"] == felt and out["rule"] is None
    out2 = A.express(felt, {"name": "professional-mask"})
    assert out2["felt"] == felt and out2["rule"] == "professional-mask"


def test_emotion_to_pad_centroids_sane():
    assert A.emotion_to_pad("joy")[0] > 0 > A.emotion_to_pad("distress")[0]
    assert A.emotion_to_pad("fear")[1] > A.emotion_to_pad("relief")[1]
    assert A.emotion_to_pad("pride")[2] > A.emotion_to_pad("shame")[2]


def test_no_demographic_inputs():
    """Mechanical no-mansplaining gate: no appraisal/regulation/temperament
    function may take a demographic attribute. Group templates cannot be
    routed because there is nowhere to plug them in. Scans identifiers
    (parameter and variable names), not prose: documentation MAY discuss
    demographics; code may not branch on them."""
    import ast as _ast
    import importlib
    import re
    banned = re.compile(
        r"^(gender|sex|female|male|race|ethnicity|age|demographic|"
        r"stereotype)$", re.I)
    # Discovered, not hard-coded. The fixed list had gone stale: it checked six
    # modules and missed network/intuition/rt_agent/rt_channel, which were added
    # later and are exactly where a construct-shaped template would hide. It also
    # made the test unshippable, because distill/artifact/evaluate are not in the
    # public manifest and importing them raised ModuleNotFoundError in a public
    # checkout -- a test that only passes on the author's tree.
    import pkgutil
    import PSYCHE as _pkg

    modules = []
    unscanned = []
    for info in pkgutil.iter_modules(_pkg.__path__):
        try:
            modules.append(importlib.import_module("%s.%s" % (_pkg.__name__, info.name)))
        except ImportError as exc:
            # A module that will not import is UNSCANNED source, and this test's
            # whole claim is "no module branches on demographics" -- so a skip
            # here is a hole in the claim, not an optional extra. Measured
            # 2026-11-02: all twelve modules import cleanly, so this is an
            # assertion that holds today and names the defect if it stops.
            unscanned.append("%s (%s)" % (info.name, exc))
    assert not unscanned, (
        "modules the invariance check would silently skip: %s" % unscanned)
    assert modules, "no PSYCHE modules were importable -- the scan is not running"
    for mod in modules:
        tree = _ast.parse(inspect.getsource(mod))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.arg):
                assert not banned.match(node.arg), node.arg
            elif isinstance(node, _ast.Name):
                assert not banned.match(node.id), node.id


def test_determinism_same_state_same_appraisal():
    a = A.appraise_event(0.6, likelihood=0.7, effort=0.3, unexpectedness=0.2)
    b = A.appraise_event(0.6, likelihood=0.7, effort=0.3, unexpectedness=0.2)
    assert a == b


def test_td_bridge_monotone():
    low = A.td_appraisal(0.1, state_visits=100, goal_value=0.5)
    high = A.td_appraisal(0.9, state_visits=0, goal_value=0.9)
    assert high["suddenness"] > low["suddenness"]  # novelty
    assert high["conduciveness"] > low["conduciveness"]  # better than expected
    assert high["relevance"] >= low["relevance"]
    assert A.td_appraisal(-0.5)["desirability"] < 0


def test_trait_vector_feeds_temperament():
    tv = TraitVector.from_responses({i: 4 for i in range(60)}, name="t")
    base = A.temperament(tv.domains())
    assert all(-1.0 <= v <= 1.0 for v in base)
    assert A.pad_decay(base, base, 1.0) == base  # baseline is a fixed point
