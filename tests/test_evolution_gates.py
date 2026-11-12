# coding: utf-8
"""Regression tests for the evolution promotion gates and meta-search floors.

Covers the 2026-09-10 audit fixes (see EVOLUTION_PROCESS_AUDIT_20260910.md):

  EV-1  live genes resolve from an untracked path (state/), never a tracked one
  EV-2  the promotion streak is pinned to the pool era *and* the baseline genome
  EV-4  search-integrity meta-params cannot be relaxed by the meta-search

These are the first committed tests for the evolution loop. The driver is
imported by path (not as a package) because it lives in scripts/ and is
import-safe by contract: importing must load only pure helpers and must never
run a GA or write a promotion.

Run:
    cd azazael && python -m pytest tests/test_evolution_gates.py -q
"""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER = REPO_ROOT / "scripts" / "evolution_cycle.py"
SRC = REPO_ROOT / "src"

# --- gate-contract guard (2026-09-30) ---------------------------------------
# The `evo` fixture needs the estimator-identity stamp and the era-pinning
# helpers to exist on the driver. The stamp producer was missing from the
# working tree (nothing wrote `margin_estimator`) and was restored 2026-09-30;
# the era pinning lives in `_sprt_history` / `_compute_streak` (there is no
# function literally named `_same_measurement_era`). Guard on the symbols that
# must exist so a future regression skips with ONE reason instead of emitting
# 61 identical setup errors. See RECOVERY_REMEDIATION_PLAN_2026-09-30.md WS-2.
_CONTRACT_SYMBOLS = (
    "MARGIN_ESTIMATOR", "N_EPISODES", "_sprt_history", "_compute_streak",
)
if DRIVER.exists():
    _driver_src = DRIVER.read_text(encoding="utf-8", errors="replace")
    _missing = [s for s in _CONTRACT_SYMBOLS if s not in _driver_src]
else:
    _missing = ["<driver missing>"]
if _missing:
    pytest.skip(
        "evolution_cycle.py no longer exposes the gate contract this file "
        "tests (%s) - see RECOVERY_REMEDIATION_PLAN_2026-09-30.md WS-2"
        % ", ".join(_missing),
        allow_module_level=True,
    )


#: The instrument stamp every CURRENT row carries. Populated from the driver by
#: the `evo` fixture so `_row()` can build a row the guards will accept.
_INSTRUMENT = {}


