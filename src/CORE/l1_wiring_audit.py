"""Is the L1 wiring genome ADAPTING, or merely being MAINTAINED?

The loop runs `l1` on every cycle and it reports a genome, a generation and a
fitness. None of that says whether the search is *going anywhere*. This module
answers the three questions a genome audit has to answer with evidence:

1. **Is the generation advancing?** A promoted genome whose `generation` never
   grows is a file that is rewritten, not a search that is progressing.
2. **Which axes are actually being searched?** The genome has four dimensions --
   `active`, `priority`, `budget_ms`, `cooldown_s`. A mutation operator that
   exists is not an axis that is exercised: if a promoted genome carries
   `priority == {}` for its whole life, that dimension contributes nothing to the
   phenotype and every candidate is competing on one axis while the search space
   is advertised as four.
3. **Does the genome change anything that gets DONE?** This is the one that
   matters. The L1 effector is `decide_execution_order`, which demotes every
   organ absent from `active` to the tail. A promoted genome whose `active` list
   covers the whole registry has an observable effect of exactly zero.

The measurement for (3) is a real A/B through the real ranking code: order every
Nature's candidates with the promoted genome loaded, then with the genome
resolved away, and compare. `record=False` on both arms -- an audit asking a
hypothetical must not write `spawn_selected` rows into the ledger that exists to
join decisions to outcomes, and replicating the ranking key here instead would
be a second copy of one formula, which is how telemetry comes to disagree with
the thing it describes.

Stdlib only. Never raises: a corrupt genome yields a report saying so.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

VERSION = "1.1.0"

__all__ = ["audit", "compact", "load_promoted", "load_lineage", "effector_effect",
           "dimensions", "objective_health", "history_summary",
           "margin_reachability"]

#: Project root, resolved the same way `scripts/wiring_cycle.py` resolves it, so
#: the audit reads the artifacts the producer actually wrote.
_ROOT = Path(__file__).resolve().parents[2]


def _state_dir() -> Path:
    return Path(os.environ.get("AZAZAEL_WIRING_STATE_DIR") or (_ROOT / "state"))


def _promoted_path() -> Path:
    env = os.environ.get("AZAZAEL_WIRING_PROMOTED")
    if env:
        return Path(env)
    try:
        from CORE import worm_registry as wr
        return Path(wr.WIRING_PROMOTED_PATH)
    except Exception as exc:
        note_failure("l1_wiring_audit:L59", exc)
        return _state_dir() / "wiring_promoted.json"


def load_lineage(limit: int = 4000) -> List[Dict[str, Any]]:
    """Every lineage row the L1 cycle has written. Unparseable rows are skipped."""
    out: List[Dict[str, Any]] = []
    path = _state_dir() / "wiring_lineage.jsonl"
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception as exc:
                    note_failure("l1_wiring_audit:L75", exc)
                    continue
                if isinstance(row, dict):
                    out.append(row)
                    if len(out) >= limit:
                        break
    except Exception as exc:
        note_failure("l1_wiring_audit:L81", exc)
        return []
    return out


def load_promoted() -> Optional[Dict[str, Any]]:
    """The accepted genome, or None. Absent is the correct initial state."""
    try:
        data = json.loads(_promoted_path().read_text(encoding="utf-8"))
    except Exception as exc:
        note_failure("l1_wiring_audit:L90", exc)
        return None
    return data if isinstance(data, dict) else None


#: The genotype's declared fields and the mutation operator for each, or None
#: when no operator exists. Explicit because the categories below are derived
#: from it, and a field added to `WiringGenome` without being listed here should
#: show up as unreported rather than silently counted as an axis.
_AXIS_OPERATOR: Dict[str, Optional[str]] = {
    "active": "mutate_flip_active",
    "priority": "mutate_bump_priority",
    "cooldown_s": "mutate_adjust_cooldown",
    "budget_ms": None,
}


def dimensions(genome: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Which of the genotype's declared fields are actually searched.

    Three categories, and the difference between the last two is the point:

    * **live** -- an operator in `OBSERVABLE_MUTATIONS`: the objective and the
      effector can both act on it, so a child can be selected for or against.
    * **excluded** -- an operator exists but is withheld (`mutate_adjust_cooldown`),
      because its children score EXACTLY their parent on the live objective
      (measured: best delta +0.00000000 over 60 children).
    * **unwired** -- no operator exists at all (`budget_ms`).

    `live` is decided by the CODE, not by whether the current genome happens to
    populate a field: `priority` is live even when the promoted genome carries an
    empty map, because what that means is "no priority child has won yet", which
    is a fact about the search, not about whether the axis exists. The earlier
    version reported all four fields as axes with three "dormant", which implied
    they were waiting on time. Two of them are waiting on code that does not
    exist.
    """
    g = genome or {}
    values = {
        "active": len(g["active"]) if isinstance(g.get("active"), list) else 0,
        "priority": (len(g["priority"])
                     if isinstance(g.get("priority"), dict) else 0),
        "cooldown_s": (len(g["cooldown_s"])
                       if isinstance(g.get("cooldown_s"), dict) else 0),
        "budget_ms": (len(g["budget_ms"])
                      if isinstance(g.get("budget_ms"), dict) else 0),
    }
    try:
        from CORE.WORMS.expansion import wiring_genome as wg
        observable = {op.__name__ for op in wg.OBSERVABLE_MUTATIONS}
        exists = {op.__name__ for op in wg.MUTATIONS}
    except Exception as exc:
        note_failure("l1_wiring_audit:L141", exc)
        observable, exists = set(), set()
    live, excluded, unwired = [], [], []
    for field, op in _AXIS_OPERATOR.items():
        if op is None or op not in exists:
            unwired.append(field)
        elif op in observable:
            live.append(field)
        else:
            excluded.append(field)
    return {"values": values, "live": sorted(live),
            "excluded": sorted(excluded), "unwired": sorted(unwired),
            # Kept because the previous report used it; now it means only the
            # fields that a reader could reasonably call "not yet expressed".
            "dormant": sorted(excluded + unwired)}


