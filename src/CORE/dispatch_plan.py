#!/usr/bin/env python3
"""dispatch_plan.py — Plan-modeled dispatch (SOTA blueprint P2b, L5).

Composes two L5 steals as pure helpers over the existing flat dispatch
(MetaCluster orchestrates per-nature with no plan model — see
CORE/WORMS/METACLUSTER.py):

- Helium (plan modeling): model a multi-nature dispatch as a task DAG and
  topologically order it before execution, so shared prefixes are visible
  instead of being re-discovered per nature at runtime.
- Common-subgraph elimination: find identical ordered task segments shared
  by >=2 nature plans and report dedup savings (run once, fan results out).

Deliberately stdlib-only and LLM-free. Plan verdicts ride the existing
`verification_gate` ledger entry (subject "dispatch:<name>") — no new ledger
type. This module never executes dispatches; it validates plans and reports
sharing (remediate-don't-deny, PolicyGuide rule).

Usage:
    from CORE.dispatch_plan import plan_dag, eliminate_common_subgraphs

    dag = plan_dag([{"id": "discover", "deps": []},
                    {"id": "execute", "deps": ["discover"]}])
    sharing = eliminate_common_subgraphs(
        {"security": ["discover", "execute"],
         "monitor": ["discover", "execute", "report"]})
"""

from typing import Any, Dict, List, Set

#: Fail-open swallows recorded when the runtime recorder is unavailable. An
#: unrecorded swallow is indistinguishable from success, so every handler in
#: this module records first (degradation stays observable).
_SWALLOWED: List[str] = []


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # runtime absent: stay importable
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        _SWALLOWED.append("%s:no-recorder" % where)


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow. Never raises."""
    try:
        note_failure(where, exc)
    except Exception as _e:
        _SWALLOWED.append("%s:%s" % (where, type(_e).__name__))


def _as_float(value: Any) -> float:
    """A task's numeric field as float. Unusable input is 0.0, never a raise."""
    if isinstance(value, bool):
        return float(value)          # bool is an int subclass; keep it explicit
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        _note_failure("dispatch_plan:task-magnitude", exc)
        return 0.0


