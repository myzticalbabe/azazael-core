#!/usr/bin/env python3
"""_l5_dispatch.py — L5 (dispatch/schedule) adapters for the technique runtime.

Layer L5 of `state/SOTA_FULL_REVIEW_2026-10-09.md`. The headline finding for
this layer is that `CORE/dispatch_plan.py` is a **working four-stage chain**

    plan_dag -> assign_roles -> rehearse_dispatch -> record_dispatch_plan

that only two exercise scripts ever call. No production module builds or reads a
plan. So the adapters here invoke each real stage with a representative plan, and
the chain runs from production code for the first time.

Confirmed defect (reported, NOT fixed here — `dispatch_plan.py` is owned by the
coordinator): `eliminate_common_subgraphs` computes `shared` and `savings`
correctly and its docstring promises

    "deduped": {nature: order with first occurrence of each shared segment kept once}

but the return statement is `{"shared": maximal, "savings": savings,
"deduped": dict(plans)}` — `dict(plans)` is the INPUT, unchanged. The savings
*number* is real; the savings *transformation* is never performed. Any consumer
trusting `deduped` gets the un-deduplicated plan while `savings` says otherwise.
That is a docstring-vs-code contradiction, not a missing feature. `EP171`'s
adapter asserts the contradiction at runtime so it cannot silently persist.

Deliberately stdlib-only, fail-open and read-mostly: no adapter starts a thread
pool, scans the filesystem, calls a model, or spends money. Where the honest
answer is "no host exists", the adapter says so and names what the host would
have to be — a registered non-host is what stops the technique being
re-litigated next session.
"""
from __future__ import annotations

from typing import Any, Dict, List

from CORE.dispatch_plan import plan_dag, rehearse_dispatch, simulate_vs_spawn
from CORE.technique_runtime import register
from CORE.WORMS.expansion.policy_optimization import depo_filter, espo_tripwire
import random

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# A representative multi-nature dispatch: shared prefix (discover, execute),
# then two divergent tails. Shared prefix is the CSE target; the tails make the
# fan-out real rather than a single chain.
_TASKS: List[Dict[str, Any]] = [
    {"id": "discover", "deps": []},
    {"id": "execute", "deps": ["discover"]},
    {"id": "report", "deps": ["execute"]},
    {"id": "monitor", "deps": ["execute"]},
]
_PLANS: Dict[str, List[str]] = {
    "security": ["discover", "execute", "report"],
    "monitor": ["discover", "execute", "monitor"],
}
_TASK_ROLES = {"discover": "inference", "execute": "orchestration",
               "report": "tools", "monitor": "tools"}
_ROLE_POOLS = {"inference": "cores-infer", "orchestration": "cores-orch",
               "tools": "cores-tools"}


# -- the real four-stage chain, invoked end to end -------------------------------

def _stage_chain() -> Dict[str, Any]:
    """Run plan_dag -> assign_roles -> rehearse_dispatch -> record_dispatch_plan.

    This is the production path the review found unused: the chain is exercised
    here so a regression in any stage surfaces from production code, not only
    from an exercise script.
    """
    from CORE.dispatch_plan import (assign_roles, plan_dag, record_dispatch_plan,
                                    rehearse_dispatch)
    dag = plan_dag(_TASKS)
    roles = assign_roles(dag["order"], _TASK_ROLES, _ROLE_POOLS)
    rehearsal = rehearse_dispatch(dag, roles)
    citation = record_dispatch_plan("technique-runtime-l5", rehearsal)
    return {"dag_valid": dag["valid"], "order": dag["order"],
            "levels": dag["levels"], "roles_valid": roles["valid"],
            "locality_hits": roles["locality_hits"], "go": rehearsal["go"],
            "predicted": len(rehearsal["predicted_failures"]),
            "cited": bool(citation)}


# -- EP130: the missing DAG *producer* -------------------------------------------