def effector_effect(q_weights: Optional[Dict[str, float]] = None,
                    examples: int = 3,
                    registry: Optional[Dict[str, Dict[str, Any]]] = None,
                    ) -> Dict[str, Any]:
    """A/B the real ranking with the promoted genome and without it.

    "Without it" is produced by resolving `WIRING_PROMOTED_PATH` to a path that
    does not exist, so the genome loader returns None through its own fail-open
    path. The comparison therefore exercises the production code on both arms
    rather than re-implementing the ranking here.

    `registry` is the settled fleet to measure. Callers that run other stages
    first (see `audit()`) pass the snapshot they took at entry, because the
    live `WORM_REGISTRY` global does not hold still: importing a `CORE.WORMS`
    package registers worms through `@register_worm` (worm_registry.py:479) and
    `discover_worms()` writes the remainder. MEASURED 2026-09-21: an audit of a
    2-worm registry read 113 organs here and left 234 behind, so it reported 111
    organs demoted by a genome that could touch neither of its two worms. A
    measurement that rewrites the table it reads is not a measurement.
    """
    result: Dict[str, Any] = {
        "ok": False, "natures_with_choices": 0, "natures_reordered": 0,
        "first_slot_changes": 0, "positions_changed": 0,
        "demoted_organs": 0, "registry_size": 0, "examples": [],
        "recorded": False, "reason": "",
    }
    try:
        from CORE import worm_registry as wr
        from CORE.worm_registry import WORM_REGISTRY, discover_worms
        if registry is None:
            if not WORM_REGISTRY:
                discover_worms()
            registry = dict(WORM_REGISTRY)
        if not registry:
            result["reason"] = "registry empty after discover_worms()"
            return result
        result["registry_size"] = len(registry)

        genome = load_promoted()
        active = genome.get("active") if isinstance(genome, dict) else None
        if isinstance(active, list) and active:
            aset = set(active)
            result["demoted_organs"] = sum(1 for n in registry
                                           if n not in aset)

        by_nature: Dict[str, List[str]] = {}
        for name, entry in registry.items():
            nat = (entry or {}).get("category") or "unknown"
            by_nature.setdefault(str(nat), []).append(name)

        if q_weights is None:
            try:
                from CORE import spine_registry as sr
                q_weights = sr.get_q_weights()
            except Exception as exc:
                note_failure("l1_wiring_audit:L212", exc)
                q_weights = {}

        engine = wr.SpawnDiversityEngine()
        real_path = wr.WIRING_PROMOTED_PATH
        absent = os.path.join(os.path.dirname(real_path), "__no_genome__.json")
        try:
            os.remove(absent)
        except OSError as exc:
            note_failure("l1_wiring_audit:L220", exc)
        try:
            for nat, cands in by_nature.items():
                if len(cands) < 2:
                    continue          # one candidate: no order to change
                result["natures_with_choices"] += 1
                with_g, _ = engine.decide_execution_order(
                    nat, cands, q_weights=q_weights, record=False)
                wr.WIRING_PROMOTED_PATH = absent
                try:
                    without, _ = engine.decide_execution_order(
                        nat, cands, q_weights=q_weights, record=False)
                finally:
                    wr.WIRING_PROMOTED_PATH = real_path
                if with_g == without:
                    continue
                pos = sum(1 for a, b in zip(with_g, without) if a != b)
                result["natures_reordered"] += 1
                result["positions_changed"] += pos
                if with_g and without and with_g[0] != without[0]:
                    result["first_slot_changes"] += 1
                    if len(result["examples"]) < examples:
                        result["examples"].append({
                            "nature": nat, "with_genome_first": with_g[0],
                            "without_genome_first": without[0],
                            "positions_changed": pos})
        finally:
            wr.WIRING_PROMOTED_PATH = real_path
        result["ok"] = True
    except Exception as exc:
        note_failure("l1_wiring_audit:L250", exc)
        result["reason"] = "%s: %s" % (type(exc).__name__, str(exc)[:140])
    return result