# -- Helium: DAG plan modeling ------------------------------------------------
def plan_dag(tasks: List[Dict[str, Any]], *,
             split_on_capability: bool = False,
             tool_boundaries: Dict[str, str] | None = None) -> Dict[str, Any]:
    """Model a dispatch as a DAG and topologically order it (Helium steal).

    tasks: [{"id" (required, non-empty), "deps" (optional list of ids),
             "complexity" (optional number), "priority" (optional number),
             "capabilities" (optional list of tool/capability names)}].

    Returns {"order", "levels" ({id: depth}), "valid", "gaps"} — valid iff
    every dep names a known task and the graph is acyclic. Gaps name what's
    wrong (repair targets): "task missing id", "duplicate-task:<id>",
    "unknown-dep:<task>:<dep>", "cycle-detected(...)".

    Two SOTA-row additions, each inert unless asked for:

    * "complexity" / "priority" ({id: number}, 0.0 when undeclared) and
      "ready_order": within each `levels` tier, tasks ordered by descending
      priority then descending complexity, ties broken by `order`. `order`
      itself is NEVER reordered by them, so every existing caller keeps its
      one behaviour while a learner/router host (EP130, EP197) reads the
      signal instead of re-deriving granularity from `levels` alone.
    * split_on_capability=True splits any task whose `capabilities` span more
      than one tool boundary into one sub-task per boundary
      ("<id>#<boundary>", each inheriting the original deps) plus a join under
      the original id that depends on all of them; the split is reported in
      "fissions". That is EP374's atomic fission: a node crossing a tool
      boundary is two nodes, not one. tool_boundaries maps capability ->
      boundary name; an unmapped capability is its own boundary.
    """
    gaps: List[str] = []
    ids: List[str] = []
    deps_of: Dict[str, List[str]] = {}
    complexity: Dict[str, float] = {}
    priority: Dict[str, float] = {}
    capabilities: Dict[str, List[str]] = {}
    for t in tasks or []:
        tid = str((t or {}).get("id", "")).strip()
        if not tid:
            gaps.append("task missing id")
            continue
        if tid in deps_of:
            gaps.append(f"duplicate-task:{tid}")
            continue
        ids.append(tid)
        raw = (t or {}).get("deps", []) or []
        deps_of[tid] = [str(d).strip() for d in raw if str(d).strip()]
        complexity[tid] = _as_float((t or {}).get("complexity"))
        priority[tid] = _as_float((t or {}).get("priority"))
        caps = (t or {}).get("capabilities", []) or []
        capabilities[tid] = [str(c).strip() for c in caps if str(c).strip()]
    for tid in ids:
        for dep in deps_of[tid]:
            if dep not in deps_of:
                gaps.append(f"unknown-dep:{tid}:{dep}")

    # -- EP374: fission a task that crosses a tool boundary (opt-in) --------
    # A task declaring capabilities in more than one boundary becomes one
    # sub-task per boundary plus a join under its own id, so the join keeps
    # the original dependency position and every existing reader of `order`
    # still sees the id it knows.
    fissions: List[Dict[str, Any]] = []
    if split_on_capability:
        boundaries = dict(tool_boundaries or {})
        for tid in list(ids):
            groups: List[str] = []
            for cap in capabilities.get(tid) or []:
                bound = str(boundaries.get(cap, cap))
                if bound not in groups:
                    groups.append(bound)
            if len(groups) < 2:
                continue                    # one boundary: nothing to split
            inherited = list(deps_of.get(tid) or [])
            subs = [f"{tid}#{bound}" for bound in groups]
            for sub, bound in zip(subs, groups):
                deps_of[sub] = list(inherited)
                complexity[sub] = complexity.get(tid, 0.0)
                priority[sub] = priority.get(tid, 0.0)
                capabilities[sub] = [
                    cap for cap in capabilities.get(tid) or []
                    if str(boundaries.get(cap, cap)) == bound]
            deps_of[tid] = list(subs)       # the original id becomes the join
            complexity[tid] = 0.0
            at = ids.index(tid)
            ids[at + 1:at + 1] = subs
            fissions.append({"task": tid, "boundaries": groups,
                             "subtasks": subs, "join": tid})

    # Kahn's algorithm over known edges only.
    indegree = {tid: 0 for tid in ids}
    children: Dict[str, List[str]] = {tid: [] for tid in ids}
    for tid in ids:
        for dep in deps_of[tid]:
            if dep in deps_of:
                indegree[tid] += 1
                children[dep].append(tid)
    order: List[str] = sorted([t for t in ids if indegree[t] == 0])
    levels: Dict[str, int] = {t: 0 for t in order}
    queue = list(order)
    while queue:
        node = queue.pop(0)
        for child in sorted(children[node]):
            indegree[child] -= 1
            levels[child] = max(levels.get(child, 0), levels[node] + 1)
            if indegree[child] == 0:
                order.append(child)
                queue.append(child)
    if len(order) != len(ids):
        stuck = sorted(set(ids) - set(order))
        gaps.append(f"cycle-detected({','.join(stuck)})")
    for tid in ids:
        levels.setdefault(tid, 0)

    # -- EP130/EP197: a within-tier readiness signal the hosts can act on ---
    position = {tid: i for i, tid in enumerate(order)}
    tiers: Dict[int, List[str]] = {}
    for tid in order:
        tiers.setdefault(levels.get(tid, 0), []).append(tid)
    ready_order: List[str] = []
    for depth in sorted(tiers):
        ready_order.extend(sorted(
            tiers[depth],
            key=lambda t: (-priority.get(t, 0.0), -complexity.get(t, 0.0),
                           position.get(t, 0))))
    return {"order": order, "levels": levels,
            "valid": not gaps, "gaps": gaps,
            "complexity": {t: complexity.get(t, 0.0) for t in order},
            "priority": {t: priority.get(t, 0.0) for t in order},
            "ready_order": ready_order,
            "fissions": fissions}


# -- Common-subgraph elimination ----------------------------------------------
def _first_segment_at(order: List[str], seg: tuple) -> int:
    """Index of the first occurrence of `seg` in `order` (-1 when absent)."""
    n = len(seg)
    if n == 0 or n > len(order):
        return -1
    for i in range(len(order) - n + 1):
        if tuple(order[i:i + n]) == seg:
            return i
    return -1


