"""Wiring genome — the codebase as evolvable state (genotype rung L1).

WHY THIS EXISTS (2026-09-21)
----------------------------
The architecture could not self-evolve, and the reason is not a wiring bug. The
system states it itself, in `state/morph_policy.json`:

    "search_space": {"addable": [], "base_genes": [
        "discount_factor", "epsilon", "epsilon_decay", "learning_rate"]},
    "rotation": {"reason": "no active candidate and the search space is closed",
                 "search_space_closed": true,
                 "retired_genes": ["use_schema_features"]}

The genome was FOUR FLOATS. Its one extension candidate was tested and retired
(n=8, mean -10.64, p_hurts 0.004). `state/tensor_genome.spec.json` is a surrogate
MLP whose input is those same four floats - a fitness predictor, not a wider
genotype, and `"trained": false`.

So "the state is the entire codebase" cannot be reached by tuning harder. The
search space has to be opened, and it has to be opened at a rung where
mutation cannot corrupt the running system.

THE LADDER (each rung reuses rails this repo already has)

    L0  4 RL floats                value substitution        CLOSED (above)
    L1  WIRING GENOME  <-- here     enable/disable/rewire     always valid
    L2  constants (thresholds)      bounded value substitution
    L3  module source (organs)      template instantiation    (MorphogenesisWorm)
    L4  free-form source mutation   AST / LLM patch           last, behind L1-L3

Why L1 first: it is the only rung where a mutation is ALWAYS syntactically valid
(it is registry DATA, not Python), trivially reversible (restore one dict), and
already has a validity instrument in this repo - `scripts/audit_wiring.py`
reports dead entry points, so a variant that wires nothing is detectable before
it runs. Largest behavioural change per unit of risk.

WHAT A GENOME IS HERE
---------------------
The phenotype is the FLEET: which registered organs run, in what order, with
what budget. The genotype is the data that produces it:

    active      - which organs participate
    priority    - per-organ spawn priority (1-10)
    cooldown_s  - per-organ minimum interval
    budget_ms   - per-organ execution budget

That is genuinely "the codebase as state" in the only sense that is safe to
mutate automatically: its COMPOSITION. Emitting Python is rung L3 and belongs
behind the containment pen, not here.

THE ONE INVARIANT THAT MATTERS MOST
-----------------------------------
**The modifier is not evolvable.** Organs that implement the loop, the gates,
the quarantines and the audits are excluded from the genome by
construction - `INFRA_EXCLUDED` and `_INFRA_PATTERNS`. A system that can mutate
the thing that decides whether its mutations are safe has no safety property at
all. This is the same principle the spawn decision already obeys ("ordering
only - a policy able to drop a worm could starve the fleet"): self-preservation
of the evaluating machinery is a hard constraint, not a fitness term.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ---------------------------------------------------------------------------
# The invariant: the modifier must not be evolvable
# ---------------------------------------------------------------------------

# Named organs that implement the loop, its gates, or its audits.
# NOTE the distinction this list lives on: it holds organs that decide whether
# a mutation is SAFE, not organs that audit a domain. `PortAuditor` is a domain
# auditor and is deliberately evolvable.
INFRA_EXCLUDED = frozenset({
    "SpineCycleWorm", "MorphogenesisWorm", "HomeostatWorm",
    "EvolutionIntegrityWorm", "MetaCognitionWorm",
    "ClusterManager", "ClusterWORMZ", "MetaCluster", "WormFleet",
    "ConcurrencyManager", "ErrorRecoveryOrchestrator",
    "QuarantineWorm", "AttestationWorm", "WormGovernance",
})

# Name rules for the same purpose, so a newly added gate is excluded by default
# rather than by remembering to add it here.
#
# PRECISION MATTERS ON BOTH SIDES. A broad rule is not the safe choice: an
# over-broad pattern silently SHRINKS the evolvable pool, which is the same
# defect as `search_space_closed` - the search space closes and nothing says so.
# An earlier revision matched bare `audit`, which excluded the real organ
# `PortAuditor` (it contains "Auditor") and quietly removed it from every
# generation. These patterns therefore describe infrastructure NAMING, and the
# authoritative list remains `INFRA_EXCLUDED`. Verified non-vacuous by
# `test_filter_does_not_exclude_by_substring`.
_INFRA_PATTERNS = (
    re.compile(r"queen", re.I),        # the colour-queen governance set
    re.compile(r"spine", re.I),
    re.compile(r"morphogen", re.I),
    re.compile(r"evolution", re.I),
    re.compile(r"attest", re.I),
    re.compile(r"quarantine", re.I),
    re.compile(r"homeostat", re.I),
    re.compile(r"governance", re.I),
    re.compile(r"wiring", re.I),       # the wiring audit itself
    re.compile(r"integrity", re.I),
    re.compile(r"sentinel", re.I),
    re.compile(r"compliance", re.I),
    # THE BOUNDARY, stated so it can be argued with rather than guessed at:
    # a DOMAIN auditor is evolvable (`PortAuditor` audits ports, `NewAuditWorm`
    # audits whatever its domain is - both are ordinary organs whose behaviour
    # may improve). An auditor of THE LOOP is not (`EvolutionAuditWorm`, `^Audit`
    # as the generic meta-auditor). Only the second kind can invalidate the
    # verdict on a mutation, so only the second kind is excluded.
    re.compile(r"^audit", re.I),
)

# ---------------------------------------------------------------------------
# Bounds and constants
# ---------------------------------------------------------------------------

PRIORITY_LO, PRIORITY_HI = 1, 10
COOLDOWN_LO_S, COOLDOWN_HI_S = 5.0, 3600.0
BUDGET_LO_MS, BUDGET_HI_MS = 100.0, 600_000.0

#: Priority used when a genome carries no override for an organ. Matches the
#: registry default, so a genome with no overrides weights only by the registry.
DEFAULT_PRIORITY = 5

# Coverage floor: mean credit alone would be maximised by activating one
# reliable organ and nothing else, which is starvation dressed as fitness.
MIN_COVERAGE = 0.5
COVERAGE_PENALTY = 1.0

# A genome must keep at least this many organs active to be a fleet at all.
MIN_ACTIVE = 1


def is_evolvable_organ(name: str) -> bool:
    """False for anything implementing the loop, its gates, or its audits."""
    if not name or name in INFRA_EXCLUDED:
        return False
    return not any(p.search(name) for p in _INFRA_PATTERNS)


# ---------------------------------------------------------------------------
# The genotype
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WiringGenome:
    """A composition of the fleet. Immutable, so a mutation is a new genome."""

    active: frozenset
    priority: Mapping[str, int] = field(default_factory=dict)
    cooldown_s: Mapping[str, float] = field(default_factory=dict)
    budget_ms: Mapping[str, float] = field(default_factory=dict)
    generation: int = 0
    parent: Optional[str] = None
    origin: str = "init"

    # -- identity ------------------------------------------------------

    def fingerprint(self) -> str:
        """Stable id over the composition and its overrides.

        Sorted, and deliberately excludes `generation`/`parent`/`origin`: two
        genomes with the same composition are the same genotype however they
        were reached, so lineage cannot masquerade as novelty.
        """
        payload = json.dumps({
            "active": sorted(self.active),
            "priority": dict(sorted(self.priority.items())),
            "cooldown_s": dict(sorted(self.cooldown_s.items())),
            "budget_ms": dict(sorted(self.budget_ms.items())),
        }, sort_keys=True)
        return "wg-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # -- serialisation -------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint(),
            "active": sorted(self.active),
            "priority": dict(sorted(self.priority.items())),
            "cooldown_s": dict(sorted(self.cooldown_s.items())),
            "budget_ms": dict(sorted(self.budget_ms.items())),
            "generation": self.generation,
            "parent": self.parent,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WiringGenome":
        return cls(
            active=frozenset(data.get("active") or ()),
            priority={k: int(v) for k, v in (data.get("priority") or {}).items()},
            cooldown_s={k: float(v)
                        for k, v in (data.get("cooldown_s") or {}).items()},
            budget_ms={k: float(v)
                       for k, v in (data.get("budget_ms") or {}).items()},
            generation=int(data.get("generation", 0)),
            parent=data.get("parent"),
            origin=data.get("origin", "restored"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def seed_genome(registry: Mapping[str, Any], origin: str = "init") -> WiringGenome:
    """The incumbent: every eligible organ active, registry defaults intact.

    The baseline is the CURRENT composition, not a blank slate, so a candidate
    is only ever accepted by beating what is actually running.
    """
    eligible = sorted(n for n in registry if is_evolvable_organ(n))
    return WiringGenome(active=frozenset(eligible), origin=origin)


# ---------------------------------------------------------------------------
# Mutation operators (bounded, reversible, always valid)
# ---------------------------------------------------------------------------

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mutate_flip_active(genome: WiringGenome, eligible: Iterable[str],
                       rng) -> WiringGenome:
    """Add or drop one organ, never below MIN_ACTIVE, never infra."""
    pool = [n for n in eligible if is_evolvable_organ(n)]
    if not pool:
        return genome
    name = rng.choice(sorted(pool))
    active = set(genome.active)
    priority = dict(genome.priority)
    cooldown = dict(genome.cooldown_s)
    budget = dict(genome.budget_ms)
    if name in active:
        if len(active) <= MIN_ACTIVE:
            return genome
        active.discard(name)
        # An override means nothing for an organ that is not in the fleet, so
        # dropping the organ clears its tuning. This keeps the genotype
        # canonical: the same fleet has the same fingerprint however it was
        # reached, and no dead config accumulates in the payload.
        priority.pop(name, None)
        cooldown.pop(name, None)
        budget.pop(name, None)
        origin = f"drop:{name}"
    else:
        active.add(name)
        origin = f"add:{name}"
    return WiringGenome(active=frozenset(active), priority=priority,
                        cooldown_s=cooldown, budget_ms=budget,
                        generation=genome.generation + 1,
                        parent=genome.fingerprint(), origin=origin)


def mutate_bump_priority(genome: WiringGenome, eligible: Iterable[str],
                         rng, step: int = 1) -> WiringGenome:
    """Nudge one ACTIVE organ's priority within bounds.

    Pool is the active fleet, not the whole registry: tuning an organ that is
    not running produces a genome that fails its own validity gate (override
    set for an inactive organ) - an operator must not be able to emit an
    invalid candidate.
    """
    pool = sorted(n for n in genome.active if is_evolvable_organ(n))
    if not pool:
        return genome
    name = rng.choice(pool)
    current = int(genome.priority.get(name, 5))
    new = _clamp(current + rng.choice((-step, step)), PRIORITY_LO, PRIORITY_HI)
    if new == current:
        return genome
    priority = dict(genome.priority)
    priority[name] = new
    return WiringGenome(active=genome.active, priority=priority,
                        cooldown_s=genome.cooldown_s, budget_ms=genome.budget_ms,
                        generation=genome.generation + 1,
                        parent=genome.fingerprint(),
                        origin=f"priority:{name}:{current}->{new}")


def mutate_adjust_cooldown(genome: WiringGenome, eligible: Iterable[str],
                           rng) -> WiringGenome:
    """Scale one ACTIVE organ's cooldown within bounds."""
    pool = sorted(n for n in genome.active if is_evolvable_organ(n))
    if not pool:
        return genome
    name = rng.choice(pool)
    current = float(genome.cooldown_s.get(name, 30.0))
    factor = rng.choice((0.5, 2.0))
    new = _clamp(current * factor, COOLDOWN_LO_S, COOLDOWN_HI_S)
    if abs(new - current) < 1e-9:
        return genome
    cooldown = dict(genome.cooldown_s)
    cooldown[name] = round(new, 3)
    return WiringGenome(active=genome.active, priority=genome.priority,
                        cooldown_s=cooldown, budget_ms=genome.budget_ms,
                        generation=genome.generation + 1,
                        parent=genome.fingerprint(),
                        origin=f"cooldown:{name}:{current:g}->{new:g}")


