# coding: utf-8
"""HYPEROPT -- the outer loop: tune this system's own thresholds from outcomes.

WHY THIS EXISTS

Every other loop here optimises *inside* a fixed frame. `evolution_cycle`
evolves gene values, `wiring_cycle` evolves which organs run in what order, and
`morphogenesis` generates candidates -- all of them scored against thresholds
that were typed by hand (`L1_MIN_WINS = 3`, `L1_MIN_EVIDENCE = 0.25`,
`MAX_OPPORTUNITY = 2`, cooldown clamps `0.5x .. 4x`). Those thresholds decide
how much evidence is called evidence, how many candidates are allowed, and how
fast a failing organ is backed off. They were never measured, and nothing
updates them, so the system's *sensitivity* is frozen while everything it
controls keeps moving. That is the missing recursion: the architecture can
change its behaviour but not its calibration.

WHAT "HYPER-OPTIMIZATION" MEANS HERE, PRECISELY

Not a grid search over a simulator. Three properties make this honest:

1. **One dimension per cycle, evaluated in TIME, not in a model.** A candidate
   is promoted, its objective is recorded at promotion, and the NEXT cycle
   re-measures it. There is no fabricated fitness: the score of a candidate is
   the score the running system actually produced while it was installed.
2. **Revert on measured decay.** The same canary shape `wiring_cycle` uses:
   if the promoted parameters' objective falls by more than the tolerance below
   their baseline, the previous parameters are restored and the revert is
   recorded. The tolerance is not a constant -- see `canary_tolerance()`, and
   the measurement of a canary that could never fire.
3. **An evidence floor, reported not acted on.** Below the `hyperopt.min_samples`
   outcomes or `hyperopt.min_coverage` observed organs there is no measurement
   worth acting on, so a proposal is logged and refused rather than promoted on
   noise.
4. **A measurement that can move.** Four terms, and the objective must be able
   to tell two candidates apart. `objective_health()` answers that, because an
   objective pinned at its ceiling reports a healthy loop that cannot learn.

This module is the SPACE and the MEASUREMENT. The cycle that proposes, gates,
promotes and reverts is `scripts/hyperopt_cycle.py`; the gate deliberately
mirrors the L1 wiring gate so there is one vocabulary for "enough evidence".

READERS -- a parameter that nothing reads is decoration

The parameters below are read by real hot paths, and each reader falls back to
its own literal default when this module or its state file is unavailable, so
hyper-optimization can never be a new way for the system to fail to start:

    wiring.min_wins         scripts/wiring_cycle.py  -> L1_MIN_WINS
    wiring.min_observed     scripts/wiring_cycle.py  -> L1_MIN_OBSERVED
    wiring.min_evidence     scripts/wiring_cycle.py  -> L1_MIN_EVIDENCE
    wiring.margin_floor     scripts/wiring_cycle.py  -> L1_MARGIN
    morph.max_opportunity   spine.SpineCycleWorm     -> MAX_OPPORTUNITY
    spine.cooldown_min_scale / cooldown_max_scale  -> the back-off clamp
    spine.homeostat_min_samples / fail_rate_high / fail_rate_low
    spine.backoff_factor / recover_factor / priority_step
                           spine.HomeostatWorm       -> how it LEARNS
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ---------------------------------------------------------------------------
# The space
# ---------------------------------------------------------------------------

#: name -> {kind, default, low, high, desc}. Bounds are what make a proposal a
#: *calibration* rather than a redesign: `wiring.min_wins` cannot be tuned to 0,
#: because a single winning draw is the noise this repo already measured
#: (0.75 against 2.4-30.2) and a threshold of zero would act on it.
PARAM_SPACE: Dict[str, Dict[str, Any]] = {
    "wiring.min_wins": {
        "kind": "int", "default": 3, "low": 2, "high": 8,
        "desc": "consecutive winning cycles before a wiring candidate is promoted",
    },
    "wiring.min_observed": {
        "kind": "int", "default": 3, "low": 2, "high": 8,
        "desc": "organs an L1 candidate must be scored on",
    },
    "wiring.min_evidence": {
        "kind": "float", "default": 0.25, "low": 0.10, "high": 0.90,
        "desc": "share of a candidate's active set that must carry evidence",
    },
    "wiring.elite_replace_ratio": {
        "kind": "float", "default": 0.5, "low": 0.1, "high": 3.0,
        "desc": "relative margin a fresh wiring child must beat the carried elite by",
    },
    # RENAMED from `wiring.margin`, and the rename is the migration: `load()`
    # reads only names present in PARAM_SPACE, so the promoted 0.002 that was
    # blocking every promotion is retired here rather than left in the state file
    # to keep blocking. Same quantity, but its DEFAULT is now 0.0 because 0.002
    # was measured to be above the largest effect the objective can express.
    "wiring.margin_floor": {
        "kind": "float", "default": 0.0, "low": 0.0, "high": 0.02,
        "desc": ("absolute epsilon a wiring candidate must beat the incumbent by "
                 "(0.0 = any positive paired delta; a paired comparison has no "
                 "measurement noise for a constant to filter)"),
        # 2026-09-21, second attempt. The first set this to 0.002 and justified
        # it against the credit signal's population spread (0.1228). Wrong
        # quantity: the objective is a priority-weighted mean over the observed
        # fleet, so ONE organ's mutation moves it by ~step/total_weight*(c_i -
        # mean). Measured over 60 children per operator on the live genome:
        # flip_active best +0.00008363, bump_priority best +0.00001666 -- both
        # below 0.002, so the gate became UNREACHABLE and L1 promoted nothing
        # for 33 cycles while reporting `ok`. The lesson recorded in the param
        # rather than in a comment, because this is the value that would be
        # re-set wrong: an effect floor must be quoted in the units of the
        # objective it guards, never of a neighbouring signal.
        "measured_max_achievable_delta": 0.00008363,
    },
    "morph.max_opportunity": {
        "kind": "int", "default": 2, "low": 1, "high": 6,
        "desc": "opportunity-driven components morphogenesis may stage per cycle",
    },
    "spine.cooldown_min_scale": {
        "kind": "float", "default": 0.5, "low": 0.25, "high": 1.0,
        "desc": "floor of the failing-organ cooldown back-off multiplier",
    },
    "spine.cooldown_max_scale": {
        "kind": "float", "default": 4.0, "low": 2.0, "high": 8.0,
        "desc": "ceiling of the failing-organ cooldown back-off multiplier",
    },
    # ── Learning dynamics (2026-09-21) ─────────────────────────────────────
    # How the system LEARNS was as hand-typed as what it learns about: how much
    # evidence before it acts, at what failure rate it punishes, and how hard it
    # backs off all lived as literals in `spine.HomeostatWorm`, so the learner's
    # own sensitivity was frozen while everything it controls kept moving. That
    # is the same missing recursion this module exists to close, one level down.
    #
    # Each is read through `spine._tuned_*` and falls back to the shipped
    # literal, so the registry behaves identically with hyperopt absent.
    #
    # DELIBERATELY ABSENT: the canary tolerance. The canary is the gate that
    # reverts a bad promotion, so a candidate able to raise its own tolerance
    # could install a change nothing could ever revert. It is DERIVED from the
    # objective's own resolution instead -- see `canary_tolerance()`.
    "spine.homeostat_min_samples": {
        "kind": "int", "default": 5, "low": 2, "high": 50,
        "desc": "outcomes a (nature, worm) pair needs before the homeostat acts",
    },
    "spine.fail_rate_high": {
        "kind": "float", "default": 0.5, "low": 0.2, "high": 0.9,
        "desc": "failure rate above which an organ is backed off and downgraded",
    },
    "spine.fail_rate_low": {
        "kind": "float", "default": 0.1, "low": 0.01, "high": 0.4,
        "desc": "failure rate below which an organ's cooldown is shortened",
    },
    "spine.backoff_factor": {
        "kind": "float", "default": 2.0, "low": 1.2, "high": 4.0,
        "desc": "cooldown multiplier applied to an organ above the failure band",
    },
    "spine.recover_factor": {
        "kind": "float", "default": 0.75, "low": 0.5, "high": 1.0,
        "desc": "cooldown multiplier applied to an organ below the failure band",
    },
    "spine.priority_step": {
        "kind": "int", "default": 1, "low": 1, "high": 3,
        # Both a calibration and a lever. The L1 genome's `priority` axis carries
        # an empty map because a one-step child moves the priority-weighted
        # objective by ~1.7e-5, below its measured effect floor -- so the size of
        # the correction decides whether that axis can express anything at all.
        "desc": "priority levels the homeostat moves an organ per proposal",
    },
    # Evidence floor for promoting anything at all. Tunable, but the reader is
    # not the guard: `objective()` refuses on a thin measurement regardless of
    # how low this is set, because "we lowered our own bar" is not evidence.
    "hyperopt.min_samples": {
        "kind": "int", "default": 20, "low": 5, "high": 500,
        "desc": "outcome rows below which no promotion may be scored",
    },
    "hyperopt.min_coverage": {
        "kind": "float", "default": 0.10, "low": 0.02, "high": 0.90,
        "desc": "registered organs that must carry telemetry before scoring",
    },
    "hyperopt.holdout_share": {
        "kind": "float", "default": 0.2, "low": 0.1, "high": 0.5,
        "desc": "share of decisions held out of the credit table for scoring",
    },
    "hyperopt.min_skill_views": {
        "kind": "int", "default": 5, "low": 2, "high": 50,
        "desc": "train observations a worm needs before it gets its own prediction",
    },
}

#: Hand-typed floors, kept as the fallback and as the shipped defaults. The
#: tunable forms live in PARAM_SPACE above; these literals are what a reader
#: falls back to when the outer loop is absent or its state is unreadable.
MIN_SAMPLES = 20
MIN_COVERAGE = 0.10

#: Objective weights. NOT tunable, on purpose: a loop that can reweight its own
#: objective can make any change look like an improvement by moving weight onto
#: whichever term happened to rise. The weights are the frame; only the values
#: inside it are searched.
W_RESOLUTION = 0.35
W_SUCCESS = 0.25
W_COVERAGE = 0.15
W_SKILL = 0.25

#: A measured term at or above this is at its ceiling: it cannot reward a
#: candidate, so a term pinned here carries no selection pressure at all.
PINNED_HIGH = 0.999
PINNED_LOW = -0.999

#: Canary floor. The EFFECTIVE tolerance is `canary_tolerance()`, derived from
#: the objective's own resolution -- see the docstring there for why a constant
#: 0.05 was 12x larger than a whole cycle's achievable shift.
CANARY_TOLERANCE_FLOOR = 0.001

#: Kept for compatibility with readers that imported the old constant. It is no
#: longer the value the cycle compares against.
CANARY_TOLERANCE = CANARY_TOLERANCE_FLOOR

#: hyperopt.py is src/CORE/WORMS/expansion/, so parents[4] is the repo root.
_STATE_DIR = Path(os.environ.get("AZAZAEL_HYPEROPT_DIR")
                  or (Path(__file__).resolve().parents[4] / "state" / "hyperopt"))
PARAMS_JSON = Path(os.environ.get("AZAZAEL_HYPEROPT_PARAMS") or _STATE_DIR / "hyperparams.json")


def _read_json(path) -> Optional[dict]:
    """Best-effort read. Coerces to Path so a caller may pass either.

    A `str` reaching `str.read_text` raises AttributeError and the whole
    parameter set silently reverts to defaults -- which reads as "the optimizer
    is off" rather than "the read failed", and would have hidden every promoted
    value from its own readers.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        note_failure("hyperopt:L236", exc)
        return None


