"""_l4_exec.py — L4 (execution) wirings: rehearse -> transact -> undo.

Layer thesis from the review: L4 techniques are the ones that make an execution
*recoverable* — rehearse before committing, localize a failure to a phase, and be
able to undo a branch while keeping its lesson. The module-level defect this slice
repairs is the one the deep dive found in every one of these modules:

    the instrument exists, and NOTHING CONSUMES IT.

The sharpest case is EP129. `BreakerRegistry.get_dead_letters()` and
`get_dead_letter_summary()` are a complete, tested, thread-safe read API over the
dead-letter queue (circuit_breaker.py:313 / :339) and no module in the tree ever
calls them — a dead-letter queue nobody reads is a write-only log. So this slice
does not register a wrapper that merely *calls* the API and reports a count: it
ships `replan_from_dead_letters()`, the missing consumer, which reads the queue and
returns an actual replan. `DeadLetter` carries no phase field, so rather than
inventing one the phase is INFERRED from the error text by `infer_phase()` and
labelled `phase_inferred: True` in every result.

Every adapter here is read-only apart from EP129's bounded demonstration, which
uses a PRIVATE breaker registry so the global DLQ is never touched, and read-mostly
per the slice contract. Nothing imports torch, numpy, or the network.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Sequence

from CORE.technique_runtime import note_failure, register

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["replan_from_dead_letters", "infer_phase", "ADAPTERS"]


# -- shared helpers -------------------------------------------------------------

def _repo_root() -> Path:
    """azazael/ — the directory holding src/ and tools/."""
    return Path(__file__).resolve().parents[3]


_SYMBOL_CACHE: Dict[str, set] = {}


def _symbols(rel: str) -> set:
    """Top-level names in a repo file, plus 'Class.method', without importing it.

    AST rather than import on purpose: this slice asserts whether a host exists,
    and importing a host to find out would run its module-level side effects for
    a question that is purely static. Returns an empty set when the file is
    missing or unparseable, which is the correct answer for "no host".
    """
    if rel in _SYMBOL_CACHE:
        return _SYMBOL_CACHE[rel]
    found: set = set()
    path = _repo_root() / rel
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        note_failure("_l4_exec:L60", exc)
        _SYMBOL_CACHE[rel] = found
        return found
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        found.add(f"{node.name}.{sub.name}")
    _SYMBOL_CACHE[rel] = found
    return found


def _exists(rel: str) -> bool:
    return (_repo_root() / rel).is_file()


def _result(**kw: Any) -> Dict[str, Any]:
    return kw


# -- EP129: the missing consumer of the dead-letter queue ------------------------

#: Error-text -> phase. Ordered: the first matching marker wins. Kept deliberately
#: small and explainable; the point is that a phase LABEL exists at all.
_PHASE_MARKERS: Sequence[tuple] = (
    ("response", ("timeout", "timed out", "connection", "http", "socket",
                  "rate limit", "429", "503")),
    ("planning", ("no such file", "modulenotfound", "importerror",
                  "unknown-dep", "cycle", "unassigned", "plan-gap")),
    ("execution", ("assert", "valueerror", "keyerror", "typeerror",
                   "indexerror", "runtimeerror", "attributeerror")),
)


def infer_phase(error: str) -> str:
    """Infer a failure phase from an error string (EP129 taxonomy).

    `DeadLetter` has no phase field, so this is an INFERENCE, not a read: every
    result from `replan_from_dead_letters()` is stamped `phase_inferred: True` so
    the inference is never mistaken for the schema-native field the technique
    actually wants.
    """
    text = str(error or "").lower()
    for phase, markers in _PHASE_MARKERS:
        if any(m in text for m in markers):
            return phase
    return "execution"


def _signature(error: str) -> str:
    """Collapse an error to a repeatable signature (first 60 chars, lowercased)."""
    text = " ".join(str(error or "").lower().split())
    return text[:60] or "unknown"


def replan_from_dead_letters(name: str | None = None,
                             since: float = 0.0) -> Dict[str, Any]:
    """Read the DLQ and return a replan (EP129 replan-from-feedback).

    The consumer the tree was missing: `CircuitBreaker.execute()` has always
    WRITTEN a `DeadLetter` on failure and `BreakerRegistry` has always offered
    `get_dead_letters()` / `get_dead_letter_summary()` to read them back, but no
    production module called either, so the feedback half of the loop did not
    exist. This groups the queue by error signature, labels a phase per group,
    and returns an ordered replan — the same group failed twice or more is the
    early-stop signal, and the rest are retry candidates.

    Returns {"n_dead_letters", "groups", "replan", "early_stop",
    "phase_inferred", "summary", "gaps"}. Read-only: it never clears the queue
    (purging is an operator decision, not a planner's).
    """
    out: Dict[str, Any] = {"n_dead_letters": 0, "groups": [], "replan": [],
                           "early_stop": [], "phase_inferred": True,
                           "summary": {}, "gaps": []}
    try:
        from CORE.circuit_breaker import breaker_registry
        letters = breaker_registry.get_dead_letters(name=name, since=since) or []
        out["summary"] = breaker_registry.get_dead_letter_summary() or {}
    except Exception as exc:
        note_failure("_l4_exec:L140", exc)
        out["gaps"].append(f"dlq-unavailable:{type(exc).__name__}")
        return out

    out["n_dead_letters"] = len(letters)
    by_sig: Dict[str, Dict[str, Any]] = {}
    for dl in letters:
        err = getattr(dl, "error", "") or ""
        sig = _signature(err)
        row = by_sig.setdefault(sig, {
            "signature": sig, "phase": infer_phase(err), "count": 0,
            "breakers": set(), "first_ts": getattr(dl, "timestamp", 0.0),
            "last_ts": getattr(dl, "timestamp", 0.0),
            "max_retries": 0, "example": err[:160]})
        row["count"] += 1
        row["breakers"].add(getattr(dl, "worm_name", "?"))
        row["first_ts"] = min(row["first_ts"], getattr(dl, "timestamp", 0.0))
        row["last_ts"] = max(row["last_ts"], getattr(dl, "timestamp", 0.0))
        row["max_retries"] = max(row["max_retries"],
                                 int(getattr(dl, "retry_count", 0) or 0))

    groups: List[Dict[str, Any]] = []
    replan: List[Dict[str, Any]] = []
    early_stop: List[Dict[str, Any]] = []
    for row in sorted(by_sig.values(), key=lambda r: -r["count"]):
        row["breakers"] = sorted(row["breakers"])
        row["phase"] = row["phase"]
        groups.append(row)
        step = {"action": "retry", "signature": row["signature"],
                "phase": row["phase"], "targets": row["breakers"],
                "attempts_observed": row["count"]}
        # A signature that has already failed twice with retries exhausted is the
        # early-stop case: retrying again is the loop the technique exists to break.
        if row["count"] >= 2 and row["max_retries"] >= 2:
            step["action"] = "stop-and-escalate"
            step["why"] = (f"{row['count']} identical failures, "
                           f"retries exhausted ({row['max_retries']})")
            early_stop.append(step)
        elif row["phase"] == "planning":
            step["action"] = "replan"
            step["why"] = "failure localizes to planning; repair the plan"
        elif row["phase"] == "response":
            step["action"] = "backoff-and-retry"
            step["why"] = "failure localizes to the response path"
        replan.append(step)

    out["groups"] = groups
    out["replan"] = replan
    out["early_stop"] = early_stop
    return out


def _dlq_fields() -> set:
    """`DeadLetter`'s dataclass field names, or an empty set. Lazy import."""
    try:
        from CORE.circuit_breaker import DeadLetter
        return set(getattr(DeadLetter, "__dataclass_fields__", {}) or {})
    except Exception as exc:
        note_failure("_l4_exec:L197", exc)
        return set()


def _ep129() -> Dict[str, Any]:
    """EP129 — phase taxonomy + replan-from-feedback + loop early-stop.

    Reports both the live queue (read through the previously-unconsumed API) and
    a bounded demonstration on a PRIVATE registry, so the consumer is proven to
    consume rather than merely proven to import. The global DLQ is never written.
    """
    plan = replan_from_dead_letters()
    fields = _dlq_fields()
    missing = "" if (fields and "phase" in fields) else \
        "DeadLetter.phase (phase is inferred from the error text, not a field)"
    band = _ep129_demo()
    return _result(engaged=True, n_dead_letters=plan["n_dead_letters"],
                   replan_steps=len(plan["replan"]),
                   early_stop=len(plan["early_stop"]),
                   phases=sorted({g["phase"] for g in plan["groups"]}),
                   consumer="CORE.circuit_breaker:get_dead_letters",
                   demo_dead_letters=band.get("demo_dead_letters"),
                   demo_phase=band.get("demo_phase"),
                   private_registry=band.get("private_registry", False),
                   missing_field=missing,
                   gaps=plan["gaps"])


def _ep129_demo() -> Dict[str, Any]:
    """Drive two identical failures through a PRIVATE breaker and read the queue."""
    try:
        from CORE.circuit_breaker import BreakerRegistry, CircuitConfig
        reg = BreakerRegistry()
        cb = reg.get("technique-wiring-demo",
                     CircuitConfig(failure_threshold=1, recovery_timeout=0.01))

        def _boom():
            raise ValueError("probe: planning failure, unknown-dep:execute")

        for _ in range(2):
            try:
                cb.execute(_boom)
            except Exception as exc:                  # noqa: BLE001 — expected: probe trips it
                note_failure("l4_exec:breaker-probe(expected)", exc)
        letters = reg.get_dead_letters("technique-wiring-demo") or []
        return _result(demo_dead_letters=len(letters),
                       demo_phase=(infer_phase(letters[0].error) if letters else ""),
                       private_registry=True)
    except Exception as exc:
        note_failure("_l4_exec:L245", exc)
        return _result(demo_dead_letters=0,
                       error=f"{type(exc).__name__}: {exc}")


# -- EP188 / EP307: the missing environment-generation half ---------------------

def _ep188() -> Dict[str, Any]:
    """EP188 — difficulty-controlled task discovery (co-evolution)."""
    has_choose = "choose_candidate" in _symbols("src/CORE/meta_evolution.py")
    has_improve = "improvement_proved" in _symbols("src/CORE/meta_evolution.py")
    return _result(engaged=True, consumes_findings=has_choose,
                   improvement_predicate=has_improve,
                   task_generator=False, difficulty_controller=False,
                   note="candidate selection is a CONSUMER of findings; nothing "
                        "GENERATES tasks or controls their difficulty")


def _ep307() -> Dict[str, Any]:
    """EP307 — environment auto-reset + parallel rollout farm."""
    env = _symbols("src/CORE/WORMS/expansion/arena.py")
    return _result(engaged=True, environment=("Environment" in env),
                   has_reset=("Environment.reset" in env),
                   rollout_farm=False,
                   note="worlds exist and mutate; there is no auto-reset and no "
                        "N-way rollout farm")


def _ep267() -> Dict[str, Any]:
    """EP267 — co-evolution rehearsal (does rehearsal learn from its own output?)."""
    arena = _symbols("src/CORE/WORMS/expansion/arena.py")
    dream = _symbols("src/CORE/WORMS/expansion/dreaming.py")
    return _result(engaged=True, arena=("Arena" in arena),
                   keeper_split=("keeper_split" in arena),
                   dreamer=("DreamingModule.dream" in dream),
                   rehearsal_learns_from_own_trajectories=False,
                   note="both halves exist (world mutation + dream generation); "
                        "the join is what is missing")


# -- EP313 / EP393: the reachability instrument ----------------------------------

_INSTRUMENT_CACHE: Dict[str, Any] = {}

#: The four reachability labels the EP313 discriminator is built from.
_REACHABILITY_LABELS = ("scheduled_entry", "production_call_site",
                        "test_only", "no_external_call_site")


def _instrument() -> Dict[str, Any]:
    """Describe `meta_evolution.architecture_snapshot` WITHOUT invoking it.

    Invoking it is deliberately avoided here. The function calls
    `_active_python_files(root)` and `_build_call_index(root)` — a repo-wide scan
    on a tree with 10k+ files — so calling it from `run_all()` would make the
    self-test look hung, and a self-test nobody runs is a self-test nobody has.
    Its *contract* is what matters for this wiring, and that is readable statically
    from the source: the discriminator, and whether the predicate is reachable.

    (The instrument itself is real and worth running deliberately — see
    `python -m scripts.meta_evolution_cycle` — just not on every self-test.)
    """
    if _INSTRUMENT_CACHE:
        return _INSTRUMENT_CACHE
    rel = "src/CORE/meta_evolution.py"
    try:
        text = (_repo_root() / rel).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        note_failure("_l4_exec:L312", exc)
        _INSTRUMENT_CACHE.update({"ok": False, "error": f"{type(exc).__name__}"})
        return _INSTRUMENT_CACHE
    labels = [lab for lab in _REACHABILITY_LABELS if lab in text]
    _INSTRUMENT_CACHE.update({
        "ok": True,
        "symbol": "architecture_snapshot" in _symbols(rel),
        "labels": labels,
        "proof_obligations": text.count('"Static call sites'),
        "scan_is_repo_wide": True,
    })
    return _INSTRUMENT_CACHE


def _ep313() -> Dict[str, Any]:
    """EP313 — faulty-implementation vs bad-hypothesis discriminator."""
    inst = _instrument()
    if not inst.get("ok"):
        return _result(engaged=False, error=inst.get("error"))
    return _result(engaged=bool(inst["symbol"]),
                   discriminator=str(inst["labels"]),
                   labels_present=len(inst["labels"]),
                   invoked=False,
                   scan_is_repo_wide=inst["scan_is_repo_wide"],
                   note="separates 'the mechanism exists but nothing calls it' "
                        "from 'the hypothesis was wrong'; not invoked from the "
                        "self-test because it builds a repo-wide call index")


def _ep393() -> Dict[str, Any]:
    """EP393 — recursive research -> constraint-audit loop."""
    inst = _instrument()
    if not inst.get("ok"):
        return _result(engaged=False, error=inst.get("error"))
    return _result(engaged=True,
                   proof_obligations=inst["proof_obligations"],
                   recursive_driver=False,
                   note="the constraint instrument declares its proof "
                        "obligations; the recursive driver that re-audits its "
                        "own research output does not exist")


# -- EP237: map-before-act ------------------------------------------------------

_GRAPH_MODULES = (
    "src/CORE/codebase_graph.py",
    "src/CORE/project_atlas.py",
    "src/CORE/graph_artifacts.py",
    "src/CORE/graph_drift.py",
)


def _ep237() -> Dict[str, Any]:
    """EP237 — map-before-act + a cognitive map as backtrackable state."""
    present = [m for m in _GRAPH_MODULES if _exists(m)]
    return _result(engaged=True, graph_modules_present=len(present),
                   modules=[Path(m).stem for m in present],
                   act_time_map_stage=False,
                   note="the review's '0 matches' for cognitive_map/map_before was "
                        "WRONG: the map substrate ships here. Only the act-time "
                        "stage is missing")


# -- EP238 / EP425 / EP437: rehearse, budget, undo ------------------------------

def _dag_and_assignment() -> tuple:
    """A small valid plan + role assignment, so the rehearsal adapters run real."""
    from CORE.dispatch_plan import assign_roles, plan_dag
    dag = plan_dag([{"id": "discover", "deps": []},
                    {"id": "execute", "deps": ["discover"]},
                    {"id": "report", "deps": ["execute"]}])
    roles = assign_roles(dag["order"],
                         {"discover": "inference", "execute": "tools",
                          "report": "inference"},
                         {"inference": "accel", "tools": "io"})
    return dag, roles


def _ep238() -> Dict[str, Any]:
    """EP238 — action-conditioned next-state scoring."""
    try:
        from CORE.dispatch_plan import rehearse_dispatch
        dag, roles = _dag_and_assignment()
        r = rehearse_dispatch(dag, roles)
        return _result(engaged=bool(r.get("rehearsed")),
                       static_gaps=len(r.get("predicted_failures", [])),
                       pool_load=r.get("pool_load"),
                       action_conditioned=False,
                       note="rehearsal folds plan/assignment gaps only: it holds "
                            "no state and takes no action, so it cannot predict "
                            "a next state")
    except Exception as exc:
        note_failure("_l4_exec:L403", exc)
        return _result(engaged=False, error=f"{type(exc).__name__}: {exc}")


def _ep425() -> Dict[str, Any]:
    """EP425 — action-conditioned rehearsal environment + test-time budget."""
    try:
        from CORE.dispatch_plan import rehearse_dispatch
        dag, roles = _dag_and_assignment()
        _ = rehearse_dispatch(dag, roles)
        return _result(engaged=True, rehearsal_call="rehearse_dispatch",
                       budget_parameter=False, environment=False,
                       note="rehearsal invocation exists; the environment it "
                            "should rehearse against and its budget do not")
    except Exception as exc:
        note_failure("_l4_exec:L417", exc)
        return _result(engaged=False, error=f"{type(exc).__name__}: {exc}")


def _ep437() -> Dict[str, Any]:
    """EP437 — reversible trajectory tree (undo a branch, keep the lesson)."""
    dp = _symbols("src/CORE/dispatch_plan.py")
    return _result(engaged=True,
                   backtrack_with_lesson=("record_backtrack" in dp),
                   reversible_tree=False, prefix_reuse=False,
                   note="record_backtrack keeps the corrective lesson but the "
                        "trajectory is not stored as a tree, so a branch cannot "
                        "be revisited or its prefix reused")


# -- EP335 / EP337: world model + budgeted probing ------------------------------

def _ep335() -> Dict[str, Any]:
    """EP335 — WorldEvolver (action-consequence prediction)."""
    return _result(engaged=True,
                   compile_world=("compile_world"
                                  in _symbols("src/CORE/dream_arena.py")),
                   action_consequence_prediction=False,
                   mismatch_to_heuristic=False,
                   note="worlds are compiled from dreams; nothing predicts the "
                        "consequence of a given action, so a mismatch cannot "
                        "become a heuristic")


def _ep337() -> Dict[str, Any]:
    """EP337 — budgeted probing against a belief table."""
    spine = _symbols("src/CORE/WORMS/expansion/spine.py")
    return _result(engaged=True,
                   probe=any(s == "_probe_candidates"
                             or s.endswith("._probe_candidates") for s in spine),
                   belief_table_targeting=False,
                   note="probing exists and is budget-bounded; it does not score "
                        "probes against belief-table fields")


# -- EP343: agentic test-time training -----------------------------------------

def _ep343() -> Dict[str, Any]:
    """EP343 — agentic test-time training (weights vs verbal)."""
    rx = _symbols("src/CORE/WORMS/expansion/reflexion.py")
    return _result(engaged=True, verbal_buffer=("record_reflection" in rx),
                   weight_update_path=False,
                   weight_free_approximation=True,
                   note="reflexion.py documents itself as verbal reinforcement "
                        "WITHOUT weight updates, so the weight-free "
                        "approximation composes from it; true TTT needs an "
                        "on-policy weight-update host this tree does not have")


# -- wiring ---------------------------------------------------------------------

ADAPTERS: Dict[str, tuple] = {
    "EP129": ("Agent Failure Taxonomy", "CORE.circuit_breaker:BreakerRegistry.get_dead_letters", _ep129),
    "EP188": ("Agent-World Evolving Arena", "CORE.meta_evolution:choose_candidate", _ep188),
    "EP237": ("Why AI agents must map before acting", "no-host: act-time map stage missing", _ep237),
    "EP238": ("SepsisAgent Clinical World Model", "CORE.dispatch_plan:rehearse_dispatch", _ep238),
    "EP267": ("CoMAP co-evolution", "CORE.WORMS.expansion.dreaming:DreamingModule.dream", _ep267),
    "EP307": ("ENPIRE robot self-improvement", "CORE.WORMS.expansion.arena:Environment", _ep307),
    "EP313": ("ARTS tree-search", "CORE.meta_evolution:architecture_snapshot", _ep313),
    "EP335": ("WorldEvolver", "CORE.dream_arena:compile_world", _ep335),
    "EP337": ("EnvProbe budgeted probing", "no-host: budgeted belief-table probing", _ep337),
    "EP343": ("Agentic Test-Time Training", "CORE.WORMS.expansion.reflexion:record_reflection", _ep343),
    "EP393": ("AREX self-audit research loop", "CORE.meta_evolution:architecture_snapshot", _ep393),
    "EP425": ("EnvACE world rehearsal", "CORE.dispatch_plan:rehearse_dispatch", _ep425),
    "EP437": ("LoongReflect undo/backtrack", "CORE.dispatch_plan:record_backtrack", _ep437),
}

for _ep, (_name, _where, _fn) in ADAPTERS.items():
    register(_ep, _name, _where, _fn, layer="L4")
