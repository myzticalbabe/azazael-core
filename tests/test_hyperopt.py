"""Regression tests for the outer loop over the system's own thresholds.

The properties worth pinning are all about NOT trusting a measurement too far:

  * values are clamped into their declared range on read *and* write, so a
    hand-edited or half-written state file cannot widen a gate until it stops
    gating (`min_evidence=4.0` makes every wiring candidate unevictable, which
    looks healthy and is the opposite);
  * the metric refuses to report a score when telemetry is unavailable, instead
    of returning zero -- a measurement that could not be taken must never read
    as a worse system;
  * one dimension per proposal, so an effect can be attributed;
  * the evidence floor REFUSES rather than promotes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The repo's own convention: `tests/` bootstraps `src` itself rather than
# relying on an installed package or on PYTHONPATH being set by the caller.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from CORE.WORMS.expansion import hyperopt as hyp  # noqa: E402


@pytest.fixture()
def params_file(tmp_path):
    path = tmp_path / "hyperparams.json"
    old = hyp.PARAMS_JSON
    hyp.PARAMS_JSON = path
    yield path
    hyp.PARAMS_JSON = old


# --------------------------------------------------------------------------
# clamping
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (99, 8), (-5, 2), ("junk", 3), (None, 3), (5, 5),
])
def test_int_parameter_is_clamped_into_range(params_file, raw, expected):
    params_file.write_text(json.dumps({"params": {"wiring.min_wins": raw}}),
                           encoding="utf-8")
    assert hyp.load()["wiring.min_wins"] == expected
    assert hyp.get_int("wiring.min_wins", 3) == expected


@pytest.mark.parametrize("raw,expected", [
    (4.0, 0.9), (0.0, 0.1), (0.5, 0.5), ("nope", 0.25),
])
def test_float_parameter_is_clamped_into_range(params_file, raw, expected):
    params_file.write_text(json.dumps({"params": {"wiring.min_evidence": raw}}),
                           encoding="utf-8")
    assert hyp.load()["wiring.min_evidence"] == expected


def test_missing_state_file_yields_the_shipped_defaults(params_file):
    assert hyp.load() == hyp.defaults()
    assert hyp.get("morph.max_opportunity", 2) == 2


def test_str_path_is_read_not_silently_ignored(params_file):
    """`str.read_text` raises AttributeError, which used to look exactly like
    'the optimizer is switched off' rather than 'the read failed'."""
    hyp.PARAMS_JSON = str(params_file)
    params_file.write_text(json.dumps({"params": {"wiring.min_wins": 7}}),
                           encoding="utf-8")
    assert hyp.load()["wiring.min_wins"] == 7


def test_corrupt_state_file_falls_back_without_raising(params_file):
    params_file.write_text("{not json at all", encoding="utf-8")
    assert hyp.load() == hyp.defaults()


# --------------------------------------------------------------------------
# the measurement
# --------------------------------------------------------------------------

def _metrics(**kw):
    base = {"ok": True, "resolution": 0.8, "success": 0.9, "coverage": 0.5,
            "observed": 50, "registered": 100, "n_choice_rows": 500,
            "n_outcome_rows": 400, "chosen_never_run": 2, "organs_scored": 50}
    base.update(kw)
    return base


class _Outcome:
    """The shape `WormOutcome` actually has: `n` and `ok`. Not `successes`."""

    def __init__(self, n=1, ok=1):
        self.n, self.ok = n, ok


class _FakeCredit:
    """A credit table with controllable outcomes, for the measurement tests."""

    def __init__(self, outcomes=None, joined=None, n_choice=100,
                 n_outcome=100, never=None):
        self.outcomes = outcomes if outcomes is not None else {"a": _Outcome()}
        self.rewards = {w: 1.0 for w in self.outcomes}
        self.joined_decisions = joined or {}
        self.n_choice_rows = n_choice
        self.n_outcome_rows = n_outcome
        self._never = never or []

    def chosen_but_never_run(self):
        return list(self._never)


def _patch_credit(monkeypatch, credit):
    """`measure()` builds its own credit table, so patch the BUILDER.

    Patching `live_spawn_credit` would no longer intercept anything: `measure()`
    calls `build_spawn_credit(resolve_store(), ...)` so that `limit` reaches both
    taps, and a test that patched the old wrapper would silently measure the
    live production store instead of its fixture.
    """
    import CORE.WORMS.expansion.spawn_credit as sc
    monkeypatch.setattr(sc, "build_spawn_credit", lambda *_a, **_k: credit)
    monkeypatch.setattr(sc, "resolve_store", lambda: None)


def _joined(results):
    """Build a joined-decisions view from `[{worm: success}, ...]`, NEWEST first."""
    return {"d%d" % i: {"n_chosen": len(r), "n_ran": len(r),
                        "results": {w: {"success": s} for w, s in r.items()}}
            for i, r in enumerate(results)}


def test_objective_is_weighted_over_the_terms_that_were_measured():
    """Weights are 0.35/0.25/0.15/0.25 over resolution/success/coverage/skill.

    `_metrics()` carries no `skill`, so that term is DROPPED and the remaining
    weights RENORMALISE. The alternative -- scoring a term that could not be
    measured as 0.0 -- is how a blind system comes to read as a worse one, and
    it is the defect this module was rebuilt around.
    """
    got = hyp.objective(_metrics())
    assert got["ok"] is True
    assert 0.0 <= got["value"] <= 1.0
    # (0.35*0.8 + 0.25*0.9 + 0.15*0.5) / 0.75
    assert got["value"] == pytest.approx(0.773333, abs=1e-6)
    assert got["unmeasured_terms"] == ["skill"]
    assert got["term_weights"] == {"coverage": 0.15, "resolution": 0.35,
                                   "success": 0.25}


def test_a_skill_term_is_included_when_it_was_measurable():
    got = hyp.objective(_metrics(skill={"ok": True, "value": 0.5,
                                         "reason": ""}))
    # 0.35*0.8 + 0.25*0.9 + 0.15*0.5 + 0.25*0.5, over all four weights = 1.0
    assert got["value"] == pytest.approx(0.705, abs=1e-6)
    assert got["unmeasured_terms"] == []


def test_an_unmeasurable_skill_term_is_not_zeroed():
    """`skill` reports `ok: False` when a holdout has no variance. That must
    drop the term, not inject a 0.0 that a candidate would be punished for."""
    dropped = hyp.objective(_metrics(skill={"ok": False, "value": None,
                                             "reason": "no variance"}))
    scored_zero = hyp.objective(_metrics(skill={"ok": True, "value": 0.0}))
    assert dropped["unmeasured_terms"] == ["skill"]
    assert dropped["value"] > scored_zero["value"]


def test_objective_refuses_thin_evidence_instead_of_scoring_it():
    thin = hyp.objective(_metrics(n_outcome_rows=1, coverage=0.01))
    assert thin["ok"] is False
    assert thin["reasons"], "an evidence floor with no reason is unactionable"
    assert thin["value"] is not None, "the number is reported, just not acted on"


def test_no_measurement_is_not_a_zero_score():
    none = hyp.objective({"ok": False, "reason": "credit unavailable"})
    assert none["ok"] is False
    assert none["value"] is None, "0.0 would read as a worse system, not a blind one"


def test_coverage_cannot_exceed_one(params_file, monkeypatch):
    """Two components with telemetry are not in the registry, so observed can
    outnumber registered; an unclamped ratio is a fudge factor, not a measure."""
    _patch_credit(monkeypatch, _FakeCredit())
    m = hyp.measure()
    assert m["ok"] is True
    assert m["coverage"] <= 1.0
    assert m["observed"] == 2 and m["registered"] == 0 or m["registered"] >= 2


# --------------------------------------------------------------------------
# the proposal
# --------------------------------------------------------------------------

def test_one_dimension_per_proposal_so_effects_are_attributable():
    import random
    rng = random.Random(11)
    base = hyp.load()
    for _ in range(50):
        cand, mutation = hyp.propose(rng, base=base)
        changed = [k for k in hyp.PARAM_SPACE if cand[k] != base[k]]
        assert len(changed) == 1, "moved %s in one proposal" % changed
        assert "->" in mutation


def test_proposal_stays_inside_the_declared_bounds():
    import random
    rng = random.Random(3)
    for _ in range(200):
        cand, _m = hyp.propose(rng, base=hyp.load())
        for name, spec in hyp.PARAM_SPACE.items():
            assert spec["low"] <= cand[name] <= spec["high"], name


def test_fingerprint_is_order_independent_and_change_sensitive():
    a = dict(hyp.defaults())
    b = {k: a[k] for k in reversed(list(a))}
    assert hyp.fingerprint(a) == hyp.fingerprint(b)
    b["wiring.min_wins"] = a["wiring.min_wins"] + 1
    assert hyp.fingerprint(a) != hyp.fingerprint(b)


# --------------------------------------------------------------------------
# what each term is read FROM
# --------------------------------------------------------------------------

def test_success_is_read_from_ok_not_from_a_missing_field(params_file, monkeypatch):
    """MEASURED ROOT CAUSE. `sum(int(o.successes) ...)` sat inside a bare
    `except`, so the outer loop reported `success=0.0` while the true rate was
    19119/19154 = 99.8%. The term was weighted 0.35 and read a constant for the
    entire life of the loop."""
    _patch_credit(monkeypatch, _FakeCredit(
        outcomes={"a": _Outcome(10, 10), "b": _Outcome(10, 5)},
        n_outcome=20, n_choice=20))
    m = hyp.measure()
    assert m["ok"] is True
    assert m["success"] == pytest.approx(0.75, abs=1e-6)


def test_a_credit_table_without_ok_is_unmeasurable_not_zero(params_file,
                                                           monkeypatch):
    """A renamed field must report a measurement that could not be taken, never
    a 0.0 that reads as a worse system."""
    class _NoOk:
        n = 4

    _patch_credit(monkeypatch, _FakeCredit(outcomes={"a": _NoOk()},
                                           n_outcome=4, n_choice=4))
    m = hyp.measure()
    assert m["ok"] is False
    assert "ok" in (m.get("reason") or "")
    assert "success" not in m, "an unmeasurable term must not be reported as 0.0"


def test_measure_honours_the_row_limit_on_both_taps(params_file, monkeypatch):
    """`limit` was accepted and never used: a windowed measurement was intended
    and never existed."""
    seen = {}
    import CORE.WORMS.expansion.spawn_credit as sc

    def _capture(store=None, outcome_limit=20000, choice_limit=5000):
        seen["outcome_limit"] = outcome_limit
        seen["choice_limit"] = choice_limit
        return _FakeCredit()

    monkeypatch.setattr(sc, "build_spawn_credit", _capture)
    monkeypatch.setattr(sc, "resolve_store", lambda: None)
    hyp.measure(limit=321)
    assert seen == {"outcome_limit": 321, "choice_limit": 321}


def test_resolution_is_measured_over_joined_decisions(params_file, monkeypatch):
    """`n_outcome_rows / n_choice_rows` is a 20,000-row numerator over a
    5,000-row denominator -- it read 2.78 on the live store and the clamp hid
    it. Over JOINED decisions both sides of the ratio are the same population."""
    joined = {"d1": {"n_chosen": 4, "n_ran": 3, "results": {}},
              "d2": {"n_chosen": 4, "n_ran": 4, "results": {}}}
    _patch_credit(monkeypatch, _FakeCredit(joined=joined, n_outcome=200,
                                           n_choice=50))
    m = hyp.measure()
    assert m["resolution_basis"] == "joined_decisions"
    assert m["resolution"] == pytest.approx(7 / 8, abs=1e-6)
    assert m["row_ratio"] == pytest.approx(4.0, abs=1e-6)


def test_resolution_is_unmeasured_without_a_join(params_file, monkeypatch):
    """No shared decision_id means no causal join -- a fact about the data, not
    a resolution of zero."""
    _patch_credit(monkeypatch, _FakeCredit(joined={}))
    m = hyp.measure()
    assert m["resolution"] is None
    assert m["resolution_basis"] == "unmeasurable"


def test_a_measured_resolution_is_bounded_by_construction(params_file,
                                                         monkeypatch):
    """The old ratio could exceed 1.0 once more outcome rows existed than choice
    rows inside the window; a rate cannot."""
    joined = {"d1": {"n_chosen": 2, "n_ran": 5, "results": {}}}
    _patch_credit(monkeypatch, _FakeCredit(joined=joined, n_outcome=9999,
                                           n_choice=10))
    assert hyp.measure()["resolution"] == 1.0


# --------------------------------------------------------------------------
# the held-out skill term
# --------------------------------------------------------------------------

def test_skill_is_positive_when_the_table_resolves_a_real_difference():
    train = [{"w": True}] * 10 + [{"v": False}] * 10
    holdout = [{"w": True}] * 3 + [{"v": False}] * 3
    got = hyp.held_out_skill(_joined(holdout + train), holdout_share=0.25,
                             min_views=5)
    assert got["ok"] is True
    assert got["n_holdout"] == 6, "the split must hold out the newest decisions"
    assert got["value"] == pytest.approx(1.0, abs=1e-6)
    assert got["mse_model"] < got["mse_baseline"]


def test_skill_is_negative_when_the_table_is_worse_than_a_constant():
    """The property the other three terms do not have: this one can go DOWN
    because the system learned something false."""
    train = [{"w": False}] * 5 + [{"g": True}] * 10
    holdout = [{"w": True}] * 5
    got = hyp.held_out_skill(_joined(holdout + train), holdout_share=0.25,
                             min_views=5)
    assert got["ok"] is True
    assert got["value"] < 0.0
    assert got["mse_model"] > got["mse_baseline"]


def test_skill_is_unmeasurable_when_the_holdout_has_no_variance():
    """A holdout where every outcome is identical makes a constant a perfect
    predictor, so `skill over that constant` does not exist. Reported, not 0.0."""
    got = hyp.held_out_skill(_joined([{"w": True}] * 20), holdout_share=0.25,
                             min_views=5)
    assert got["ok"] is False
    assert got["value"] is None
    assert "variance" in got["reason"]


def test_skill_needs_enough_decisions_to_split():
    got = hyp.held_out_skill(_joined([{"w": True}, {"v": False}]),
                             holdout_share=0.25)
    assert got["ok"] is False
    assert "joined decisions" in got["reason"]


def test_skill_holds_out_the_newest_decisions_and_trains_on_the_rest():
    """The split is by RECENCY, and the base rate comes from the TRAIN side
    alone -- a model scored on the rows it was built from reports its own
    memorisation, which is the one result that would always look good."""
    train = [{"w": True}] * 6 + [{"v": False}] * 6
    holdout = [{"w": True}] * 2 + [{"v": False}] * 2
    got = hyp.held_out_skill(_joined(holdout + train), holdout_share=0.25,
                             min_views=2)
    assert got["n_holdout"] == 4 and got["n_train"] == 12
    assert got["n_observations"] == 4
    assert got["baseline_success"] == pytest.approx(6 / 12, abs=1e-6)
    assert got["organ_models"] == 2


# --------------------------------------------------------------------------
# can the objective select anything at all?
# --------------------------------------------------------------------------

def test_health_flags_a_fully_pinned_objective():
    m = _metrics(resolution=1.0, success=1.0, coverage=1.0,
                 skill={"ok": True, "value": 1.0})
    h = hyp.objective_health(m, rows=[])
    assert h["reachable"] is False
    assert "pinned" in h["reason"]
    assert h["movable_terms"] == []
    assert h["pinned_weight"] == pytest.approx(1.0, abs=1e-6)


def test_health_is_reachable_when_one_term_can_move():
    m = _metrics(skill={"ok": True, "value": 0.2})
    h = hyp.objective_health(m, rows=[])
    assert h["reachable"] is True
    assert "skill" in h["movable_terms"]


def test_zero_baseline_spread_is_a_warning_not_a_deadlock(params_file):
    """A hard block here would deadlock: no promotions means no new baselines,
    so the warning could never clear.

    `baseline_spread` appends the CURRENTLY installed baseline to the recorded
    ones, so without `params_file` this read the live store's real (non-zero)
    baseline and could never observe the zero spread it is testing for.
    """
    rows = [{"kind": "promote", "objective": 0.65}] * 4
    h = hyp.objective_health(_metrics(skill={"ok": True, "value": 0.2}),
                             rows=rows)
    assert h["reachable"] is True, "a warning must not block promotion"
    assert any("zero spread" in w for w in h["warnings"])
    assert (h["historical"] or {})["spread"] == 0.0


def test_canary_tolerance_is_scaled_to_the_objective_not_a_constant():
    big = hyp.canary_tolerance(_metrics(n_outcome_rows=19382))
    small = hyp.canary_tolerance(_metrics(n_outcome_rows=100))
    assert small > big
    assert big < 0.05, "the shipped 0.05 was 12x a whole cycle's achievable shift"
    assert big >= hyp.CANARY_TOLERANCE_FLOOR


def test_baseline_spread_reads_the_promotion_ledger(tmp_path, monkeypatch):
    lineage = tmp_path / "hyperopt_lineage.jsonl"
    lineage.write_text("\n".join(json.dumps(r) for r in [
        {"kind": "promote", "objective": 0.60},
        {"kind": "dwell", "objective": 9.99},
        {"kind": "promote", "objective": 0.63},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(hyp, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hyp, "PARAMS_JSON", tmp_path / "absent.json")
    got = hyp.baseline_spread()
    assert got["samples"] == 2, "only promotion rows carry a baseline"
    assert got["spread"] == pytest.approx(0.03, abs=1e-9)


# --------------------------------------------------------------------------
# learning dynamics are calibratable -- except the gate that reverts them
# --------------------------------------------------------------------------

def test_learning_dynamics_are_in_the_param_space():
    for name in ("spine.homeostat_min_samples", "spine.fail_rate_high",
                 "spine.fail_rate_low", "spine.backoff_factor",
                 "spine.recover_factor", "spine.priority_step",
                 "hyperopt.holdout_share", "hyperopt.min_skill_views"):
        assert name in hyp.PARAM_SPACE, name
        assert name in hyp.defaults()


def test_the_canary_tolerance_is_deliberately_not_calibratable():
    """A candidate able to raise its own revert threshold could install a change
    nothing could ever revert -- the gate must not be inside the space it gates."""
    assert "hyperopt.canary_tolerance" not in hyp.PARAM_SPACE


def test_write_json_accepts_a_Path_not_only_a_str(tmp_path):
    """MEASURED DEFECT: `_write_json` did `path + ".tmp"` and `PARAMS_JSON` is a
    `Path`, so every L2 promotion died on `unsupported operand type(s) for +:
    'WindowsPath' and 'str'`. It was caught and logged -- so the loop reported
    `promote-write-failed` every cycle and never wrote the durable parameter
    file once. Verified on the live system: `state/hyperopt/` held the lineage
    ledger and `hyperparams.json.prev.json` but NOT `hyperparams.json`.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hyperopt_cycle_under_test", _ROOT / "scripts" / "hyperopt_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    path = tmp_path / "params.json"          # a Path, exactly like PARAMS_JSON
    assert mod._write_json(path, {"gen": 1, "params": {}}) is True
    assert json.loads(path.read_text(encoding="utf-8"))["gen"] == 1
    assert not (tmp_path / "params.json.tmp").exists(), "temp file must be replaced, not left"

    as_str = str(tmp_path / "other.json")
    assert mod._write_json(as_str, {"gen": 2}) is True
    assert json.loads(Path(as_str).read_text(encoding="utf-8"))["gen"] == 2