def objective_health() -> Dict[str, Any]:
    """The outer loop's ability to SELECT, which the genome audit depends on.

    A genome audit answers "is the search going anywhere?". If the objective the
    search is scored against is a mathematical constant, then no answer to that
    question is meaningful -- and this was not hypothetical: measured
    2026-09-21, `resolution` and `coverage` were pinned at their clamps, `success`
    was being read through an attribute that does not exist, and the loop
    reported `objective=0.65, canary=held` for its entire life while installing
    every promotion as an unselectable random walk.

    Reported here rather than only inside the hyperopt cycle because the verdict
    that matters is the WHOLE-STACK one: `ADAPTING_PARTIAL` was being printed by
    a search that could not have distinguished its own candidates.
    """
    out: Dict[str, Any] = {"ok": False, "reachable": None, "reason": "",
                           "value": None, "canary_tolerance": None,
                           "movable_terms": [], "pinned_terms": [],
                           "unmeasured_terms": [], "warnings": [],
                           "historical": {}}
    try:
        from CORE.WORMS.expansion import hyperopt as hyp
        health = hyp.objective_health()
        out.update({
            "ok": bool(health.get("ok")),
            "reachable": health.get("reachable"),
            "reason": health.get("reason"),
            "value": health.get("value"),
            "canary_tolerance": health.get("canary_tolerance"),
            "movable_terms": health.get("movable_terms") or [],
            "pinned_terms": health.get("pinned_terms") or [],
            "unmeasured_terms": health.get("unmeasured_terms") or [],
            "pinned_weight": health.get("pinned_weight"),
            "terms": health.get("terms"),
            "warnings": health.get("warnings") or [],
            "historical": health.get("historical") or {},
        })
    except Exception as exc:
        note_failure("l1_wiring_audit:L292", exc)
        out["reason"] = "%s: %s" % (type(exc).__name__, str(exc)[:140])
    return out


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        note_failure("l1_wiring_audit:L300", exc)
        return None


