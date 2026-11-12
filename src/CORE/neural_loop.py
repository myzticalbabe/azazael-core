"""NEURAL LOOP — the canonical closed cycle over the neural substrate.

WHY THIS EXISTS
===============

The system's learning was scheduled as a FLAT JOB TABLE: fifteen independent
entries, each with its own interval, each racing for one execution slot, run in
an order decided by "most overdue, ties broken alphabetically". Nothing in that
shape made one job's OUTPUT the next job's INPUT, so a fire could truthfully
report success while the substrate it was supposed to move had not changed at
all. "Ran" and "learned" were the same word, and the difference was invisible.

A neural loop is not a set of timers. It is a CAUSAL SEQUENCE:

    load    -> perceive -> credit -> plasticity -> route -> act -> grade
            -> consolidate -> prune -> grow -> l1 -> l2 -> verify

Each arrow is a data dependency: `route` cannot run without the weights
`plasticity` just wrote, `grade` cannot run without the executions `act`
produced, and `verify` is the closure that makes "nothing changed" a FAILURE
rather than a success. That last stage is the whole point -- an inert cycle is
the specific failure mode this system kept reporting as healthy.

THREE PROPERTIES THAT ARE DELIBERATELY ENFORCED
================================================

1. `changed` is a first-class field, separate from `ok`. A stage can succeed
   and change nothing (`perceive` on an empty store); that is not the same as
   a stage that was supposed to write and did not. `verify` fails the cycle
   when NO stage changed anything.

2. A blocked stage is BLOCKED, not skipped. The write-authority gate (a stale
   self-image) refuses the writing half of the cycle; before this module a
   refusal was recorded as a quiet success, which is how a permanently refused
   heartbeat reported itself as a running one. `blocked` carries the reason.

3. Stages are INJECTED. `default_stages()` returns the real adapters, and every
   one of them is a thin, fail-open wrapper over an existing subsystem; tests
   replace them. The loop engine itself imports only the stdlib at module level,
   so it can be tested without the substrate and the substrate can change
   without the engine changing.

The engine never invents a learning signal. It reads credit where credit is
already computed (`spawn_credit`), writes it through the one write path
(`spine_registry.record_outcome`), and reads the decision back out through the
one read path (`spine_registry.get_q_weights` / `q_order`). Those three entry
points are the loop's entire interface to the brain.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

VERSION = "1.0.0"

__all__ = [
    "STAGES",
    "WRITE_STAGES",
    "StageResult",
    "LoopContext",
    "Loop",
    "default_stages",
    "state_fingerprint",
    "run_cycle",
]

#: The causal order. Declared once, used for execution AND for the report, so a
#: stage cannot be silently dropped from the run while still appearing in a
#: summary (the failure mode of a hand-maintained report list).
STAGES: Tuple[str, ...] = (
    "load",         # restore the brain state this process will learn on
    "perceive",     # read what actually happened
    "credit",       # turn it into a reward vector
    "plasticity",   # write the reward into the weights
    "route",        # read the weights back out as a dispatch order
    "act",          # execute in that order
    "grade",        # record the executions' outcomes
    "consolidate",  # sleep/replay over the cycle's experience
    "prune",        # structural forgetting
    "grow",         # structural exploration
    "l1",           # genetic: wiring genome promotion
    "l2",           # parametric: outer-loop calibration
    "persist",      # durability: flush the weights to disk
    "verify",       # closure: did ANY of this change the substrate?
)

#: Stages that mutate durable state. Refused together by the write gate, and
#: the reason a refusal is recorded rather than absorbed.
WRITE_STAGES: frozenset = frozenset({
    "plasticity", "act", "grade", "consolidate", "prune", "grow", "l1", "l2",
    "persist",
})

#: Learning rate for the loop's TD write. One constant, stated here rather than
#: buried, because it is the only number in this file that changes behaviour.
ALPHA = 0.15

#: Minimum individual observations before an organ's reward is treated as
#: EVIDENCE about that organ. `spawn_credit` reports a Wilson lower bound, which
#: on one or two observations is a prior wearing the costume of a measurement:
#: 232 of 234 organs cluster between 0.87 and 0.99, and the value is driven
#: mostly by how many times the organ was seen, not by how it did. Writing that
#: into the weights is how a table stops discriminating.
MIN_EVIDENCE = 3

#: Below this, a weight update is not worth a write (a no-op write would mark a
#: cycle as `changed` and let an inert cycle pass `verify`).
EPSILON = 1e-9

#: The smallest change to the weights that `verify` is willing to call a change.
#: Shared with `plasticity` ON PURPOSE: if the write threshold were smaller, the
#: loop could write an update of 7.5e-9, report `changed=True`, and then be
#: failed by its own closure for "a change the weights do not show". Two
#: tolerances for one question is the bug; a caught test is how it surfaced.
MOVE_TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# Result + context
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """What one stage did, in terms a caller can act on.

    `ok` and `changed` are deliberately independent. `ok` means the stage did
    its job (including correctly finding nothing to do); `changed` means durable
    state differs afterwards. Collapsing them is how "no data" became "no
    problem" -- a perceiving stage on an empty store is `ok=True,
    changed=False`, and that must not count as progress.
    """

    stage: str
    ok: bool
    changed: bool = False
    blocked: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = {"stage": self.stage, "ok": self.ok, "changed": self.changed,
             "ms": round(self.ms, 2)}
        if self.blocked:
            d["blocked"] = self.blocked
        if self.error:
            d["error"] = self.error
        if self.evidence:
            d["evidence"] = self.evidence
        return d


@dataclass
class LoopContext:
    """The data that flows between stages. This IS the contract."""

    apply: bool = False
    limit: int = 200
    state_before: Dict[str, Any] = field(default_factory=dict)
    state_after: Dict[str, Any] = field(default_factory=dict)
    credit: Any = None
    outcome_rows_before: int = 0
    rewards: Dict[str, float] = field(default_factory=dict)
    #: observations per organ -- the evidence behind `rewards[organ]`
    evidence: Dict[str, int] = field(default_factory=dict)
    categories: Dict[str, str] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    executed: List[Dict[str, Any]] = field(default_factory=list)
    results: List[StageResult] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    # -- EP266: the per-turn latent-vs-explicit mode selector ----------------
    #: Consulted once per `route` stage with the order it is deciding about.
    #: INJECTED rather than imported, for the same reason the stages are:
    #: this engine imports only the stdlib at module level, so a hard import
    #: would make the mode policy unswappable in tests without the substrate.
    #: `None` (the default) keeps the loop's original behaviour exactly.
    mode_decider: Optional[Callable[..., Any]] = None
    #: The modes the decider returned, one per `route` stage, in turn order.
    modes: List[Any] = field(default_factory=list)
    #: How many `route` stages this context has run -- the decider's clock, so
    #: a policy can decay over turns (the "adaptive" half of the row).
    turns: int = 0

    # -- helpers the stages share -------------------------------------------

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def result_for(self, stage: str) -> Optional[StageResult]:
        for r in self.results:
            if r.stage == stage:
                return r
        return None

    def any_changed(self) -> bool:
        return any(r.changed for r in self.results)

    def blocked_reason(self) -> str:
        for r in self.results:
            if r.blocked:
                return r.blocked
        return ""


# ---------------------------------------------------------------------------
# State fingerprint — what "the substrate changed" means, measurably
# ---------------------------------------------------------------------------

def weights_signature(q_weights: MappingLike) -> str:
    """A digest of the weight VALUES, not of how many there are.

    Edge count cannot answer "did the weights change": a cycle that nudges 232
    EXISTING edges leaves the count exactly where it was. Measured on the first
    monitored cycle -- the loop reported OK, this file's own persistence check
    saw 264 before and 264 after, and only the value digest showed the update
    had landed. Any check that counts edges when the question is about values
    will call a real update a no-op.
    """
    parts = []
    for k in sorted(q_weights):
        try:
            parts.append("%s=%.9f" % (k, float(q_weights[k])))
        except (TypeError, ValueError) as exc:
            note_failure("neural_loop:L220", exc)
            parts.append("%s=?" % k)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


#: Structural alias so the signature helper can be typed without importing
#: `typing.Mapping` at module scope for one annotation.
MappingLike = Any


def state_fingerprint(q_weights: Dict[str, float]) -> Dict[str, Any]:
    """A cheap, comparable summary of the learned weights.

    Comparable is the requirement, not exact. This is used to answer one
    question -- "does the substrate differ after the cycle?" -- and a summary
    that answers it with a stable structure is better than a digest that
    changes when a float is re-serialised.

    `distinct` is reported separately because it caught a real defect: a table
    of 31 edges with 3 distinct values cannot route, and `edges` alone said the
    table was healthy.
    """
    out: Dict[str, Any] = {"edges": 0, "distinct": 0, "nonzero": 0,
                           "sum": 0.0, "mean": 0.0, "l2": 0.0, "max": 0.0}
    if not q_weights:
        return out
    vals: List[float] = []
    for v in q_weights.values():
        try:
            vals.append(float(v))
        except (TypeError, ValueError) as exc:
            note_failure("neural_loop:L250", exc)
            continue
    if not vals:
        return out
    out["edges"] = len(q_weights)
    out["distinct"] = len({round(v, 6) for v in vals})
    out["nonzero"] = sum(1 for v in vals if abs(v) > EPSILON)
    out["sum"] = round(sum(vals), 6)
    out["mean"] = round(sum(vals) / len(vals), 6)
    out["l2"] = round(math.sqrt(sum(v * v for v in vals)), 6)
    out["max"] = round(max(abs(v) for v in vals), 6)
    return out


def _fingerprint_moved(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Did the substrate actually move? Compares the fields that mean learning."""
    if not a or not b:
        return bool(a) != bool(b)
    return (a.get("edges") != b.get("edges")
            or a.get("distinct") != b.get("distinct")
            or abs(float(a.get("l2") or 0.0) - float(b.get("l2") or 0.0)) > MOVE_TOLERANCE
            or abs(float(a.get("sum") or 0.0) - float(b.get("sum") or 0.0)) > MOVE_TOLERANCE)


# ---------------------------------------------------------------------------
# Default stages — thin, fail-open adapters over the real subsystems
# ---------------------------------------------------------------------------
#
# Every adapter follows the same three rules:
#   * it never raises (a stage failure is evidence, not a crash),
#   * it reports `changed` only for a durable change it can point at,
#   * it records WHAT it saw in `evidence`, because "ok" with no evidence is
#     the shape of a lie.

def _stage_load(ctx: LoopContext) -> StageResult:
    """Restore the weights this process will learn on, and fingerprint them.

    Without this the cycle runs on an empty table and every TD update is
    computed against a zero baseline -- learning that cannot accumulate across
    processes. `get_q_weights()` (not the private dict) is used on purpose: it
    is the call path that triggers `spine_registry._ensure_loaded()`, so a
    direct read of `_Q_WEIGHTS` returns {} in a fresh process and would make
    this stage report amnesia the system does not actually have.
    """
    from CORE import spine_registry as sr
    qw = sr.get_q_weights()
    ctx.state_before = state_fingerprint(qw)
    return StageResult("load", True, changed=False, evidence=ctx.state_before)


def _stage_perceive(ctx: LoopContext) -> StageResult:
    """Read what actually happened, from the one live credit source."""
    from CORE.WORMS.expansion import spawn_credit
    credit = spawn_credit.live_spawn_credit()
    ctx.credit = credit
    ctx.rewards = dict(getattr(credit, "rewards", {}) or {})
    # Sample size per organ, carried forward because the write stage must be
    # able to tell a measurement from a prior. Read from the outcome objects
    # rather than re-derived: `n` is the count the reward was computed from.
    for name, outcome in (getattr(credit, "outcomes", {}) or {}).items():
        try:
            ctx.evidence[name] = int(getattr(outcome, "n", 0) or 0)
        except (TypeError, ValueError) as exc:
            note_failure("neural_loop:L312", exc)
            ctx.evidence[name] = 0
    never = list(getattr(credit, "chosen_but_never_run", lambda: [])() or [])
    grounded = sum(1 for n in ctx.evidence.values() if n >= MIN_EVIDENCE)
    # Baseline for `grade`'s independent check: how many outcome rows the tap
    # held BEFORE this cycle executed anything. `grade` compares against this
    # rather than trusting the executor's summary.
    ctx.outcome_rows_before = int(getattr(credit, "n_outcome_rows", 0) or 0)
    return StageResult(
        "perceive", True, changed=False,
        evidence={"observed": len(ctx.rewards), "chosen_never_run": len(never),
                  "with_evidence": grounded, "outcome_rows": ctx.outcome_rows_before,
                  "attribution_mode": getattr(credit, "attribution_mode", "")},
    )


def _category_of(entry: Any) -> Optional[str]:
    """An organ's nature, from whichever shape the registry entry has.

    `WORM_REGISTRY` values are plain DICTS whose field is `category`, not objects
    with a `.nature` attribute. The first version of this module read
    `getattr(reg, "nature")`, which returned None for all 234 rewarded organs and
    made the loop unable to key a single write -- the pipeline ran, reported an
    orderly stage table, and resolved zero natures. Read the shape you have.
    """
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("category") or entry.get("nature") or None
    return getattr(entry, "category", None) or getattr(entry, "nature", None)


def _stage_credit(ctx: LoopContext) -> StageResult:
    """Resolve each rewarded organ's nature, so the write can key correctly.

    `spawn_credit` is keyed by organ name; the Q-table is keyed `nature:worm`.
    A reward whose nature is unknown is DROPPED and counted -- writing it to a
    guessed nature would put a real reward on the wrong edge, which is worse
    than not writing it.
    """
    from CORE.worm_registry import WORM_REGISTRY, discover_worms
    try:
        discover_worms()
    except Exception as exc:
        note_failure("neural_loop:L355", exc)
    unresolved, nonzero = [], 0
    for name, val in ctx.rewards.items():
        category = _category_of(WORM_REGISTRY.get(name))
        if not category:
            unresolved.append(name)
            continue
        ctx.categories[name] = category
        if abs(float(val or 0.0)) > EPSILON:
            nonzero += 1
    ctx.note("credit: %d resolved, %d unresolved, %d nonzero"
             % (len(ctx.categories), len(unresolved), nonzero))
    return StageResult(
        "credit", True, changed=False,
        evidence={"resolved": len(ctx.categories), "unresolved": len(unresolved),
                  "nonzero": nonzero, "unresolved_sample": unresolved[:5]},
    )


def _stage_plasticity(ctx: LoopContext) -> StageResult:
    """Write credit into the weights -- as an ADVANTAGE, over EVIDENCE only.

    Two rules, and both were learned from a measurement rather than assumed:

    1. **Advantage, not level.** The learning signal is `reward - mean(reward)`.
       Writing the absolute success rate makes every organ converge toward the
       population mean, so 232 organs become one value and the table loses the
       ability to order anything -- the same failure the Q-table's own history
       records ("31 edges collapsed to 3 distinct values, 25 at exactly
       +0.0909"). An advantage changes SIGN with the organ's standing, so the
       update carries discrimination instead of injecting the mean.

    2. **Evidence, not prior.** `spawn_credit`'s reward is a Wilson lower bound,
       and on a handful of observations it reports the population value. A first
       version of this stage joined the table against credit and read
       `synapse:SSMSequenceWorm q=0.1395` beside `p=0.9465`; that specific pair
       was an apples-to-oranges join (the `synapse:` prefix is the synapse
       worm's own edge namespace, not an organ's fleet score), but the general
       hazard is real: an organ seen once has no score of its own, and the write
       must refuse to overwrite a learned weight with a population prior.
       `MIN_EVIDENCE` is that refusal.

    The delta convention is used rather than the target convention because this
    is a TD-style update: `delta=` adds, `reward=` converges. The two are equal
    only when reward == q + delta.
    """
    from CORE import spine_registry as sr
    if not ctx.apply:
        return StageResult("plasticity", True, changed=False,
                           blocked="dry-run (apply=False)")

    # Population baseline over the SAME organs the write will touch, so the
    # advantage is relative to a comparable population rather than to the fleet
    # as a whole (which includes organs this stage will skip).
    scored = [(n, float(ctx.rewards.get(n) or 0.0)) for n in ctx.categories]
    if not scored:
        return StageResult("plasticity", True, changed=False,
                           blocked="no resolved organs to score")
    vals = [v for _, v in scored]
    mean = sum(vals) / len(vals)
    spread = max(vals) - min(vals)

    updates = wrote = no_evidence = flat = errored = 0
    for name, reward in scored:
        if ctx.evidence.get(name, 0) < MIN_EVIDENCE:
            no_evidence += 1
            continue
        advantage = reward - mean
        step = ALPHA * advantage
        # Same threshold the closure uses, so what this stage calls a write is
        # what `verify` will be able to see.
        if abs(step) <= MOVE_TOLERANCE:
            flat += 1
            continue
        category = ctx.categories[name]
        try:
            sr.record_outcome(category, name, success=advantage > 0.0,
                              duration_ms=0.0, delta=step)
            updates += 1
            wrote += 1
        except Exception as exc:
            note_failure("neural_loop:L436", exc)
            errored += 1
    return StageResult(
        "plasticity", True, changed=wrote > 0,
        evidence={"updates": updates, "no_evidence": no_evidence,
                  "flat": flat, "errored": errored,
                  "alpha": ALPHA, "min_evidence": MIN_EVIDENCE,
                  "baseline": round(mean, 6), "spread": round(spread, 6)},
    )


def _stage_route(ctx: LoopContext) -> StageResult:
    """Read the weights back out as a dispatch order.

    This is the loop's EFFECTOR -- the point where learning becomes a decision.
    A loop whose weights are written and never read is a logging system, which
    is exactly what this system was: it recorded 608 "choices" that nothing
    executed. The order is computed from the just-updated table, so the same
    cycle that learns is the cycle that acts on it.
    """
    from CORE import spine_registry as sr
    categories: Dict[str, List[str]] = {}
    for name, category in ctx.categories.items():
        categories.setdefault(category, []).append(name)
    order: List[str] = []
    for category, names in sorted(categories.items()):
        try:
            order.extend(sr.q_order(category, names))
        except Exception as exc:
            note_failure("neural_loop:L464", exc)
            order.extend(sorted(names))
    ctx.order = order
    # EP266 (ALAR -- adaptive latent agentic reasoning). The loop decided WHAT
    # to run (the order above) and never decided HOW: no per-turn
    # latent-vs-explicit mode policy sat anywhere on this path. The latent
    # carriers themselves live in their own modules; the missing piece is the
    # SELECTOR, and the loop's decision point is here.
    #
    # A decider that raises must not fail the effector -- the order it was
    # consulted about is already computed -- so the failure is RECORDED, not
    # swallowed, and the stage still returns its order. With no decider
    # injected the evidence dict is exactly the pre-EP266 one.
    evidence: Dict[str, Any] = {"ordered": len(order), "head": order[:8]}
    if ctx.mode_decider is not None:
        try:
            mode = ctx.mode_decider({
                "order": list(order),
                "categories": dict(categories),
                "turn": ctx.turns,
            })
        except Exception as exc:
            note_failure("neural_loop:L512", exc)
            mode = None
        if mode is not None:
            ctx.modes.append(mode)
            evidence["mode"] = mode
            evidence["turn"] = ctx.turns
    ctx.turns += 1
    # The head is reported because an order's TAIL changing is not observable
    # in a summary, and "the first organ changed" is the thing a reader checks.
    return StageResult(
        "route", True, changed=False,
        evidence=evidence,
    )


def _stage_act(ctx: LoopContext) -> StageResult:
    """Execute the fleet. Bounded, and only when the gate allows writing."""
    if not ctx.apply:
        return StageResult("act", True, changed=False,
                           blocked="dry-run (apply=False)",
                           evidence={"would_run": len(ctx.order)})
    import subprocess, sys
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_mesh_cycle.py"
    if not script.exists():
        return StageResult("act", False, error="executor missing: %s" % script)
    try:
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=ctx.limit)
    except subprocess.TimeoutExpired as exc:
        note_failure("neural_loop:L488", exc)
        return StageResult("act", False, error="executor timed out after %ds" % ctx.limit)
    except Exception as exc:
        note_failure("neural_loop:L490", exc)
        return StageResult("act", False, error="executor error: %s" % str(exc)[:120])
    tail = [ln for ln in (proc.stdout or "").splitlines() if " cycle " in ln]
    summary = tail[-1] if tail else ""
    # Parse the executor's own summary rather than re-deriving counts; if the
    # summary cannot be parsed the cycle is reported as having run nothing,
    # which is the honest reading of an unreadable result.
    ran = 0
    for tok in summary.split():
        if tok.startswith("ok="):
            try:
                ran = int(tok[3:])
            except ValueError as exc:
                note_failure("neural_loop:L502", exc)
                ran = 0
    ctx.executed = [{"summary": summary, "rc": proc.returncode}]
    return StageResult(
        "act", proc.returncode == 0, changed=ran > 0,
        error="" if proc.returncode == 0 else "executor rc=%s" % proc.returncode,
        evidence={"ok": ran, "rc": proc.returncode, "line": summary[:160]},
    )


def _stage_grade(ctx: LoopContext) -> StageResult:
    """Confirm grading happened, by re-reading the tap.

    Grading itself is done by the executor (ClusterWORMZ records an outcome per
    organ as it runs), so this stage's honest job is NOT to grade again -- it is
    to CHECK that grading occurred. The check is independent of the executor's
    own summary: it re-reads the outcome tap and compares its row count to the
    baseline `perceive` took, so an executor that reports success while writing
    nothing is caught here rather than believed.
    """
    if not ctx.apply:
        return StageResult("grade", True, changed=False, blocked="dry-run (apply=False)")
    if not ctx.executed:
        return StageResult("grade", True, changed=False,
                           blocked="nothing executed to grade")
    from CORE.WORMS.expansion import spawn_credit
    try:
        after = int(getattr(spawn_credit.live_spawn_credit(), "n_outcome_rows", 0) or 0)
    except Exception as exc:
        note_failure("neural_loop:L530", exc)
        return StageResult("grade", True, changed=False,
                           blocked="could not re-read the outcome tap: %s" % str(exc)[:80])
    new_rows = after - ctx.outcome_rows_before
    if new_rows <= 0:
        return StageResult(
            "grade", False, changed=False,
            error="executor reported success but the outcome tap gained no rows",
            evidence={"before": ctx.outcome_rows_before, "after": after})
    # Auto/CA gate (2026-09-29, free-Jev): a perfect win-rate on a large
    # batch is evidence of an uncalibrated guard, not of perfection.
    # Recorded, not blocking: calibration is fixed by witnessed-
    # determinism, not by halting the loop.
    evidence: dict = {"new_outcome_rows": new_rows,
                      "before": ctx.outcome_rows_before, "after": after}
    try:
        from CORE.WORMS.expansion import spawn_credit as _sc
        _credit = _sc.live_spawn_credit()
        _stat = _sc.abstention_stats(getattr(_credit, "outcomes", {}) or {})
        evidence["win_rate"] = _stat["win_rate"]
        evidence["n_standby"] = _stat["n_standby"]
        evidence["abstain"] = _stat["abstain"]
        evidence["abstain_reason"] = _stat["reason"]
        if _stat["abstain"] and _stat["reason"] == "uncalibrated_perfect":
            evidence["uncalibrated_perfect"] = True
    except Exception as exc:
        note_failure("neural_loop:L555", exc)
    return StageResult("grade", True, changed=False, evidence=evidence)


def _stage_consolidate(ctx: LoopContext) -> StageResult:
    """Sleep phase: replay offline, then PROVE the replay wrote something.

    The dream cycle is run as the real subsystem rather than re-applying this
    cycle's credit through `dream_reinforce`. That distinction matters: a
    reinforcement pass fed the same rewards that `plasticity` just wrote would
    DOUBLE-COUNT them, and the duplicate would be invisible -- it would look
    like strong evidence for a signal that was measured once.

    It runs TWO things, because they are two different subsystems and only one
    of them touches Q:

    1. `dream_cycle.py` -- dreaming plus the policy bridge, which updates
       strategy policy.
    2. `ConsolidationWorm().consolidate()` -- the offline Q channel, the only
       code path that calls `dream_reinforce()`.

    Running only (1) is what this stage used to do, and the measured
    consequence is on disk: the dream-count table was **empty** for the whole
    life of this loop, so the stage could not have proved a delta no matter how
    long it ran. It reported `ok` while the offline channel had never once been
    invoked.

    The delta is read from two independent places, neither of which is the
    worm's own report: the dream-count table (`dream:writes`) and the weights
    ON DISK (`weights_moved`). A worm that says `reinforced=7` while both stay
    flat has argued with the evidence.
    """
    from CORE import spine_registry as sr
    if not ctx.apply:
        return StageResult("consolidate", True, changed=False,
                           blocked="dry-run (apply=False)")
    before_sig = weights_signature(_disk_weights())
    try:
        before_dreams = sum(int(v or 0) for v in sr.get_q_dream_counts().values())
    except Exception as exc:
        note_failure("neural_loop:L595", exc)
        before_dreams = 0
    # (1) the dream cycle -- real subsystem, real process
    dream = _run_script_stage("consolidate", "dream_cycle.py", ctx.limit)
    # (2) the offline Q channel
    reinforced, dreams_n, skipped, channel_error = 0, 0, None, ""
    try:
        from CORE.WORMS.expansion.spine import ConsolidationWorm
        rep = ConsolidationWorm().consolidate(force=True) or {}
        reinforced = int(rep.get("reinforced") or 0)
        dreams_n = int(rep.get("dreams") or 0)
        skipped = rep.get("skipped")
    except Exception as exc:
        note_failure("neural_loop:L607", exc)
        channel_error = "%s: %s" % (type(exc).__name__, str(exc)[:100])
    # Read the evidence BEFORE deciding to persist, and persist on the EVIDENCE.
    # The first version saved when the worm's own report said `reinforced > 0`,
    # which is the self-report shape this loop exists to stop trusting -- a test
    # with a worm that reports `reinforced=5` and writes nothing caught it: the
    # stage saved an unchanged table and reported `changed=True`. The counts are
    # in-process state, so they are visible the moment the worm returns.
    try:
        after_dreams = sum(int(v or 0) for v in sr.get_q_dream_counts().values())
    except Exception as exc:
        note_failure("neural_loop:L617", exc)
        after_dreams = before_dreams
    dream_delta = after_dreams - before_dreams
    if dream_delta > 0:
        try:
            sr.save_spine_state()
        except Exception as exc:
            note_failure("neural_loop:L623", exc)
    after_sig = weights_signature(_disk_weights())
    moved = after_sig != before_sig
    return StageResult(
        "consolidate", dream.ok and not channel_error,
        changed=bool(moved or dream_delta > 0),
        error=(channel_error or ("" if dream.ok else "dream_cycle %s" % dream.error)),
        evidence={"rc": dream.evidence.get("rc"),
                  "tail": dream.evidence.get("tail"),
                  "dreams": dreams_n, "reinforced": reinforced,
                  "skipped": skipped,
                  "dream_counts_before": before_dreams,
                  "dream_counts_after": after_dreams,
                  "dream_delta": dream_delta,
                  "weights_moved_on_disk": moved,
                  "sig_before": before_sig, "sig_after": after_sig},
    )


def _stage_prune(ctx: LoopContext) -> StageResult:
    """Structural forgetting: DELETE the edges that carry no signal.

    This stage used to report candidates and delete nothing, because
    `spine_registry` exposed no public Q-edge prune. That is no longer true:
    `purge_edges()` exists and enforces the safety property this stage needs,
    so the block is gone and the deletion is real.

    The property that matters: a candidate must have NO evidence at all -- no
    observed update and no dream count. NOT "a small weight". The edges
    `plasticity` just created start near the neutral value by construction, so a
    magnitude threshold prunes the learning the loop was called to produce, and
    it does it one cycle after producing it. `purge_edges` refuses any key with
    evidence and reports the refusal, so this stage cannot delete a learned edge
    even if the candidate list is wrong.

    Also enforces the capacity bound. `cap_q_tables()` drops by evidence when
    the table exceeds `_Q_KEYS_MAX` -- and until today its drops were re-adopted
    by the next save, so the bound it reported did not hold.
    """
    from CORE import spine_registry as sr
    if not ctx.apply:
        return StageResult("prune", True, changed=False,
                           blocked="dry-run (apply=False)")
    try:
        weights = sr.get_q_weights()
        updates = sr.get_q_update_counts()
        dreams = sr.get_q_dream_counts()
    except Exception as exc:
        note_failure("neural_loop:L671", exc)
        return StageResult("prune", False, error="read failed: %s" % str(exc)[:120])
    # "No evidence" is not the same claim as "no record of a decision".
    #
    # 2026-09-29. This stage calls a key dead when its count is 0, and 0 is
    # exactly what `invalidate_q(factor=0.0)` and `reset_counters()` write.
    # So the two repairs that exist to preserve a disowned measurement are the
    # two that make its key look untouched by the system, and the next hourly
    # meta cycle purges it. MEASURED on the live ledger: restoring
    # `route:windows>wsl2-ubuntu` and `route:windows>wsl2-kali` at 09:47 with
    # their genuine latency history cost two full purge/restore cycles, because
    # both keys were tombstoned `neural_loop_prune` by 10:05.
    #
    # A key carrying an evidence floor or a counter floor has a recorded
    # decision against it. It is discounted, not absent, and pruning on
    # absence deletes the record of the discount along with the value --
    # which is how a repair becomes a second, quieter data-loss path.
    floored = set()
    for table in ("_Q_EVIDENCE_FLOOR", "_COUNTER_FLOOR"):
        floors = getattr(sr, table, None)
        if isinstance(floors, dict):
            floored.update(floors)
    dead = [k for k in weights
            if int(updates.get(k, 0) or 0) == 0
            and int(dreams.get(k, 0) or 0) == 0
            and k not in floored]
    try:
        rep = sr.purge_edges(dead, reason="neural_loop_prune", persist=False)
    except Exception as exc:
        note_failure("neural_loop:L699", exc)
        return StageResult("prune", False,
                           error="purge_edges raised: %s" % str(exc)[:120])
    removed = int(rep.get("removed_count") or 0)
    refused = int(rep.get("refused_count") or 0)
    try:
        capped = int(sr.cap_q_tables() or 0)
    except Exception as exc:
        note_failure("neural_loop:L706", exc)
        capped = 0
    # `persist=False` is deliberate: the tombstones and the deletions are
    # durable because the `persist` stage that follows saves in THIS process,
    # where the tombstones live. Saving here too would double the write for no
    # gain. `changed` reflects the removal, and `verify` still has to see it on
    # disk before the cycle counts as closed.
    return StageResult(
        "prune", True, changed=bool(removed or capped),
        evidence={"scanned": len(weights), "candidates": len(dead),
                  "removed": removed, "refused": refused, "capped": capped,
                  "sample": list(rep.get("removed") or [])[:5],
                  "note": ("nothing to forget: every edge carries evidence"
                           if not dead else "")},
    )


def _run_script_stage(stage: str, script_name: str, timeout: int,
                      expect_change: bool = False) -> StageResult:
    """Run one of the repo's own cycle scripts as a loop stage.

    `changed` is never inferred from the script's exit code. A script can exit
    0 having computed nothing, which is the report-shape this whole module
    exists to stop trusting; the delta that counts is the one `verify` measures
    on the weights.
    """
    import subprocess, sys
    script = Path(__file__).resolve().parents[2] / "scripts" / script_name
    if not script.exists():
        return StageResult(stage, False, error="missing: %s" % script_name)
    try:
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        note_failure("neural_loop:L739", exc)
        return StageResult(stage, True, changed=False,
                           blocked="timed out after %ds" % timeout)
    except Exception as exc:
        note_failure("neural_loop:L742", exc)
        return StageResult(stage, False, error=str(exc)[:120])
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return StageResult(
        stage, proc.returncode == 0, changed=False,
        error="" if proc.returncode == 0 else "rc=%s" % proc.returncode,
        evidence={"rc": proc.returncode, "tail": (tail[-1][:120] if tail else "")},
    )


def _stage_grow(ctx: LoopContext) -> StageResult:
    """Structural exploration: stage specialist candidates for capability gaps.

    `morph()` is the proposal, `promote()` is the gate; they are called in that
    order and NEVER collapsed. Staging writes source into the containment pen,
    which discovery cannot import, so a dry run genuinely cannot produce a worm.
    Both pattern classes are reported separately (chronic repair vs opportunity
    exploration) because collapsing them hides that one of the two triggers is
    silent -- which is how the opportunity trigger went unnoticed.
    """
    from CORE.WORMS.expansion.spine import MorphogenesisWorm
    try:
        worm = MorphogenesisWorm()
        preview = worm.morph(dry_run=True) or {}
    except Exception as exc:
        note_failure("neural_loop:L766", exc)
        return StageResult("grow", False,
                           error="morphogenesis scan raised: %s" % str(exc)[:120])
    ev = {"patterns": preview.get("patterns"),
          "chronic": preview.get("patterns_chronic"),
          "opportunity": preview.get("patterns_opportunity"),
          "opportunity_enabled": preview.get("opportunity_enabled")}
    if not ctx.apply:
        return StageResult("grow", True, changed=False,
                           blocked="dry-run (apply=False)", evidence=ev)
    try:
        staged = worm.morph(dry_run=False) or {}
        gate = worm.promote() or {}
    except Exception as exc:
        note_failure("neural_loop:L779", exc)
        return StageResult("grow", False,
                           error="morphogenesis apply raised: %s" % str(exc)[:120],
                           evidence=ev)
    promoted = int(gate.get("promoted") or 0)
    ev.update({"staged": staged.get("staged"), "promoted": promoted,
               "gate": {k: gate.get(k) for k in ("decided", "held", "quarantined")
                        if k in gate}})
    return StageResult("grow", True, changed=promoted > 0, evidence=ev)


def _l1_stage(ctx: LoopContext) -> StageResult:
    """Genetic rung: promote a wiring genome that measurably beats the incumbent."""
    mod = _load_script_module("wiring_cycle")
    if mod is None:
        return StageResult("l1", True, changed=False, blocked="wiring_cycle not importable")
    try:
        rep = mod.run_cycle(apply_promotion=bool(ctx.apply))
    except Exception as exc:
        note_failure("neural_loop:L797", exc)
        return StageResult("l1", False, error="wiring cycle raised: %s" % str(exc)[:120])
    # The promotion decision alone does not say whether the SEARCH is going
    # anywhere: `promoted=False` is equally consistent with "nothing beat the
    # incumbent" and "every candidate is competing on one axis that cannot
    # improve anything". The audit answers that from the lineage ledger, the
    # genome's own dimensions, and an A/B of the dispatch order the genome
    # actually produces -- so the evidence next to `promoted` is about the
    # search, not about the file. It never records a decision (`record=False`).
    genome: Dict[str, Any] = {}
    try:
        from CORE import l1_wiring_audit
        genome = l1_wiring_audit.compact(l1_wiring_audit.audit())
    except Exception as exc:
        note_failure("neural_loop:L810", exc)
        genome = {"verdict": "AUDIT_FAILED",
                  "error": "%s: %s" % (type(exc).__name__, str(exc)[:100])}
    return StageResult("l1", bool(rep.get("ok", True)), changed=bool(rep.get("promoted")),
                       evidence={"promoted": rep.get("promoted"),
                                 "trials": rep.get("trials"),
                                 "streak": (rep.get("streak") or {}).get("wins"),
                                 "genome": genome})


def _l2_stage(ctx: LoopContext) -> StageResult:
    """Parametric rung: recalibrate the loop's own constants.

    The candidate generator is separate from the gate -- `step()` proposes, and
    only `apply_promotion=True` writes -- so incident-rate proposals are drained
    every cycle without an incident-rate promotion. The dwell/canary state
    inside the hyperopt cycle is what stops it oscillating between two
    objectives that are within noise of each other.
    """
    if not ctx.apply:
        # `step(apply_promotion=False)` still appends a LINEAGE ROW before it
        # returns, so calling it in a dry run is a durable write wearing the
        # name of a proposal. Found by the test that asserts every write stage
        # is blocked in a dry run: l2 reported no block and had written.
        return StageResult("l2", True, changed=False,
                           blocked="dry-run (apply=False)")
    mod = _load_script_module("hyperopt_cycle")
    if mod is None:
        return StageResult("l2", True, changed=False,
                           blocked="hyperopt_cycle not importable")
    try:
        rep = mod.step(apply_promotion=bool(ctx.apply))
    except Exception as exc:
        note_failure("neural_loop:L842", exc)
        return StageResult("l2", False, error="hyperopt step raised: %s" % str(exc)[:120])
    obj = rep.get("objective") or {}
    return StageResult(
        "l2", True, changed=bool(rep.get("promoted")),
        evidence={"promoted": bool(rep.get("promoted")),
                  "objective": obj.get("value"),
                  "canary": (rep.get("canary") or {}).get("status"),
                  "gen": (rep.get("candidate") or {}).get("generation")},
    )


def _disk_weights() -> Dict[str, float]:
    """The weights as they exist ON DISK -- a read independent of this process.

    Reading the durable artifact rather than the in-memory table is the whole
    point of the durability check: an update that lives only in this process is
    exactly what a closure must not accept.
    """
    from CORE import spine_registry as sr
    try:
        payload = json.loads(Path(sr._STATE_FILE).read_text(encoding="utf-8"))
    except Exception as exc:
        note_failure("neural_loop:L864", exc)
        return {}
    return {k: float(v) for k, v in (payload.get("q_weights") or {}).items()}


def _stage_persist(ctx: LoopContext) -> StageResult:
    """Write the weights to disk. Without this the cycle learns and forgets.

    MEASURED, and the reason this stage exists: the first full end-to-end cycle
    moved the table from 56 edges to 264 in memory and wrote 232 graded
    outcomes, and a fresh process afterwards still read **56 edges**. The whole
    cycle's learning was lost at interpreter exit, because nothing called
    `save_spine_state()` -- even though the spine's own cycle documents
    persistence as step 4 of its flow and does it. A loop that learns in memory
    is a loop that learns nothing across processes, and it reports success the
    whole time.
    """
    from CORE import spine_registry as sr
    if not ctx.apply:
        return StageResult("persist", True, changed=False,
                           blocked="dry-run (apply=False)")
    before_weights = _disk_weights()
    before = len(before_weights)
    before_sig = weights_signature(before_weights)
    try:
        saved = sr.save_spine_state() or {}
    except Exception as exc:
        note_failure("neural_loop:L890", exc)
        return StageResult("persist", False,
                           error="save_spine_state raised: %s" % str(exc)[:140])
    after_weights = _disk_weights()
    after = len(after_weights)
    after_sig = weights_signature(after_weights)
    return StageResult(
        "persist", True, changed=after_sig != before_sig,
        evidence={"edges_on_disk_before": before, "edges_on_disk_after": after,
                  "sig_before": before_sig, "sig_after": after_sig,
                  "saved": {k: saved.get(k) for k in ("keys", "path", "bytes")
                            if isinstance(saved, dict) and k in saved}},
    )


def _stage_verify(ctx: LoopContext) -> StageResult:
    """CLOSURE. The stage that makes an inert cycle a failure.

    This is the one stage whose absence made every other stage's report
    meaningless: without it, a fire that ran fifteen jobs and changed nothing
    durable is indistinguishable from a fire that learned. Re-reading the
    weights (rather than trusting `changed` flags) is deliberate -- a stage's
    self-report is a claim, and the table is the evidence.
    """
    from CORE import spine_registry as sr
    ctx.state_after = state_fingerprint(sr.get_q_weights())
    moved = _fingerprint_moved(ctx.state_before, ctx.state_after)
    # Durability is part of the closure. An applying cycle whose learning is not
    # on disk has not closed: the next process starts from the old table, so
    # "it moved" without "it stuck" is not progress. The disk is read
    # independently of the in-memory table this process just wrote.
    in_memory = sr.get_q_weights()
    on_disk = _disk_weights()
    # Value digests, both sides. Comparing per-key floats with a tolerance was
    # the first attempt and it was weak in the same way edge-counting is: it
    # could only speak about keys present in BOTH tables, so a key that existed
    # in memory and was never written looked identical to one that was.
    durable = bool(on_disk) and (weights_signature(on_disk)
                                 == weights_signature(in_memory))
    if ctx.apply and moved and not durable:
        return StageResult(
            "verify", False, changed=False,
            error="the cycle moved the weights but they are NOT durable: the saved "
                  "table does not match what this process learned",
            evidence={"edges_in_memory": len(in_memory), "edges_on_disk": len(on_disk),
                      "sig_memory": weights_signature(in_memory),
                      "sig_disk": weights_signature(on_disk)})
    claimed = ctx.any_changed()
    # A stage claiming a durable change the table does not show is the more
    # dangerous of the two disagreements, so it is reported as a failure.
    if claimed and not moved:
        return StageResult(
            "verify", False, changed=False,
            error="a stage claimed a durable change the weights do not show",
            evidence={"before": ctx.state_before, "after": ctx.state_after})
    if not ctx.apply:
        return StageResult("verify", True, changed=False,
                           blocked="dry-run (apply=False): no write attempted",
                           evidence={"before": ctx.state_before, "after": ctx.state_after})
    return StageResult(
        "verify", moved, changed=moved,
        error="" if moved else "inert cycle: no stage changed the substrate",
        evidence={"before": ctx.state_before, "after": ctx.state_after,
                  "durable": durable, "edges_on_disk": len(on_disk),
                  "delta_l2": round(float(ctx.state_after.get("l2") or 0.0)
                                    - float(ctx.state_before.get("l2") or 0.0), 6)})


#: The real adapters, in causal order. Keys MUST match `STAGES`; `Loop` asserts
#: that at construction so a typo is a startup error and not a silently missing
#: stage that `verify` would then have to notice from the outside.
REAL_STAGES: Dict[str, Callable[[LoopContext], StageResult]] = {
    "load": _stage_load,
    "perceive": _stage_perceive,
    "credit": _stage_credit,
    "plasticity": _stage_plasticity,
    "route": _stage_route,
    "act": _stage_act,
    "grade": _stage_grade,
    "consolidate": _stage_consolidate,
    "prune": _stage_prune,
    "grow": _stage_grow,
    "l1": _l1_stage,
    "l2": _l2_stage,
    "persist": _stage_persist,
    "verify": _stage_verify,
}


def default_stages() -> Dict[str, Callable[[LoopContext], StageResult]]:
    return dict(REAL_STAGES)


def _load_script_module(name: str):
    """Import a module from `scripts/` without polluting `sys.path` globally."""
    import importlib.util, sys
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / ("%s.py" % name)
    if not path.exists():
        return None
    mod_name = "azazael_script_%s" % name
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(path))
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    except Exception as exc:
        note_failure("neural_loop:L999", exc)
        sys.modules.pop(mod_name, None)
        return None


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

class Loop:
    """Runs the stages in `STAGES` order and reports what each one did."""

    def __init__(self, stages: Optional[Dict[str, Callable[[LoopContext], StageResult]]] = None):
        self.stages = stages if stages is not None else default_stages()
        missing = [s for s in STAGES if s not in self.stages]
        if missing:
            raise ValueError("loop is missing stages: %s" % ", ".join(missing))
        extra = [k for k in self.stages if k not in STAGES]
        if extra:
            raise ValueError("loop has unknown stages: %s" % ", ".join(sorted(extra)))

    def run(self, ctx: Optional[LoopContext] = None) -> Dict[str, Any]:
        ctx = ctx or LoopContext()
        for name in STAGES:
            fn = self.stages[name]
            t0 = time.perf_counter()
            try:
                res = fn(ctx)
            except Exception as exc:
                note_failure("neural_loop:L1027", exc)
                res = StageResult(name, ok=False,
                                  error="unhandled: %s" % str(exc)[:160])
            res.ms = (time.perf_counter() - t0) * 1000.0
            ctx.results.append(res)
            # `act` may have executed organs the later stages grade; a hard
            # failure there is reported but does NOT abort the cycle, because
            # the grading and the closure still have evidence to produce.
        return self.report(ctx)

    @staticmethod
    def report(ctx: LoopContext) -> Dict[str, Any]:
        by = {r.stage: r for r in ctx.results}
        blocked = {n: r.blocked for n, r in by.items() if r.blocked}
        failed = [r.stage for r in ctx.results if not r.ok]
        changed = [r.stage for r in ctx.results if r.changed]

        # Verdict. The distinction that matters: an APPLY cycle that changed
        # nothing is INERT (a failure), while a DRY cycle that changed nothing
        # did exactly what it was asked. The first version computed `inert` as
        # "nothing changed AND a write stage was blocked", which let an apply
        # cycle that blocked NOTHING and changed NOTHING fall through to OK --
        # i.e. the precise case this loop exists to name, reported as healthy.
        if failed and failed != ["verify"]:
            verdict = "FAIL"
        elif failed:                     # verify alone: the closure did not pass
            verdict = "INERT"
        elif changed:
            verdict = "OK"
        elif ctx.apply:
            verdict = "INERT"
        else:
            verdict = "DRY"
        return {
            "version": VERSION,
            "at": datetime.now().isoformat(timespec="seconds"),
            "apply": ctx.apply,
            "verdict": verdict,
            # `ok` follows the VERDICT, not merely "no stage raised". An INERT
            # applying cycle raised nothing and still did not do its job; a
            # caller reading `ok` must not be told otherwise.
            "ok": verdict in ("OK", "DRY"),
            "changed_stages": changed,
            "failed_stages": failed,
            "blocked_stages": blocked,
            "state_before": ctx.state_before,
            "state_after": ctx.state_after,
            "stages": [r.to_dict() for r in ctx.results],
            "notes": ctx.notes,
            "ms": round((time.time() - ctx.started) * 1000.0, 1),
        }


def run_cycle(apply: bool = False, limit: int = 200,
              stages: Optional[Dict[str, Callable[[LoopContext], StageResult]]] = None,
              ledger: Optional[str] = None) -> Dict[str, Any]:
    """Run one full cycle. `apply=False` runs the whole causal chain but writes nothing."""
    ctx = LoopContext(apply=apply, limit=limit)
    rep = Loop(stages).run(ctx)
    if ledger:
        _append_ledger(ledger, rep)
    return rep


def _append_ledger(path: str, rep: Dict[str, Any]) -> None:
    """One row per cycle. Never raises: a ledger failure must not fail a cycle."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": rep.get("at"), "apply": rep.get("apply"),
                                 "verdict": rep.get("verdict"),
                                 "changed": rep.get("changed_stages"),
                                 "blocked": list(rep.get("blocked_stages") or {}),
                                 "before": rep.get("state_before"),
                                 "after": rep.get("state_after")},
                                sort_keys=True) + "\n")
    except Exception as exc:
        note_failure("neural_loop:L1104", exc)
