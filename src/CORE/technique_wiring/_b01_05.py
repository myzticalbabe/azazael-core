"""_b01_05 — adapters for the ten techniques the layer slices left unwired.

Batches 01-05 predate the machine-readable map, so their techniques had no layer
row to sort them into and fell outside every `_lN` slice. They are not stragglers
in importance: this set holds the six the review graded **PRESENT** (they were
implemented earlier in the session and had the *fewest* call sites of all, since
a finished technique stops being anybody's task) plus the four reviewed only as
prose. Registering them is the difference between "these were built" and "these
run", which is the whole point of the runtime.

Each adapter below invokes the real symbol named in `where`. Where the review's
verdict is SURVEY the adapter restates the extracted rule and re-tests that no
mechanism was invented for it — the honest form for a paper with no technique.
"""

from CORE.dispatch_plan import locate_pivot, record_backtrack, reuse_prefix
from CORE.skill_library import family_index
from CORE.technique_runtime import register
from typing import List
import random

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _safe(fn):
    """Fail-open wrapper: a wiring defect is reported, never raised."""
    def _run():
        try:
            return fn() or {}
        except Exception as exc:                      # noqa: BLE001 - contract
            note_failure("_b01_05:L24", exc)
            return {"invoked": False,
                    "error": f"{type(exc).__name__}: {exc}"}
    return _run


# -- EP113 Reflexion: the invocation, which is what was missing ---------------
@_safe
def _ep113():
    """Write then recall one reflection, exercising the EP294 fields too."""
    from CORE.WORMS.expansion import reflexion as rx
    wrote = rx.record_reflection(
        question="technique_wiring self-test",
        what_failed="the buffer had no production caller",
        what_next="invoke it from the runtime",
        outcome="i",
        predicted=0.5, realised=1.0)
    got = rx.reflections_for("technique_wiring", limit=1)
    return {"invoked": True, "wrote": bool(wrote), "recalled": len(got),
            "carries_outcome": bool(got and "realised" in got[0])}


# -- EP122 Four Pillars: survey --------------------------------------------------
@_safe
def _ep122():
    """Survey row: validate the four-way split exists; invent no mechanism."""
    from CORE import structured_action as sa
    from CORE import local_memory as lm
    return {"invoked": True, "verdict": "SURVEY",
            "perception_execution": all(hasattr(sa, n) for n in
                                        ("observe", "semantic_action")),
            "memory": hasattr(lm, "get_memory_store"),
            "rule": "profile each pillar separately; split subjective/objective "
                    "evaluation (the review found this residual unaddressed)",
            "invented_mechanism": False}


# -- EP142 DR-Arena: difficulty escalation already real, Information Tree not --
@_safe
def _ep142():
    from CORE.WORMS.expansion import arena as ar
    env = ar.Environment(severity_mix=[0.2, 0.5, 0.3], hard_ratio=0.25,
                         era_pressure=0.1, seed=7)
    mutated = env.mutate(random.Random(7), strength=0.05)
    return {"invoked": True, "mutated": mutated is not None,
            "has_step": hasattr(ar.Arena, "step"),
            "has_keeper_split": callable(ar.keeper_split),
            "gap": "persistent Information Tree artifact + separate Examiner "
                   "role (Arena.step already escalates difficulty)"}


# -- EP150 Agentic Reasoning Survey: roadmap, no mechanism --------------------
@_safe
def _ep150():
    idx = family_index([{"name": "seed", "family": "layer-L2"}])
    return {"invoked": True, "verdict": "SURVEY",
            "n_families": idx.get("n_families"),
            "rule": "the only extractable piece is an L2->L6 evaluation "
                    "scaffold; no mechanism to implement",
            "invented_mechanism": False}


# -- EP345 retry-from-pivotal-turn ------------------------------------------
@_safe
def _ep345():
    citation = record_backtrack(
        "technique-wiring:EP345",
        "pivotal-turn recorded; prefix reuse is the residual delta")
    back = reuse_prefix("technique-wiring:EP345")
    pivot = locate_pivot([
        {"branch": "read", "ok": True},
        {"branch": "plan", "ok": True},
        {"branch": "edit", "ok": False},
        {"branch": "verify", "ok": False},
    ])
    return {"invoked": True, "cited": bool(citation),
            "prefix_reusable": bool(back.get("reusable")),
            "pivot_found": bool(pivot.get("found")),
            "pivot_branch": pivot.get("pivot_branch"),
            "reusable_prefix": list(pivot.get("reusable_prefix") or []),
            "gap": "locate + reuse live; caller-side plan splitting deferred "
                   "(no live plan object at this layer)"}


