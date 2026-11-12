#!/usr/bin/env python3
"""_l2_skills.py — L2 (skill library) adapters for `technique_runtime`.

Twenty-three harvested techniques land on the skill layer. The decisive fact,
established by reading on 2026-10-09: `CORE/skill_library.py` exports eight
functions and has **zero importers**. So the largest part of this slice is not
writing skill machinery — it is *calling* machinery that already exists:

    protocol_artifact   EP401's JSON-protocol format      -> invoked
    commit_gate         EP162's gate (family/proof/mask)  -> invoked
    family_index        EP474/EP455/EP265/EP250's input   -> invoked
    merge_sops          EP354's merge operator            -> invoked
    prune_sops          EP354's prune operator            -> invoked
    record_sop_ops      EP354's ledger citation           -> invoked

and for the corpus->skill half, the real host is `scripts/meta_reflect.py`
(`gather_trajectories`, `validate_candidate`, `load_ledger`) — NOT
`PSYCHE/distill.py`, which is persona distillation (PAD traits, BC/DAgger/dDPO).
The review's EP427/EP308 rows point at the wrong file; the correction is carried
in those adapters below.

Where a technique is genuinely absent, the adapter still runs and returns the
residual delta *against the exercised host*, so the registry records what was
called and what is missing in the same row. That is the difference between a
wiring and a wish list.

Read-mostly by contract: no model calls (so `meta_reflect.distill`, which posts
to Ollama, is deliberately never invoked), no repo mutation. The only side
effect any adapter may have is the ledger citation its own module is designed to
write.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List

from CORE.audit_trail import read_citation
from CORE.skill_library import (
    PROTOCOL_REQUIRED, check_transfer_mask, commit_gate,
    compile_leaf_rules, distill_trajectory_to_protocol, emit_skill_md,
    family_index, mine_action_sequences, promotion_gate, promotion_pack,
    protocol_artifact,
    record_experience, render_plan_to_prompt, replicator_step,
    route_hierarchy, scan_witness_canonical, shapley_marginal,
    spine_eval,
)
from CORE.technique_runtime import note_failure, register
from CORE.WORMS.expansion.learning_grpo import GRPOTrainer

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# --- fixtures -----------------------------------------------------------------
# Representative, small, and stable: these exist to give the invoked symbols real
# input, not to simulate a workload.

_PLAN = ["read the target", "plan the edit", "apply the edit", "verify the result"]
_PLAN_B = ["read the target", "plan the edit", "audit the ledger"]
_GROUNDING = ["worm_registry holds 200+ registered worms",
              "the audit ledger is hash-chained"]


def _proto(plan: List[str] | None = None) -> Dict[str, Any]:
    return protocol_artifact("worm-edit", plan or _PLAN, _GROUNDING)


def _evidence() -> Dict[str, Any]:
    return {"proof": ".audit/session_2026-10-09.jsonl:12",
            "transfer_masking": "masked to dispatch-family queries",
            "version": 2}


def _meta_reflect():
    """Import scripts/meta_reflect.py by path (it is not on the import path).

    Import-time only: the module builds no client and reads nothing until a
    function is called, and `distill()` — the one function that posts to a model
    — is never called from an adapter here.
    """
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "meta_reflect.py"
    spec = importlib.util.spec_from_file_location("_azazael_meta_reflect", script)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- EP34: Chain-of-Thought prompting ------------------------------------------
def _ep34() -> Dict[str, Any]:
    """The stored `reasoning_plan` IS an ordered step chain; nothing renders it.

    Review verdict PARTIAL. Invoked: `protocol_artifact` (the plan's home). The
    delta is a thin renderer, not a subsystem — hence S.
    """
    proto = _proto()
    steps = list(proto.get("reasoning_plan") or [])
    rendered = render_plan_to_prompt(proto)
    return {"ok": True, "invoked": "CORE.skill_library:protocol_artifact+"
                                   "render_plan_to_prompt",
            "steps": len(steps), "plan_valid": bool(proto.get("valid")),
            "rendered_steps": rendered["n_steps"],
            "prompt_chars": len(rendered["prompt"]),
            "delta": "renderer live; exemplar sourcing + prompt consumer "
                     "deferred"}


# --- EP135: SoK agentic skills ---------------------------------------------------
def _ep135() -> Dict[str, Any]:
    """Artifact is a 3-field record; the 4-tuple and lifecycle/trust are absent.

    Invoked: `protocol_artifact` + `PROTOCOL_REQUIRED`, which show exactly the
    three fields the schema has to grow from.
    """
    proto = _proto()
    return {"ok": True, "invoked": "CORE.skill_library:PROTOCOL_REQUIRED+"
                                   "protocol_artifact",
            "fields": list(PROTOCOL_REQUIRED), "n_fields": len(PROTOCOL_REQUIRED),
            "artifact_valid": bool(proto.get("valid")),
            "delta": "applicability/policy/termination/interface + 7-stage "
                     "lifecycle + 4-tier trust (must stay OPTIONAL: merge_sops "
                     "constructs through protocol_artifact)"}


# --- EP150: agentic reasoning survey / layer-by-layer spine scaffold --------------
def _ep150() -> Dict[str, Any]:
    """The L2->L6 spine existed as separate wired slices with no per-layer tie.

    Review verdict PARTIAL. Invoked: `spine_eval` (the scaffold), structure-only
    by default — set SPINE_RUN=1 env to also engage every adapter.
    """
    report = spine_eval(run=bool(os.environ.get("SPINE_RUN")))
    return {"ok": bool(report.get("ok")),
            "invoked": "CORE.skill_library:spine_eval",
            "coverage": report.get("coverage"),
            "missing_layers": list(report.get("missing_layers") or []),
            "layers": {layer: info["n_wired"]
                       for layer, info in (report.get("layers") or {}).items()},
            "delta": "scaffold live; per-layer metric thresholds deferred"}


# --- EP155: agentic proposing / corpus -> skill acquisition ----------------------
def _ep155() -> Dict[str, Any]:
    """The corpus->instruction host already exists — in scripts/meta_reflect.py.

    Invoked: `gather_trajectories` (read-only), `load_ledger` (read-only),
    and `validate_candidate` against the stored ledger. The delta is the
    join: emit a `protocol_artifact` beside the prose instruction, and split
    step-vs-outcome reward.
    """
    mr = _meta_reflect()
    trajs, note = mr.gather_trajectories(limit=25)
    ledger = mr.load_ledger()
    ok, reason, _record = mr.validate_candidate(
        {"instruction": "prefer measured verdicts over claimed ones",
         "evidence": "meta lineage", "scope": None}, ledger)
    from CORE.skill_library import distill_trajectory_to_protocol
    distilled = distill_trajectory_to_protocol(
        {"actions": _PLAN, "outcome": "plan rendered",
         "evidence": _GROUNDING}, "worm-edit")
    mgpo = GRPOTrainer(load_policy=False).compute_mgpo_advantages(
        [1.0, 0.9, 0.0, -0.1], subgroups=[[0, 1], [2, 3]])
    return {"ok": True, "invoked": "scripts/meta_reflect:gather_trajectories+"
                                   "load_ledger+validate_candidate+"
                                   "distill_trajectory_to_protocol+"
                                   "GRPOTrainer.compute_mgpo_advantages",
            "trajectories": len(trajs), "note": note, "ledger_rows": len(ledger),
            "validator_accepts_synthetic": bool(ok), "validator_reason": str(reason),
            "distilled_steps": distilled["n_steps"],
            "distilled_valid": bool(distilled["protocol"].get("valid")),
            "mgpo_advantages": [round(a, 4) for a in mgpo],
            "delta": "protocol join live (shared EP401 wire); MGPO "
                     "multi-granularity objective live"}


# --- EP162: agent skill governance / four-gate trust path ------------------------
def _ep162() -> Dict[str, Any]:
    """One gate exists (family + proof + masking); the survey's four gates do not.

    Invoked in full: `commit_gate` on valid evidence, so the accepting path is
    exercised rather than only described.
    """
    from CORE.skill_library import commit_gate
    verdict = commit_gate({"name": "WormEdit", "family": "dispatch"}, _evidence())
    return {"ok": True, "invoked": "CORE.skill_library:commit_gate",
            "accepted": bool(verdict.get("accepted")),
            "reasons": list(verdict.get("reasons") or []),
            "citation": bool(verdict.get("citation")),
            "delta": "static/semantic/sandbox/permission gates + provenance "
                     "tiers (sandbox can only check a DECLARED proof here)"}


# --- EP180: generalize propose/assess/commit; restore ----------------------------
def _ep180() -> Dict[str, Any]:
    """The propose/assess/commit cycle exists for SKILLS only; no restore.

    Invoked: `commit_gate` (the cycle's admission half).
    """
    verdict = commit_gate({"name": "WormEdit", "family": "dispatch"}, _evidence())
    root = Path(__file__).resolve().parents[3]
    restored = read_citation(verdict.get("citation", ""), str(root))
    return {"ok": True, "invoked": "CORE.skill_library:commit_gate+"
                                   "CORE.audit_trail:read_citation",
            "accepted": bool(verdict.get("accepted")),
            "restored": bool(restored.get("found")),
            "delta": "restore helper live; subject-parameterization deferred"}


# --- EP250: compiled subterranean agents (procedures -> weights) -----------------
def _ep250() -> Dict[str, Any]:
    """No text->weights path exists. NOT a wiring gap — a missing weight host.

    Exercised: `family_index`, which is this technique's INPUT side (which
    families are hot enough to be worth compiling).
    """
    from CORE.skill_library import family_index
    fx = family_index([{"name": "WormEdit", "family": "dispatch"},
                       {"name": "LedgerAudit", "family": "audit"}])
    return {"ok": True, "invoked": "CORE.skill_library:family_index",
            "n_families": fx.get("n_families"),
            "input_side_ready": True,
            "delta": "text->weights compiler needs a training host; "
                     "PSYCHE/distill is persona BC/DAgger, unrelated",
            "blocked": "no weight-update host on this box"}


# --- EP255: per-skill experience store + evaluate->refine ------------------------
def _ep255() -> Dict[str, Any]:
    """`commit_gate` records a verdict, not per-skill experience.

    Exercised: the gate whose verdict shape a refine loop would ride.
    """
    verdict = commit_gate({"name": "WormEdit", "family": "dispatch"},
                          _evidence(), record=False)
    rec = record_experience("technique-wiring:self-test",
                            {"reasons": list(verdict.get("reasons") or [])},
                            "")
    return {"ok": True, "invoked": "CORE.skill_library:commit_gate(record=False)+"
                                   "record_experience",
            "reasons": list(verdict.get("reasons") or []),
            "experience_recorded": bool(rec.get("recorded")),
            "delta": "recording live (uncited self-test row, typed outcome); "
                     "refine_prompt() deferred"}


# --- EP265: SkillSmith co-evolution (replicator dynamics) ------------------------
def _ep265() -> Dict[str, Any]:
    """`family_index` supplies family sizes — the raw input. The step is absent.

    Invoked: `family_index`; the shares below are plain arithmetic over its
    output (the input side), not the replicator step itself.
    """
    from CORE.skill_library import family_index
    fx = family_index([{"name": "A", "family": "dispatch"},
                       {"name": "B", "family": "dispatch"},
                       {"name": "C", "family": "audit"}])
    sizes = {f: len(v) for f, v in (fx.get("families") or {}).items()}
    total = sum(sizes.values()) or 1
    rep = replicator_step(sizes, {"dispatch": 1.0, "audit": 0.5})
    return {"ok": True, "invoked": "CORE.skill_library:family_index+"
                                   "replicator_step",
            "family_sizes": sizes,
            "shares": {f: round(n / total, 3) for f, n in sizes.items()},
            "next_shares": rep["next_shares"],
            "delta": "replicator step live; version-scoped nogoods deferred"}


# --- EP270: reasoning-primitive induction (trace -> pseudo-tool) -----------------
def _ep270() -> Dict[str, Any]:
    """Traces exist (meta_reflect), no typed-primitive miner does.

    Invoked: `gather_trajectories` — the trace source a miner would read.
    """
    mr = _meta_reflect()
    trajs, note = mr.gather_trajectories(limit=15)
    kinds = sorted({str(t.get("kind")) for t in trajs})
    mined = mine_action_sequences([{"actions": kinds}])
    return {"ok": True, "invoked": "scripts/meta_reflect:gather_trajectories+"
                                   "mine_action_sequences",
            "trajectories": len(trajs), "kinds": kinds, "note": note,
            "motifs": mined["motifs"][:3],
            "delta": "miner live; `types` field deferred (no schema consumer)"}


# --- EP275: Socratic-SWE trace-derived skills ------------------------------------
def _ep275() -> Dict[str, Any]:
    """Failure ingestion is narrow: only `evolution_result` rows are read.

    Invoked: `gather_trajectories`, whose own source selection is the evidence
    for the delta (it queries `event_type=\"evolution_result\"`).
    """
    mr = _meta_reflect()
    trajs, note = mr.gather_trajectories(limit=25)
    return {"ok": True, "invoked": "scripts/meta_reflect:gather_trajectories",
            "trajectories": len(trajs), "note": note,
            "delta": "widen the source from evolution_result-only to failure "
                     "rows; induce a skill from them; emit repair tasks"}


# --- EP302: transferable interaction patterns ------------------------------------
def _ep302() -> Dict[str, Any]:
    """`family` is a flat string and `transfer_masking` is unenforced free text.

    Invoked: `commit_gate` (which accepts masking as prose — the gap itself) and
    `family_index` (the flat index a sketch index would replace).
    """
    from CORE.skill_library import commit_gate, family_index
    verdict = commit_gate({"name": "WormEdit", "family": "dispatch"},
                          {"proof": ".audit/x.jsonl:1",
                           "transfer_masking": "n/a: single-domain skill"})
    fx = family_index([{"name": "WormEdit", "family": "dispatch"}])
    mask = check_transfer_mask("dispatch", "n/a: single-domain skill",
                               ["dispatch"])
    return {"ok": True, "invoked": "CORE.skill_library:commit_gate(+family_index)+"
                                   "check_transfer_mask",
            "masking_accepted_as_prose": bool(verdict.get("accepted")),
            "n_families": fx.get("n_families"),
            "mask_enforced": bool(mask.get("enforced")),
            "delta": "mask enforcement live; layout-sketch index deferred"}


# --- EP308: SoftSkill / virtual-token skills ------------------------------------
def _ep308() -> Dict[str, Any]:
    """No virtual-token skill representation. TARGET CORRECTION: the review cited
    `PSYCHE/distill.py :: test_theta_frozen`; that symbol is not in the module —
    it is a test at tests/test_psyche_distill.py. PSYCHE is persona distillation.
    """
    root = Path(__file__).resolve().parents[3]
    psyche = root / "src" / "PSYCHE" / "distill.py"
    has_symbol = False
    try:
        src = psyche.read_text(encoding="utf-8", errors="replace")
        # Definition check, not substring: the module docstring MENTIONS
        # `test_theta_frozen`, so a bare `in` test overstates. Only a
        # `def`/`class` definition counts as the symbol being present.
        has_symbol = ("def test_theta_frozen" in src
                      or "class test_theta_frozen" in src)
    except OSError as exc:                            # noqa: BLE001 — probe is advisory
        note_failure("l2_skills:distill-probe", exc)
    return {"ok": True, "invoked": "filesystem probe: src/PSYCHE/distill.py",
            "psyche_is_persona_distillation": True,
            "cited_symbol_present": has_symbol,
            "delta": "virtual-token (soft-prompt) skill representation",
            "blocked": "needs a trainable soft prompt; no such host here"}


# --- EP347: Auto AGI Compiler (witness determinism) ------------------------------
def _ep347() -> Dict[str, Any]:
    """No determinism scan; the gate's reasons/remediations shape is reusable.

    Invoked: `commit_gate` — the shape a scan verdict would be reported in.
    """
    verdict = commit_gate({"name": "WormEdit", "family": "dispatch"}, _evidence())
    scan = scan_witness_canonical(_proto())
    return {"ok": True, "invoked": "CORE.skill_library:commit_gate+"
                                   "scan_witness_canonical",
            "reasons": list(verdict.get("reasons") or []),
            "witness_stable": bool(scan.get("deterministic")),
            "delta": "scan live; calibrated-deopt guard flag deferred "
                     "(no consumer)"}


# --- EP354: EvoSOP lifecycle — the merge and prune operators ---------------------
def _ep354() -> Dict[str, Any]:
    """ALREADY IMPLEMENTED, zero callers. The review's row ("missing merge +
    prune") is STALE: both operators exist and are invoked here in full,
    with `record_sop_ops` citing the outcome.

    `merge_sops` computes a shortest common supersequence (so order is preserved
    and a real order conflict is NAMED, never silently resolved); `prune_sops`
    drops only what the evidence does not support and explains each decision.
    """
    from CORE.skill_library import merge_sops, prune_sops, record_sop_ops
    merged = merge_sops(_proto(_PLAN), _proto(_PLAN_B))
    pruned = prune_sops([{"name": "WormEdit"}, {"name": "Unproven"}],
                        {"WormEdit": [".audit/x.jsonl:12"]})
    citation = record_sop_ops(merged)
    return {"ok": True,
            "invoked": "CORE.skill_library:merge_sops+prune_sops+record_sop_ops",
            "merged_steps": len((merged.get("merged") or {}).get("reasoning_plan") or []),
            "shared_steps": (merged.get("from") or {}).get("shared"),
            "conflicts": len(merged.get("conflicts") or []),
            "merge_ok": bool(merged.get("ok")),
            "kept": len(pruned.get("kept") or []),
            "pruned": len(pruned.get("pruned") or []),
            "cited": bool(citation), "delta": ""}


# --- EP363: LOGOS living logic / versioned agent packs ---------------------------
def _ep363() -> Dict[str, Any]:
    """Untrusted-until-evidenced promotion beyond skills is live.

    Review verdict PARTIAL. Invoked: `promotion_pack` + `promotion_gate`
    across a skill and a non-skill kind — the versioned bundle plus the
    extended rule the row said was missing.
    """
    skill = promotion_gate(promotion_pack(
        "skill", "WormEdit", 2, {"family": "dispatch"}, _evidence()),
        record=False)
    prompt = promotion_gate(promotion_pack(
        "prompt", "Router", 1, {"template": "route {q}"},
        {"proof": ".audit/session_2026-10-09.jsonl:12",
         "scope_note": "dispatch only"}), record=False)
    return {"ok": True, "invoked": "CORE.skill_library:promotion_pack+"
                                   "promotion_gate",
            "skill_accepted": bool(skill["gate"]["accepted"]),
            "prompt_accepted": bool(prompt["gate"]["accepted"]),
            "packs_untrusted_at_build": promotion_pack(
                "role", "R", 1, {})["trusted"] is False,
            "delta": "packs + extended rule live; per-kind evidence "
                     "thresholds deferred"}


# --- EP364: capability trees -----------------------------------------------------
def _ep364() -> Dict[str, Any]:
    """`family_index` is flat, unordered, with no manifests.

    Invoked: `family_index` (the flat structure to nest).
    """
    routed = route_hierarchy([{"name": "WormEdit", "family": "dispatch",
                               "domain": "edit"},
                              {"name": "LedgerAudit", "family": "audit"}])
    return {"ok": True, "invoked": "CORE.skill_library:route_hierarchy",
            "flat_families": sorted(routed["families"]),
            "depth": routed["depth"],
            "lazy_manifests": routed["lazy_manifests"],
            "delta": "tree live; LIFO is leaf order — deeper nesting needs "
                     "sub-domain producers"}


# --- EP387: knowledge-centric self-improvement (cross-task distillation) ---------
def _ep387() -> Dict[str, Any]:
    """Distillation that exists is AGED/CAPPED, not cross-family.

    Deliberately does NOT call `data_retention.distill_aged_memories`: it is a
    mutating maintenance pass and adapters must be read-mostly. Exercised
    instead: `family_index`, the membership input a cross-family distill needs.
    """
    from CORE.skill_library import family_index
    fx = family_index([{"name": "WormEdit", "family": "dispatch"},
                       {"name": "LedgerAudit", "family": "audit"},
                       {"name": "Bare", "family": ""}])
    return {"ok": True, "invoked": "CORE.skill_library:family_index",
            "n_families": fx.get("n_families"),
            "unassigned": list(fx.get("unassigned") or []),
            "delta": "distill ACROSS families (existing distillation is aged/"
                     "capped rows only); forum protocol",
            "note": "data_retention.distill_* is mutating - not invoked here"}


# --- EP401: MAPD protocol distillation ------------------------------------------
def _ep401() -> Dict[str, Any]:
    """`protocol_artifact` IS the style-normalized JSON protocol format.

    Invoked in full; the delta is the trajectory->protocol join (shared with
    EP155).
    """
    proto = _proto()
    distilled = distill_trajectory_to_protocol(
        {"actions": _PLAN, "outcome": "plan rendered",
         "evidence": _GROUNDING}, "worm-edit")
    return {"ok": True, "invoked": "CORE.skill_library:protocol_artifact+"
                                   "distill_trajectory_to_protocol",
            "task_type": proto.get("task_type"),
            "steps": len(proto.get("reasoning_plan") or []),
            "facts": len(proto.get("grounding_facts") or []),
            "valid": bool(proto.get("valid")), "gaps": list(proto.get("gaps") or []),
            "distilled_steps": distilled["n_steps"],
            "distilled_valid": bool(distilled["protocol"].get("valid")),
            "delta": "distiller live (shared EP155 wire); MGPO split "
                     "reported-not-optimized"}


# --- EP427: Reason Wide, Not Deep (corpus -> cheap-model SKILL.md) ---------------
def _ep427() -> Dict[str, Any]:
    """TARGET CORRECTION: the review's host `PSYCHE/distill.py` is persona
    distillation; the corpus->skill host is `scripts/meta_reflect.py`.

    Invoked: `gather_trajectories` (the corpus) + `protocol_artifact` (the
    payload a SKILL.md would render).
    """
    mr = _meta_reflect()
    trajs, note = mr.gather_trajectories(limit=15)
    proto = _proto()
    md = emit_skill_md(proto, "worm-edit skill distilled from trajectories")
    return {"ok": True, "invoked": "scripts/meta_reflect:gather_trajectories "
                                   "+ CORE.skill_library:protocol_artifact+"
                                   "emit_skill_md",
            "corpus_rows": len(trajs), "note": note,
            "payload_valid": bool(proto.get("valid")),
            "skill_md_chars": len(md["markdown"]),
            "delta": ""}


# --- EP447: HaReCAP compiled leaf reflexes --------------------------------------
def _ep447() -> Dict[str, Any]:
    """Nothing compiles leaf decisions into rules; the abstain fallback is absent.

    Exercised: `commit_gate`'s remediation pattern, the shape such rules would
    be admitted and repaired through.
    """
    verdict = commit_gate({"name": "", "family": ""}, {})
    compiled = compile_leaf_rules(
        [{"state": "deny-empty", "action": "remediate"}] * 3
        + [{"state": "deny-empty", "action": "escalate"}])
    return {"ok": True, "invoked": "CORE.skill_library:commit_gate(reject path)+"
                                   "compile_leaf_rules",
            "rejected": not bool(verdict.get("accepted")),
            "remediations": len(verdict.get("remediations") or []),
            "compiled_rules": len(compiled["rules"]),
            "delta": "miner live; abstain-fallback consumer deferred "
                     "(no leaf-decision call site)"}


# --- EP455: CASS coalition reliability (Shapley marginal audit) -----------------
def _ep455() -> Dict[str, Any]:
    """`family_index` gives coalition membership; the marginal audit is absent.

    Invoked: `family_index`; the per-family sizes it reports are the coalition
    population the audit would attribute gains across.
    """
    from CORE.skill_library import family_index
    fx = family_index([{"name": "A", "family": "dispatch"},
                       {"name": "B", "family": "dispatch"},
                       {"name": "C", "family": "audit"}])
    sizes = {f: len(v) for f, v in (fx.get("families") or {}).items()}
    shap = shapley_marginal(["A", "B"], lambda s: float(len(s)))
    return {"ok": True, "invoked": "CORE.skill_library:family_index+"
                                   "shapley_marginal",
            "coalition_sizes": sizes,
            "shapley": shap["shapley"],
            "delta": "audit live; post-transfer masking flag deferred "
                     "(no transfer pipeline)"}


# --- EP458: TRACE consistency (Pass@3 vs Pass^k) --------------------------------
def _ep458() -> Dict[str, Any]:
    """`skill_library` never executes a skill (its own docstring says so), so no
    pass@k exists here. Exercised: the commit path that would consume outcomes.
    (PrioritizedReplayBuffer retired 2026-10-11 with zero production callers;
    the contrast demo moved out with it — the commit path below is unchanged.)
    """
    verdict = commit_gate({"name": "WormEdit", "family": "dispatch"}, _evidence())
    return {"ok": True, "invoked": "CORE.skill_library:commit_gate",
            "accepted": bool(verdict.get("accepted")),
            "delta": "commit path live; pass@k OUTCOME production still needs an execution host",
            "blocked": "needs an execution host to produce pass@k outcomes"}


# --- EP474: SkillGLoW procedural families ---------------------------------------
def _ep474() -> Dict[str, Any]:
    """PRESENT — invoked in full. One prior per family, instance detail
    regenerated per task; the regeneration half is the remaining delta.
    """
    from CORE.skill_library import family_index
    fx = family_index([{"name": "WormEdit", "family": "dispatch"},
                       {"name": "LedgerAudit", "family": "audit"},
                       {"name": "Bare", "family": ""}])
    return {"ok": True, "invoked": "CORE.skill_library:family_index",
            "families": sorted((fx.get("families") or {})),
            "n_skills": fx.get("n_skills"),
            "unassigned": list(fx.get("unassigned") or []),
            "delta": "per-task instance regeneration inside a family prior"}


# --- registration ---------------------------------------------------------------
_L2 = [
    ("EP34", "Chain-of-Thought prompting", "CORE.skill_library:protocol_artifact", _ep34),
    ("EP135", "SoK agentic skills", "CORE.skill_library:PROTOCOL_REQUIRED", _ep135),
    ("EP150", "Agentic reasoning survey", "CORE.skill_library:spine_eval", _ep150),
    ("EP155", "Agentic proposing skill synthesis", "scripts/meta_reflect:gather_trajectories", _ep155),
    ("EP162", "Agent skills survey + governance", "CORE.skill_library:commit_gate", _ep162),
    ("EP180", "Autogenesis protocol AGP/AGS", "CORE.skill_library:commit_gate", _ep180),
    ("EP250", "Compiled subterranean agents", "CORE.skill_library:family_index", _ep250),
    ("EP255", "MUSE-Autoskill lifecycle", "CORE.skill_library:commit_gate", _ep255),
    ("EP265", "SkillSmith co-evolution", "CORE.skill_library:family_index", _ep265),
    ("EP270", "Reasoning primitive induction", "scripts/meta_reflect:gather_trajectories", _ep270),
    ("EP275", "Socratic-SWE trace-derived skills", "scripts/meta_reflect:gather_trajectories", _ep275),
    ("EP302", "Transferable interaction patterns", "CORE.skill_library:commit_gate", _ep302),
    ("EP308", "SoftSkill virtual tokens", "no-host: trainable soft-prompt skill representation", _ep308),
    ("EP347", "Auto AGI compiler", "CORE.skill_library:commit_gate", _ep347),
    ("EP354", "EvoSOP procedure synthesis", "CORE.skill_library:merge_sops", _ep354),
    ("EP363", "LOGOS living logic", "CORE.skill_library:promotion_gate", _ep363),
    ("EP364", "Capability trees", "CORE.skill_library:family_index", _ep364),
    ("EP387", "Knowledge-centric self-improvement", "CORE.skill_library:family_index", _ep387),
    ("EP401", "MAPD protocol distillation", "CORE.skill_library:protocol_artifact", _ep401),
    ("EP427", "Reason Wide, Not Deep", "scripts/meta_reflect:gather_trajectories", _ep427),
    ("EP447", "HaReCAP leaf reflexes", "CORE.skill_library:commit_gate", _ep447),
    ("EP455", "CASS coalition reliability", "CORE.skill_library:family_index", _ep455),
    ("EP458", "TRACE consistency", "CORE.skill_library:commit_gate", _ep458),
    ("EP474", "SkillGLoW procedural families", "CORE.skill_library:family_index", _ep474),
]

for _ep, _name, _where, _fn in _L2:
    register(_ep, _name, _where, _fn, layer="L2")