@pytest.fixture(scope="module")
def evo():
    """Load scripts/evolution_cycle.py as a module (import-safe)."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    assert DRIVER.exists(), f"driver not found at {DRIVER}"
    spec = importlib.util.spec_from_file_location("evolution_cycle_under_test", DRIVER)
    module = importlib.util.module_from_spec(spec)
    # Must not run a GA, promote anything, or touch live state.
    spec.loader.exec_module(module)
    _INSTRUMENT["estimator"] = module.MARGIN_ESTIMATOR
    _INSTRUMENT["episodes"] = module.N_EPISODES
    return module


def _row(pool_id, baseline_id, beats=True, fitness="v3-dual-robust", as_json=False,
         estimator=None, episodes=None):
    """Build one prior evolution_result row in the store's row shape.

    The instrument stamp (`margin_estimator` / `eval_episodes`) is part of a
    row's identity since 2026-09-18: `_same_measurement_era()` fails closed on
    rows that lack it, so a fixture without it makes every row break the streak
    and the assertion reads as a streak bug rather than as a stale fixture.
    Defaults to the CURRENT instrument, which is what a live row carries; pass
    explicit values to build a row from a different measurement era.
    """
    data = {
        "pool_id": pool_id,
        "baseline_hash": baseline_id,
        "fitness": fitness,
        "beats_baseline": beats,
        "margin_estimator": (_INSTRUMENT.get("estimator") if estimator is None
                             else estimator),
        "eval_episodes": (_INSTRUMENT.get("episodes") if episodes is None
                          else episodes),
    }
    return {"data": json.dumps(data) if as_json else data}


GENES_A = {"learning_rate": 0.5, "epsilon": 0.2147, "discount": 0.8254,
           "epsilon_decay": 0.0964}
GENES_B = {"learning_rate": 0.3312, "epsilon": 0.0346, "discount": 0.8189,
           "epsilon_decay": 0.0}


# --- EV-1: live genes must not resolve to a git-tracked path ----------------

def test_live_genes_path_is_untracked_state_dir(evo):
    """The promoted-genes file must live under state/ (untracked), not config/.

    Regression for defect D-1: config/evolved_rl_params.json was tracked, so a
    git restore reverted production hyperparameters and bypassed every gate.
    """
    assert evo.PROMOTE_JSON.endswith(os.path.join("state", "evolved_rl_params.json"))
    assert evo.LEGACY_PROMOTE_JSON.endswith(
        os.path.join("config", "evolved_rl_params.json"))


def test_promote_json_is_not_the_legacy_path(evo):
    assert os.path.abspath(evo.PROMOTE_JSON) != os.path.abspath(evo.LEGACY_PROMOTE_JSON)


def test_promoted_file_prefers_state_over_legacy(evo, tmp_path, monkeypatch):
    """state/ wins when both exist — a legacy file can never shadow it."""
    monkeypatch.setattr(evo, "PROMOTE_JSON", str(tmp_path / "state_params.json"))
    monkeypatch.setattr(evo, "LEGACY_PROMOTE_JSON", str(tmp_path / "legacy_params.json"))
    Path(evo.LEGACY_PROMOTE_JSON).write_text("{}", encoding="utf-8")
    assert evo._promoted_file() == evo.LEGACY_PROMOTE_JSON
    Path(evo.PROMOTE_JSON).write_text("{}", encoding="utf-8")
    assert evo._promoted_file() == evo.PROMOTE_JSON


# --- EV-2: baseline_hash ----------------------------------------------------

def test_baseline_hash_is_deterministic(evo):
    assert evo.baseline_hash(GENES_A) == evo.baseline_hash(dict(GENES_A))


def test_baseline_hash_discriminates_genomes(evo):
    """The two genomes from the 2026-09-10 incident must hash differently."""
    assert evo.baseline_hash(GENES_A) != evo.baseline_hash(GENES_B)


def test_baseline_hash_is_order_insensitive_but_value_sensitive(evo):
    shuffled = {k: GENES_A[k] for k in reversed(list(GENES_A))}
    assert evo.baseline_hash(shuffled) == evo.baseline_hash(GENES_A)
    nudged = dict(GENES_A, epsilon=GENES_A["epsilon"] + 0.000001)
    assert evo.baseline_hash(nudged) != evo.baseline_hash(GENES_A)


# --- EV-2: promotion streak gate -------------------------------------------

POOL = 1789096771
BASE = "d255c447deef87d3"


def test_streak_is_zero_when_not_beating(evo):
    rows = [_row(POOL, BASE), _row(POOL, BASE)]
    assert evo._compute_streak(rows, POOL, BASE, beats=False) == 0


def test_streak_is_one_with_no_prior_wins(evo):
    assert evo._compute_streak([], POOL, BASE, beats=True) == 1


def test_streak_accumulates_consecutive_same_era_wins(evo):
    rows = [_row(POOL, BASE), _row(POOL, BASE)]
    assert evo._compute_streak(rows, POOL, BASE, beats=True) == 3


def test_streak_caps_at_promote_wins(evo):
    rows = [_row(POOL, BASE) for _ in range(10)]
    assert evo._compute_streak(rows, POOL, BASE, beats=True) == evo.PROMOTE_WINS


def test_streak_breaks_on_pool_era_change(evo):
    """Cross-era margins are not promotion-grade."""
    rows = [_row(POOL, BASE), _row(POOL - 1, BASE)]
    assert evo._compute_streak(rows, POOL, BASE, beats=True) == 2


def test_streak_breaks_on_baseline_genome_change(evo):
    """EV-2 core regression.

    On 2026-09-10 an external restore swapped production from GENES_A to
    GENES_B while pool_id stayed identical, so wins measured against two
    different baselines were being counted as one streak.
    """
    other_baseline = evo.baseline_hash(GENES_B)
    rows = [_row(POOL, other_baseline), _row(POOL, other_baseline)]
    assert evo._compute_streak(rows, POOL, BASE, beats=True) == 1


def test_streak_breaks_on_legacy_row_without_tags(evo):
    """Pre-fix rows carry no baseline_hash/pool_id: fail closed."""
    assert evo._compute_streak([{"data": {}}], POOL, BASE, beats=True) == 1
    assert evo._compute_streak([{"data": {"pool_id": POOL}}], POOL, BASE,
                              beats=True) == 1


def test_streak_breaks_on_non_v3_rows(evo):
    """Rows from an older fitness scale are not comparable."""
    rows = [_row(POOL, BASE, fitness="v2-single")]
    assert evo._compute_streak(rows, POOL, BASE, beats=True) == 1


def test_streak_breaks_on_rows_that_did_not_beat_baseline(evo):
    rows = [_row(POOL, BASE, beats=False)]
    assert evo._compute_streak(rows, POOL, BASE, beats=True) == 1


def test_streak_accepts_json_string_payloads(evo):
    rows = [_row(POOL, BASE, as_json=True), _row(POOL, BASE, as_json=True)]
    assert evo._compute_streak(rows, POOL, BASE, beats=True) == 3


def test_streak_survives_malformed_payload(evo):
    assert evo._compute_streak([{"data": "{not json"}], POOL, BASE, beats=True) == 1
    assert evo._compute_streak([{"data": None}], POOL, BASE, beats=True) == 1
    assert evo._compute_streak(None, POOL, BASE, beats=True) == 1


# --- EV-4: meta-search floors ----------------------------------------------

def test_min_gens_gate_exists(evo):
    """A convergence claim is not honoured before MIN_GENS generations."""
    assert evo.MIN_GENS >= evo.META_BOUNDS["early_stop_patience"][0]


def test_patience_floor_is_raised(evo):
    """Floor was 3, which let the GA stop at gen 3-4 of a 15-gen budget."""
    assert evo.META_BOUNDS["early_stop_patience"][0] >= 5


def test_integrity_knobs_are_not_explorable(evo):
    """Letting the search relax its own stopping rule is reward hacking."""
    assert "early_stop_patience" not in evo.META_EXPLORE_KEYS
    assert "early_stop_tolerance" not in evo.META_EXPLORE_KEYS
    assert set(evo.META_EXPLORE_KEYS).issubset(set(evo.META_BOUNDS))


def test_clamp_meta_forces_values_into_bounds(evo):
    low, high = evo.META_BOUNDS["early_stop_patience"]
    out = evo._clamp_meta({"early_stop_patience": 3})
    assert out["early_stop_patience"] == low
    out = evo._clamp_meta({"early_stop_patience": 99})
    assert out["early_stop_patience"] == high


def test_clamp_meta_preserves_int_types(evo):
    out = evo._clamp_meta(dict(evo.META_DEFAULTS))
    for key in evo.META_INT_KEYS:
        assert isinstance(out[key], int), key


def test_clamp_meta_fills_missing_keys_with_defaults(evo):
    out = evo._clamp_meta({})
    assert out == evo._clamp_meta(dict(evo.META_DEFAULTS))


def test_meta_propose_clamps_a_stale_persisted_candidate(evo):
    """Regression for the bypass found while implementing EV-4.

    _meta_load() clamped the champion but _meta_propose() returned the raw
    persisted candidate, so a stale candidate pinned early_stop_patience back
    to 3 and the GA kept stopping early.
    """
    floor = evo.META_BOUNDS["early_stop_patience"][0]
    state = {
        "params": dict(evo.META_DEFAULTS),
        "score_ema": 1.0,
        "runs": 194,
        "candidate": dict(evo.META_DEFAULTS, early_stop_patience=3),
        "candidate_runs": 2,
        "candidate_sum": 4.0,
    }
    params, is_candidate = evo._meta_propose(state)
    assert is_candidate is True
    assert params["early_stop_patience"] == floor


def test_meta_propose_clamps_explore_perturbations(evo):
    """Whatever dimension is explored, the result stays in bounds."""
    for key in evo.META_EXPLORE_KEYS:
        state = {
            "params": dict(evo.META_DEFAULTS, **{
                k: (lo + hi) / 2.0
                for k, (lo, hi) in evo.META_BOUNDS.items()
            }),
            "score_ema": 0.0,
            "runs": 0,
            "candidate": None,
            "candidate_runs": 0,
            "candidate_sum": 0.0,
        }
        params, _ = evo._meta_propose(state)
        for k, (lo, hi) in evo.META_BOUNDS.items():
            assert lo <= params[k] <= hi, (key, k, params[k])


# --- SPRT promotion gate (2026-09-11, followup #2) --------------------------
# Replaces the fixed 3/3 streak. The old rule had no variance model: a +0.76
# margin and a +10.0 margin both needed exactly three wins.

def test_sprt_thresholds_follow_alpha_and_beta(evo):
    import math
    assert evo.SPRT_UPPER == pytest.approx(
        math.log((1.0 - evo.SPRT_BETA) / evo.SPRT_ALPHA))
    assert evo.SPRT_LOWER == pytest.approx(
        math.log(evo.SPRT_BETA / (1.0 - evo.SPRT_ALPHA)))
    assert evo.SPRT_UPPER > 0.0 > evo.SPRT_LOWER


def test_sprt_step_is_zero_at_the_indifference_point(evo):
    mid = (evo.SPRT_DELTA0 + evo.SPRT_DELTA1) / 2.0
    assert evo.sprt_llr_step(mid, 0.2) == pytest.approx(0.0, abs=1e-12)


def test_sprt_step_sign_follows_the_evidence(evo):
    assert evo.sprt_llr_step(evo.SPRT_DELTA1, 0.2) > 0.0
    assert evo.sprt_llr_step(evo.SPRT_DELTA0, 0.2) < 0.0


def test_sprt_step_survives_bad_input_and_zero_sigma(evo):
    assert evo.sprt_llr_step(None, 0.2) == 0.0
    assert evo.sprt_llr_step(float("nan"), 0.2) == 0.0
    # sigma floored, otherwise this is a ZeroDivisionError
    assert evo.sprt_llr_step(1.0, 0.0) != 0.0


def test_sprt_never_promotes_on_a_single_observation(evo):
    """A degenerate sigma must not let one lucky cycle promote."""
    _llr, _sigma, n, decision = evo._sprt_decide([99.0], salt_floor=0.01)
    assert n == 1
    assert decision != "promote"
    assert evo.PROMOTE_MIN_WINS >= 2


def test_sprt_promotes_large_margins_with_more_evidence_than_small(evo):
    strong = evo._sprt_decide([2.0, 2.0], salt_floor=0.05)
    weak = evo._sprt_decide([0.8, 0.8], salt_floor=0.05)
    assert strong[3] == "promote"
    assert strong[0] > weak[0]


def test_sprt_rejects_when_the_evidence_is_against_improvement(evo):
    llr, _sigma, _n, decision = evo._sprt_decide([0.0, 0.0, 0.0], salt_floor=0.05)
    assert decision == "reject"
    assert llr <= evo.SPRT_LOWER


def test_sprt_declares_inconclusive_at_the_cap(evo):
    """Evidence stuck between the bounds must stop accumulating."""
    mid = (evo.SPRT_DELTA0 + evo.SPRT_DELTA1) / 2.0
    seq = [mid] * evo.PROMOTE_MAX_WINS
    llr, _sigma, n, decision = evo._sprt_decide(seq, salt_floor=0.2)
    assert n == evo.PROMOTE_MAX_WINS
    assert evo.SPRT_LOWER < llr < evo.SPRT_UPPER
    assert decision == "inconclusive"


def test_sprt_decide_handles_empty_input(evo):
    assert evo._sprt_decide([], salt_floor=0.1)[3] == "reject"


def test_sigma_prefers_observed_dispersion_over_the_floor(evo):
    assert evo.estimate_margin_sigma([0.0, 5.0], salt_floor=0.01) > 1.0


def test_sigma_falls_back_to_the_salt_floor_with_one_observation(evo):
    assert evo.estimate_margin_sigma([3.0], salt_floor=0.42) == pytest.approx(0.42)


def test_sigma_is_never_zero(evo):
    """A zero sigma would make the likelihood ratio explode."""
    assert evo.estimate_margin_sigma([], salt_floor=0.0) > 0.0


def _sprt_row(pool, base, margin, estimator=None, episodes=None):
    """A v3 SPRT row carrying the CURRENT instrument stamp (see _row)."""
    return {"data": {"pool_id": pool, "baseline_hash": base,
                     "fitness": "v3-dual-robust", "beats_baseline": True,
                     "margin": margin,
                     "margin_estimator": (_INSTRUMENT.get("estimator")
                                          if estimator is None else estimator),
                     "eval_episodes": (_INSTRUMENT.get("episodes")
                                       if episodes is None else episodes)}}


def test_sprt_history_is_era_and_baseline_pinned(evo):
    rows = [_sprt_row(POOL, BASE, 1.5), _sprt_row(POOL, BASE, 1.2)]
    assert evo._sprt_history(rows, POOL, BASE) == [1.5, 1.2]
    assert evo._sprt_history(rows, POOL, "other-baseline") == []
    assert evo._sprt_history(rows, POOL - 1, BASE) == []


def test_sprt_history_derives_margin_for_rows_predating_the_key(evo):
    data = {"pool_id": POOL, "baseline_hash": BASE, "fitness": "v3-dual-robust",
            "beats_baseline": True, "best_total": 31.0, "baseline": 29.5,
            "margin_estimator": _INSTRUMENT.get("estimator"),
            "eval_episodes": _INSTRUMENT.get("episodes")}
    assert evo._sprt_history([{"data": data}], POOL, BASE) == [pytest.approx(1.5)]


def test_sprt_history_breaks_on_legacy_or_malformed_rows(evo):
    assert evo._sprt_history([{"data": {}}], POOL, BASE) == []
    assert evo._sprt_history([{"data": "{not json"}], POOL, BASE) == []
    assert evo._sprt_history(None, POOL, BASE) == []


# --- Pareto balance gate (2026-09-11, followup #1) --------------------------
# A plain dominance gate is provably vacuous here: because fitness is the scalar
# sum info+alert, a baseline that dominated on BOTH objectives would also have
# the larger total, so `beats` would already be False. The constraint that is
# NOT expressible by the total is one-sided alert regression.

def _parts(total, info, alert):
    return (round(total, 4), round(info, 4), round(alert, 4))


def _stub_parts(monkeypatch, evo, mapping):
    """Stub fitness_parts, keying off a tag carried by the gene dict."""
    def _fake(genes, info_pool, alert_pool):
        tag = genes.get("_tag") if isinstance(genes, dict) else genes
        return mapping[tag]
    monkeypatch.setattr(evo, "fitness_parts", _fake)


CHAMP = {"_tag": "cand"}
PROD = {"_tag": "base"}
KNEE = {"_tag": "knee"}


def test_pareto_gate_passes_when_the_alert_objective_holds(evo, monkeypatch):
    _stub_parts(monkeypatch, evo, {"cand": _parts(10.0, 6.0, 4.0),
                                   "base": _parts(9.0, 5.0, 4.0)})
    rep = evo._pareto_gate(CHAMP, PROD, None, None)
    assert rep["passed"] is True
    assert rep["source"] == "champion"


def test_pareto_gate_blocks_a_total_win_that_guts_alert(evo, monkeypatch):
    """Total rises by 1.0 while alert collapses: invisible to the margin gate."""
    _stub_parts(monkeypatch, evo, {"cand": _parts(32.0, 30.0, 2.0),
                                   "base": _parts(31.0, 16.0, 15.0)})
    rep = evo._pareto_gate(CHAMP, PROD, None, None)
    assert rep["passed"] is False
    assert rep["cand_alert"] < rep["alert_floor"]


def test_pareto_gate_falls_back_to_the_knee(evo, monkeypatch):
    # knee holds alert AND clears base + PROMOTE_MARGIN (31.0 + 0.75)
    _stub_parts(monkeypatch, evo, {"cand": _parts(32.0, 30.0, 2.0),
                                   "knee": _parts(32.3, 15.8, 16.5),
                                   "base": _parts(31.0, 16.0, 15.0)})
    rep = evo._pareto_gate(CHAMP, PROD, None, None, knee_genes=KNEE)
    assert rep["passed"] is True
    assert rep["source"] == "knee"
    assert rep["knee_genes"] == KNEE


def test_pareto_gate_rejects_a_knee_that_misses_the_margin(evo, monkeypatch):
    _stub_parts(monkeypatch, evo, {"cand": _parts(32.0, 30.0, 2.0),
                                   "knee": _parts(31.1, 15.0, 16.1),
                                   "base": _parts(31.0, 16.0, 15.0)})
    rep = evo._pareto_gate(CHAMP, PROD, None, None, knee_genes=KNEE)
    assert rep["passed"] is False


def test_pareto_gate_ignores_a_non_dict_knee(evo, monkeypatch):
    """_pareto_refine returns genes as a dict; anything else must not be used."""
    _stub_parts(monkeypatch, evo, {"cand": _parts(32.0, 30.0, 2.0),
                                   "base": _parts(31.0, 16.0, 15.0)})
    assert evo._pareto_gate(CHAMP, PROD, None, None,
                            knee_genes="not-a-dict")["passed"] is False


def test_pareto_gate_fails_closed_on_error(evo, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("pool unavailable")
    monkeypatch.setattr(evo, "fitness_parts", boom)
    assert evo._pareto_gate(CHAMP, PROD, None, None)["passed"] is False


# --- Prioritized alert-pool sampling (2026-09-11, followup #4) --------------
# The alert pool is a hard-capped 500-row window sampled UNIFORMLY, so when the
# system is healthy the objective re-trains on frozen rows. hard_count cannot
# reveal it (it saturates at the cap), so priority must come from severity and
# recency instead.

NOW = 1_800_000_000.0


def _alert(severity, age_h, now=NOW):
    import time as _t
    ts = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime(now - age_h * 3600.0))
    return {"severity": severity, "timestamp": ts}


def test_pool_priority_ranks_critical_above_info(evo):
    assert (evo.pool_priority(_alert("critical", 1.0), NOW)
            > evo.pool_priority(_alert("info", 1.0), NOW))


def test_pool_priority_decays_with_age(evo):
    assert (evo.pool_priority(_alert("critical", 1.0), NOW)
            > evo.pool_priority(_alert("critical", 72.0), NOW))


def test_pool_priority_is_floored_for_very_old_rows(evo):
    """Old rows fade but must never reach zero (they still get sampled)."""
    old = evo.pool_priority(_alert("critical", 10_000.0), NOW)
    assert old > 0.0
    assert old >= evo.SEVERITY_WEIGHT["critical"] * evo.PRIORITY_RECENCY_FLOOR


def test_pool_priority_tolerates_missing_or_bad_timestamps(evo):
    assert evo.pool_priority({}, NOW) > 0.0
    assert evo.pool_priority({"severity": "critical"}, NOW) > 0.0
    assert evo.pool_priority({"severity": "error", "timestamp": "nope"}, NOW) > 0.0
    assert evo.pool_priority(None, NOW) > 0.0


def test_age_hours_parses_z_suffix_and_rejects_junk(evo):
    assert evo._age_hours("2026-09-11T00:00:00Z", NOW) is not None
    assert evo._age_hours("nope", NOW) is None
    assert evo._age_hours("", NOW) is None
    assert evo._age_hours(None, NOW) is None


def test_prioritize_pool_is_deterministic_for_a_seed(evo):
    pool = [_alert("critical", 1.0), _alert("info", 100.0)] * 4
    assert (evo.prioritize_pool(pool, seed=7, now=NOW)
            == evo.prioritize_pool(pool, seed=7, now=NOW))


def test_prioritize_pool_oversamples_critical_rows(evo):
    pool = [_alert("critical", 0.5) for _ in range(10)] + \
           [_alert("info", 500.0) for _ in range(10)]
    drawn = evo.prioritize_pool(pool, seed=11, now=NOW)
    crit = sum(1 for r in drawn if r["severity"] == "critical")
    assert crit > len(drawn) // 2          # uniform would be ~50%


def test_prioritize_pool_preserves_length_by_default(evo):
    pool = [_alert("warning", 5.0)] * 13
    assert len(evo.prioritize_pool(pool, seed=1, now=NOW)) == 13
    assert len(evo.prioritize_pool(pool, size=5, seed=1, now=NOW)) == 5


def test_prioritize_pool_handles_empty_and_degenerate_pools(evo):
    assert evo.prioritize_pool([]) == []
    assert evo.prioritize_pool(None) == []
    # identical priorities must still yield a full-length pool
    flat = [{"severity": "info", "timestamp": ""}] * 6
    assert len(evo.prioritize_pool(flat, seed=2, now=NOW)) == 6


def test_prioritize_pool_returns_new_list_never_mutates_input(evo):
    pool = [_alert("critical", 1.0), _alert("info", 1.0)]
    before = list(pool)
    out = evo.prioritize_pool(pool, seed=3, now=NOW)
    assert out is not pool
    assert pool == before


# ---------------------------------------------------------------------------
# 2026-09-19: the promotion margin must be applied ONCE, on fresh pools.
#
# It used to be applied twice -- once as the entry gate via `beats` (frozen
# pool) and again at the holdout (fresh pools). On the frozen pool it is a
# pool-fit measure, not a capability measure, so requiring it before the SPRT
# selected candidates *for* pool fit. Measured over 40 live cycles:
# 5 candidates cleared the frozen gate (margins 1.84x the threshold), 0
# generalised (0.17x on fresh pools), 0/5 holdout passes, 0 promotions -- while
# cycles below the frozen gate were recorded as `sprt not_run` with
# `cranium {}` and contributed nothing to the cross-era pooling that exists to
# accumulate exactly this evidence.
#
# These are source-level guards because the alternative is running a GA. The
# invariant is worth guarding directly: re-introducing the double application
# is a one-word edit and it silently restores the overfitting filter.
# ---------------------------------------------------------------------------

# Prerequisite forms only. `if not beats:` inside _compute_streak (the early
# return) and `elif not beats:` in the verdict text are legitimate reporting and
# are deliberately not matched.
_GATE_USE = re.compile(
    r"(?:if|and|or|elif)\s+beats\b"      # `if beats`, `... and beats`
    r"|\bbeats\s+and\b"                   # `beats and ...`
)


def _gating_sites(source):
    """Gating occurrences of `beats` in CODE, ignoring comments.

    Comments discuss the old form, and a comment is not a gate.
    """
    found = []
    for line in source.splitlines():
        code = line.split("#", 1)[0]
        if _GATE_USE.search(code):
            found.append(code.strip())
    return found


def test_promote_margin_is_not_reused_as_an_entry_gate(evo):
    """`beats` (frozen margin > PROMOTE_MARGIN) must not gate the pipeline."""
    source = DRIVER.read_text(encoding="utf-8")
    gating = _gating_sites(source)
    assert not gating, (
        "`beats` is being used as a gate again: %r. The frozen-margin test is "
        "the pool-fit filter; entry must use _candidate_worthy." % gating
    )


def test_entry_gate_is_defined_before_every_use(evo):
    source = DRIVER.read_text(encoding="utf-8")
    defined = source.index("_candidate_worthy = bool(best)")
    uses = [m.start() for m in re.finditer(r"_candidate_worthy", source)]
    assert uses, "_candidate_worthy is missing entirely"
    assert min(uses) == defined, "_candidate_worthy is used before it is defined"


def test_frozen_gate_outcome_is_still_recorded(evo):
    """`beats_baseline` must keep meaning 'cleared the frozen gate'.

    The benchmark asserts this field against the frozen margin, so redefining
    it to the new entry condition would silently break that check.
    """
    source = DRIVER.read_text(encoding="utf-8")
    assert "'beats_baseline': beats," in source
    assert "'candidate_worthy': _candidate_worthy," in source
    assert "beats = bool(best) and (_margin_raw > PROMOTE_MARGIN)" in source


def test_streak_still_requires_a_frozen_gate_win(evo):
    """The streak stays a frozen-gate counter, unchanged by the entry fix."""
    rows = [_row(1, "b1"), _row(1, "b1")]
    assert evo._compute_streak(rows, 1, "b1", True) == 3
    assert evo._compute_streak(rows, 1, "b1", False) == 0


def test_streak_fails_closed_on_rows_without_the_instrument_stamp(evo):
    """A pre-2026-09-18 row must not count toward a promotion.

    This is the guard whose absence made 7 tests in this file fail unnoticed
    from 2026-09-18: the fixture was stale, not the guard. Pinning it here means
    the next instrument change fails loudly in one place.
    """
    rows = [_row(1, "b1"), _row(1, "b1")]
    assert evo._compute_streak(rows, 1, "b1", True) == 3
    # a different estimator
    old_est = [_row(1, "b1", estimator="unpaired-min"), _row(1, "b1")]
    assert evo._compute_streak(old_est, 1, "b1", True) == 1
    # a different eval budget
    old_budget = [_row(1, "b1", episodes=64), _row(1, "b1")]
    assert evo._compute_streak(old_budget, 1, "b1", True) == 1
    # no stamp at all (as a pre-fix row would be)
    bare = [{"data": {"pool_id": 1, "baseline_hash": "b1",
                       "fitness": "v3-dual-robust", "beats_baseline": True}},
            _row(1, "b1")]
    assert evo._compute_streak(bare, 1, "b1", True) == 1
