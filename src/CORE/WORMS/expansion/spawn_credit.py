"""Spawn credit — the missing join that lets the ecosystem learn from itself.

WHY THIS FILE EXISTS (2026-09-21)
---------------------------------
The architecture already records BOTH halves of a decision it never learns from:

  * WHAT IT CHOSE - ``spawn_selected`` (worm_registry.py, SpawnDiversityEngine)
    {worm_type, category, priority}
  * WHAT HAPPENED - ``clusterwormz_worm_executed`` (CLUSTERWORMS.py)
    {worm, success, duration_ms, error}

Both streams exist in LocalMemoryStore today. Neither is consumed by any
learner: the only readers are dashboard scripts (``lilith_live_monitor.py``),
and the decider itself (``SpawnDiversityEngine``) is instantiated once at
``CLUSTERWORMS.py`` and never called - ``pick_next_spawn`` has no live
caller outside the archived Libra copy.

So the self-improvement loop is open at exactly one place: **the join**. The
system records its state-action and its reward and never computes the update.

This module performs that join and derives a reward that is GROUNDED in
observed telemetry rather than authored by hand.

THE DESIGN RULE THAT MAKES THIS LEARNABLE
-----------------------------------------
The existing RL task is degenerate by construction:

    reward = _compute_reward(record, action)      # depends only on severity
    state  = f"{source}:{severity}"               # contains that same severity

The reward's only input is already inside the state key, so ``argmax_a Q(s,a)``
is determined before any learning happens. Measured consequence: the learned
policy is worse than a hardcoded constant (-0.039/step) and the per-state
oracle EQUALS the best constant - the state carries zero information.

The rule that follows, and that this module obeys:

    *** THE REWARD MUST NOT BE A FUNCTION OF THE STATE ***

Which worm to spawn cannot be read off the worm's own identity: it depends on
how that worm has been PERFORMING, which is only known after the fact. That
structural difference is the whole reason this task can be learned and the
severity->action one cannot.

ATTRIBUTION HONESTY
-------------------
Neither tap carries a correlation id, so a spawn CHOICE cannot be matched to
the specific EXECUTION it caused. This join is therefore *statistical*
(per-worm-type credit) and not *causal* (per-decision counterfactual).

It is still the right first step: per-worm credit is enough to reweight spawn
selection, and it is a strict improvement on the hand-authored
``_guess_priority`` heuristic it would replace. Decision-level attribution
needs a ``decision_id`` written to both taps - see ``MISSING_CORRELATION_ID``.

Everything here is read-only and fail-soft: a missing store, missing table or
malformed row degrades to an empty credit table, never an exception, because
this feeds a scheduling decision that must always produce an answer.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPAWN_EVENT = "spawn_selected"
OUTCOME_EVENT = "clusterwormz_worm_executed"

# Mirrors LocalMemoryStore.MAX_QUERY_LIMIT: one query cannot return more than
# this, so a full read must page. Kept as a constant so a change in the store
# surfaces here as an obvious mismatch rather than a silent under-read.
_PAGE_SIZE = 500
_MAX_PAGES = 100

# Latency-drift sensitivity, in reward units per doubling beyond tolerance.
# Used ONLY by `slow_regression`, which compares a worm to its OWN baseline.
# Deliberately NOT part of `reward()` - see that method for why a cross-worm
# latency term was removed on 2026-09-21 after live measurement.
W_COST = 0.25

# How much slower than its own baseline a worm may run before it counts as a
# regression (2.0 = twice its own historical duration).
SLOW_TOLERANCE = 2.0

# Clamp so one pathological worm cannot dominate the credit table.
REWARD_LO = -1.0
REWARD_HI = 1.0

# Confidence level for the reward's lower bound (~90% one-sided).
# A success rate measured over ONE execution is not evidence of reliability,
# so the reward is a Wilson score LOWER BOUND rather than the raw rate. Without
# this, a worm that ran once and succeeded scores 1.0 and outranks one with
# 1,236 successes in 1,246 runs - which would teach the scheduler to prefer
# unproven components. See `_wilson_lower_bound`.
Z_CONF = 1.28


def _wilson_lower_bound(ok: int, n: int, z: float = Z_CONF) -> float:
    """Wilson score interval lower bound for a binomial success rate.

    Rewards demonstrated reliability and discounts thin evidence, so sample
    size is priced in without authoring a prior by hand.
    """
    if n <= 0:
        return 0.0
    p = max(0.0, min(1.0, ok / n))
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt(max(0.0, p * (1.0 - p) / n + z2 / (4.0 * n * n)))
    return max(0.0, (centre - margin) / denom)

MISSING_CORRELATION_ID = (
    "No choice row carried a decision_id, so choice->outcome attribution is "
    "per worm_type (statistical), not per decision (causal). Write a shared "
    "decision_id into both spawn_selected and clusterwormz_worm_executed to "
    "upgrade this to decision-level credit."
)

JOINED_NOTE = (
    "Choice and outcome rows share a decision_id, so credit is attributed to "
    "the specific decision that caused each execution."
)



# ---------------------------------------------------------------------------
# Datatypes
# ---------------------------------------------------------------------------

@dataclass
class WormOutcome:
    """Observed execution outcomes for one worm type."""

    worm_type: str
    n: int = 0
    ok: int = 0
    mean_duration_ms: float = 0.0
    last_error: Optional[str] = None
    n_errors: int = 0
    # Decision ids whose executions are represented here. Non-empty means this
    # worm's credit can be attributed to a specific decision, not just a type.
    decision_ids: set = field(default_factory=set)
    # Standby rows: liveness only, excluded from ok. Added 2026-09-29.
    n_standby: int = 0

    @property
    def p_success(self) -> float:
        """Observed success rate, or 0.0 when never observed."""
        return (self.ok / self.n) if self.n else 0.0

    def reward(self) -> float:
        """Grounded reward for spawning this worm type: observed success rate.

        Deliberately contains no severity term and no state term - the decision
        to spawn a worm must not be readable from the worm's own identity.

        LATENCY IS DELIBERATELY EXCLUDED. An earlier revision penalised being
        slower than the FLEET median. Measured against live telemetry
        (2026-09-21) that gave: median 1.89 ms, while SYSTEMZ / PROCESSZ /
        DISKZ - which carry 86% of all executions - average ~700 ms. The
        penalty therefore drove the busiest and most reliable components to
        the clamp floor (-1.000) and ranked one-shot probes above them, which
        would have taught the scheduler to starve the core monitors.

        Cross-worm latency is not comparable: worms do different work. Slowness
        is a DRIFT question - is THIS worm slower than it used to be? - and
        needs the worm's own baseline. See :meth:`slow_regression`.
        """
        if self.n <= 0:
            # Never observed -> neutral, so an unexplored worm is neither
            # rewarded nor starved. Exploration pressure belongs to the
            # scheduler's own staleness term, not to the reward.
            return 0.0
        # Lower confidence bound, not the raw rate: 1 success in 1 run must
        # not outrank 1,236 successes in 1,246 runs.
        return max(REWARD_LO, min(REWARD_HI, _wilson_lower_bound(self.ok, self.n)))

    def slow_regression(self, baseline_ms: float,
                        tolerance: float = SLOW_TOLERANCE) -> float:
        """Non-negative latency-drift penalty vs this worm's OWN baseline.

        Same-worm comparison holds the work constant, which is the only way a
        duration figure means anything. Returns 0.0 while within tolerance, so
        a normally variable worm is not taxed for ordinary jitter.
        """
        if self.n <= 0 or baseline_ms <= 0 or self.mean_duration_ms <= 0:
            return 0.0
        ratio = self.mean_duration_ms / baseline_ms
        if ratio <= tolerance:
            return 0.0
        return min(1.0, W_COST * math.log2(ratio / tolerance))

    def to_dict(self, median_duration_ms: float = 0.0) -> Dict[str, Any]:
        latency_ratio = 0.0
        if median_duration_ms > 0 and self.mean_duration_ms > 0:
            latency_ratio = round(self.mean_duration_ms / median_duration_ms, 3)
        return {
            "worm_type": self.worm_type,
            "n": self.n,
            "ok": self.ok,
            "p_success": round(self.p_success, 4),
            "mean_duration_ms": round(self.mean_duration_ms, 2),
            "n_errors": self.n_errors,
            "last_error": self.last_error,
            "reward": round(self.reward(), 4),
            # Descriptive only. Cross-worm latency is NOT a reward input.
            "latency_vs_fleet_median": latency_ratio,
        }


@dataclass
class SpawnCredit:
    """Joined view of spawn choices and observed outcomes."""

    outcomes: Dict[str, WormOutcome] = field(default_factory=dict)
    choices: Dict[str, int] = field(default_factory=dict)
    decisions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    joined_decisions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    median_duration_ms: float = 0.0
    n_outcome_rows: int = 0
    n_choice_rows: int = 0
    n_malformed: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def rewards(self) -> Dict[str, float]:
        """Grounded reward per worm type, for reweighting spawn selection."""
        return {w: o.reward() for w, o in self.outcomes.items()}

    @property
    def attribution_mode(self) -> str:
        """'per-decision (causal)' once any choice shares an id with an outcome."""
        return ("per-decision (causal)" if self.joined_decisions
                else "per-worm-type (statistical)")

    def chosen_but_never_run(self) -> List[str]:
        """Chosen for a spawn but with no recorded execution.

        These are the silent failures: the decider selected them and nothing
        ever came back. A credit table that only looked at outcomes would
        simply never see them.
        """
        return sorted(w for w in self.choices if w not in self.outcomes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "median_duration_ms": round(self.median_duration_ms, 2),
            "n_outcome_rows": self.n_outcome_rows,
            "n_choice_rows": self.n_choice_rows,
            "n_malformed": self.n_malformed,
            "rewards": {w: round(r, 4) for w, r in self.rewards.items()},
            "outcomes": {w: o.to_dict(self.median_duration_ms)
                         for w, o in self.outcomes.items()},
            "chosen_but_never_run": self.chosen_but_never_run(),
            "attribution": self.attribution_mode,
            "attribution_note": (JOINED_NOTE if self.joined_decisions
                                 else MISSING_CORRELATION_ID),
            "n_decisions": len(self.decisions),
            "n_joined_decisions": len(self.joined_decisions),
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Reading the two taps
# ---------------------------------------------------------------------------

def _try_data(raw: Any) -> Optional[Dict[str, Any]]:
    """Parse a row's ``data`` field, returning None when it cannot be read.

    None is deliberately distinct from {}: an unreadable row is data loss and
    is counted, while an empty object is a legitimately empty payload.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError) as exc:
            note_failure("spawn_credit:L289", exc)
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def new_decision_id() -> str:
    """Mint the id that ties a spawn CHOICE to the EXECUTION it caused.

    This is the missing correlation between the two taps. A decider mints one
    id, writes it into every choice row of that decision, and passes it to the
    executor, which writes the same id into every outcome row. Without it the
    join can only be per worm type (statistical); with it the join is per
    decision (causal).
    """
    return "d-%d-%04x" % (time.time_ns(), random.getrandbits(16))