def defaults() -> Dict[str, Any]:
    """The hand-typed values, which every reader already falls back to."""
    return {name: spec["default"] for name, spec in PARAM_SPACE.items()}


def clamp(name: str, value: Any) -> Any:
    """Force a value into its declared kind and bounds.

    Applied on read AND on write. A state file that was hand-edited, truncated
    by a killed process, or written by an older schema must not be able to push
    a threshold outside the calibrated range -- a `min_evidence` of 4.0 would
    silently make every candidate unevictable, which looks like a healthy gate
    and is the opposite of one.
    """
    spec = PARAM_SPACE.get(name)
    if spec is None:
        return value
    try:
        v = int(round(float(value))) if spec["kind"] == "int" else float(value)
    except (TypeError, ValueError) as exc:
        note_failure("hyperopt:L259", exc)
        return spec["default"]
    return max(spec["low"], min(spec["high"], v))


def load() -> Dict[str, Any]:
    """Active parameters: defaults overlaid with the promoted set, clamped."""
    params = defaults()
    data = _read_json(PARAMS_JSON)
    raw = (data or {}).get("params")
    if isinstance(raw, dict):
        for name in PARAM_SPACE:
            if name in raw:
                params[name] = clamp(name, raw[name])
    return params


def get(name: str, default: Any = None) -> Any:
    """One parameter. Never raises -- readers are in hot paths."""
    spec = PARAM_SPACE.get(name)
    if spec is None:
        return default
    try:
        data = _read_json(PARAMS_JSON)
        raw = (data or {}).get("params") or {}
        return clamp(name, raw.get(name, spec["default"]))
    except Exception as exc:
        note_failure("hyperopt:L285", exc)
        return spec["default"] if default is None else default