def eliminate_common_subgraphs(plans: Dict[str, List[str]],
                               min_len: int = 2) -> Dict[str, Any]:
    """Find ordered task segments shared by >=2 nature plans (CSE steal).

    plans: {nature: [task ids in execution order]}. A shared segment is a
    contiguous subsequence of length >= min_len appearing in >=2 natures'
    orders.

    Returns {"shared": [{"segment", "natures"}], "savings", "owners":
    [{"segment", "owner", "inherited_by"}], "deduped": {nature: order as
    actually run}, "elision": {nature: [task ids dropped]}, "executed_slots":
    {"before", "after"}}.

    `savings` is the redundant task-slot count the sharing creates (total
    shared slots beyond the first copy). `owners` names the ONE nature that
    executes each shared segment; every other nature holding it has its FIRST
    occurrence elided in `deduped`, because it inherits the owner's results.
    So the reducer is real -- "run once, fan results out" -- and `elision`
    records exactly which task ids each nature stopped running, which is what
    makes the saving auditable instead of merely reported. `deduped` equals
    the input for every nature that owns all of its shared segments.
    """
    plans = {str(k): [str(t) for t in (v or [])]
             for k, v in (plans or {}).items()}
    span: Dict[tuple, List[str]] = {}
    for nature, order in plans.items():
        seen = set()
        for i in range(len(order)):
            for j in range(i + int(min_len or 2), len(order) + 1):
                seg = tuple(order[i:j])
                if seg not in seen:
                    seen.add(seg)
                    span.setdefault(seg, []).append(nature)
    # Keep only maximal segments (drop any segment contained in a longer
    # shared one with identical nature sets).
    shared = [{"segment": list(seg), "natures": sorted(nats)}
              for seg, nats in span.items() if len(nats) >= 2]
    shared.sort(key=lambda s: (-len(s["segment"]), -len(s["natures"]),
                               s["segment"]))
    maximal = []
    for cand in shared:
        if any(set(cand["segment"]) <= set(kept["segment"])
               and len(cand["segment"]) < len(kept["segment"])
               and set(cand["natures"]) <= set(kept["natures"])
               for kept in maximal):
            continue
        maximal.append(cand)
    savings = sum(len(s["segment"]) * (len(s["natures"]) - 1)
                  for s in maximal)

    # -- the reducer: one deterministic owner per shared segment ------------
    # `natures` is already sorted, so the owner is stable across runs; a
    # non-owner elides its first occurrence and inherits the owner's result.
    owners: List[Dict[str, Any]] = []
    owner_of: Dict[tuple, str] = {}
    for info in maximal:
        seg = tuple(info["segment"])
        nats = list(info["natures"])
        owner_of[seg] = nats[0]
        owners.append({"segment": list(seg), "owner": nats[0],
                       "inherited_by": nats[1:]})

    deduped: Dict[str, List[str]] = {}
    elision: Dict[str, List[str]] = {}
    for nature, order in plans.items():
        drop: Set[int] = set()
        for seg, owner in owner_of.items():
            if owner == nature:
                continue
            at = _first_segment_at(order, seg)
            if at >= 0:
                drop.update(range(at, at + len(seg)))
        if drop:
            deduped[nature] = [t for i, t in enumerate(order) if i not in drop]
            elision[nature] = [order[i] for i in sorted(drop)]
        else:
            deduped[nature] = list(order)

    return {"shared": maximal, "savings": savings, "owners": owners,
            "deduped": deduped, "elision": elision,
            "executed_slots": {"before": sum(len(v) for v in plans.values()),
                               "after": sum(len(v) for v in deduped.values())}}