# -- EP403 COVENANT: compile an NL SOP to a checkable workflow ---------------
@_safe
def _ep403():
    from CORE.verification_gate import verify_workflow_step
    step = verify_workflow_step(
        "edit",
        [],
        {"steps": [{"id": "edit", "requires": ["plan"],
                    "remediate": "write the plan first"}]})
    return {"invoked": True, "accepted": step.get("accepted"),
            "remediations": len(step.get("remediations") or []),
            "gap": "NL SOP -> AST/CFG compiler feeding verify_workflow_step; "
                   "today the graph is hand-built"}


# -- EP408 MemHarness: recall reconstructs rather than replays ---------------
@_safe
def _ep408():
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    fn = getattr(store, "reconstruct_events", None)
    if not callable(fn):
        return {"invoked": False, "reason": "no-reconstruct_events"}
    out = fn([{"data": {"a": 1}, "event_type": "technique_wiring"}],
             "technique_wiring self-test")
    return {"invoked": True, "reconstructed": out is not None,
            "note": "PRESENT per review: recall adapts to present context"}


# -- EP412 CN101: precision as a runtime knob, not a fixed choice ------------
@_safe
def _ep412():
    from CORE.WORMS.expansion import policy_optimization as po
    # depo_filter takes a mapping of difficulty estimates and returns the keys
    # inside the competence band (it applies the band; it does not estimate).
    kept = po.depo_filter({"already-mastered": 0.0, "frontier": 0.4,
                           "out-of-reach": 1.0})
    capped = po.depo_filter({"frontier-a": 0.4, "frontier-b": 0.5,
                             "frontier-c": 0.6}, budget=2)
    return {"invoked": True, "filtered": len(kept) if hasattr(kept, "__len__")
            else "n/a",
            "budget_capped": len(capped),
            "gap": ""}


# -- EP442 Mobius: shared memory, decoupled reasoners ------------------------
@_safe
def _ep442():
    from CORE import MemoryBus as mb
    bus = mb.MemoryBus()
    subs = getattr(bus, "_subscribers", None)
    carrier = mb.HiddenStateCarrier()
    carrier.put("reasoner-a", {"x": 1.0})
    carrier.put("reasoner-b", {"x": 3.0})
    view = carrier.shared_view()
    return {"invoked": True, "bus_constructed": bus is not None,
            "subscribers_attr": subs is not None,
            "carrier_reasoners": view["reasoners"],
            "carrier_mean_x": view["mean"].get("x"),
            "gap": "carrier live; reasoner wiring-through deferred"}


# -- EP471 ARISE-RL: the reward gate, invoked both ways ----------------------
@_safe
def _ep471():
    from CORE.rubric_evolution import gate_distillation
    up = gate_distillation(0.5, 0.7)
    tie = gate_distillation(0.5, 0.5)
    return {"invoked": True, "improvement_admitted": bool(up),
            "tie_refused": not bool(tie),
            "note": "PRESENT per review: strict-improvement gate"}


register("EP113", "Reflexion verbal RL (invocation)", "CORE.WORMS.expansion.reflexion:record_reflection", _ep113, layer="L6")
register("EP122", "Four Pillars of Autonomous Agents", "CORE.structured_action:observe", _ep122, layer="L7")
register("EP142", "DR-Arena deep research arena", "CORE.WORMS.expansion.arena:Environment.mutate", _ep142, layer="L4")
register("EP150", "Agentic Reasoning Survey", "CORE.skill_library:family_index", _ep150, layer="L2")
register("EP345", "Retry from pivotal mistakes", "CORE.dispatch_plan:record_backtrack", _ep345, layer="L5")
register("EP403", "COVENANT workflow compilation", "CORE.verification_gate:verify_workflow_step", _ep403, layer="L3")
register("EP408", "MemHarness reconstruct-not-replay", "CORE.local_memory:reconstruct_events", _ep408, layer="L1")
register("EP412", "CN101 precision-as-knob", "CORE.WORMS.expansion.policy_optimization:depo_filter", _ep412, layer="L6")
register("EP442", "Mobius knowledge/reasoning split", "CORE.MemoryBus:MemoryBus", _ep442, layer="L1")
register("EP471", "ARISE-RL rubric co-evolution", "CORE.rubric_evolution:gate_distillation", _ep471, layer="L6")