def get_int(name: str, default: int) -> int:
    try:
        return int(get(name, default))
    except Exception as exc:
        note_failure("hyperopt:L292", exc)
        return int(default)


def get_float(name: str, default: float) -> float:
    try:
        return float(get(name, default))
    except Exception as exc:
        note_failure("hyperopt:L299", exc)
        return float(default)


def fingerprint(params: Dict[str, Any]) -> str:
    """Stable id for a parameter set, so a revert can name what it restored."""
    blob = ",".join("%s=%s" % (k, params[k]) for k in sorted(params))
    return "hp-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The objective
# ---------------------------------------------------------------------------

def _objective_params() -> Dict[str, Any]:
    """The evidence floor and hold-out shape, read through the clamped path.

    Read at MEASUREMENT time rather than at import: a value loaded once at
    import is a constant with extra steps, and the whole point of putting these
    in PARAM_SPACE is that a promotion changes the next cycle's behaviour.
    """
    return {
        "min_samples": get_int("hyperopt.min_samples", MIN_SAMPLES),
        "min_coverage": get_float("hyperopt.min_coverage", MIN_COVERAGE),
        "holdout_share": get_float("hyperopt.holdout_share", 0.2),
        "min_skill_views": get_int("hyperopt.min_skill_views", 5),
    }