# -- Ledger citation (P1 gate, remediate-don't-deny) ---------------------------
def record_dispatch_plan(name: str, result: Dict[str, Any]) -> str:
    """Cite a plan verdict on the hash-chained ledger (fails open).

    subject "dispatch:<name>", accepted iff result valid / savings >= 0
    (sharing reports are findings, never denies). Returns the citation.
    """
    accepted = bool((result or {}).get("valid", True))
    gaps = list((result or {}).get("gaps", []) or [])
    remediations = [f"repair plan gap: {g}" for g in gaps]
    citation = ""
    try:
        from CORE import audit_trail as _at  # noqa: PLC0415 (lazy: no cycle)
        citation = _at.audit_verification_gate(
            f"dispatch:{str(name or '?').strip()}", accepted,
            [f"plan-{('ok' if accepted else 'gaps')}:{len(gaps)}"],
            remediations)
    except Exception as exc:                      # noqa: BLE001 — ledger best-effort
        _note_failure("dispatch_plan:dag-ledger", exc)
    return citation


# -- Agora: role pools + affinity scheduling (P2b slice 2) ----------------------
def assign_roles(order: List[str],
                 task_roles: Dict[str, str],
                 role_pools: Dict[str, Any],
                 participation: Dict[str, float] | None = None) -> Dict[str, Any]:
    """Pool dispatch tasks by role with affinity scheduling (Agora steal).

    order: task ids in execution order (a plan_dag "order").
    task_roles: {task: role} (inference vs orchestration vs tools, ...).
    role_pools: {role: pool} (cores pooled by role). A role may instead map to
    a LIST of candidate pools; a single string keeps behaving exactly as
    before, and a list with no `participation` prior picks its first entry.
    participation: optional learned-participation prior {pool: weight}, e.g.
    summed Thompson posteriors. When supplied it selects among a role's
    candidate pools (highest weight; ties by declared order) instead of
    always taking the first — EP253's learned participation replacing the
    static map. Pools absent from the prior weigh 0.0.

    Returns {"assignment" ({task: {"role", "pool"}}, plus "prior" per task
    iff a prior was supplied), "pools" ({pool: [tasks in order]}),
    "locality_hits" (adjacent same-pool pairs — affinity preserved, no pool
    switch), "pool_weights" ({pool: weight} for pools actually used),
    "prior_used" (True iff the prior changed at least one choice), "valid",
    "gaps"}. Gaps: "unassigned-task:<t>" (no role),
    "role-for-unknown-task:<t>" (role for a task outside the order),
    "no-pool-for-role:<role>".
    """
    order = [str(t) for t in (order or [])]
    in_order = set(order)
    task_roles = {str(k): str(v).strip() for k, v in (task_roles or {}).items()}
    candidates: Dict[str, List[str]] = {}
    for key, value in (role_pools or {}).items():
        role = str(key).strip()
        if isinstance(value, (list, tuple, set)):
            pool_list = [str(p).strip() for p in value if str(p).strip()]
        else:
            single = str(value).strip()
            pool_list = [single] if single else []
        candidates[role] = pool_list
    prior = {str(k).strip(): _as_float(v)
             for k, v in (participation or {}).items()}
    prior_used = False
    gaps: List[str] = []
    assignment: Dict[str, Dict[str, Any]] = {}
    for t in task_roles:
        if t not in in_order:
            gaps.append(f"role-for-unknown-task:{t}")
    for t in order:
        role = task_roles.get(t, "")
        if not role:
            gaps.append(f"unassigned-task:{t}")
            assignment[t] = {"role": "", "pool": ""}
            continue
        available = candidates.get(role) or []
        if not available:
            gaps.append(f"no-pool-for-role:{role}")
            assignment[t] = {"role": role, "pool": ""}
            continue
        if prior:
            chosen = max(range(len(available)),
                         key=lambda i: (prior.get(available[i], 0.0), -i))
            pool = available[chosen]
            prior_used = prior_used or chosen != 0
            assignment[t] = {"role": role, "pool": pool,
                             "prior": prior.get(pool, 0.0)}
            continue
        assignment[t] = {"role": role, "pool": available[0]}
    pools: Dict[str, List[str]] = {}
    for t in order:
        pool = assignment[t]["pool"] or "unpooled"
        pools.setdefault(pool, []).append(t)
    pools_seq = [assignment[t]["pool"] for t in order]
    locality_hits = sum(1 for a, b in zip(pools_seq, pools_seq[1:])
                        if a and a == b)
    pool_weights = {pool: prior.get(pool, 0.0) for pool in pools} if prior else {}
    return {"assignment": assignment, "pools": pools,
            "locality_hits": locality_hits,
            "pool_weights": pool_weights, "prior_used": prior_used,
            "valid": not gaps, "gaps": gaps}