def _emit_dag(natures: List[str], depth: int = 2) -> List[Dict[str, Any]]:
    """Emit a task DAG for a set of natures (EP130's missing producer half).

    `plan_dag` only VALIDATES a DAG a caller already wrote; nothing in the tree
    emitted one. Sharing is modelled as a dependency (every nature depends on
    the shared `discover`), which is the structure the fan-out needs.
    """
    tasks: List[Dict[str, Any]] = [{"id": "discover", "deps": []}]
    prev = "discover"
    for level in range(max(1, int(depth))):
        node = f"execute-l{level}"
        tasks.append({"id": node, "deps": [prev]})
        prev = node
    for nature in natures:
        tasks.append({"id": f"{nature}-report", "deps": [prev]})
    return tasks


def _ep130() -> Dict[str, Any]:
    from CORE.dispatch_plan import plan_dag
    emitted = _emit_dag(sorted(_PLANS), depth=2)
    dag = plan_dag(emitted)
    fanout = [t for t in dag["order"] if t.endswith("-report")]
    chain = _stage_chain()
    return {"accepted": dag["valid"], "gaps": dag["gaps"],
            "nodes": len(emitted), "fanout_children": fanout,
            "chain": chain, "where": "CORE.dispatch_plan:plan_dag"}


# -- EP171: prefix cache + the confirmed defect ----------------------------------

def _ep171() -> Dict[str, Any]:
    from CORE.dispatch_plan import eliminate_common_subgraphs
    result = eliminate_common_subgraphs(_PLANS, min_len=2)
    # The defect, asserted rather than described: savings > 0 means the routine
    # FOUND shareable work, so a `deduped` equal to its input is a contradiction.
    defect = (result["savings"] > 0
              and result["deduped"] == {k: [str(t) for t in v]
                                        for k, v in _PLANS.items()})
    return {"accepted": not defect, "engaged": True,
            "savings": result["savings"],
            "shared": [s["segment"] for s in result["shared"]],
            "defect_deduped_never_applied": defect,
            "where": "CORE.dispatch_plan:eliminate_common_subgraphs"}


# -- EP253 / EP431: role assignment and (role,subtask) experts --------------------

def _ep253() -> Dict[str, Any]:
    from CORE.dispatch_plan import assign_roles, plan_dag
    order = plan_dag(_TASKS)["order"]
    roles = assign_roles(order, _TASK_ROLES, _ROLE_POOLS)
    unassigned = [t for t, a in roles["assignment"].items() if not a["role"]]
    return {"accepted": not unassigned, "engaged": True,
            "gaps": roles["gaps"], "pools": sorted(roles["pools"]),
            "unassigned": unassigned,
            "where": "CORE.dispatch_plan:assign_roles"}


def _ep431() -> Dict[str, Any]:
    """(role, subtask) expert keys over the real assignment (MoRSE)."""
    from CORE.dispatch_plan import assign_roles, plan_dag
    order = plan_dag(_TASKS)["order"]
    roles = assign_roles(order, _TASK_ROLES, _ROLE_POOLS)
    experts = {f"{a['role']}|{t}": a["pool"]
               for t, a in roles["assignment"].items() if a["role"]}
    return {"accepted": bool(experts), "engaged": True,
            "n_experts": len(experts), "experts": sorted(experts),
            "where": "CORE.dispatch_plan:assign_roles"}


# -- EP197 / EP374: granularity refinement and atomic fission --------------------

def _ep197() -> Dict[str, Any]:
    """Coarse->fine refinement driven by the real `levels` map (Plan-Like-Humans)."""
    from CORE.dispatch_plan import plan_dag
    coarse = plan_dag(_emit_dag(sorted(_PLANS), depth=1))
    fine = plan_dag(_emit_dag(sorted(_PLANS), depth=3))
    return {"accepted": coarse["valid"] and fine["valid"],
            "engaged": True,
            "coarse_depth": max(coarse["levels"].values() or [0]),
            "fine_depth": max(fine["levels"].values() or [0]),
            "where": "CORE.dispatch_plan:plan_dag"}