def held_out_skill(joined: Dict[str, Dict[str, Any]],
                   holdout_share: float = 0.2,
                   min_views: int = 5) -> Dict[str, Any]:
    """Can the credit table predict an outcome it was NOT trained on?

    THE PROBLEM THIS SOLVES. The other three quantities are all *shares of a
    census*: everything that happened, divided by everything that happened.
    They saturate. Measured live on 2026-09-21: `resolution` 1.001,
    `coverage` 1.009 -- so the only term with any headroom was `success`, and it
    was also the term being read through a non-existent attribute. An objective
    whose every term is pinned at its ceiling cannot distinguish two candidates,
    which is the same defect as an effect floor quoted 24x above the largest
    achievable effect. Once. Twice is a pattern, so the third term is built to
    have headroom by construction.

    The measurement is ordinary held-out prediction, on data the system already
    writes. Decisions are split by RECENCY: the newest `holdout_share` of them
    are never shown to the model, which is trained on the older remainder. The
    model predicts a worm's success from its own train split record; the
    BASELINE predicts the global train success rate for everything. The score is
    skill over that baseline,

        skill = 1 - MSE(model) / MSE(baseline)

    which is 0.0 for a table that knows nothing beyond the base rate, positive
    when it resolves organs that really do differ, and NEGATIVE when it is worse
    than a constant. That last property is the point: it is the only term here
    that can go down because the system learned something false.

    Deliberately not a fit on the training rows. A model that is scored on the
    data it was built from reports its own memorisation, and a credit table
    built from all outcomes trivially "predicts" those outcomes.

    Unmeasurable is reported, not defaulted: a holdout where every outcome is
    identical has zero baseline variance, so no skill figure exists and the term
    is dropped from the objective rather than scored as 0.0.
    """
    out: Dict[str, Any] = {"ok": False, "value": None, "reason": "",
                           "n_decisions": 0, "n_train": 0, "n_holdout": 0,
                           "mse_model": None, "mse_baseline": None,
                           "baseline_success": None, "organ_models": 0}
    # Fetch order is newest-first, and that order is the only clock these rows
    # have: a decision carries no timestamp of its own in the joined view.
    decisions: List[List[Tuple[str, bool]]] = []
    for entry in joined.values():
        results = entry.get("results") if isinstance(entry, dict) else None
        if not isinstance(results, dict):
            continue
        row = [(str(w), bool(r.get("success")))
               for w, r in results.items() if isinstance(r, dict)]
        if row:
            decisions.append(row)
    out["n_decisions"] = len(decisions)
    if len(decisions) < 4:
        out["reason"] = "fewer than 4 joined decisions"
        return out

    share = min(0.9, max(0.05, float(holdout_share)))
    cut = max(1, int(round(len(decisions) * share)))
    holdout, train = decisions[:cut], decisions[cut:]
    out["n_holdout"], out["n_train"] = len(holdout), len(train)
    if not holdout or not train:
        out["reason"] = "split produced an empty side"
        return out

    seen: Dict[str, int] = {}
    ok_by_worm: Dict[str, int] = {}
    total = total_ok = 0
    for row in train:
        for worm, success in row:
            seen[worm] = seen.get(worm, 0) + 1
            ok_by_worm[worm] = ok_by_worm.get(worm, 0) + (1 if success else 0)
            total += 1
            total_ok += 1 if success else 0
    if total <= 0:
        out["reason"] = "train split carried no observations"
        return out
    base = total_ok / float(total)
    out["baseline_success"] = round(base, 6)

    se_model = se_base = 0.0
    n = 0
    for row in holdout:
        for worm, success in row:
            y = 1.0 if success else 0.0
            if seen.get(worm, 0) >= int(min_views):
                p = ok_by_worm[worm] / float(seen[worm])
            else:
                p = base
            se_model += (p - y) ** 2
            se_base += (base - y) ** 2
            n += 1
    out["organ_models"] = sum(1 for w, c in seen.items() if c >= int(min_views))
    if n < 2:
        out["reason"] = "holdout carried fewer than 2 observations"
        return out
    mse_model, mse_base = se_model / n, se_base / n
    out["mse_model"], out["mse_baseline"] = round(mse_model, 6), round(mse_base, 6)
    if mse_base <= 0.0:
        # Every held-out outcome was the same, so a constant is a perfect
        # predictor and "skill over it" is undefined rather than zero.
        out["reason"] = "holdout had no variance to predict"
        return out
    out["ok"] = True
    out["value"] = round(max(-1.0, min(1.0, 1.0 - mse_model / mse_base)), 6)
    out["n_observations"] = n
    return out