# -- L4: rehearse-before-execute + backtrack-with-lesson -------------------------
def rehearse_dispatch(dag: Dict[str, Any],
                      assignment: Dict[str, Any]) -> Dict[str, Any]:
    """Dry-run a planned dispatch and predict failures (rehearsal slice).

    Never executes: folds plan gaps and assignment gaps into predicted
    failures plus per-pool load (oversubscription signal — Agora). Returns
    {"rehearsed": True, "predicted_failures", "pool_load", "go"} — go iff
    no predicted failures.
    """
    dag = dag or {}
    assignment = assignment or {}
    predicted = ([f"plan-gap:{g}" for g in dag.get("gaps", []) or []]
                 + [f"assign-gap:{g}" for g in assignment.get("gaps", []) or []])
    pool_load = {pool: len(tasks)
                 for pool, tasks in (assignment.get("pools", {}) or {}).items()}
    return {"rehearsed": True, "predicted_failures": predicted,
            "pool_load": pool_load, "go": not predicted}


def record_backtrack(branch: str, corrective_lesson: str,
                     _from_rcpd: bool = False) -> str:
    """Prune a failed branch but keep its corrective lesson (LoongReflect).

    Always accepted — backtracking is a finding, never a deny. The lesson
    rides the ledger as the remediation so the failure mode stays measured.
    Returns the citation (fails open).

    Central RCPD hook: every backtrack also feeds the flip counter via
    monitor_backtrack, so the ladder step-downs and plan prunes that call
    this are measured as flips without each caller wiring RCPD itself.
    `_from_rcpd` is the recursion guard for RCPDEarlyExit.evaluate_flip,
    which records through this function.
    """
    branch = str(branch or "?").strip()
    lesson = str(corrective_lesson or "").strip() or "no lesson recorded"
    _remember_prefix(branch, lesson)
    if not _from_rcpd:
        try:
            from CORE.WORMS.expansion.rcpd_early_exit import (  # noqa: PLC0415 (lazy: no cycle)
                monitor_backtrack as _monitor)
            _monitor(branch, lesson)
        except Exception as exc:                  # noqa: BLE001 — telemetry best-effort
            _note_failure("dispatch_plan:rcpd-hook", exc)
    citation = ""
    try:
        from CORE import audit_trail as _at  # noqa: PLC0415 (lazy: no cycle)
        citation = _at.audit_verification_gate(
            f"backtrack:{branch}", True, [f"pruned:{branch}"],
            [lesson])
    except Exception as exc:                      # noqa: BLE001 — ledger best-effort
        _note_failure("dispatch_plan:subgraph-ledger", exc)
    return citation


#: Pivotal-turn prefix memory (EP345): branch -> lesson, capped at 50.
#: record_backtrack prunes AND remembers; reuse_prefix reads back.
_PREFIX_MEMORY: dict = {}


def _remember_prefix(branch: str, lesson: str) -> None:
    """Keep the pruned prefix's lesson for reuse. Bounded, in-memory."""
    _PREFIX_MEMORY[str(branch)] = str(lesson)
    while len(_PREFIX_MEMORY) > 50:
        _PREFIX_MEMORY.pop(next(iter(_PREFIX_MEMORY)))


def reuse_prefix(branch: str) -> dict:
    """Prefix reuse + suffix isolation (EP345, reuse half).

    Returns the remembered lesson for a branch ("" if none) plus the
    isolation rule: suffixes after a pivotal turn are quarantined from the
    reused prefix — the caller must not blend them. The caller-side
    enforcement (actually splitting a live plan at the pivotal turn) stays
    deferred: no live plan object exists at this layer.
    """
    lesson = _PREFIX_MEMORY.get(str(branch or ""), "")
    return {"branch": str(branch or "?"), "lesson": lesson,
            "reusable": bool(lesson),
            "isolation": ("suffix-after-pivotal-turn is quarantined; "
                          "do not blend into the reused prefix")}