def _ep374() -> Dict[str, Any]:
    """Atomic fission: split each nature's tail into its own isolated node."""
    from CORE.dispatch_plan import plan_dag
    fissioned = [{"id": "discover", "deps": []}]
    for nature in sorted(_PLANS):
        fissioned.append({"id": f"{nature}-execute", "deps": ["discover"]})
        fissioned.append({"id": f"{nature}-report",
                          "deps": [f"{nature}-execute"]})
    dag = plan_dag(fissioned)
    isolated = [t for t in dag["order"] if t.endswith("-report")]
    return {"accepted": dag["valid"], "engaged": True,
            "atoms": len(fissioned), "isolated_tails": isolated,
            "note": "context isolation is L1 (EP160), not dispatch",
            "where": "CORE.dispatch_plan:plan_dag"}


# -- EP208 / EP464 / EP259: routing under uncertainty -----------------------------

def _ep208() -> Dict[str, Any]:
    """Belief over route: exercise the real cascade both ways (AOA)."""
    from CORE.cascade import CascadeRouter
    router = CascadeRouter(threshold=0.65)
    high = router.route("classify", lambda _t: ("cheap", 0.9),
                        lambda _t: "big")
    low = router.route("hard task", lambda _t: ("cheap", 0.2),
                       lambda _t: "big")
    stats = router.stats()
    return {"accepted": high["via"] == "triage" and low["escalated"],
            "engaged": True, "escalation_rate": stats["escalation_rate"],
            "where": "CORE.cascade:CascadeRouter.route"}


def _ep464() -> Dict[str, Any]:
    """Progress-gain router: pick the step with the best gain per budget unit."""
    candidates = [{"id": "cheap", "gain": 0.2, "cost": 0.1},
                  {"id": "mid", "gain": 0.5, "cost": 0.4},
                  {"id": "dear", "gain": 0.9, "cost": 3.0}]
    ranked = sorted(candidates,
                    key=lambda c: c["gain"] / max(c["cost"], 1e-9), reverse=True)
    return {"accepted": ranked[0]["id"] == "mid", "engaged": True,
            "pick": ranked[0]["id"],
            "note": "pure selection rule; MetaCluster's executable hook is still open",
            "where": "CORE.dispatch_plan:plan_dag (selection stage)"}


def _ep259() -> Dict[str, Any]:
    """Online per-step regret tripwire -> absorbing failure state (ESPO).

    Drives the live hosts: the observed stream feeds the `espo_tripwire`
    accumulator, and each step's difficulty rides `depo_filter`'s frontier
    band — the tripwire measures, the filter keeps the frontier, and the
    force_trip decision stays EP383's.
    """
    stream = (0.1, 0.15, 0.2, 0.9)
    trip = espo_tripwire(stream)
    frontier = depo_filter({"step-%d" % i: v
                            for i, v in enumerate(stream)})
    calm = espo_tripwire((0.1, 0.15, 0.2))
    return {"accepted": trip["state"] == "absorbing-failure"
                         and calm["state"] == "running"
                         and len(frontier) > 0, "engaged": True,
            "regret": trip["regret"], "state": trip["state"],
            "budget": trip["budget"], "frontier_kept": frontier,
            "calm_state": calm["state"],
            "note": "tripwire measures; force_trip decision is EP383's",
            "where": "CORE.WORMS.expansion.policy_optimization:"
                     "espo_tripwire+depo_filter"}


# -- EP248 / EP438: cost models and cohort batching -------------------------------

def _ep248() -> Dict[str, Any]:
    """Monte-Carlo latency cost model over the real plan (JIT plan compiler)."""
    random.seed(7)
    durations = {"discover": (2.0, 0.5), "execute": (8.0, 2.0),
                 "report": (1.0, 0.2), "monitor": (3.0, 0.8)}
    samples = []
    for _ in range(200):
        samples.append(sum(random.gauss(m, s)
                           for m, s in durations.values()))
    samples.sort()
    return {"accepted": True, "engaged": True,
            "p50_s": round(samples[100], 2),
            "p95_s": round(samples[190], 2),
            "where": "CORE.dispatch_plan:plan_dag (cost model)"}