def history_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Promotion cadence from the lineage ledger itself, not from the genome."""
    l1 = [r for r in rows if str(r.get("cycle_kind") or "l1") == "l1"]
    promoted = [r for r in l1 if r.get("promoted")]
    # A dry evaluation of the same candidates and a cycle that could promote both
    # write a row (the append sits in a `finally`). Counting them together would
    # overstate how much search has happened, so they are split -- and rows from
    # before the `apply` field existed are reported as unknown rather than
    # assumed to be either.
    applied = sum(1 for r in l1 if r.get("apply") is True)
    dry = sum(1 for r in l1 if r.get("apply") is False)
    unknown = len(l1) - applied - dry
    reasons: Dict[str, int] = {}
    for r in l1:
        key = str(r.get("reason") or "unknown")
        reasons[key] = reasons.get(key, 0) + 1
    stamps = [str(r.get("at")) for r in l1 if r.get("at")]
    promoted_at = [str(r.get("at")) for r in promoted if r.get("at")]
    best = [r.get("best") for r in promoted]
    return {
        "rows": len(l1),
        "applied_rows": applied,
        "dry_rows": dry,
        "unknown_apply_rows": unknown,
        "promotions": len(promoted),
        "rejections": len(l1) - len(promoted),
        "by_reason": reasons,
        "first_at": min(stamps) if stamps else None,
        "last_at": max(stamps) if stamps else None,
        "last_promoted_at": max(promoted_at) if promoted_at else None,
        "promoted_fingerprints": [b for b in best if b],
    }


def margin_reachability(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Can the L1 gate's effect floor be cleared at all?

    This is the measurement whose absence let a mis-scaled floor disable the
    search for 33 cycles in silence. Every one of those cycles reported `ok`,
    `promoted: false` and `no candidate beat the incumbent` -- a reading
    identical to a mature optimum. The difference is visible in two numbers the
    lineage did not record until now: the floor, and the best delta that was
    compared against it.

    `blocked` means no recorded cycle had a best delta above its floor, i.e. the
    gate is unreachable and the search is not slow, it is stopped.
    """
    samples, reachable, deltas, margins = 0, 0, [], []
    for r in rows:
        if r.get("best_delta") is None or r.get("margin") is None:
            continue
        try:
            d = float(r["best_delta"])
            m = float(r["margin"])
        except (TypeError, ValueError) as exc:
            note_failure("l1_wiring_audit:L358", exc)
            continue
        samples += 1
        deltas.append(d)
        margins.append(m)
        if d > m:
            reachable += 1
    out: Dict[str, Any] = {
        "samples": samples, "reachable_rows": reachable,
        "unmeasured_rows": sum(1 for r in rows if r.get("margin") is None),
        "max_best_delta": max(deltas) if deltas else None,
        "latest_margin": margins[-1] if margins else None,
        "blocked": bool(samples) and reachable == 0,
    }
    if out["max_best_delta"] is not None and out["latest_margin"]:
        out["headroom"] = round(out["max_best_delta"] / out["latest_margin"], 6)
    return out