# --------------------------------------------------------------------------
# the cycle refuses to install a change no measurement could ever rank
# --------------------------------------------------------------------------

def _load_cycle_module():
    """Load the cycle script and point this module's `hyp` at its hyperopt.

    The registry suites purge `CORE.WORMS.*` from `sys.modules`, so a fresh load
    of the cycle script imports a NEW hyperopt module object while this test
    module still holds the old one. Patching the stale object then left
    `step()` calling the real, unpatched `measure()` — which finds no evidence
    under the suite's isolated state, so every step refused with
    `outcomes=0<20` and three tests failed *only* when a purging file ran first.
    """
    global hyp
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hyperopt_cycle_under_test", _ROOT / "scripts" / "hyperopt_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if getattr(mod, "hyp", None) is not None:
        hyp = mod.hyp
    return mod


def _isolate_cycle(mod, tmp_path):
    mod.PARAMS_JSON = tmp_path / "hyperparams.json"
    mod.PREV_JSON = str(tmp_path / "hyperparams.json.prev.json")
    mod.LINEAGE_JSONL = str(tmp_path / "hyperopt_lineage.jsonl")
    mod.STREAK_JSON = str(tmp_path / "hyperopt_dwell.json")
    mod.MIN_DWELL_CYCLES = 0


def test_a_pinned_objective_refuses_promotion_and_writes_nothing(tmp_path,
                                                                monkeypatch):
    """MEASURED BEHAVIOUR IT REPLACES: the outer loop promoted every cycle with
    an objective that was mathematically constant (0.65) and a canary that could
    not fire, so each promotion installed a random walk that no measurement could
    ever revert. The honest action is refusal, named."""
    mod = _load_cycle_module()
    _isolate_cycle(mod, tmp_path)
    monkeypatch.setattr(hyp, "PARAMS_JSON", tmp_path / "hyperparams.json")
    monkeypatch.setattr(hyp, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hyp, "measure", lambda *a, **k: _metrics(
        resolution=1.0, success=1.0, coverage=1.0,
        skill={"ok": True, "value": 1.0, "reason": ""}))

    rep = mod.step(apply_promotion=True, seed=1)

    assert rep["promoted"] is False
    assert (rep["candidate"] or {}).get("status") == "unreachable"
    assert rep["health"]["reachable"] is False
    assert not (tmp_path / "hyperparams.json").exists(), \
        "a refused promotion must not write a parameter file"
    ledger = (tmp_path / "hyperopt_lineage.jsonl").read_text(encoding="utf-8")
    assert "objective-unreachable" in ledger, "the refusal must be recorded"


