# coding: utf-8
"""
PROTEUS hooks — the Phase D and Phase E integration surface.

Kept OUT of `scripts/evolution_cycle.py` on purpose. The cycle is a 2,200-line
production script; putting this logic inline would make it untestable and would
bury the two rules that actually matter here.

Phase D
-------
* **Cranium** (`cranium_decide`) pools margin evidence ACROSS eras.
  `_sprt_history` hard-stops at the first `pool_id`/`baseline_hash` change, and
  on the 2026-09-11 batch that break happened 28 times in 51 cycles (~1.8 cycles
  per era). The SPRT needs 2..6 same-era observations, so evidence could never
  accumulate: 1 promotion in 51 cycles. Treating each era as a random effect and
  pooling through the grand mean means a new era ADDS information rather than
  resetting the count.

* **Circulation** (`circulation_decide` / `circulation_update`) replaces the
  fixed arm gate. The old rule was
  `_cma_wanted = margin < 2 * PROMOTE_MARGIN`; every margin on that batch was
  >= 2.91 against a 1.5 threshold, so the second arm was skipped 50/50. A fixed
  threshold can only say "not needed right now" — it can never say "this operator
  earns its keep", and once a regime shifts the arm is structurally unreachable.

Phase E
-------
`morph_candidate_gene` rotates ONE extra gene into the search space per cycle.

The candidate list is short and deliberate, because a gene that is not CONSUMED
is worse than no gene at all: `_eval_salt` reads exactly four keys
(`learning_rate`, `epsilon`, `epsilon_decay`, `discount`), so a fifth gene added
to `GENE_BOUNDS` without a matching pass-through would be silently ignored and
the whole experiment void. `morph_trainer_kwargs` is the matching pass-through,
and only kwargs `train_from_memory` genuinely accepts are offered.

`val_ratio`/`test_ratio` are deliberately EXCLUDED: they only take effect when
the split flag is on, so alone they are inert. That is the same "every gene pays
rent" rule the Morph organ applies to pruning.

An inert candidate is not only one that is off. `by_session` DEFAULT-True means
a `by_session=True` trial passes the trainer exactly what production passes it,
so that row is equally unable to attribute the margin it reports. `morph_is_inert`
therefore compares the RESOLVED value against the trainer's own default
(`MORPH_TRAINER_DEFAULTS`) instead of against zero, `morph_record` stamps the
verdict on every row it writes, and `morph_dimension_summary` re-derives it for
older rows so the correction is retroactive.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import random
import sys
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .allocation import ThompsonAllocator, PROTEUS_ARMS, reward_for_outcome
from .evidence import EraEvidence

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "cranium_decide",
    "cranium_override",
    "circulation_decide",
    "circulation_update",
    "SEARCH_ARMS",
    "MORPH_CANDIDATES",
    "MORPH_KNOWN_GENES",
    "MORPH_TRAINER_DEFAULTS",
    "morph_candidate_gene",
    "morph_strip",
    "morph_trainer_kwargs",
    "morph_effective",
    "morph_is_inert",
    "morph_treatment_value",
    "morph_ab_plan",
    "morph_record",
    "morph_dimension_summary",
    "row_payload",
]

# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def row_payload(row: Any) -> Optional[dict]:
    """Normalise a memory-store row into its payload dict.

    Mirrors `evolution_cycle._row_payload` so the hooks can be used without
    importing the cycle (which runs a GA on import in older revisions).
    """
    if not isinstance(row, dict):
        return None
    data = row.get("data", row)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as exc:
            note_failure("proteus_hooks:L101", exc)
            return None
    return data if isinstance(data, dict) else None


def _margin_of(data: dict) -> Optional[float]:
    """Margin from a row, deriving it when the key predates the fix."""
    m = data.get("margin")
    if m is None:
        try:
            best_raw = data.get("best_total")
            base_raw = data.get("baseline")
            if not isinstance(best_raw, (int, float, str)) or not isinstance(base_raw, (int, float, str)):
                raise TypeError(f"non-numeric best_total/baseline: {type(best_raw).__name__}/{type(base_raw).__name__}")
            m = float(best_raw) - float(base_raw)
        except (TypeError, ValueError) as exc:
            note_failure("proteus_hooks:L112", exc)
            return None
    try:
        f = float(m)
    except (TypeError, ValueError) as exc:
        note_failure("proteus_hooks:L116", exc)
        return None
    return f if math.isfinite(f) else None


# --------------------------------------------------------------------------
# Phase D — Cranium
# --------------------------------------------------------------------------
# A posterior sd at (or numerically indistinguishable from) zero is NOT
# confidence — it is an unidentifiable variance component pinned at its
# boundary. Measured 2026-09-12: three observations whose era means happened to
# coincide gave sd = 0.0, hence `p_above = 1.0` exactly, and the organ proposed
# a promotion. `prob_above` cannot distinguish "certain" from "could not
# estimate", so the decision gate must refuse to act on the degenerate case.
CRANIUM_MIN_SD = 1e-9
# ...and the floor is scaled to the decision scale, because an absolute
# epsilon cannot tell "confident" from "collapsed": measured sd for three
# coincident era means was 5.8e-07, i.e. ~1.3 million sigma of clearance on a
# 0.75 threshold. A sd that small is the moments estimator pinned at its
# boundary, not knowledge.
CRANIUM_MIN_SD_FRAC = 1e-3

def cranium_decide(rows: Iterable[Any], pool_id: str, margin_now: float,
                   threshold: float, alpha: float = 0.05,
                   limit: int = 400) -> Dict[str, Any]:
    """Cross-era promotion decision. Pure stdlib; identical under both interpreters.

    Returns `{'decision': 'promote'|'reject'|'continue', ...}` using the same
    vocabulary as `_sprt_decide`, so callers can swap it in without changes.
    """
    ev = EraEvidence(threshold=float(threshold), alpha=float(alpha))
    n_eras_seen: Dict[str, int] = {}
    # Within-era duplicate margins are ONE observation, not many.
    #
    # Measured on the live store (2026-09-12): one era carried 58 rows whose
    # margin was 3.295 on every one, and another repeated 0.403 and 0.212. A
    # bit-identical margin is the same candidate re-evaluated against an
    # unchanged baseline — it adds no information, but it DOES shrink the
    # posterior sd and push `p_above` toward certainty. Counting those repeats
    # is the "absence of new data read as evidence" failure the COMPAGES
    # cointegration fix already caught once in this codebase. Deduping is the
    # conservative direction: it can only make promotion HARDER.
    seen: Dict[str, set] = {}
    dup = 0
    used = 0
    for row in list(rows or [])[:int(limit)]:
        data = row_payload(row)
        if data is None:
            continue
        # Only v3-scale rows carry a comparable margin (same rule as the SPRT).
        if not str(data.get("fitness", "")).startswith("v3"):
            continue
        m = _margin_of(data)
        if m is None:
            continue
        era = str(data.get("pool_id") or "unknown")
        key = round(float(m), 6)
        bucket = seen.setdefault(era, set())
        if key in bucket:
            dup += 1
            n_eras_seen[era] = n_eras_seen.get(era, 0) + 1
            used += 1
            continue
        bucket.add(key)
        ev.observe(era, m)
        n_eras_seen[era] = n_eras_seen.get(era, 0) + 1
        used += 1

    # The cycle's own observation, attributed to the CURRENT era.
    try:
        _cur = round(float(margin_now), 6)
        if _cur not in seen.setdefault(str(pool_id), set()):
            ev.observe(str(pool_id), float(margin_now))
    except (TypeError, ValueError) as exc:
        note_failure("proteus_hooks:L189", exc)

    post = ev.posterior()
    p_above = ev.prob_above(float(threshold))
    _sd = float(post["sd"])
    _floor = max(CRANIUM_MIN_SD, abs(float(threshold)) * CRANIUM_MIN_SD_FRAC)
    # NaN-safe: `not (x > floor)` is True for NaN too.
    degenerate = not (_sd > _floor)
    why = None
    if ev.n_eras < 2:
        # Cranium exists to POOL. With one era there is nothing to pool, so it
        # must defer to the SPRT rather than restate it with a point-estimate
        # variance. Measured: one era with two observations reported
        # p_above = 1.0000 and proposed a promotion from two numbers.
        decision = "continue"
        why = "single era: no cross-era pooling available"
    elif degenerate:
        # Variance is unidentifiable, so p_above (necessarily exactly 0.0 or
        # 1.0 here) carries no information. Refuse to act rather than convert
        # a boundary estimate into a decision.
        decision = "continue"
        why = "posterior sd %.2e below credible floor %.2e" % (_sd, _floor)
    # Strong evidence AGAINST improvement mirrors the SPRT's 'reject'.
    elif ev.n_observations >= 2 and p_above < float(alpha):
        decision = "reject"
    elif ev.n_observations >= 2 and ev.should_promote(float(alpha)):
        decision = "promote"
    else:
        decision = "continue"

    return {
        "decision": decision,
        "n_eras": ev.n_eras,
        # n_obs keeps its ORIGINAL contract — evidence units in the posterior,
        # the cycle's own observation included — because existing tests and
        # log consumers read it that way. The two new keys expose what it no
        # longer counts: rows read, and rows discarded as repeats.
        "n_obs": ev.n_observations,
        "rows_read": used,
        "duplicate_rows": dup,
        "prior_rows_used": used,
        "eras_prior": len(n_eras_seen),
        "posterior_mean": round(float(post["mean"]), 4),
        "posterior_sd": round(float(post["sd"]), 4),
        "tau": round(float(post["tau"]), 4),
        "sigma": round(float(post["sigma"]), 4),
        "p_above_threshold": round(float(p_above), 4),
        "threshold": float(threshold),
        # True when the variance could not be estimated, so the reported
        # p_above is a boundary artifact and the decision is forced to
        # 'continue'. Surfaced rather than swallowed: an organ that silently
        # declines looks identical to one that found nothing.
        "degenerate_variance": bool(degenerate),
        "raw_posterior_sd": _sd,
        "sd_floor": _floor,
        # Set only when the organ DECLINED to decide. An organ that silently
        # declines is indistinguishable from one that found nothing.
        "deferred": why,
    }


# --------------------------------------------------------------------------
# Phase D — Circulation
# --------------------------------------------------------------------------

# Arms that are genuinely FUNDABLE as an alternative search operator.
#
# PROTEUS_ARMS lists every organ named in the plan, but only `cma` and `de` have
# an implemented second-search-arm in the cycle (`_run_cma_arm`); `tpe`,
# `morph`, `arena` and `chorus` are wired elsewhere or not as a duel.
#
# Measured on the first live circulation batch (2026-09-12): the allocator drew
# `arena`, `morph` and `chorus`, the cycle ran the slot-alternating `cma` arm
# anyway, and the feedback credited `cma` — three posterior updates for an arm
# that was never selected. That is exactly the mislabelling the forcing
# parameter was added to prevent, and it happens whenever the decision set is
# wider than the executable set. So the set is narrowed to what can run.
SEARCH_ARMS: Tuple[str, ...] = ("ga", "cma", "de")


def cranium_override(sprt_decision: str, cranium: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the Cranium policy to an SPRT decision. Pure; no I/O, no globals.

    Extracted from `evolution_cycle.main` on purpose. Inline, this two-branch
    policy sat 2,000 lines inside the production script behind a `beats` guard
    that no cycle in the 2026-09-12 batches satisfied (25/25 had `beats=False`),
    so it was reachable only in a regime that never occurred and therefore
    never executed, never asserted, and never observed. As a pure function both
    directions are testable without running a 2,350-line cycle.

    Both directions are deliberate:
      * **rescue** — the SPRT needs 2..6 SAME-era observations and the era
        breaks every ~1.8 cycles, so `continue` after n=1 is the common shape
        even when the candidate cleared the margin gate. Pooled cross-era
        evidence is the only way that case can ever promote.
      * **veto** — the mirror case, and the E9 protection: a promotion whose
        pooled posterior sits below the threshold across eras is the exact
        shape that bought 0/4 holdout passes.

    Returns `{'decision', 'sprt_ok', 'action'}` where action is one of
    `rescue` / `veto` / `agree` / `noop`. A malformed or absent Cranium verdict
    yields `noop`, so the SPRT decision stands unchanged.
    """
    cd = (cranium or {}).get("decision")
    sprt_ok = str(sprt_decision) == "promote"
    if cd not in ("promote", "reject", "continue"):
        return {"decision": sprt_decision, "sprt_ok": sprt_ok, "action": "noop"}
    if cd == "promote" and not sprt_ok:
        return {"decision": "promote", "sprt_ok": True, "action": "rescue"}
    if cd == "reject" and sprt_ok:
        # Withdrawn to 'continue', never to 'reject': the SPRT has not itself
        # rejected anything, and recording a rejection would overstate how
        # decisive the evidence was (the E2 lesson from the 50-run analysis).
        return {"decision": "continue", "sprt_ok": False, "action": "veto"}
    return {"decision": sprt_decision, "sprt_ok": sprt_ok, "action": "agree"}