#: Every L1 mutation operator the genotype supports.
MUTATIONS = (mutate_flip_active, mutate_bump_priority, mutate_adjust_cooldown)

#: Operators whose output the CURRENT objective and effector can actually act on.
#:
#: Found by running the cycle, not by reading the code: with all three operators
#: enabled, 40 live mutations produced 0 accepted candidates, because `fitness`
#: read only `active` and the effector read only `priority` — so a cooldown or
#: budget mutation produced a genome whose score was IDENTICAL to its parent
#: (delta exactly 0.0). Two of three operators were null operations against the
#: objective.
#:
#: That is the same defect class as `search_space_closed`: a search dimension
#: nothing can observe or act on. Restricting the cycle to the observable set is
#: the fix, and the excluded pair is kept named rather than deleted so the
#: condition for adopting them is explicit — each needs BOTH a consumer and a
#: measurable effect before it re-joins the cycle.
OBSERVABLE_MUTATIONS = (mutate_flip_active, mutate_bump_priority)
NOT_YET_OBSERVABLE_MUTATIONS = (mutate_adjust_cooldown,)

#: Genotype fields with NO producer and NO consumer -- declared, not searched.
#:
#: `budget_ms` has no mutation operator at all; `cooldown_s` has one
#: (`mutate_adjust_cooldown`) whose children score EXACTLY their parent on the
#: live objective (best delta +0.00000000 over 60 children, measured 2026-09-21),
#: so nothing can select for or against it. Neither field is read by the L1
#: effector either: `decide_execution_order` ranks on `priority` and demotes on
#: `active`, and grepping the tree for a reader of either field finds none.
#:
#: Named here so the count is honest. `WiringGenome` therefore has TWO live axes
#: (`active`, `priority`), one operator excluded pending a consumer
#: (`cooldown_s`), and one field that is not a dimension at all (`budget_ms`).
#: Reporting four axes with three "dormant" implied they were waiting on time;
#: two of them are waiting on code that does not exist. This is `wire_or_cut`
#: applied to the genotype: cut from the advertised set, kept in the payload so
#: the condition for adopting them stays explicit.
UNWIRED_DIMENSIONS = ("cooldown_s", "budget_ms")