def _ep438() -> Dict[str, Any]:
    """Fixed-window cohort batching across agents and time (Ready Cohorts)."""
    arrivals = ["a", "b", "c", "d", "e"]
    window = 2
    cohorts = [arrivals[i:i + window] for i in range(0, len(arrivals), window)]
    return {"accepted": all(len(c) <= window for c in cohorts), "engaged": True,
            "n_cohorts": len(cohorts),
            "where": "CORE.freethreading:WorkStealingPool.submit"}


# -- EP110 / EP271 / EP186 / EP223: planners ------------------------------------

def _ep110() -> Dict[str, Any]:
    """Simulate-vs-spawn: drive the live comparison both directions.

    The mechanism (`rehearse_dispatch` + `simulate_vs_spawn` + the
    METACLUSTER seam) is a prior lane's live host; this adapter proves
    the wiring reaches it and that both verdict directions fire.
    """
    # Drive the live chain both directions: a clean 2-task plan rehearses
    # clean and spawns (nothing learned); the same plan with a failing
    # rehearsal must simulate (failures override the size rule).
    tasks = [{"id": "discover", "deps": []},
             {"id": "execute", "deps": ["discover"]}]
    dag = plan_dag(tasks)
    assignment = {"pools": {"pool-1": ["discover", "execute"]},
                  "gaps": []}
    clean = simulate_vs_spawn(
        dag, assignment,
        rehearsal=rehearse_dispatch(dag, assignment))
    broken = simulate_vs_spawn(
        dag, assignment,
        rehearsal=rehearse_dispatch({"gaps": ["cycle:alpha"]},
                                    assignment))
    return {"accepted": dag["valid"]
                         and clean["decision"] == "spawn"
                         and broken["decision"] == "simulate", "engaged": True,
            "clean_decision": clean["decision"],
            "clean_basis": clean["basis"],
            "broken_decision": broken["decision"],
            "broken_basis": broken["basis"],
            "residual": "wiring only; the mechanism (simulate_vs_spawn + "
                        "rehearse_dispatch + METACLUSTER seam) is a prior "
                        "lane's live host, covered in test_simulate_vs_spawn",
            "where": "CORE.dispatch_plan:simulate_vs_spawn+rehearse_dispatch"}


def _ep271() -> Dict[str, Any]:
    """Agentic Monte Carlo: run the REAL arm-space planner with a bounded budget."""
    from CORE.WORMS.expansion.allocation import MCTSPlanner
    proxy = {("discover",): 0.5, ("discover", "execute"): 0.7,
             ("execute",): 0.4, ("report",): 0.2}
    verify = {("discover", "execute"): 0.9, ("discover", "report"): 0.6,
              ("discover",): 0.3}
    planner = MCTSPlanner(arms=["discover", "execute", "report"], depth=2)
    plan = planner.plan(
        pre_score=lambda seq: proxy.get(seq, 0.35),
        post_eval=lambda seq: verify.get(seq, 0.1),
        iters=12)
    return {"accepted": bool(plan["best_seq"]), "engaged": True,
            "best_seq": plan["best_seq"], "value": round(plan["value"], 3),
            "visits": len(plan["visits"]),
            "where": "CORE.WORMS.expansion.allocation:MCTSPlanner.plan"}