def audit(q_weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """The full report. Never raises.

    Every stage below reports on ONE registry view, snapshotted here before any
    stage runs. This is not tidiness: `dimensions()` imports
    `CORE.WORMS.expansion.wiring_genome`, which drags in the worm packages, and
    their `@register_worm` decorators register the fleet into whatever dict is
    bound to the global. Stages that read the live global therefore disagreed
    with each other and with the registry they were handed (measured
    2026-09-21: 2 at entry, 113 in `effector`, 234 at exit), which is how a
    genome that changed nothing was reported as having demoted 111 organs.
    """
    from CORE.worm_registry import WORM_REGISTRY, discover_worms
    if not WORM_REGISTRY:
        discover_worms()
    registry = dict(WORM_REGISTRY)

    genome = load_promoted()
    lineage = load_lineage()
    hist = history_summary(lineage)
    dims = dimensions(genome)
    eff = effector_effect(q_weights, registry=registry)
    margin = margin_reachability(lineage)
    objective_block = objective_health()

    fingerprint = (genome or {}).get("fingerprint")
    parent = (genome or {}).get("parent")
    generation = (genome or {}).get("generation")
    promoted_fps = hist.get("promoted_fingerprints") or []
    # A promotion that reappears in the ledger, and a genome that names a parent
    # it does not equal, are the two ways to tell "advanced" from "rewritten".
    advanced = bool(genome) and (
        str(parent or "") != str(fingerprint or "")
        or len(promoted_fps) > 1)
    paired = _coerce_float((genome or {}).get("paired_delta"))

    if not genome:
        verdict = "NO_GENOME"
    elif margin.get("blocked"):
        # Checked BEFORE the effector questions: a gate that cannot be cleared
        # makes every other measurement moot, and this is the verdict whose
        # absence made 33 dead cycles read as `ok`.
        verdict = "SEARCH_BLOCKED_BY_MARGIN"
    elif objective_block.get("ok") and objective_block.get("reachable") is False:
        # Also before the effector questions, and for the same reason one level
        # up: if the objective every candidate is scored against cannot move,
        # then "reordering N natures" describes a mutation nothing could have
        # selected for. This is the verdict that was `ADAPTING_PARTIAL`.
        verdict = "NOT_LEARNING_OBJECTIVE_PINNED"
    elif not eff.get("ok") or not eff.get("natures_with_choices"):
        verdict = "UNMEASURABLE"
    elif eff.get("positions_changed", 0) == 0:
        # Promoted, and changes nothing that gets done.
        verdict = "MAINTENANCE"
    elif len(dims["live"]) <= 1:
        verdict = "ADAPTING_ONE_AXIS"
    elif dims["excluded"] or dims["unwired"]:
        verdict = "ADAPTING_PARTIAL"
    else:
        verdict = "ADAPTING"

    return {
        "version": VERSION,
        "at": datetime.now().isoformat(timespec="seconds"),
        "path": str(_promoted_path()),
        "promoted": {
            "present": bool(genome),
            "fingerprint": fingerprint,
            "generation": generation,
            "parent": parent,
            "origin": (genome or {}).get("origin"),
            "paired_delta": paired,
            "promoted_at": (genome or {}).get("promoted_at"),
            "promoted_by": (genome or {}).get("promoted_by"),
            "generation_advanced": advanced,
            "differs_from_parent": bool(genome)
            and str(parent or "") != str(fingerprint or ""),
        },
        "dimensions": dims,
        "history": hist,
        "effector": eff,
        "margin": margin,
        "objective": objective_block,
        "verdict": verdict,
    }


def compact(report: Dict[str, Any]) -> Dict[str, Any]:
    """The few fields a loop stage wants in its evidence, not the whole report."""
    p = report.get("promoted") or {}
    e = report.get("effector") or {}
    dims = report.get("dimensions") or {}
    m = report.get("margin") or {}
    ob = report.get("objective") or {}
    return {
        "verdict": report.get("verdict"),
        "objective_reachable": ob.get("reachable"),
        "objective_movable_terms": ob.get("movable_terms"),
        "objective_pinned_terms": ob.get("pinned_terms"),
        "objective_canary_tolerance": ob.get("canary_tolerance"),
        "genome": p.get("fingerprint"),
        "generation": p.get("generation"),
        "generation_advanced": p.get("generation_advanced"),
        "live_axes": dims.get("live"),
        "excluded_axes": dims.get("excluded"),
        "unwired_axes": dims.get("unwired"),
        "dormant_axes": dims.get("dormant"),
        "promotions": (report.get("history") or {}).get("promotions"),
        "margin_blocked": m.get("blocked"),
        "margin": m.get("latest_margin"),
        "max_best_delta": m.get("max_best_delta"),
        "natures_reordered": e.get("natures_reordered"),
        "first_slot_changes": e.get("first_slot_changes"),
        "demoted_organs": e.get("demoted_organs"),
    }