def mutate(genome: WiringGenome, eligible: Iterable[str], rng) -> WiringGenome:
    """Apply one uniformly chosen bounded mutation."""
    return rng.choice(MUTATIONS)(genome, eligible, rng)


# ---------------------------------------------------------------------------
# Validity — the gate a candidate must pass before it is ever scored
# ---------------------------------------------------------------------------

def validate(genome: WiringGenome, registry: Mapping[str, Any]) -> List[str]:
    """Structural problems with a genome. Empty list means valid.

    Kept separate from fitness on purpose: an invalid genome has no fitness, it
    has a defect. Scoring it anyway is how a broken variant wins on a metric.
    """
    problems: List[str] = []

    unknown = sorted(n for n in genome.active if n not in registry)
    if unknown:
        problems.append(f"active organs not in registry: {', '.join(unknown[:8])}")

    infra = sorted(n for n in genome.active if not is_evolvable_organ(n))
    if infra:
        problems.append(
            "INFRASTRUCTURE ORGAN IN GENOME (self-modification of the "
            f"evaluator is forbidden): {', '.join(infra[:8])}")

    if len(genome.active) < MIN_ACTIVE:
        problems.append(f"no active organs (min {MIN_ACTIVE})")

    for name, value in genome.priority.items():
        if not (PRIORITY_LO <= int(value) <= PRIORITY_HI):
            problems.append(f"priority out of bounds for {name}: {value}")
    for name, value in genome.cooldown_s.items():
        if not (COOLDOWN_LO_S <= float(value) <= COOLDOWN_HI_S):
            problems.append(f"cooldown out of bounds for {name}: {value}")
    for name, value in genome.budget_ms.items():
        if not (BUDGET_LO_MS <= float(value) <= BUDGET_HI_MS):
            problems.append(f"budget out of bounds for {name}: {value}")

    for mapping_name in ("priority", "cooldown_s", "budget_ms"):
        overridden = set(getattr(genome, mapping_name))
        stray = sorted(overridden - set(genome.active))
        if stray:
            problems.append(
                f"{mapping_name} set for inactive organs: {', '.join(stray[:8])}")

    return problems