def measure(limit: int = 20000) -> Dict[str, Any]:
    """The system's current observable state, from execution telemetry only.

    Four quantities, each traceable to rows the emitter wrote:

    * `resolution` -- dispatches that produced an outcome / dispatches chosen.
      Computed over JOINED decisions, so numerator and denominator describe the
      same population. It used to be `n_outcome_rows / n_choice_rows` -- a
      20,000-row numerator over a 5,000-row denominator, which read 3.8x over
      its own ceiling and was hidden by the clamp. The row ratio is still
      reported, as `row_ratio`, described as the window mismatch it is.
    * `success`    -- successful outcomes / outcomes, read from `WormOutcome.ok`.
    * `coverage`   -- registered components with any outcome / registered.
    * `skill`      -- held-out prediction skill; see :func:`held_out_skill`.

    `limit` is honoured: it is passed to BOTH taps, so a caller asking for a
    windowed measurement gets one. It was accepted and never used, so a
    windowed measurement was intended and never existed.

    Returns `ok: False` with a reason rather than zeros when telemetry is
    unavailable: a measurement that could not be taken is not evidence of a
    worse system, and acting on it as if it were is how a loop destroys itself.
    """
    params = _objective_params()
    out: Dict[str, Any] = {"ok": False, "reason": ""}
    try:
        from CORE.WORMS.expansion.spawn_credit import (build_spawn_credit,
                                                       resolve_store)
        credit = build_spawn_credit(resolve_store(), outcome_limit=int(limit),
                                    choice_limit=int(limit))
    except Exception as exc:
        note_failure("hyperopt:L467", exc)
        out["reason"] = "credit unavailable: %s" % str(exc)[:120]
        return out

    try:
        rewards = dict(credit.rewards)
        outcomes = {w: int(o.n) for w, o in credit.outcomes.items()}
        n_outcome = int(getattr(credit, "n_outcome_rows", 0) or 0)
        n_choice = int(getattr(credit, "n_choice_rows", 0) or 0)
        never = list(credit.chosen_but_never_run())
        # `ok`, not `successes`. The latter does not exist on `WormOutcome`, and
        # the bare `except` that used to answer for it silently substituted 0.0
        # for a measured 99.8% -- a term that looked live, was weighted, and read
        # a constant for the entire life of the loop. Read INSIDE this guard so a
        # renamed field is a reported `ok: False` rather than an exception: a
        # measurement that could not be taken is not a worse system.
        n_seen = sum(int(o.n) for o in credit.outcomes.values())
        successes = sum(int(o.ok) for o in credit.outcomes.values())
    except Exception as exc:
        note_failure("hyperopt:L485", exc)
        out["reason"] = "credit shape unexpected: %s" % str(exc)[:120]
        return out

    try:
        from CORE.worm_registry import WORM_REGISTRY, discover_worms
        # Lazy-init ONLY, never a repopulate. `registered` is a denominator for
        # the coverage figure below, so this call read as harmless -- but an
        # UNGUARDED `discover_worms()` writes the whole fleet into whatever dict
        # is currently bound to the global. MEASURED 2026-09-21: a caller that
        # had deliberately set a 2-worm registry for an audit watched it become
        # 234 mid-measurement, so the audit's report disagreed with the table it
        # had just read (113 organs demoted by a genome that could not touch
        # them). A measurement that changes what it measures is not a
        # measurement. Guarded the way every other call site in CORE guards it;
        # when discovery has already run this call was a no-op anyway, so the
        # guard costs nothing in production.
        if not WORM_REGISTRY:
            try:
                discover_worms()
            except Exception as exc:
                note_failure("hyperopt:L505", exc)
        registered = len(WORM_REGISTRY)
    except Exception as exc:
        note_failure("hyperopt:L508", exc)
        registered = 0

    observed = sum(1 for n in outcomes.values() if n > 0)

    # resolution: over JOINED decisions, so the numerator and the denominator
    # describe the same population. Unmeasurable when nothing joined, and
    # reported as unmeasurable rather than defaulted to a number.
    joined = getattr(credit, "joined_decisions", {}) or {}
    n_chosen = sum(int(d.get("n_chosen", 0) or 0) for d in joined.values())
    n_ran = sum(int(d.get("n_ran", 0) or 0) for d in joined.values())
    resolution: Optional[float] = None
    if joined and n_chosen > 0:
        resolution = round(min(1.0, n_ran / float(n_chosen)), 6)
    # The old quantity, kept and LABELLED. It is a row count over a 20,000-row
    # numerator and a 5,000-row denominator: a descriptive figure about two
    # windows, not a resolution rate.
    row_ratio = round(float(n_outcome) / n_choice, 6) if n_choice else None

    success: Optional[float] = None
    if n_seen > 0:
        success = round(max(0.0, min(1.0, successes / float(n_seen))), 6)

    # Clamped to 1.0: two components with telemetry (SYSTEMZ, CUSTOM) are not in
    # the registry, so the raw ratio reads 1.009 -- a "coverage" above 100%.
    # An unclamped ratio in a weighted objective is not a coverage measure, it
    # is a fudge factor that can push the score past 1 and hide a real loss.
    coverage = min(1.0, (float(observed) / registered)) if registered else 0.0

    skill = held_out_skill(joined, params["holdout_share"],
                           params["min_skill_views"])

    out.update({
        "ok": True,
        "resolution": resolution,
        "resolution_basis": ("joined_decisions" if resolution is not None
                             else "unmeasurable"),
        "row_ratio": row_ratio,
        "success": success,
        "coverage": round(coverage, 6),
        "skill": skill,
        "observed": observed,
        "registered": registered,
        "n_choice_rows": n_choice,
        "n_outcome_rows": n_outcome,
        "chosen_never_run": len(never),
        "organs_scored": len(rewards),
        "params_used": params,
    })
    return out


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        note_failure("hyperopt:L563", exc)
        return None