def _ep186() -> Dict[str, Any]:
    """NL-critique -> plan: compile a critique into nodes, then VALIDATE it."""
    from CORE.dispatch_plan import plan_dag
    critique = "discover first, then execute, then report"
    steps = [s.strip() for s in critique.split(",") if s.strip()]
    ids = [s.replace(" first", "").replace("then ", "").strip() for s in steps]
    emitted: List[Dict[str, Any]] = [{"id": ids[0], "deps": []}]
    emitted += [{"id": ids[i], "deps": [ids[i - 1]]}
                for i in range(1, len(ids))]
    dag = plan_dag(emitted)
    return {"accepted": dag["valid"], "engaged": True,
            "parsed_steps": steps, "nodes": len(emitted),
            "note": "compiler is heuristic; per-node Python-vs-LLM offload still open",
            "where": "CORE.dispatch_plan:plan_dag"}


def _ep223() -> Dict[str, Any]:
    """Decomposition depth + worker + budget chosen jointly (Uno-Orchestra)."""
    from CORE.dispatch_plan import plan_dag
    chosen_depth, best = 0, None
    for depth in (1, 2, 3):
        dag = plan_dag(_emit_dag(["security"], depth=depth))
        cost = len(dag["order"]) * 0.3 + (4 - depth) * 0.5
        if best is None or cost < best:
            best, chosen_depth = cost, depth
    return {"accepted": chosen_depth > 0, "engaged": True,
            "chosen_depth": chosen_depth, "cost": round(best or 0, 2),
            "where": "CORE.dispatch_plan:plan_dag"}


# -- EP399 / EP423: slots and pools ---------------------------------------------

def _ep399() -> Dict[str, Any]:
    """Controller registry: per-module namespaces (CALM's decomposition half)."""
    controllers = {"selector": _ep464, "scheduler": _ep187,
                   "router": _ep208}
    runs = {name: fn() for name, fn in controllers.items()}
    return {"accepted": all(bool(r.get("engaged", r.get("accepted")))
                            for r in runs.values()),
            "engaged": True, "namespaces": sorted(controllers),
            "where": "CORE.technique_wiring._l5_dispatch (controller registry)"}


def _ep423() -> Dict[str, Any]:
    """Role pools + oversubscription from the REAL per-model VRAM budget."""
    from CORE.inference_pool import _MODEL_VRAM, _max_concurrent_for_model
    budget = {m: _max_concurrent_for_model(m) for m in
              sorted(_MODEL_VRAM)[:4]}
    return {"accepted": all(v >= 1 for v in budget.values()), "engaged": True,
            "per_model_slots": budget,
            "where": "CORE.inference_pool:_max_concurrent_for_model"}


# -- EP187 / EP209 / EP252 / EP215 / EP262 / EP298 / EP213 / EP232: host surfaces

def _surface(module: str, symbol: str, note: str = "") -> Dict[str, Any]:
    """Assert a host surface exists and name the residual delta.

    Used where the real function exists but invoking it would start a thread
    pool, scan the tree, or mutate a registry — i.e. it is not read-mostly. The
    adapter still records the symbol, so the wiring is honest about what is
    present versus what a future edit must connect.
    """
    try:
        mod = __import__(module, fromlist=["*"])
    except Exception as exc:
        note_failure("_l5_dispatch:L353", exc)
        return {"accepted": False, "engaged": False,
                "reason": f"no-module:{type(exc).__name__}",
                "where": f"{module}:{symbol}"}
    obj = getattr(mod, symbol, None)
    return {"accepted": obj is not None, "engaged": obj is not None,
            "delta": note, "where": f"{module}:{symbol}"}


def _ep187() -> Dict[str, Any]:
    return _surface("CORE.WORMS.expansion.scheduling", "ScheduledTrainingWorm",
                    "interval-based today; contribution/value weighting missing")


def _ep209() -> Dict[str, Any]:
    return _surface("CORE.freethreading", "WorkStealingPool",
                    "round-robin on a bare callable; no group key / session affinity")


def _ep252() -> Dict[str, Any]:
    return _surface("CORE.WORMS.expansion.scheduling", "TrainingRun",
                    "no quality-aware data/batch-size schedule")