def _row_sig(row: Any):
    """Identity of a row, used to detect a store that ignores ``offset``."""
    if isinstance(row, dict):
        for k in ("id", "timestamp"):
            if row.get(k) is not None:
                return (k, row.get(k))
    return None


def _fetch(store: Any, event_type: str, limit: int,
           errors: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Query one tap without raising, but without hiding the failure either.

    A query that FAILS and a query that legitimately returns NOTHING are
    different outcomes. The first means this credit table was built on absent
    data; the second means the ecosystem simply has not run that worm yet.
    Both yield an empty list so the caller still gets an answer, but the
    failure is recorded separately so the two can never be confused - which is
    the silent-failure class this module exists to close.

    PAGINATION IS MANDATORY, not an optimisation. LocalMemoryStore clamps a
    single query to MAX_QUERY_LIMIT (500) and only warns, so an unpaginated
    read of this tap silently returns 500 of 4286 rows - a 12% sample reported
    as if it were the whole population. A store that ignores ``offset`` is
    detected and reported rather than looping.
    """
    if store is None:
        if errors is not None:
            errors.append(f"{event_type}: no store available")
        return []

    rows: List[Dict[str, Any]] = []
    target = max(1, int(limit))
    offset = 0
    pages = 0
    prev_sig = None
    while len(rows) < target and pages < _MAX_PAGES:
        want = min(_PAGE_SIZE, target - len(rows))
        try:
            batch = store.query_memories(event_type=event_type, limit=want,
                                         offset=offset)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            note_failure("spawn_credit:L348", exc)
            if errors is not None:
                errors.append(f"{event_type}: {type(exc).__name__}: {exc}")
            return rows
        if not isinstance(batch, list):
            if errors is not None:
                errors.append(f"{event_type}: query returned {type(batch).__name__}")
            return rows
        if not batch:
            break
        sig = _row_sig(batch[0])
        if sig is not None and sig == prev_sig:
            if errors is not None:
                errors.append(
                    f"{event_type}: store ignored offset (cannot paginate past "
                    f"{len(rows)} rows)")
            break
        prev_sig = sig
        rows.extend(batch)
        offset += len(batch)
        pages += 1
        if len(batch) < want:
            break

    if len(rows) < target and pages >= _MAX_PAGES and errors is not None:
        errors.append(f"{event_type}: truncated at {len(rows)} rows (page cap)")
    return rows


def read_outcomes(store: Any = None, limit: int = 20000,
                  errors: Optional[List[str]] = None,
                  sink: Optional[Dict[str, int]] = None) -> Dict[str, WormOutcome]:
    """Build per-worm outcome aggregates from the execution tap."""
    per: Dict[str, Dict[str, Any]] = {}
    n_malformed = 0
    for row in _fetch(store, OUTCOME_EVENT, limit, errors):
        d = _try_data(row.get("data")) if isinstance(row, dict) else None
        if d is None:
            n_malformed += 1
            continue
        worm = d.get("worm")
        if not worm:
            continue
        acc = per.setdefault(worm, {"n": 0, "ok": 0, "durs": [], "errs": 0,
                                    "last_error": None, "decisions": set(),
                                    "standby": 0})
        acc["n"] += 1
        did = d.get("decision_id")
        if did:
            acc["decisions"].add(str(did))
        # Standby rows are liveness, not correctness: never count as ok.
        # (PivoARL/Auto forensic 2026-09-29, free-Jev: no model calls.)
        if d.get("standby"):
            acc["standby"] += 1
        elif d.get("success"):
            acc["ok"] += 1
        try:
            dur = float(d.get("duration_ms") or 0.0)
        except (TypeError, ValueError) as exc:
            note_failure("spawn_credit:L406", exc)
            dur = 0.0
        if dur > 0:
            acc["durs"].append(dur)
        err = d.get("error")
        if err:
            acc["errs"] += 1
            acc["last_error"] = str(err)[:300]

    out: Dict[str, WormOutcome] = {}
    for worm, acc in per.items():
        out[worm] = WormOutcome(
            worm_type=worm,
            n=int(acc["n"]),
            ok=int(acc["ok"]),
            mean_duration_ms=(statistics.fmean(acc["durs"]) if acc["durs"] else 0.0),
            last_error=acc["last_error"],
            n_errors=int(acc["errs"]),
            decision_ids=acc["decisions"],
            n_standby=int(acc.get("standby", 0)),
        )
    if sink is not None:
        sink["malformed"] = sink.get("malformed", 0) + n_malformed
    return out


def read_choices(store: Any = None, limit: int = 5000,
                 errors: Optional[List[str]] = None,
                 sink: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    """Count spawn CHOICES per worm type from the decision tap."""
    counts: Dict[str, int] = {}
    n_malformed = 0
    for row in _fetch(store, SPAWN_EVENT, limit, errors):
        d = _try_data(row.get("data")) if isinstance(row, dict) else None
        if d is None:
            n_malformed += 1
            continue
        worm = d.get("worm_type") or d.get("worm")
        if worm:
            counts[worm] = counts.get(worm, 0) + 1
    if sink is not None:
        sink["malformed"] = sink.get("malformed", 0) + n_malformed
    return counts


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------

def read_decisions(store: Any = None, limit: int = 5000,
                   errors: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Map decision_id -> the SET of choices it made.

    One decision chooses many worms and therefore writes one row per worm, so
    this aggregates rather than indexing by id alone - keying by id would let
    the last row of a decision silently stand in for the whole thing.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for row in _fetch(store, SPAWN_EVENT, limit, errors):
        d = _try_data(row.get("data")) if isinstance(row, dict) else None
        if d is None or not d.get("decision_id"):
            continue
        did = str(d["decision_id"])
        entry = out.setdefault(did, {"nature": d.get("nature"), "worms": {}})
        if entry.get("nature") is None:
            entry["nature"] = d.get("nature")
        worm = d.get("worm_type") or d.get("worm")
        if worm:
            entry["worms"][worm] = {"rank": d.get("rank"),
                                    "reason": d.get("reason")}
    for entry in out.values():
        entry["n_targets"] = len(entry["worms"])
    return out


def read_outcome_decisions(store: Any = None, limit: int = 20000,
                           errors: Optional[List[str]] = None
                           ) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Map decision_id -> {worm: observed outcome} for rows carrying an id.

    Only rows written after decision ids existed appear here. An empty result
    means the two taps still cannot be joined causally - a fact about the data,
    reported rather than assumed away.
    """
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in _fetch(store, OUTCOME_EVENT, limit, errors):
        d = _try_data(row.get("data")) if isinstance(row, dict) else None
        if d is None or not d.get("decision_id"):
            continue
        worm = d.get("worm")
        if not worm:
            continue
        try:
            dur = float(d.get("duration_ms") or 0.0)
        except (TypeError, ValueError) as exc:
            note_failure("spawn_credit:L500", exc)
            dur = 0.0
        out.setdefault(str(d["decision_id"]), {})[worm] = {
            "success": bool(d.get("success")),
            "duration_ms": dur,
            "error": (str(d.get("error"))[:300] if d.get("error") else None),
        }
    return out


def build_spawn_credit(store: Any = None, outcome_limit: int = 20000,
                       choice_limit: int = 5000) -> SpawnCredit:
    """Join the two taps into a per-worm grounded credit table.

    Read-only and fail-soft. Returns an empty :class:`SpawnCredit` rather than
    raising, because callers use this inside a scheduling path that must always
    produce a decision.
    """
    credit = SpawnCredit()
    sink: Dict[str, int] = {}
    try:
        credit.outcomes = read_outcomes(store, limit=outcome_limit,
                                        errors=credit.errors, sink=sink)
        credit.choices = read_choices(store, limit=choice_limit,
                                      errors=credit.errors, sink=sink)
        credit.n_malformed = int(sink.get("malformed", 0))

        # Causal join: only possible where a decision_id is present on BOTH
        # sides. Kept separate from the statistical per-worm credit so the two
        # can never be confused for one another.
        credit.decisions = read_decisions(store, limit=choice_limit,
                                          errors=credit.errors)
        _obs = read_outcome_decisions(store, limit=outcome_limit,
                                      errors=credit.errors)
        credit.joined_decisions = {}
        for did, decision in credit.decisions.items():
            observed = _obs.get(did)
            if not observed:
                continue
            chosen = set(decision["worms"])
            ran = set(observed)
            credit.joined_decisions[did] = {
                "nature": decision.get("nature"),
                "n_chosen": len(chosen),
                "n_ran": len(ran),
                # Per-decision silent failures: chosen by this decision and
                # never observed executing. This is the counterfactual a
                # decision-level learner needs. (A worm that never ran has no
                # reward, so it cannot be learned from - only counted.)
                "chosen_but_never_ran": sorted(chosen - ran),
                "unexpected": sorted(ran - chosen),
                "results": observed,
            }
        sampled_outcomes = sum(o.n for o in credit.outcomes.values())
        sampled_choices = sum(credit.choices.values())
        # The fetches above are CAPPED (outcome_limit / choice_limit), so these
        # sums are SAMPLES. Once a tap outgrows its cap the sum pins at the
        # limit and every later read reports the same number -- which is how
        # the neural loop's grade stage came to see "the tap gained no rows"
        # after a successful 248-worm cycle (20000 before, 20000 after). Ask
        # the store for the true COUNT and prefer it when it exceeds the
        # sample; fall back to the sum when the store cannot count.
        credit.n_outcome_rows = sampled_outcomes
        credit.n_choice_rows = sampled_choices
        if store is not None:
            try:
                true_outcomes = int(store.count_memories(event_type=OUTCOME_EVENT))
                if true_outcomes > sampled_outcomes:
                    credit.n_outcome_rows = true_outcomes
            except Exception as exc:
                note_failure("spawn_credit:L569", exc)
            try:
                true_choices = int(store.count_memories(event_type=SPAWN_EVENT))
                if true_choices > sampled_choices:
                    credit.n_choice_rows = true_choices
            except Exception as exc:
                note_failure("spawn_credit:L575", exc)
        durations = [o.mean_duration_ms for o in credit.outcomes.values()
                     if o.mean_duration_ms > 0]
        credit.median_duration_ms = (statistics.median(durations) if durations
                                     else 0.0)
    except Exception as exc:  # noqa: BLE001 - must never break a schedule
        note_failure("spawn_credit:L581", exc)
        credit.errors.append(f"{type(exc).__name__}: {exc}")
    return credit


#: Minimum outcome rows before a perfect win-rate is trusted. Below this a
#: perfect rate is "no evidence yet"; at or above it a perfect rate is
#: "uncalibrated guard" (Conformal Abstention(CONFIG), 2026-09-29: abstain
#: rather than route on an uncalibrated 1.0).
CALIBRATION_MIN_N = 50


def abstention_stats(outcomes: dict | None) -> dict:
    """Post-hoc abstention decision over per-worm outcomes (no retraining).

    Returns participation + conditional-correctness proxy + a hold decision:

    * ``abstain=True`` with reason ``insufficient_evidence`` when fewer than
      ``CALIBRATION_MIN_N`` rows exist (nothing to calibrate on).
    * ``abstain=True`` with reason ``uncalibrated_perfect`` when the win
      rate is exactly 1.0 on a large sample (the Auto §6 silent-mislabel
      shape: a loose guard that never says no).
    * Otherwise ``abstain=False``.

    Advisory only: callers record the verdict, they do not change routing
    on it until the differential-replay gate lands. Free-Jev: pure
    arithmetic over the local tap, no model calls.
    """
    outcomes = outcomes or {}
    n = sum(int(getattr(o, "n", 0) or 0) for o in outcomes.values())
    ok = sum(int(getattr(o, "ok", 0) or 0) for o in outcomes.values())
    sb = sum(int(getattr(o, "n_standby", 0) or 0) for o in outcomes.values())
    rate = (ok / n) if n else 0.0
    if n < CALIBRATION_MIN_N:
        return {"n": n, "ok": ok, "n_standby": sb,
                "win_rate": round(rate, 4),
                "participation": round(n / CALIBRATION_MIN_N, 4),
                "abstain": True, "reason": "insufficient_evidence"}
    if rate == 1.0:
        return {"n": n, "ok": ok, "n_standby": sb,
                "win_rate": 1.0,
                "participation": 1.0,
                "abstain": True, "reason": "uncalibrated_perfect"}
    return {"n": n, "ok": ok, "n_standby": sb,
            "win_rate": round(rate, 4),
            "participation": 1.0,
            "abstain": False, "reason": "calibrated"}


def resolve_store() -> Any:
    """Best-effort handle on LocalMemoryStore, or None."""
    try:
        from CORE.local_memory import get_memory_store
        return get_memory_store()
    except Exception as exc:
        note_failure("spawn_credit:L635", exc)
        return None


def live_spawn_credit() -> SpawnCredit:
    """Convenience wrapper: join against the process-wide memory store."""
    return build_spawn_credit(resolve_store())