def _term_state(value: Optional[float]) -> str:
    """Is a measured term contributing, or pinned against a bound?"""
    if value is None:
        return "unmeasured"
    if value >= PINNED_HIGH:
        return "pinned_high"
    if value <= PINNED_LOW:
        return "pinned_low"
    return "movable"


def objective(m: Dict[str, Any]) -> Dict[str, Any]:
    """Weighted score over the terms that were ACTUALLY measurable.

    Weights: resolution 0.35, success 0.25, coverage 0.15, skill 0.25. They are
    module constants and NOT parameters: a loop able to reweight its own
    objective can make any change look like an improvement by moving weight onto
    whichever term happened to rise. The frame is fixed; what lives inside it is
    what gets searched. (The previous weights were 0.45/0.35/0.20 over three
    terms, one of which could not be read and two of which were pinned -- the
    weighted average of a constant is a constant.)

    Unmeasured terms are DROPPED and the remaining weights RENORMALISED, and the
    result names which ones were dropped. Substituting 0.0 for a term that could
    not be measured is the defect this module was rebuilt around: a blind
    measurement is not a worse system, and averaging it in as a zero silently
    converts one into the other.

    `term_states` exists because a term pinned at its ceiling is not a term with
    a small effect, it is a term with NO effect. When every measured term is
    pinned the objective is mathematically constant, and saying so is the whole
    job of :func:`objective_health`.
    """
    if not m.get("ok"):
        return {"ok": False, "reason": m.get("reason", "no measurement"),
                "value": None, "metrics": m}
    params = _objective_params()

    skill_block = m.get("skill")
    if isinstance(skill_block, dict):
        skill = _coerce_float(skill_block.get("value")) if skill_block.get("ok") else None
    else:
        skill = _coerce_float(skill_block)

    weights = {"resolution": W_RESOLUTION, "success": W_SUCCESS,
               "coverage": W_COVERAGE, "skill": W_SKILL}
    raw = {"resolution": _coerce_float(m.get("resolution")),
           "success": _coerce_float(m.get("success")),
           "coverage": _coerce_float(m.get("coverage")),
           "skill": skill}
    measured = {k: v for k, v in raw.items() if v is not None}
    unmeasured = sorted(k for k, v in raw.items() if v is None)
    states = {k: _term_state(v) for k, v in measured.items()}
    movable = sorted(k for k, s in states.items() if s == "movable")
    pinned = sorted(k for k, s in states.items() if s != "movable")

    n_outcomes = int(m.get("n_outcome_rows") or 0)
    coverage = float(m.get("coverage") or 0.0)
    thin = []
    if n_outcomes < int(params["min_samples"]):
        thin.append("outcomes=%d<%d" % (n_outcomes, int(params["min_samples"])))
    if coverage < float(params["min_coverage"]):
        thin.append("coverage=%.3f<%.2f" % (coverage, float(params["min_coverage"])))

    value = None
    if measured:
        wsum = sum(weights[k] for k in measured) or 1.0
        value = round(sum(weights[k] * v for k, v in measured.items()) / wsum, 6)
    pinned_weight = 0.0
    if measured:
        wsum = sum(weights[k] for k in measured) or 1.0
        pinned_weight = round(sum(weights[k] for k in pinned) / wsum, 6)

    return {
        "ok": (not thin) and value is not None,
        "value": value,
        "reasons": thin,
        "evidence": {
            "outcomes": m.get("n_outcome_rows"),
            "coverage": m.get("coverage"),
            "observed": m.get("observed"),
            "registered": m.get("registered")},
        "terms": {k: round(v, 6) for k, v in measured.items()},
        "term_weights": {k: weights[k] for k in sorted(measured)},
        "term_states": states,
        "movable_terms": movable,
        "pinned_terms": pinned,
        "unmeasured_terms": unmeasured,
        "pinned_weight": pinned_weight,
        "metrics": m,
    }