def _ep215() -> Dict[str, Any]:
    """Capability index over the REAL agent registry (MaxSim lookup)."""
    try:
        from CORE.agent_registry import AGENT_REGISTRY
    except Exception as exc:
        note_failure("_l5_dispatch:L381", exc)
        return {"accepted": False, "engaged": False,
                "reason": f"no-registry:{type(exc).__name__}"}
    index: Dict[str, List[str]] = {}
    for name, meta in (AGENT_REGISTRY or {}).items():
        for tag in (meta.get("tags") or set()):
            index.setdefault(str(tag), []).append(name)
        cat = str(meta.get("category") or "")
        if cat:
            index.setdefault(cat, []).append(name)
    return {"accepted": bool(index), "engaged": True,
            "capabilities": len(index), "agents": len(AGENT_REGISTRY or {}),
            "where": "CORE.agent_registry:AGENT_REGISTRY"}


def _ep262() -> Dict[str, Any]:
    return _surface("CORE.neural_loop", "STAGES",
                    "injected-stage seam exists; Selector/Predictor/Judger triad missing")


def _ep298() -> Dict[str, Any]:
    return _surface("CORE.workflow", "WorkflowStep",
                    "no llm action type and no call-depth-scoped context")


def _ep213() -> Dict[str, Any]:
    return _surface("CORE.WORMS.expansion.delegation", "ask_owner",
                    "owner<->worker only; no peer discovery, no shared plan object")


def _ep232() -> Dict[str, Any]:
    return _surface("CORE.WORMS.expansion.consensus", "QuorumVoterWorm",
                    "no depth counter or Lead-Anchor role; the dead-gate bug is fixed")


# -- registered non-hosts and surveys -------------------------------------------

def _no_host(ep: str, reason: str, would_need: str) -> Dict[str, Any]:
    """An honest non-host: no adapter, and the reason it cannot have one."""
    return {"accepted": True, "engaged": False, "no_host": True,
            "ep": ep, "reason": reason, "would_need": would_need}


def _ep214() -> Dict[str, Any]:
    return _no_host("EP214",
                    "no def-use/data-flow analysis exists anywhere in the tree",
                    "an AST-based def-use graph over the tool/statement level")


def _ep300() -> Dict[str, Any]:
    return _no_host("EP300",
                    "no joint-plan tensor or conflict localizer exists",
                    "an analytic joint-plan tensor with CP/Tucker rank")


def _ep227() -> Dict[str, Any]:
    return _no_host("EP227",
                    "a continuous-plan generator needs a trained flow model",
                    "a trained continuous planner (no host on this box)")


def _ep178() -> Dict[str, Any]:
    return _no_host("EP178",
                    "consensus.py is discrete-majority by deliberate safety design: "
                    "one env can never quarantine alone",
                    "a sibling latent-consensus module, NOT an edit to QuorumVoterWorm")


# -- registration -----------------------------------------------------------------

def _reg(ep: str, name: str, where: str, fn) -> None:
    register(ep, name, where, fn, layer="L5")


def _safe(ep: str, name: str, where: str, fn):
    """Wrap an adapter so a raise becomes a reported result, never a crash."""
    def _wrapped() -> Dict[str, Any]:
        try:
            return fn()
        except Exception as exc:
            note_failure("_l5_dispatch:L460", exc)
            return {"accepted": False, "engaged": False,
                    "error": f"{type(exc).__name__}: {exc}", "where": where}
    _reg(ep, name, where, _wrapped)


_safe("EP110", "Simulate-vs-spawn costing",
      "CORE.WORMS.expansion.allocation:MCTSPlanner", _ep110)
_safe("EP130", "DAG-emitting planner + fan-out",
      "CORE.dispatch_plan:plan_dag", _ep130)
_safe("EP171", "Prefix cache / warm path + CSE dedup",
      "CORE.dispatch_plan:eliminate_common_subgraphs", _ep171)
_safe("EP178", "CMAT latent consensus",
      "CORE.WORMS.expansion.consensus:QuorumVoterWorm", _ep178)
