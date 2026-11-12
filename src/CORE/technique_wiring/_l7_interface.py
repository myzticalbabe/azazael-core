#!/usr/bin/env python3
"""_l7_interface.py — L7 (interfaces & comms) wirings for `technique_runtime`.

Every adapter here invokes a REAL symbol that already exists in this repo. The
layer's headline finding from the 181-technique review is that the interface
half was *implemented and then never called*: `structured_action.drive()` is a
genuine ReAct controller (it carries state across iterations and appends
`repair:<gap>` to the plan — the actual EP44 technique) and its only caller was
an exercise script. So the adapters below **invoke** the existing code; they do
not reimplement it, and they name the symbol they invoke.

Two of these registrations (EP125, EP168) are deliberately HONEST GAPS: reading
the module shows the technique's element is genuinely absent there, so the
adapter records the absence plus the evidence (the existing half it was checked
against) instead of pretending a wiring exists. That is what stops the gap being
re-litigated next session.

Read-mostly by design: no adapter writes a ledger entry (the runtime cites the
run once, in `run_all`), none mutates repo state, none calls a model.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List

from CORE.technique_runtime import note_failure, register

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LAYER = "L7"

#: A minimal application contract shared by the interface adapters, so the
#: verdicts below are deterministic and inspectable.
_CONTRACT: Dict[str, List[str]] = {"verbs": ["read", "edit"], "targets": ["config"]}


def _short(value: Any, limit: int = 90) -> str:
    s = str(value)
    return s if len(s) <= limit else s[: limit - 1] + "\u2026"


# -- EP44 ReAct driver loop -----------------------------------------------------

def _ep44_react_loop() -> Dict[str, Any]:
    """Invoke the real thought->action->observation controller (ReAct).

    The controller is `structured_action.drive`. It is handed a pure local
    `thought_fn`, so the reasoning is deterministic and this adapter stays
    read-mostly. Two valid steps followed by a terminating thought should close
    the loop: `closed=True` with no gaps is the technique working end to end
    (observe + semantic_action + plan repair, interleaved).
    """
    from CORE.structured_action import drive

    script = [
        {"thought": "read the current config",
         "action": {"verb": "read", "target": "config"},
         "state": {"config": {"value": 1}}},
        {"thought": "apply the edit",
         "action": {"verb": "edit", "target": "config"},
         "state": {"config": {"value": 2}, "edited": True}},
        None,                      # terminating (falsy) thought
    ]

    def thought_fn(_state: Dict[str, Any]) -> Any:
        return script.pop(0) if script else None

    out = drive("technique-runtime", ["read", "edit"], _CONTRACT, ["config"],
                thought_fn, max_steps=6)
    return {"closed": bool(out.get("closed")), "steps": len(out.get("steps", [])),
            "plan": out.get("plan"), "repairs": len(out.get("updates", [])),
            "gaps": out.get("gaps")}


# -- EP465 ASIL structured interface -------------------------------------------

def _ep465_asil() -> Dict[str, Any]:
    """Invoke the structured-observation and semantic-action validators."""
    from CORE.structured_action import observe, semantic_action

    obs = observe("technique-runtime", {"config": {"value": 1}}, ["config"])
    act_ok = semantic_action({"verb": "read", "target": "config"}, _CONTRACT)
    act_bad = semantic_action({"verb": "click", "target": "pixel:12,40"}, _CONTRACT)
    return {"complete": bool(obs.get("complete")),
            "executable": bool(act_ok.get("executable")),
            "gui_action_named": act_bad.get("gaps"),
            "summary": "structured state + semantic action validated"}


# -- EP125 SWE-agent ACI vocabulary (HONEST GAP) --------------------------------

def _ep125_aci_vocabulary() -> Dict[str, Any]:
    """Verify whether the ACI action vocabulary exists; record the evidence.

    Read of `structured_action.py` on 2026-10-09: it validates verb/target
    membership against a caller-supplied contract and nothing more — there is no
    ACI action table (file view / search / multi-line edit) and no auto-lint
    hook returning structured feedback on an edited artifact. The action set is
    therefore the residual delta; this adapter states that rather than claiming
    a wiring.
    """
    from CORE.structured_action import semantic_action

    target = "src/CORE/example.py"
    probe = semantic_action({"verb": "view", "target": target},
                            {"verbs": ["view", "search", "edit"],
                             "targets": [target]})
    # `view` exercised against a contract that DOES declare verb and target: the
    # contract mechanism works, so what is missing is the VOCABULARY (no
    # built-in ACI action set, no lint hook), not the validator.
    return {"vocabulary_present": False,
            "missing": "ACI action set (view/search/multiline-edit) + auto-lint hook",
            "contract_check_works": bool(probe.get("executable")),
            "evidence": "structured_action validates verb/target membership only"}


# -- EP168 transduction algebra (HONEST GAP) -----------------------------------

def _ep168_transduction_algebra() -> Dict[str, Any]:
    """Check for the typed transduction operators; record the evidence.

    `structured_action.semantic_action` validates a typed action against a
    contract, but there is no transduction operator (`<<`), no type merge (`&`),
    and no evidence-slot -> input-evidence trace. Verified by attribute probe.
    """
    import CORE.structured_action as sa

    have = [n for n in ("transduce", "type_merge", "__lshift__", "evidence_trace")
            if hasattr(sa, n)]
    return {"operators_present": bool(have), "found": have,
            "missing": "`<<` transduction, `&` type merge, evidence-slot tracing",
            "evidence": "typed-action validation exists; no algebra over it"}


# -- EP266 adaptive latent-vs-CoT mode selector --------------------------------

def _ep266_mode_selector() -> Dict[str, Any]:
    """Anchor a latent-vs-CoT decision on whether depth actually did work.

    LRT's contract is that iterating bounded corrections must CONTRACT the
    residual; if it does not, extra latent depth bought nothing and the anchored
    choice is the explicit (CoT) path. The decision is computed from
    `latent_refine.refine`, with the action anchors supplied by the interface
    layer — so the selector is decided, not asserted.
    """
    from CORE.latent_refine import refine

    anchored = refine([1.0, 0.0], [[0.5, 0.0], [0.2, 0.0], [0.05, 0.0]], 1.0)
    flat = refine([1.0, 0.0], [[0.5, 0.0]], 1.0)
    choose_latent = bool(anchored.get("contracted"))
    return {"mode": "latent" if choose_latent else "cot",
            "contracted": choose_latent,
            "single_step_contracted": bool(flat.get("contracted")),
            "residuals": anchored.get("residuals")}


# -- EP440 StateBridge latent comms --------------------------------------------

def _ep440_statebridge() -> Dict[str, Any]:
    """Invoke the training-free latent-comms primitives (StateBridge).

    `norm_calibrate` and `prefix_inject` are pure stdlib and always run. The
    closed-form Procrustes half is torch-gated by the module itself, so it is
    invoked only when torch is ALREADY imported — that keeps this adapter cheap
    and avoids pulling a heavy backend into a wiring sweep.
    """
    from CORE.latent_align import norm_calibrate, prefix_inject

    cal = norm_calibrate([3.0, 4.0])
    inj = prefix_inject([[0.1, 0.2]], [[0.3, 0.4]])
    out: Dict[str, Any] = {"norm": round(float(cal.get("norm") or 0.0), 4),
                           "n_prefix": inj.get("n_prefix"),
                           "sequence_len": len(inj.get("sequence") or [])}
    if "torch" in sys.modules:
        try:
            from CORE.latent_align import orthogonal_align
            al = orthogonal_align([[1.0, 0.0]], [[1.0, 0.0]])
            out["aligned"] = al.get("gaps") == []
        except Exception as exc:
            # Backend faults belong to the module's own gap vocabulary; report
            # the class rather than letting it look like a wiring defect.
            # Backend faults belong to the module's own gap vocabulary; report
            # the class rather than letting it look like a wiring defect.
            note_failure("_l7_interface:L177", exc)
            out["aligned"] = f"backend-error:{type(exc).__name__}"
    else:
        out["aligned"] = "torch-gated (backend not loaded; module names the gap)"
    return out


# -- EP472 LRT bounded residual refinement -------------------------------------

def _ep472_lrt_refine() -> Dict[str, Any]:
    """Invoke the bounded residual refinement loop (depth decoupled from size)."""
    from CORE.latent_refine import bounded_correct, refine

    one = bounded_correct([1.0, 1.0], [3.0, 4.0], 1.0)     # must clamp to norm 1
    loop = refine([1.0, 0.0], [[0.5, 0.0], [0.25, 0.0]], 1.0)
    return {"clamped_norm": round(float(one.get("applied_norm") or 0.0), 4),
            "bound_respected": (one.get("applied_norm") or 0.0) <= 1.0 + 1e-9,
            "steps": loop.get("steps"),
            "contracted": bool(loop.get("contracted"))}


# -- EP132 perception/reasoning/memory/execution review (SURVEY) ----------------

def _ep132_agent_split() -> Dict[str, Any]:
    """Assert the surveyed four-part agent split is realised across modules.

    The review carries this one as SURVEY (no mechanism to implement). The
    observable is that the split exists as separate modules — perception and
    execution in `structured_action`, memory in `local_memory`, and the causal
    stage sequence in `neural_loop.STAGES` — so the technique is validated
    rather than built.
    """
    from CORE import neural_loop
    from CORE import structured_action

    stages = tuple(getattr(neural_loop, "STAGES", ()) or ())
    return {"survey": True,
            "perception": hasattr(structured_action, "observe"),
            "execution": hasattr(structured_action, "semantic_action"),
            "memory": _importable("CORE.local_memory"),
            "stages": len(stages),
            "causal_order": ", ".join(stages[:5])}


# -- EP372 per-modality agents + active evidence -------------------------------

def _ep372_modality_agents() -> Dict[str, Any]:
    """Read the consensus voter through its read path and check for modalities.

    `QuorumVoterWorm.tally` is the read path (it consults the shared spine
    ledger), so this is read-only. The finding: agreement is counted per ENV by
    explicit safety design, and there is no modality dimension and no
    active-evidence loop — the residual is real, and `consensus.py` is the wrong
    place to add it.
    """
    from CORE.WORMS.expansion.consensus import QuorumVoterWorm

    voter = QuorumVoterWorm(quorum=2)
    try:
        # `tally` reads the shared spine ledger; a ledger that is unavailable
        # must not be reported as a modality finding either way.
        tally = voter.tally("technique-runtime:no-such-finding")
    except Exception as exc:
        note_failure("_l7_interface:L241", exc)
        tally = {"votes": None, "read_error": type(exc).__name__}
    return {"votes": tally.get("votes") if isinstance(tally, dict) else None,
            "modality_dimension": hasattr(voter, "_modalities"),
            "missing": "modality dimension + active-evidence loop (sibling module)"}


# -- EP375 modality suppression + frozen-MLLM anchoring ------------------------

def _ep375_modality_routing() -> Dict[str, Any]:
    """Invoke the MoE dispatcher's real expert-resolution path.

    `available_experts()` resolves each expert against its real module via
    importlib and caches the answer — the honest observable for this row, and
    read-only. Modal suppression and frozen-MLLM anchoring are absent: the
    dispatcher routes over inference worms, and no modality concept exists.
    """
    from CORE.WORMS.expansion.synapse import MoEDispatcherWorm

    dispatcher = MoEDispatcherWorm(top_k=2)
    avail = dispatcher.available_experts()
    return {"resolved": sum(1 for v in avail.values() if v), "of": len(avail),
            "modality_concept": False,
            "missing": "modality suppression + frozen-MLLM anchoring"}


def _importable(module: str) -> bool:
    """Whether a module imports, without carrying it forward on failure."""
    try:
        __import__(module)
        return True
    except Exception as exc:                          # noqa: BLE001 — probe says "absent"
        note_failure("l7_interface:presence-probe", exc)
        return False


register("EP44", "ReAct reasoning+acting",
         "CORE.structured_action:drive", _ep44_react_loop, layer=LAYER)
register("EP125", "SWE-agent Agent-Computer Interface",
         "CORE.structured_action:semantic_action", _ep125_aci_vocabulary,
         layer=LAYER)
register("EP132", "How Autonomous LLM Agents Work",
         "CORE.neural_loop:STAGES", _ep132_agent_split, layer=LAYER)
register("EP168", "Agentics 2.0 transduction algebra",
         "CORE.structured_action:semantic_action", _ep168_transduction_algebra,
         layer=LAYER)
register("EP266", "ALAR adaptive latent reasoning",
         "CORE.latent_refine:refine", _ep266_mode_selector, layer=LAYER)
register("EP372", "UrbanAgent multi-agent profiling",
         "CORE.WORMS.expansion.consensus:QuorumVoterWorm.tally",
         _ep372_modality_agents, layer=LAYER)
register("EP375", "MGDT multimodal KG completion",
         "CORE.WORMS.expansion.synapse:MoEDispatcherWorm.available_experts",
         _ep375_modality_routing, layer=LAYER)
register("EP440", "StateBridge latent comms",
         "CORE.latent_align:norm_calibrate", _ep440_statebridge, layer=LAYER)
register("EP465", "ASIL structured interfaces",
         "CORE.structured_action:observe", _ep465_asil, layer=LAYER)
register("EP472", "LRT latent recurrence",
         "CORE.latent_refine:refine", _ep472_lrt_refine, layer=LAYER)