# ---------------------------------------------------------------------------
# Is the objective capable of selecting anything?
# ---------------------------------------------------------------------------

def canary_tolerance(m: Optional[Dict[str, Any]] = None) -> float:
    """The decay a promotion must show before it is reverted, in the objective's
    OWN units.

    Why derived rather than a constant. The shipped value was 0.05. Measured on
    2026-09-21 the whole achievable shift in this objective was 4.24e-3 -- twelve
    times SMALLER than the tolerance meant to detect a loss. A canary 12x above
    the largest possible movement cannot fire, so it reported `held` for every
    promotion, including a harmful one, which is exactly what it did. The same
    mistake one layer down is `L1_MARGIN`, measured at 24x above the largest
    achievable effect: an effect floor must be quoted in the units of the
    objective it guards, never of a neighbouring signal.

    So the tolerance is a small multiple of the objective's own resolution,
    `1 / n_outcome_rows` -- the smallest step a share-of-observations metric can
    express. Three steps: a single flipped outcome cannot trigger a revert, two
    steps of real decay will.
    """
    m = m if m is not None else measure()
    try:
        n = int(m.get("n_outcome_rows") or 0)
    except (TypeError, ValueError) as exc:
        note_failure("hyperopt:L685", exc)
        n = 0
    if n <= 0:
        return CANARY_TOLERANCE_FLOOR
    return round(max(CANARY_TOLERANCE_FLOOR, 3.0 / float(n)), 6)


def _lineage_path() -> Path:
    return _STATE_DIR / "hyperopt_lineage.jsonl"


def load_lineage(limit: int = 4000) -> List[Dict[str, Any]]:
    """Every lineage row the hyperopt cycle wrote. Unparseable rows are skipped."""
    out: List[Dict[str, Any]] = []
    try:
        with _lineage_path().open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception as exc:
                    note_failure("hyperopt:L707", exc)
                    continue
                if isinstance(row, dict):
                    out.append(row)
                    if len(out) >= limit:
                        break
    except Exception as exc:
        note_failure("hyperopt:L713", exc)
        return []
    return out