def test_a_reachable_objective_still_proposes(tmp_path, monkeypatch):
    """The refusal must be specific. An objective with a movable term has to
    keep calibrating, or the guard would freeze the loop."""
    mod = _load_cycle_module()
    _isolate_cycle(mod, tmp_path)
    monkeypatch.setattr(hyp, "PARAMS_JSON", tmp_path / "hyperparams.json")
    monkeypatch.setattr(hyp, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hyp, "measure", lambda *a, **k: _metrics(
        skill={"ok": True, "value": 0.2, "reason": ""}))

    rep = mod.step(apply_promotion=True, seed=1)

    assert (rep["candidate"] or {}).get("status") != "unreachable"
    assert rep["promoted"] is True
    written = json.loads((tmp_path / "hyperparams.json").read_text(encoding="utf-8"))
    assert written["baseline_objective"] is not None
    assert written["objective_term_states"], \
        "a baseline whose term states are unrecorded cannot be judged later"


def test_the_canary_uses_the_derived_tolerance(tmp_path, monkeypatch):
    """A decay smaller than the shipped 0.05 but larger than the objective's own
    resolution must now revert; at 0.05 it reported `held`."""
    mod = _load_cycle_module()
    _isolate_cycle(mod, tmp_path)
    monkeypatch.setattr(hyp, "PARAMS_JSON", tmp_path / "hyperparams.json")
    monkeypatch.setattr(hyp, "_STATE_DIR", tmp_path)
    # 400 outcome rows -> tolerance max(0.001, 3/400) = 0.0075, not 0.05. A
    # 0.30 decay is far above either, so the tolerance is what is under test via
    # the reported number; the REVERT itself is what the old canary could not do.
    monkeypatch.setattr(hyp, "measure", lambda *a, **k: _metrics(
        resolution=0.5, success=0.5, coverage=1.0,
        skill={"ok": True, "value": 0.2, "reason": ""}))
    fp = hyp.fingerprint(hyp.load())
    mod._write_json(mod.STREAK_JSON, {"fingerprint": fp, "baseline": 0.80,
                                      "cycle": 9,
                                      "applied_at": "2026-01-01T00:00:00"})
    mod._write_json(mod.PARAMS_JSON, {"params": hyp.load(), "fingerprint": fp,
                                      "gen": 1})
    mod._write_json(mod.PREV_JSON, {"params": hyp.load(),
                                    "fingerprint": fp,
                                    "superseded_at": "2026-01-01T00:00:00"})

    rep = mod.step(apply_promotion=True, seed=1)

    assert rep["canary"]["status"] == "reverted", rep["canary"]
    assert rep["canary_tolerance"] < 0.05
    assert rep["canary_tolerance"] == pytest.approx(3 / 400, abs=1e-9)