def _load_allocator(state_path: str, arms: Sequence[str]) -> ThompsonAllocator:
    if state_path and os.path.exists(state_path):
        try:
            alloc = ThompsonAllocator.load(state_path)
            for a in arms:
                if a not in alloc.arms:
                    alloc.arms.append(a)
                    alloc._s[a] = alloc._prior[0]
                    alloc._f[a] = alloc._prior[1]
            _apply_explore_env(alloc)
            return alloc
        except Exception as exc:
            note_failure("proteus_hooks:L318", exc)
    alloc = ThompsonAllocator(list(arms), floor=0.02, rng=random.Random())
    _apply_explore_env(alloc)
    return alloc


def _apply_explore_env(alloc: ThompsonAllocator) -> None:
    """PROTEUS_EXPLORE env override (best-effort, never raises)."""
    try:
        import os as _os
        raw = (_os.environ.get("PROTEUS_EXPLORE") or "").strip()
        if raw == "":
            return
        alloc.explore = min(1.0, max(0.0, float(raw)))
    except Exception as exc:
        note_failure("proteus_hooks:L333", exc)


def circulation_decide(state_path: str, arms: Sequence[str] = SEARCH_ARMS,
                       primary: str = "ga") -> Dict[str, Any]:
    """Choose which operator to fund this cycle (Thompson sampling).

    `run_secondary` False means the primary operator stands unopposed this
    cycle. Unlike the replaced threshold, a starved arm keeps a floor share, so
    it stays discoverable when the regime changes.
    """
    arms = list(arms or SEARCH_ARMS)
    alloc = _load_allocator(state_path, arms)
    picked = alloc.choose(1)
    arm = picked[0] if picked else primary
    w = alloc.weights()
    return {
        "arm": arm,
        "run_secondary": arm != primary,
        "weights": {k: round(v, 4) for k, v in sorted(w.items())},
        "report": alloc.report(),
    }