def baseline_spread(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """How much has the objective MOVED between promotions?

    Read from the ledger rather than inferred: every promotion records the
    objective it was installed at, so the spread of those numbers is direct
    evidence of whether the outer loop has ever changed anything. Measured
    2026-09-21: every baseline was identical, because two of the three terms were
    pinned and the third was being read through a non-existent attribute.
    """
    rows = load_lineage() if rows is None else list(rows)
    values: List[float] = []
    for r in rows:
        if str(r.get("kind") or "") != "promote":
            continue
        for key in ("objective", "baseline_objective", "value", "score"):
            v = _coerce_float(r.get(key))
            if v is not None:
                values.append(v)
                break
    live = _coerce_float((_read_json(PARAMS_JSON) or {}).get("baseline_objective"))
    if live is not None:
        values.append(live)
    return {
        "samples": len(values),
        "values": values[-20:],
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "spread": (round(max(values) - min(values), 9) if values else None),
    }


def objective_health(m: Optional[Dict[str, Any]] = None,
                     rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Can this objective distinguish two candidates AT ALL?

    This is the question nobody asked for the life of the loop. `objective()`
    answered `0.65` every cycle, `canary` answered `held` every cycle, and those
    two answers are indistinguishable from a healthy system at a mature optimum.
    They were also what a system whose objective is a mathematical constant looks
    like.

    Two findings, deliberately separated because they have different remedies:

    * `pinned_by_construction` -- every measured term sits at a bound, so the
      objective cannot move for ANY parameter set. Nothing about the gate needs
      tuning; the measurement does. This is a hard finding and callers should
      refuse to promote on it.
    * `historical.no_observed_movement` -- zero spread across recorded baselines.
      Reported as a WARNING, not a block, because acting on it would deadlock:
      no promotions means no new baselines means the warning can never clear.
    """
    m = m if m is not None else measure()
    obj = objective(m)
    hist = baseline_spread(rows)
    if not obj.get("ok"):
        return {
            "ok": False, "reachable": False, "value": obj.get("value"),
            "reason": "objective not scorable: %s" % (
                "; ".join(obj.get("reasons") or []) or obj.get("reason") or "unknown"),
            "movable_terms": [], "pinned_terms": [], "unmeasured_terms": [],
            "warnings": [], "historical": hist,
            "canary_tolerance": canary_tolerance(m), "resolution": None,
        }

    states = obj.get("term_states") or {}
    movable = list(obj.get("movable_terms") or [])
    pinned = list(obj.get("pinned_terms") or [])
    unmeasured = list(obj.get("unmeasured_terms") or [])
    blocks: List[str] = []
    warnings: List[str] = []
    if not states:
        blocks.append("no term was measurable")
    elif not movable:
        blocks.append("every measured term is pinned at a bound: %s"
                      % ", ".join(pinned))
    spread = hist.get("spread")
    if hist.get("samples", 0) >= 3 and spread is not None and spread <= 0.0:
        warnings.append("%d recorded baselines with zero spread -- promotions have "
                        "never moved this objective" % hist["samples"])

    try:
        n = int(m.get("n_outcome_rows") or 0)
    except (TypeError, ValueError) as exc:
        note_failure("hyperopt:L800", exc)
        n = 0
    return {
        "ok": bool(states),
        "reachable": not blocks,
        "value": obj.get("value"),
        "reason": "; ".join(blocks) if blocks else "at least one term can move",
        "resolution": (round(1.0 / float(n), 9) if n else None),
        "canary_tolerance": canary_tolerance(m),
        "movable_terms": movable,
        "pinned_terms": pinned,
        "unmeasured_terms": unmeasured,
        "pinned_weight": obj.get("pinned_weight"),
        "terms": obj.get("terms"),
        "warnings": warnings,
        "historical": hist,
    }


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------

def propose(rng: random.Random, base: Optional[Dict[str, Any]] = None,
            names: Optional[Tuple[str, ...]] = None) -> Tuple[Dict[str, Any], str]:
    """One mutation of one dimension. Returns (params, "name=old->new").

    Exactly one dimension per candidate, on purpose: the objective is measured
    over TIME, so a candidate that moved three thresholds at once could not be
    attributed to any of them. It is the same reason `OBSERVABLE_MUTATIONS`
    exists in the wiring genome -- an operator whose effect is invisible cannot
    be selected for.
    """
    params = dict(base if base is not None else load())
    pool = list(names or tuple(PARAM_SPACE))
    pool = [n for n in pool if n in PARAM_SPACE]
    if not pool:
        return params, "no-parameters"
    name = rng.choice(pool)
    spec = PARAM_SPACE[name]
    lo, hi = spec["low"], spec["high"]
    old = params[name]
    if spec["kind"] == "int":
        span = hi - lo
        # +-1 or +-2 steps, never a jump to the opposite corner: a threshold is
        # a calibration, and a random restart would discard the current setting
        # rather than explore around it.
        new = int(old) + rng.choice((-2, -1, 1, 2))
        new = max(lo, min(hi, new))
        if new == old:
            new = hi if old == lo else lo
        new = int(new)
    else:
        step = (hi - lo) / 8.0
        new = round(float(old) + rng.choice((-2, -1, 1, 2)) * step, 4)
        new = max(lo, min(hi, new))
        if new == old:
            new = round(min(hi, old + step), 4)
    params[name] = new
    return params, "%s=%s->%s" % (name, old, new)