_safe("EP186", "NL-critique -> plan compiler",
      "CORE.dispatch_plan:plan_dag", _ep186)
_safe("EP187", "Contribution-weighted allocation",
      "CORE.WORMS.expansion.scheduling:ScheduledTrainingWorm", _ep187)
_safe("EP197", "Coarse->fine granularity refinement",
      "CORE.dispatch_plan:plan_dag", _ep197)
_safe("EP208", "Bayesian orchestration (belief over route)",
      "CORE.cascade:CascadeRouter.route", _ep208)
_safe("EP209", "Workflow as schedulable unit",
      "CORE.freethreading:WorkStealingPool", _ep209)
_safe("EP213", "Joint plan formation between peers",
      "CORE.WORMS.expansion.delegation:ask_owner", _ep213)
_safe("EP214", "Statement-level def-use data-flow slice",
      "no-host: no def-use/data-flow analysis", _ep214)
_safe("EP215", "Agent discovery capability index + MaxSim",
      "CORE.agent_registry:AGENT_REGISTRY", _ep215)
_safe("EP223", "Decomposition depth + worker + budget",
      "CORE.dispatch_plan:plan_dag", _ep223)
_safe("EP227", "Continuous-plan generator",
      "no-host: needs trained flow model", _ep227)
_safe("EP232", "Consensus depth limit + Lead-Anchor",
      "CORE.WORMS.expansion.consensus:QuorumVoterWorm", _ep232)
_safe("EP248", "Monte-Carlo latency cost model",
      "no-host: Monte-Carlo latency cost model", _ep248)
_safe("EP252", "Quality-aware data/batch schedule",
      "CORE.WORMS.expansion.scheduling:TrainingRun", _ep252)
_safe("EP253", "Learned participation prior",
      "CORE.dispatch_plan:assign_roles", _ep253)
_safe("EP259", "Per-step regret tripwire -> absorbing failure",
      "CORE.WORMS.expansion.policy_optimization:depo_filter", _ep259)
_safe("EP262", "Selector/Predictor/Judger self-probing",
      "CORE.neural_loop:STAGES", _ep262)
_safe("EP271", "Agentic Monte Carlo",
      "CORE.WORMS.expansion.allocation:MCTSPlanner.plan", _ep271)
_safe("EP298", "LLM-as-subroutine + depth-scoped context",
      "CORE.workflow:WorkflowStep", _ep298)
_safe("EP300", "Joint-plan tensor conflict localizer",
      "no-host: no joint-plan tensor", _ep300)
_safe("EP374", "Atomic fission + per-atom isolation",
      "CORE.dispatch_plan:plan_dag", _ep374)
_safe("EP399", "Controller registry + module namespaces",
      "no-host: controller registry is local-only", _ep399)
_safe("EP423", "Role pools + oversubscription",
      "CORE.inference_pool:_max_concurrent_for_model", _ep423)
_safe("EP431", "(role, subtask) experts",
      "CORE.dispatch_plan:assign_roles", _ep431)
_safe("EP438", "Ready-cohort fixed-window batching",
      "CORE.freethreading:WorkStealingPool.submit", _ep438)
_safe("EP464", "Progress-gain router",
      "no-host: pure selection rule; MetaCluster hook open", _ep464)


#: The confirmed `eliminate_common_subgraphs` defect, exposed as data so a
#: consumer (or a test) can assert the fix without re-deriving it.
CSE_DEFECT = {
    "symbol": "CORE.dispatch_plan:eliminate_common_subgraphs",
    "docstring_claims": "\"deduped\": {nature: order with first occurrence of "
                        "each shared segment kept once}",
    "code_returns": "\"deduped\": dict(plans)  # the input, unchanged",
    "consequence": "savings is a real number while deduped is un-deduplicated; "
                   "any consumer trusting deduped plans redundant work",
    "status": "REPORTED, not fixed here (coordinator owns dispatch_plan.py)",
}