def circulation_update(state_path: str, arm: str, outcome: str,
                       arms: Sequence[str] = SEARCH_ARMS) -> Dict[str, Any]:
    """Feed one cycle's outcome back to the allocator, then persist.

    Outcome strings are weighted by `reward_for_outcome`: a holdout pass is worth
    far more than beating a reused snapshot, because E9 showed a +3.3 snapshot
    margin buying 0/4 holdout passes. Unknown outcomes fail closed.
    """
    arms = list(arms or SEARCH_ARMS)
    alloc = _load_allocator(state_path, arms)
    success, weight = reward_for_outcome(outcome)
    try:
        alloc.update(str(arm), bool(success), float(weight))
    except Exception as exc:
        note_failure("proteus_hooks:L371", exc)
        return {"updated": False, "reason": str(exc)[:120]}
    try:
        if state_path:
            os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
            alloc.save(state_path)
    except Exception as exc:
        note_failure("proteus_hooks:L377", exc)
        return {"updated": True, "saved": False, "reason": str(exc)[:120]}
    return {"updated": True, "saved": True, "arm": arm, "outcome": outcome,
            "success": bool(success), "weight": float(weight)}


# --------------------------------------------------------------------------
# Phase E — Morph gene rotation
# --------------------------------------------------------------------------
# Every entry must name a kwarg `RLTrainMemory.train_from_memory` accepts AND a
# pass-through in `morph_trainer_kwargs`, or the gene is inert (see module doc).
#
# `MORPH_CANDIDATES` is the ROTATION: which gene Phase E adds this cycle.
# `use_schema_features` was REMOVED from it on 2026-10-02. It was inert by this
# module's own rule -- `MORPH_TRAINER_DEFAULTS` reads the trainer's own default
# `use_schema_features: False`, and the gene resolved OFF in 30/30 trials while
# being credited as a win -- AND, with two candidates, `cycle_index % 2`
# alternated the search space every cycle, so the per-arm gate (EV-4) could
# never accumulate a streak. One inert gene was costing the rotation its
# information. Halving the list makes the schema CONSTANT.
MORPH_CANDIDATES: Tuple[Tuple[str, str, float, float, str], ...] = (
    # (name, kind, lo, hi, trainer_kwarg)
    ("by_session", "bool", 0.0, 1.0, "by_session"),
)

# `MORPH_KNOWN_GENES` is the REGISTRY: every gene this organ has ever rotated,
# including retired ones. `use_schema_features` is no longer trialled, but rows
# that carry it already exist in the ledger and MUST still be classifiable --
# otherwise the historical verdict silently degrades from `inert` to `unknown`
# and the retire decision loses its evidence. Classification, stripping and the
# trainer pass-through read THIS list; only `morph_candidate_gene` and
# `morph_gene_specs` read the rotation above.
MORPH_KNOWN_GENES: Tuple[Tuple[str, str, float, float, str], ...] = (
    ("use_schema_features", "bool", 0.0, 1.0, "use_schema_features"),
    ("by_session", "bool", 0.0, 1.0, "by_session"),
)

# The trainer's OWN defaults for the two switch kwargs, read from
# `RLTrainLoop.train_from_memory` (learning_rl.py:302-303). This is the whole
# basis of the inert rule: a trial whose resolved value EQUALS the default
# produces a trainer call identical to production's, so the recorded
# `fitness - baseline_fitness` cannot be about the gene — it measures the
# surrounding search (GA vs the production snapshot). Measured 2026-09-12:
# 65 morph rows, 64 retained, and `use_schema_features` resolved OFF in 31/31
# of its trials (raw 0.056, 0.069, False) while being credited every time.
# Written as data, not prose, so `test_morph_inert_matches_trainer_defaults`
# can pin it to the real signature instead of letting the two drift.
MORPH_TRAINER_DEFAULTS: Dict[str, Any] = {
    "use_schema_features": False,
    "by_session": True,
}


def morph_candidate_gene(cycle_index: int,
                         candidates: Sequence[Sequence[Any]] = MORPH_CANDIDATES
                         ) -> Optional[Dict[str, Any]]:
    """Which extra gene to trial this cycle. Deterministic round-robin.

    Round-robin rather than random so every candidate gets an equal number of
    cycles over a batch — otherwise the comparison against the 4-gene best is
    confounded by which gene happened to be sampled most.
    """
    cands = list(candidates or MORPH_CANDIDATES)
    if not cands:
        return None
    name, kind, lo, hi, kwarg = cands[int(cycle_index) % len(cands)]
    return {"name": str(name), "kind": str(kind), "lo": float(lo),
            "hi": float(hi), "kwargs": str(kwarg),
            "cycle_index": int(cycle_index)}