def locate_pivot(turns: Any) -> Dict[str, Any]:
    """Pivotal-turn identification (EP345, locate half).

    reuse_prefix remembers lessons but never said WHERE the backtrack
    point is. Given an ordered turn list [{branch, ok}], the pivotal turn
    is the first failing turn: branches before it form the reusable
    prefix, branches from it on form the quarantined suffix (the
    isolation rule reuse_prefix states). All-ok runs have no pivot
    (found=False); empty input is unlocatable, not a pivot at zero.
    Pure function: no memory, no ledger, no plan mutation.
    """
    seq = list(turns or [])
    if not seq:
        return {"found": False, "pivot_index": None, "pivot_branch": None,
                "reusable_prefix": [], "quarantined_suffix": [],
                "reason": "no-turns"}
    branches = [str((t or {}).get("branch", "?")) for t in seq]
    pivot = None
    for i, t in enumerate(seq):
        if not bool((t or {}).get("ok", True)):
            pivot = i
            break
    if pivot is None:
        return {"found": False, "pivot_index": None, "pivot_branch": None,
                "reusable_prefix": branches, "quarantined_suffix": [],
                "reason": "all-ok"}
    return {"found": True, "pivot_index": pivot,
            "pivot_branch": branches[pivot],
            "reusable_prefix": branches[:pivot],
            "quarantined_suffix": branches[pivot:],
            "reason": "first-failure"}


# -- L5: simulate-vs-spawn costing (EP110) --------------------------------------
#: Default prices: a rehearsal costs ~8% of a spawn per task, so confirming an
#: n-task plan costs sim*n while blindly spawning it risks spawn*n.
#: Confirmation pays for itself once it can avoid a single wasted spawn, i.e.
#: at n >= spawn/sim -- the threshold is DERIVED from the prices, never chosen.
SIM_COST_PER_TASK = 0.08
SPAWN_COST_PER_TASK = 1.0


def simulate_vs_spawn(dag, roles, rehearsal=None, *,
                      sim_cost_per_task=SIM_COST_PER_TASK,
                      spawn_cost_per_task=SPAWN_COST_PER_TASK):
    """Price a rehearsal against the spawn it replaces (EP110).

    INFORMATION-based verdict, never a bare cost ratio: at the default prices
    sim_total < spawn_total holds for every plan of two or more tasks, so a
    ratio rule would return "simulate" unconditionally and prove nothing. The
    decision instead rides (a) predicted failures from the rehearsal, which
    override everything, and (b) a confirm threshold DERIVED from the prices
    (spawn/sim -- the plan size at which confirmation pays for itself).

    Never raises: costing runs on the dispatch hot path, so degenerate input
    reports a verdict (basis EP110:degenerate) instead of throwing. Returns
    {"decision", "predicted_failures" (count), "savings", "n_tasks",
    "confirm_threshold", "basis", "sim_total", "spawn_total"}.
    """
    try:
        order = list((dag or {}).get("order") or [])
    except Exception:
        order = []
    n_tasks = len(order)
    sim = _as_float(sim_cost_per_task)
    spawn = _as_float(spawn_cost_per_task)
    threshold = int(spawn / sim) if sim > 0 else 0
    if isinstance(rehearsal, dict):
        predicted = rehearsal.get("predicted_failures") or []
    else:
        predicted = []
    try:
        n_fail = len(predicted)
    except TypeError:
        n_fail = 0 if not predicted else 1
    sim_total = sim * n_tasks
    spawn_total = spawn * n_tasks
    if not isinstance(rehearsal, dict) and not order:
        decision = "spawn"
        basis = "EP110:degenerate-empty-plan"
    elif n_fail:
        decision = "simulate"
        basis = "EP110:predicted-failures-%d" % n_fail
    elif n_tasks >= threshold:
        decision = "simulate"
        basis = "EP110:size-confirmation-%d-ge-%d" % (n_tasks, threshold)
    else:
        decision = "spawn"
        basis = "EP110:clean-small-%d-lt-%d" % (n_tasks, threshold)
    savings = round(spawn_total - sim_total, 6) if decision == "simulate" else 0.0
    return {"decision": decision, "predicted_failures": n_fail,
            "savings": savings, "n_tasks": n_tasks,
            "confirm_threshold": threshold, "basis": basis,
            "sim_total": sim_total, "spawn_total": spawn_total}