# ---------------------------------------------------------------------------
# Fitness — grounded, never authored
# ---------------------------------------------------------------------------

def _priority_weight(genome: WiringGenome, name: str) -> float:
    """The genome's own priority for an organ, clamped into the legal band."""
    try:
        value = float(genome.priority.get(name, DEFAULT_PRIORITY))
    except (TypeError, ValueError) as exc:
        note_failure("wiring_genome:L406", exc)
        value = float(DEFAULT_PRIORITY)
    return max(float(PRIORITY_LO), min(float(PRIORITY_HI), value))


def fitness(genome: WiringGenome, credit: Mapping[str, float],
            eligible: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Score a composition against OBSERVED per-organ outcomes.

    The rule this repo learned the hard way: the reward must not be a function
    of the state. `credit` comes from `spawn_credit`, which derives it from
    post-hoc execution telemetry - success rate, sample-weighted - so a genome
    cannot improve its score by describing itself better.

    UNOBSERVED ORGANS ARE EXCLUDED FROM THE MEAN, not counted as 0.0. Measured
    on the live registry (2026-09-21: 211 eligible organs, only 15 with any
    observation), counting them as zero made DROPPING an unmeasured organ a
    free improvement - the first 300 mutations 'won' by dropping
    `AutoencoderAnomalyWorm` for +0.000276. A fitness that improves by
    forgetting unmeasured organs would shrink the fleet to the handful that
    happen to have data, which is the opposite of exploring the codebase.

    Coverage remains a FLOOR over the eligible pool, so the genotype can never
    win by activating the single most reliable organ and nothing else.

    PRIORITY IS IN THE OBJECTIVE (2026-09-21 fix). `value` is the
    priority-WEIGHTED mean credit, not the plain mean. Without this the fitness
    was blind to two of the genome's four fields, so `mutate_bump_priority`
    produced children scoring identically to their parents (delta exactly 0.0)
    and the effector's whole ordering dimension was unselectable. The weights
    come from the genome and the credit comes from telemetry, so this stays
    grounded - it is not an authored reward, it is "the credit you actually get
    when the well-ranked organs run first", which is precisely what the effector
    ordering does. When all priorities are equal it reduces to the plain mean, so
    it is monotone in the same quantity and no baseline shifts.
    """
    active = sorted(genome.active)
    pool = sorted(eligible) if eligible is not None else active
    if not active or not pool:
        return {"ok": False, "reason": "no active organs", "value": float("-inf")}

    observed = [n for n in active if n in credit]
    mean_credit = (sum(float(credit[n]) for n in observed) / len(observed)
                   if observed else 0.0)
    weights = [_priority_weight(genome, n) for n in observed]
    total_w = sum(weights)
    ranked_credit = (sum(w * float(credit[n]) for w, n in zip(weights, observed))
                     / total_w if total_w > 0 else 0.0)
    coverage = len(active) / len(pool)
    shortfall = max(0.0, MIN_COVERAGE - coverage)

    return {
        "ok": True,
        "value": ranked_credit - COVERAGE_PENALTY * shortfall,
        "ranked_credit": round(ranked_credit, 6),
        "mean_credit": round(mean_credit, 6),
        "coverage": round(coverage, 6),
        "coverage_shortfall": round(shortfall, 6),
        "n_active": len(active),
        "observed": len(observed),
        # Explicit: a score built on too few observations is not a verdict.
        "evidence_ratio": round(len(observed) / len(active), 6),
    }


def paired_compare(candidate: WiringGenome, incumbent: WiringGenome,
                   credit: Mapping[str, float],
                   eligible: Optional[Iterable[str]] = None,
                   margin: float = 0.0) -> Dict[str, Any]:
    """Compare candidate vs incumbent on the SAME credit snapshot.

    Paired by construction: both are scored on one measurement, so the
    comparison cannot be won by the snapshot drifting between evaluations - the
    estimator defect that let 159/400 rows (39.8%) clear a 0.75 gate earlier in
    this project, because independent draws add noise instead of cancelling it.
    """
    cand = fitness(candidate, credit, eligible)
    base = fitness(incumbent, credit, eligible)
    if not cand.get("ok") or not base.get("ok"):
        return {"ok": False, "reason": "unscorable", "candidate": cand,
                "incumbent": base}
    delta = cand["value"] - base["value"]
    return {
        "ok": True,
        "delta": round(delta, 6),
        "margin": margin,
        "accept": delta > margin,
        "candidate": cand,
        "incumbent": base,
        "candidate_fingerprint": candidate.fingerprint(),
        "incumbent_fingerprint": incumbent.fingerprint(),
    }