def morph_gene_specs(cycle_index: int,
                     base_bounds: Dict[str, Tuple[float, float]],
                     enabled: bool = True,
                     candidates: Sequence[Sequence[Any]] = MORPH_CANDIDATES
                     ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Mixed-type specs for `EvolutionEngine.set_gene_specs`, plus the added gene.

    The dict key is `"type"` (NOT `"kind"`) because that is the key the engine
    reads: `EvolutionEngine.set_gene_specs` looks up `spec.get("type", "float")`.
    Emitting `"kind"` was a real integration bug — the engine would fall back to
    `"float"`, so a gene declared `bool` would have been sampled as a CONTINUOUS
    coordinate and then collapsed by `morph_trainer_kwargs`'s `bool(v)`, which
    makes almost every draw True. Verified against `evolution.py`; this is why
    the round-trip test `test_morph_specs_round_trip_through_engine` exists.

    Floats keep the legacy `(lo, hi)` tuple shape the engine auto-detects; the
    added gene is an explicit spec dict, which is what makes a `bool` gene a
    switch rather than a continuous coordinate.
    """
    specs: Dict[str, Any] = {k: {"type": "float", "lo": float(v[0]), "hi": float(v[1])}
                             for k, v in (base_bounds or {}).items()}
    gene = morph_candidate_gene(cycle_index, candidates) if enabled else None
    if gene is not None:
        specs[gene["name"]] = {"type": gene["kind"],
                               "lo": gene["lo"], "hi": gene["hi"]}
    return specs, gene


def morph_strip(genes: Dict[str, Any],
                candidates: Sequence[Sequence[Any]] = MORPH_KNOWN_GENES
                ) -> Dict[str, Any]:
    """Remove every Phase E gene from a seed genome.

    THE bug this exists to prevent, measured 2026-09-12: the morph history
    contained a row with dimension **6** carrying BOTH candidate genes. Seeds
    come from `last_best_genes()`, which returns the previous cycle's winner —
    including whatever extra gene that cycle trialled. The engine's
    `initialize_population` fills MISSING genes from the current specs but
    never PRUNES keys it was not told about, so a seed carrying `by_session`
    entered the next cycle (which added `use_schema_features`) and left with
    both. Dimension then crept 4 -> 5 -> 6 and never came back.

    Two consequences, both fatal to the measurement:
      * fitness stops being comparable across rows (the plan's stated risk),
      * the per-gene verdict is confounded, because a gene trialled ALONGSIDE
        another is not the same treatment as the gene trialled alone.

    Stripping makes each cycle a clean 4 + 1 factorial.
    """
    drop = {str(c[0]) for c in (candidates or MORPH_KNOWN_GENES)}
    return {k: v for k, v in (genes or {}).items() if k not in drop}


def morph_trainer_kwargs(genes: Dict[str, Any]) -> Dict[str, Any]:
    """Pass extra genes through to the trainer. THE point of Phase E.

    Without this, `_eval_salt` reads only its four hardcoded keys and a fifth
    gene is silently discarded — the search would appear to grow while the
    fitness landscape stayed four-dimensional.
    """
    out: Dict[str, Any] = {}
    for name, kind, _lo, _hi, kwarg in MORPH_KNOWN_GENES:
        if name not in (genes or {}):
            continue
        v = genes[name]
        if kind == "bool":
            out[kwarg] = _as_bool(v)
        else:
            try:
                out[kwarg] = float(v)
            except (TypeError, ValueError) as exc:
                note_failure("proteus_hooks:L518", exc)
                continue
    return out


def _as_bool(v: Any) -> bool:
    """Coerce a gene to bool, THRESHOLDING continuous values at 0.5.

    `bool(v)` is wrong here because the secondary search arms are continuous
    optimisers: they are handed the switch as a (0.0, 1.0) coordinate, so a
    CMA draw of 0.3 would have read as True and the switch would have been
    "on" for almost every candidate. Thresholding makes the relaxed search
    agree with the GA's discrete switch.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    try:
        return float(v) >= 0.5
    except (TypeError, ValueError) as exc:
        note_failure("proteus_hooks:L538", exc)
        return False


def morph_effective(gene: Any, genes: Dict[str, Any]) -> Any:
    """What `train_from_memory` actually RECEIVES for the trialled gene.

    `None` means "not determinable" (unknown gene, gene absent from the genome,
    or an uncoercible value) and must never be read as "inert" — absence of
    evidence is not evidence, which is the rule MUTAGEN also encodes.
    """
    name = (gene or {}).get("name") if isinstance(gene, dict) else gene
    name = str(name or "")
    if not name or not isinstance(genes, dict) or name not in genes:
        return None
    for cname, kind, _lo, _hi, _kwarg in MORPH_KNOWN_GENES:
        if cname != name:
            continue
        v = genes[name]
        if kind == "bool":
            return _as_bool(v)
        try:
            return float(v)
        except (TypeError, ValueError) as exc:
            note_failure("proteus_hooks:L561", exc)
            return None
    return None


def morph_is_inert(gene: Any, genes: Dict[str, Any],
                   defaults: Optional[Dict[str, Any]] = None) -> bool:
    """True when the trial arm's trainer call is IDENTICAL to production's.

    A trial only measures its gene if it changes what the trainer is called
    with. Resolving to the trainer's own default leaves the call unchanged, so
    the margin it reports belongs to the search, not to the gene — crediting it
    is the "genome grew for free" failure MUTAGEN scores as `free_growth`.

    Note this is per-gene and that is not cosmetic: `by_session` defaults to
    **True**, so a `by_session=True` trial is as inert as a
    `use_schema_features=False` one, even though a naive on/off reading would
    call the first a real treatment.
    """
    name = (gene or {}).get("name") if isinstance(gene, dict) else gene
    name = str(name or "")
    if not name:
        return False
    kwarg = next((c[4] for c in MORPH_KNOWN_GENES if c[0] == name), None)
    if kwarg is None:
        return False
    table = MORPH_TRAINER_DEFAULTS if defaults is None else defaults
    if kwarg not in table:
        return False                     # no known default -> cannot claim inert
    eff = morph_effective(name, genes)
    if eff is None:
        return False
    return bool(eff) == bool(table[kwarg])


def morph_treatment_value(gene: Any) -> Any:
    """The value that MOVES this gene off the trainer's default.

    For a bool switch that is simply the negation, and "negation" is per-gene:
    `by_session` must be set to **False** to be a treatment, because True is what
    the trainer already does. This is the value the A/B arm is forced to.
    """
    name = (gene or {}).get("name") if isinstance(gene, dict) else gene
    name = str(name or "")
    if not name:
        return None
    kwarg = next((c[4] for c in MORPH_KNOWN_GENES if c[0] == name), None)
    if kwarg is None or kwarg not in MORPH_TRAINER_DEFAULTS:
        return None
    return not bool(MORPH_TRAINER_DEFAULTS[kwarg])


def morph_ab_plan(gene: Any, genes: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """A matched pair for the trialled gene: default arm vs treatment arm.

    THE problem this exists to solve. The Morph trial as originally recorded
    compares a 5-gene champion against the 4-gene PRODUCTION baseline, so from
    the moment the added gene resolves to its default the difference it reports
    is the search's, not the gene's — and 52 of 69 rows did exactly that while
    being credited as wins.

    The A/B holds everything else fixed: SAME champion genome, SAME frozen
    pools, SAME seeds, ONE gene changed — the trainer's default value versus
    the value that moves it. That difference is attributable to the gene by
    construction, whatever the search was doing.

    Returns `{"gene", "control", "treatment", "default"}` or None when the
    candidate is not a known switch (never a made-up pair).
    """
    name = (gene or {}).get("name") if isinstance(gene, dict) else gene
    name = str(name or "")
    if not name:
        return None
    kwarg = next((c[4] for c in MORPH_KNOWN_GENES if c[0] == name), None)
    if kwarg is None or kwarg not in MORPH_TRAINER_DEFAULTS:
        return None
    if genes is not None and name not in genes:
        return None
    default = bool(MORPH_TRAINER_DEFAULTS[kwarg])
    return {"gene": name,
            "default": default,
            "control": default,
            "treatment": (not default)}


def morph_ab_delta(control_fitness: Any, treatment_fitness: Any) -> Optional[float]:
    """treatment - control, or None when either side is missing/non-finite.

    Sign convention: POSITIVE means the trial arm beat the trainer's default,
    i.e. the gene earned its place. Kept explicit because the same quantity
    computed in the other order would invert every verdict.
    """
    if control_fitness is None or treatment_fitness is None:
        return None
    try:
        c, t = float(control_fitness), float(treatment_fitness)
        delta = t - c
        if delta != delta or delta in (float("inf"), float("-inf")):
            return None
        return round(delta, 4)
    except (TypeError, ValueError) as exc:
        note_failure("proteus_hooks:L661", exc)
        return None


def morph_record(history_path: str, cycle_index: int, gene: Optional[dict],
                 genes: Dict[str, Any], fitness: Optional[float],
                 baseline_fitness: Optional[float] = None,
                 ab: Optional[dict] = None) -> None:
    """Append one JSONL row: which gene was trialled, dimension, fitness.

    Dimension is recorded alongside fitness on purpose — the plan's honest-risk
    mitigation. Once the genome can grow, fitness is only comparable WITHIN a
    dimension, so any headline number must be reported per dimension.

    The row also carries the trial's VERDICT ABOUT ITSELF, added 2026-09-12
    after 65 rows were appended that no reader could attribute:

      * `applied` — the kwargs the trainer actually received (post-resolution),
      * `inert`   — whether those kwargs equal the trainer's defaults, i.e.
                    whether this row can speak about the gene at all,
      * `ab`      — the matched default-vs-treatment pair for the gene, when the
                    caller ran one. THIS is the attributable measurement: the
                    `fitness`/`baseline_fitness` pair above answers "did a wider
                    genome beat production", which conflates the gene with the
                    search.

    Recomputed on read for older rows (see `morph_dimension_summary`), so a
    ledger written before this change is still classified correctly rather than
    silently trusted.
    """
    try:
        applied = morph_trainer_kwargs(genes)
        row = {
            "cycle_index": int(cycle_index),
            "gene": (gene or {}).get("name"),
            "dimension": len(genes or {}),
            "fitness": None if fitness is None else float(fitness),
            "baseline_fitness": (None if baseline_fitness is None
                                 else float(baseline_fitness)),
            "extra": {k: genes[k] for k in (genes or {})
                      if k in [c[0] for c in MORPH_KNOWN_GENES]},
            "applied": {k: applied[k] for k in sorted(applied)},
            "inert": morph_is_inert(gene, genes),
            "ab": (dict(ab) if isinstance(ab, dict) and ab else None),
        }
        os.makedirs(os.path.dirname(os.path.abspath(history_path)), exist_ok=True)
        with open(history_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception as exc:
        note_failure("proteus_hooks:L709", exc)


def _row_ab_delta(r: dict) -> Optional[float]:
    """The matched-pair delta recorded on a row, if it carries a usable one.

    Derives it from the two arm scores when they are present but the summary
    `delta` is not, so a row that records the measurement still counts as
    measured instead of silently reading as "no A/B".
    """
    ab = r.get("ab")
    if not isinstance(ab, dict):
        return None
    if ab.get("delta") is not None:
        try:
            d = float(ab["delta"])
            if d == d and d not in (float("inf"), float("-inf")):
                return round(d, 4)
        except (TypeError, ValueError) as exc:
            note_failure("proteus_hooks:L728", exc)
    return morph_ab_delta(ab.get("control_fitness"),
                          ab.get("treatment_fitness"))


def _row_inert(r: dict) -> bool:
    """Inert for a ledger row: the recorded flag, else recomputed.

    Legacy rows (written before `inert` existed) are re-derived from `gene` +
    `extra` through the same rule, so the fix applies retroactively instead of
    only to future cycles.
    """
    if isinstance(r.get("inert"), bool):
        return bool(r["inert"])
    extra = r.get("extra") if isinstance(r.get("extra"), dict) else {}
    gene = r.get("gene")
    if not extra or not gene:
        return False
    if gene in extra:
        return morph_is_inert(gene, extra)
    return False


def morph_dimension_summary(history_path: str) -> Dict[str, Any]:
    """Per-dimension fitness, plus a per-gene verdict.

    The per-gene verdict is the measurement Phase E exists to produce: does a
    wider genome actually beat the 4-gene best, and if so, which added gene did
    it — or was the extra dimension dead weight that should be pruned?
    """
    rows: List[dict] = []
    try:
        with open(history_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception as exc:
                    note_failure("proteus_hooks:L768", exc)
                    continue
                if isinstance(d, dict) and d.get("fitness") is not None:
                    rows.append(d)
    except Exception as exc:
        note_failure("proteus_hooks:L772", exc)

    by_dim: Dict[int, List[float]] = {}
    by_gene: Dict[str, List[float]] = {}          # EFFECTIVE trials only
    by_gene_inert: Dict[str, List[float]] = {}
    by_gene_ab: Dict[str, List[float]] = {}       # matched-pair deltas
    inert_rows = 0
    measured_rows = 0
    for r in rows:
        try:
            fit = float(r["fitness"])
            by_dim.setdefault(int(r["dimension"]), []).append(fit)
        except (KeyError, TypeError, ValueError) as exc:
            note_failure("proteus_hooks:L785", exc)
            continue
        g = r.get("gene")
        if not g:
            continue
        _ab = _row_ab_delta(r)
        if _ab is not None:
            measured_rows += 1
            by_gene_ab.setdefault(str(g), []).append(_ab)
        if _row_inert(r):
            inert_rows += 1
            by_gene_inert.setdefault(str(g), []).append(fit)
        else:
            by_gene.setdefault(str(g), []).append(fit)

    def _stat(vals: List[float]) -> dict:
        m = sum(vals) / len(vals)
        var = sum((x - m) ** 2 for x in vals) / len(vals)
        return {"n": len(vals), "best": max(vals), "mean": round(m, 4),
                "sd": round(math.sqrt(var), 4)}

    base = [v for v in by_dim.get(4, [])]
    out = {
        "by_dimension": {str(k): _stat(v) for k, v in sorted(by_dim.items())},
        "by_gene": {k: _stat(v) for k, v in sorted(by_gene.items())},
        "by_gene_inert": {k: _stat(v) for k, v in sorted(by_gene_inert.items())},
        "by_gene_ab": {k: _stat(v) for k, v in sorted(by_gene_ab.items())},
        "n_rows": len(rows),
        "inert_rows": inert_rows,
        "effective_rows": len(rows) - inert_rows,
        "measured_rows": measured_rows,
        "inert_share": (round(inert_rows / float(len(rows)), 4) if rows else None),
        "baseline_4gene_best": max(base) if base else None,
        "verdict": {},
    }
    b4 = out["baseline_4gene_best"]
    # The verdict is computed from EFFECTIVE trials only. A gene whose every
    # trial left the trainer call at its default is reported as UNTESTED rather
    # than as a winner — that is the whole bug this guards: the previous code
    # read `by_gene` straight off every row, so `use_schema_features` "won" 31
    # times on a switch the trainer never saw change.
    for g in sorted(set(list(out["by_gene"].keys())
                         + list(out["by_gene_inert"].keys()))):
        st = out["by_gene"].get(g)
        n_inert = (out["by_gene_inert"].get(g) or {}).get("n", 0)
        ab = out["by_gene_ab"].get(g) or {}
        ab_vals = by_gene_ab.get(g) or []
        best = None if st is None else st["best"]
        out["verdict"][g] = {
            "best": best,
            "informative_n": 0 if st is None else st["n"],
            "inert_n": n_inert,
            "tested": bool(st),
            # The matched A/B is the attributable reading: same champion, same
            # pools, one gene moved. Reported alongside (not instead of) the
            # fitness comparison, because they answer different questions.
            "ab_n": ab.get("n", 0),
            "ab_mean": (None if not ab_vals
                        else round(sum(ab_vals) / len(ab_vals), 4)),
            "ab_positive": sum(1 for x in ab_vals if x > 0.0),
            # "did the treatment beat the trainer's default, on average" -- a
            # TALLY, not a significance claim; the per-row deltas are in the
            # ledger so anyone wanting a test can run one on the real numbers.
            "ab_beats_default": (None if not ab_vals
                                 else bool(sum(ab_vals) / len(ab_vals) > 0.0)),
            "delta_vs_4gene": (None if (b4 is None or best is None)
                               else round(best - b4, 4)),
            "beats_4gene": (None if (b4 is None or best is None)
                            else bool(best > b4)),
            "note": (None if st else
                     "no informative trial: every row resolved to the trainer "
                     "default, so the ledger cannot say what this gene does"),
        }
    return out


# --------------------------------------------------------------------------
# The MUTAGEN actuator — the CONSUMER the signal never had
# --------------------------------------------------------------------------
# `morph_dimension_summary` above DESCRIBES the morph rotation; the epistemic
# tier `exmachina.mutagenesis` SCORES it. Until these existed nothing ever
# ACTED on the verdict, so a rotation that had stopped producing information
# kept appending one gene per cycle forever. On 2026-09-12 that is exactly what
# the ledger showed: 89 trials, 68 of them provably inert (the trialled gene
# resolved to the trainer's own default, so the trial arm could not have
# differed), and 68/89 of them nevertheless "won" (free_growth 0.764).
#
# A gene added by a trial that cannot have tested it is not a gain. The rule is
# stated as data so the tier's band and this actuator cannot drift in silence:
# `exmachina.mutagenesis` carries the same numbers and
# `TestFreeGrowthMirrorsTheProducer` pins the two together.
MORPH_FREE_GROWTH_PAUSE = 0.35   # at/above this share, growth is unattributable
MORPH_DRIFT_BAND = 0.15          # between the two bands: drift, not selection
MORPH_MIN_TRIALS = 6             # below this, no verdict at all
MORPH_ADMIT_WIN_RATE = 0.75      # a candidate must win this often to be funded
MORPH_RETIRE_WIN_RATE = 0.25     # ...and lose this often to be retired
MORPH_MIN_N = 4                  # evidence floor before either verdict
MORPH_DECISIVE_P = 0.05          # the "decisive" tail probability


def _binomial_tail(k: int, n: int) -> Optional[float]:
    """P(>= k successes in n fair trials). None when n is not usable.

    Mirrors `exmachina._binomial_tail` exactly: the producer computes the
    strength of the "they all won anyway" claim, and the tier recomputes it, so
    a difference here is a difference the panel would display as fact.
    """
    try:
        n = int(n)
        if n <= 0:
            return None
        k = max(0, min(int(k), n))
        num = 0
        for i in range(k, n + 1):
            num += math.comb(n, i)
        return num / float(2 ** n)
    except Exception as exc:
        note_failure("proteus_hooks:L901", exc)
        return None


def _morph_read_rows(history_path: str) -> List[dict]:
    """Read a morph ledger, skipping unreadable lines. Never raises."""
    out: List[dict] = []
    try:
        with open(history_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError) as exc:
                    note_failure("proteus_hooks:L916", exc)
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError as exc:
        note_failure("proteus_hooks:L920", exc)
        return []
    return out


def _morph_default_for(name: str) -> Tuple[Any, bool]:
    """(default, known) for a candidate gene, via its trainer kwarg."""
    kwarg = next((c[4] for c in MORPH_KNOWN_GENES if c[0] == name), None)
    if kwarg is None or kwarg not in MORPH_TRAINER_DEFAULTS:
        return None, False
    return MORPH_TRAINER_DEFAULTS[kwarg], True


def morph_free_growth(history_path: str) -> Dict[str, Any]:
    """Was the genome GROWN, or was the gain given away? Never raises.

    `trials` counts only rows that actually carried a gene. A no-op cycle
    cannot speak about selection, and counting one as a non-inert trial biases
    the share DOWN: on the live artifact the same ledger read 0.5862 over every
    row and 0.7640 over the rows that trialled a gene.

    `free_growth` is the share of trials that are provably unattributable — the
    trialled gene resolved to the trainer's own default, so the arms cannot have
    differed — discounted by how strong the evidence is that those trials all
    "won" anyway. A high value means the ledger is recording the surrounding
    search, not the gene, and adding more of it buys nothing.

    Returns the same keys as `exmachina.mutagenesis` so the panel and the
    actuator cannot disagree: n, trials, inert_trials, inert_share,
    inert_positive, inert_p, free_growth, verdict.
    """
    rules = {"pause": MORPH_FREE_GROWTH_PAUSE, "drift": MORPH_DRIFT_BAND,
             "min_trials": MORPH_MIN_TRIALS, "decisive_p": MORPH_DECISIVE_P}
    reading: Dict[str, Any] = {"source": "producer", "rules": rules,
                               "n": 0, "trials": 0, "inert_trials": 0,
                               "inert_share": None, "inert_positive": 0,
                               "inert_p": None, "free_growth": None,
                               "verdict": "unavailable"}

    trials = [r for r in _morph_read_rows(history_path) if r.get("gene")]
    reading["n"] = len(trials)
    reading["trials"] = len(trials)
    if len(trials) < MORPH_MIN_TRIALS:
        return reading

    inert_deltas: List[float] = []
    positives = 0
    for row in trials:
        raw = None
        extra = row.get("extra")
        if isinstance(extra, dict) and extra:
            raw = next(iter(extra.values()))
        stamped = row.get("inert")
        if isinstance(stamped, bool):
            # The producer's own stamp is the authority; only rows written
            # before it existed are re-derived here (same rule as the tier).
            is_inert: Optional[bool] = stamped
        else:
            _default, known = _morph_default_for(str(row.get("gene") or ""))
            if not known:
                # Unknown is NOT inert, and it is not a measured treatment
                # either: it stays in the denominator but is never credited.
                is_inert = None
            else:
                try:
                    # `MORPH_TRAINER_DEFAULTS` is keyed by TRAINER KWARG, which
                    # is what morph_is_inert looks up — never by gene name.
                    is_inert = bool(morph_is_inert(str(row.get("gene")),
                                                   {str(row.get("gene")): raw},
                                                   MORPH_TRAINER_DEFAULTS))
                except Exception as exc:
                    note_failure("proteus_hooks:L990", exc)
                    is_inert = None
        if is_inert is None:
            continue
        if not is_inert:
            continue
        try:
            fit_raw = row.get("fitness")
            base_raw = row.get("baseline_fitness")
            if not isinstance(fit_raw, (int, float, str)) or not isinstance(base_raw, (int, float, str)):
                raise TypeError(f"non-numeric fitness/baseline_fitness: {type(fit_raw).__name__}/{type(base_raw).__name__}")
            delta = float(fit_raw) - float(base_raw)
        except (TypeError, ValueError) as exc:
            note_failure("proteus_hooks:L998", exc)
            delta = 0.0
        inert_deltas.append(delta)
        if delta > 0.0:
            positives += 1

    reading["inert_trials"] = len(inert_deltas)
    reading["inert_positive"] = positives
    reading["inert_share"] = round(len(inert_deltas) / float(len(trials)), 4)
    reading["inert_p"] = _binomial_tail(positives, len(inert_deltas))

    if not inert_deltas:
        reading["free_growth"] = 0.0
        reading["verdict"] = "selection active"
        return reading

    p = reading["inert_p"]
    # `p` is None only when the tail is unrepresentable, which is the strongest
    # possible evidence, so it counts as p -> 0.
    strength = 1.0 if p is None else max(0.0, 1.0 - min(1.0, float(p) / MORPH_DECISIVE_P))
    fg = reading["inert_share"] * strength
    reading["free_growth"] = round(min(1.0, max(0.0, fg)), 4)
    reading["verdict"] = ("free growth" if reading["free_growth"] >= MORPH_FREE_GROWTH_PAUSE
                          else ("drift" if reading["free_growth"] >= MORPH_DRIFT_BAND
                                else "selection active"))
    return reading


def morph_rotation_paused(history_path: str) -> Tuple[bool, Dict[str, Any]]:
    """(paused, reading): should this cycle stop adding genes?

    Fails OPEN on absent evidence. A ledger too small to judge is "unavailable",
    not "paused": absence of evidence is not evidence, and pausing the rotation
    on it would silently end the experiment.
    """
    reading = morph_free_growth(history_path)
    paused = (reading.get("free_growth") is not None
              and reading.get("verdict") == "free growth")
    return bool(paused), reading


def morph_rotation_verdict(evidence: Optional[Dict[str, Any]],
                           rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fundable, retired by evidence, or undetermined. Never a default verdict.

    `undetermined` is NOT a verdict and must never be reported as one: middling
    evidence means the experiment has not answered the question yet, which is a
    different statement from "the gene does not pay".
    """
    rules = rules or {}
    admit = float(rules.get("admit_win_rate", MORPH_ADMIT_WIN_RATE))
    retire_at = float(rules.get("retire_win_rate", MORPH_RETIRE_WIN_RATE))
    decisive = float(rules.get("decisive_p", MORPH_DECISIVE_P))
    min_n = int(rules.get("min_n", MORPH_MIN_N))

    admitted: Dict[str, Any] = {}
    retired: List[str] = []
    undetermined: List[str] = []
    for gene, ev in (evidence or {}).items():
        if not isinstance(ev, dict):
            undetermined.append(str(gene))
            continue
        try:
            n = int(ev.get("n") or 0)
            win_raw = ev.get("win_rate")
            if not isinstance(win_raw, (int, float, str)):
                raise TypeError(f"non-numeric win_rate: {type(win_raw).__name__}")
            win = float(win_raw)
        except (TypeError, ValueError) as exc:
            note_failure("proteus_hooks:L1063", exc)
            undetermined.append(str(gene))
            continue
        p_pays, p_hurts = ev.get("p_pays"), ev.get("p_hurts")
        if n < min_n:
            undetermined.append(str(gene))
        elif win >= admit and p_pays is not None and float(p_pays) <= decisive:
            admitted[str(gene)] = ev
        elif win <= retire_at and p_hurts is not None and float(p_hurts) <= decisive:
            retired.append(str(gene))
        else:
            undetermined.append(str(gene))
    return {"admitted": admitted, "retired": sorted(retired),
            "undetermined": sorted(undetermined)}


def morph_rotation_retired(policy_path: str) -> Dict[str, Any]:
    """Read the morph policy, re-derive the rotation verdict, write it back.

    Retirement is the evidence's decision, not the code's: the machinery stays
    in place so a future candidate re-opens the rotation with no code change.
    Idempotent — two calls on the same policy return the same verdict.
    """
    out: Dict[str, Any] = {"retired": False, "retired_genes": [], "active": [],
                           "admitted": {}, "search_space_closed": False,
                           "reason": ""}
    try:
        with open(policy_path, encoding="utf-8") as fh:
            policy = json.load(fh)
    except (OSError, ValueError) as exc:
        note_failure("proteus_hooks:L1092", exc)
        out["reason"] = "no readable policy: rotation state is undetermined"
        return out
    if not isinstance(policy, dict):
        out["reason"] = "policy is not a JSON object: rotation state is undetermined"
        return out

    verdict = morph_rotation_verdict(policy.get("evidence"), policy.get("rules"))
    retired_genes = list(verdict["retired"])
    candidates = list(policy.get("candidates") or []) + list(verdict["admitted"])
    active = [g for g in dict.fromkeys(candidates) if g not in retired_genes]
    closed = bool((policy.get("search_space") or {}).get("closed"))

    out.update({"retired": bool(retired_genes) or (closed and not active),
                "retired_genes": retired_genes,
                "active": active,
                "admitted": verdict["admitted"],
                "undetermined": verdict["undetermined"],
                "search_space_closed": closed})
    if closed and not active:
        out["reason"] = "no active candidate and the search space is closed"
    elif retired_genes and not active:
        out["reason"] = "every candidate was retired by evidence"
    elif active:
        out["reason"] = "candidate(s) still active"

    policy["rotation"] = out
    if not _morph_write_policy(policy_path, policy):
        # The verdict is still returned; only the write-back failed.
        out["write_error"] = True
    return out


def _morph_write_policy(policy_path: str, policy: dict) -> bool:
    """Write the policy atomically-ish, under the audit hook if it is loaded."""
    _wc = sys.modules.get("CORE.sentinel_enforcer")
    try:
        ctx = (_wc.authorized_write("morph policy") if _wc is not None
               else contextlib.nullcontext())
        with ctx:
            with open(policy_path, "w", encoding="utf-8") as fh:
                json.dump(policy, fh, indent=2, sort_keys=True)
        return True
    except OSError as exc:
        note_failure("proteus_hooks:L1135", exc)
        return False


def morph_evidence_from_ledger(history_path: str) -> Dict[str, Any]:
    """Per-gene evidence from the MATCHED A/B pairs in the morph ledger.

    Only the matched pair is admissible here. The row's
    `fitness - baseline_fitness` compares the trial against the production
    snapshot, so it conflates the gene with which search happened to run; the
    `ab` block holds the same champion and same pools with ONE gene moved, which
    is the attributable reading. Measured on 2026-09-12: across all rows
    `by_session` looked like 45/46 wins at +0.96, while its 11 matched pairs ran
    4/11 at -0.5408 and `use_schema_features`' 8 pairs went 0/8 at -10.6423.
    The row-level delta credited both genes every cycle; the pair did not.

    `p_pays` = P(>= wins) and `p_hurts` = P(<= wins) under a fair-coin null, so
    a SMALL p_pays is evidence the gene pays and a small p_hurts is evidence it
    hurts. Never raises; a ledger with no pairs yields {}.
    """
    by_gene: Dict[str, List[float]] = {}
    for row in _morph_read_rows(history_path):
        gene = row.get("gene")
        if not gene:
            continue
        ab = row.get("ab")
        if not isinstance(ab, dict):
            continue
        delta = ab.get("delta")
        if delta is None:
            continue
        try:
            by_gene.setdefault(str(gene), []).append(float(delta))
        except (TypeError, ValueError) as exc:
            note_failure("proteus_hooks:L1168", exc)
            continue

    evidence: Dict[str, Any] = {}
    for gene, deltas in sorted(by_gene.items()):
        n = len(deltas)
        if not n:
            continue
        wins = sum(1 for d in deltas if d > 0.0)
        evidence[gene] = {
            "gene": gene,
            "n": n,
            "positive": wins,
            "mean": round(sum(deltas) / float(n), 4),
            "win_rate": round(wins / float(n), 4),
            # A small p_pays means the gene pays; a small p_hurts means it hurts.
            "p_pays": _binomial_tail(wins, n),
            "p_hurts": _binomial_tail(n - wins, n),
        }
    return evidence


def morph_policy_refresh(policy_path: str, history_path: str,
                         cycle_index: Optional[int] = None) -> Dict[str, Any]:
    """Re-derive `state/morph_policy.json` from the ledger. Never raises.

    The artifact is the DECISION, so it must be re-derived rather than carried:
    evidence comes from the matched A/B pairs, the verdict from the evidence, and
    the verdict is written into `rotation` by `morph_rotation_retired` (which is
    idempotent). The rotation's candidate slot follows the round-robin, so the
    file says which gene the NEXT cycle would add and whether it is still active.

    `search_space` is PRESERVED exactly as written: it is a hand-audited record
    of which trainer kwargs can actually move the computation, and re-deriving
    it from a signature would replace an audit with a guess.
    """
    out: Dict[str, Any] = {"refreshed": False, "written": False,
                           "evidence": {}, "rotation": {}}
    policy: Dict[str, Any] = {}
    try:
        with open(policy_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            policy = loaded
    except (OSError, ValueError) as exc:
        note_failure("proteus_hooks:L1212", exc)
        policy = {}

    rules = policy.get("rules") if isinstance(policy.get("rules"), dict) else None
    if not rules:
        rules = {"admit_win_rate": MORPH_ADMIT_WIN_RATE,
                 "decisive_p": MORPH_DECISIVE_P,
                 "min_n": MORPH_MIN_N,
                 "retire_win_rate": MORPH_RETIRE_WIN_RATE}

    evidence = morph_evidence_from_ledger(history_path)
    verdict = morph_rotation_verdict(evidence, rules)
    for gene, ev in evidence.items():
        ev["status"] = ("admit" if gene in verdict["admitted"]
                        else ("retire" if gene in verdict["retired"]
                              else "undetermined"))

    candidates = list(policy.get("candidates") or [])
    if cycle_index is not None:
        gene = morph_candidate_gene(int(cycle_index))
        candidates = [gene["name"]] if gene else []

    paused, reading = morph_rotation_paused(history_path)
    policy.update({
        "rules": rules,
        "evidence": evidence,
        "candidates": candidates,
        "retired": verdict["retired"],
        "admitted": verdict["admitted"],
        "active": [g for g in candidates if g not in verdict["retired"]],
        "actuator": {
            "threshold": MORPH_FREE_GROWTH_PAUSE,
            "paused": paused,
            "verdict": reading.get("verdict"),
            "free_growth": reading.get("free_growth"),
            "reading": reading,
        },
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    })
    out["written"] = _morph_write_policy(policy_path, policy)
    out["evidence"] = evidence
    out["rotation"] = morph_rotation_retired(policy_path)
    out["refreshed"] = True
    return out
