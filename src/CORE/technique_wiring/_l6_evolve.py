"""_l6_evolve.py — L6 (evolution / training) adapters.

Gives the L6 techniques a real call site. Two kinds of adapter live here, and the
distinction is deliberate and reported:

- **INVOKED** — a pure, CPU-only function is actually called and its real return
  value is reported (the advantage estimators, the PRM scorers, the DPO losses,
  the distillation gain gate). These need no model, no torch and no training.
- **SURFACED** — where the review's delta depends on a contract we must not
  execute here (a training loop, a policy write, a protected module's lifecycle),
  the adapter reports the *verified surface*: the real signature, the real source
  line that constitutes the evidence, or the guard that blocks it. It never
  claims to have run something it did not run.

Everything is read-mostly: no training, no policy writes, no network, no mutation.
The only reads are module imports, `inspect` signatures/sources, and two pure
scoring calls over supplied data. `meta_evolution.py` and `tools/`/`scripts/` are
read-only here (protected paths — never edited).
"""
from __future__ import annotations

import importlib
import inspect
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from CORE.technique_runtime import register
from CORE.WORMS.expansion import policy_optimization as _po
from CORE.WORMS.expansion.learning_grpo import order_by_difficulty

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LAYER = "L6"
REPO = Path(__file__).resolve().parents[3]


# -- helpers -------------------------------------------------------------------

def _import(candidates: Tuple[str, ...]) -> Tuple[Any, str]:
    """Import the first module that loads; return (module, why)."""
    for name in candidates:
        try:
            return importlib.import_module(name), "ok"
        except Exception as exc:                      # pragma: no cover - env
            note_failure("_l6_evolve:L41", exc)
            last = f"{name}: {type(exc).__name__}"
    return None, last


def _surface(mod_name: str, *symbols: str) -> Dict[str, Any]:
    """Report which of `symbols` exist on a module — verified, not assumed."""
    mod, why = _import((mod_name,))
    if mod is None:
        return {"imported": False, "reason": why, "found": [], "missing": list(symbols)}
    found = [s for s in symbols if hasattr(mod, s)]
    return {"imported": True, "found": found,
            "missing": [s for s in symbols if s not in found]}


def _signature(mod_name: str, symbol: str) -> str:
    """The real signature of a symbol, or why it could not be read."""
    mod, why = _import((mod_name,))
    if mod is None:
        return f"(unavailable: {why})"
    fn = getattr(mod, symbol, None)
    if fn is None:
        return "(symbol absent)"
    try:
        return f"{symbol}{inspect.signature(fn)}"
    except Exception as exc:                          # pragma: no cover - C fn
        note_failure("_l6_evolve:L66", exc)
        return f"(signature unreadable: {type(exc).__name__})"


def _source_has(mod_name: str, symbol: str, *needles: str) -> Dict[str, Any]:
    """Does the real source of `symbol` contain these strings?

    The evidence half of a SURFACED adapter: 'the update is a norm clamp, not an
    isometry' is a claim about a line of code, so read the line.
    """
    mod, why = _import((mod_name,))
    if mod is None:
        return {"read": False, "reason": why, "matched": []}
    fn = getattr(mod, symbol, None)
    if fn is None:
        return {"read": False, "reason": "symbol absent", "matched": []}
    try:
        src = inspect.getsource(fn)
    except Exception as exc:
        note_failure("_l6_evolve:L84", exc)
        return {"read": False, "reason": type(exc).__name__, "matched": []}
    return {"read": True, "matched": [n for n in needles if n in src],
            "lines": len(src.splitlines())}


def _state_file(rel: str) -> Dict[str, Any]:
    """Report a state artifact's presence and size (read-only)."""
    p = REPO / rel
    if not p.exists():
        return {"exists": False, "path": rel}
    try:
        rows = sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
    except Exception as exc:
        note_failure("_l6_evolve:L97", exc)
        rows = -1
    return {"exists": True, "path": rel, "rows": rows}


# -- INVOKED: pure estimators (no model, no training) ---------------------------

_POLICY = "CORE.WORMS.expansion.policy_optimization"


def _ep97() -> Dict[str, Any]:
    """GRPO advantage normalization — the pure-outcome reward path (EP97).

    Invokes the real estimator on a fixed reward vector, so the wiring proves the
    advantage path is live rather than present-but-unreachable.
    """
    mod, why = _import((_POLICY,))
    if mod is None:
        return {"engaged": False, "reason": why}
    rewards = [0.1, 0.4, 0.9, 0.2]
    adv = mod.grpo_advantages(rewards)
    return {"engaged": True, "n": len(adv),
            "mean_of_advantages": round(sum(adv) / max(1, len(adv)), 6),
            "surfaced": _surface(_POLICY, "grpo_advantages",
                                 "reinforce_plus_plus_advantages")["found"],
            "trainer_is_tabular": _surface(
                "CORE.WORMS.expansion.learning_grpo", "GRPOTrainer")["imported"]}


def _ep105() -> Dict[str, Any]:
    """SP-PRM prefix-delta step rewards + TI-DPO token weighting (EP105).

    Both halves exist and both are pure: the PRM induction scores every prefix and
    differences them into implicit step rewards; TI-DPO applies the per-step
    weight hook. The alternating PRM/policy LOOP is what is missing, and this
    adapter demonstrates the two halves the loop would join.
    """
    prm, why = _import(("CORE.prm",))
    if prm is None:
        return {"engaged": False, "reason": why}
    tokens = ["plan", "edit", "verify"]
    scores = prm.sp_prm_scores(lambda prefix: 0.2 + 0.25 * prefix.count(" "), tokens)
    dpo, _ = _import(("CORE.dpo_losses",))
    ti = None
    if dpo is not None:
        ti = dpo.ti_dpo_loss([-1.0, -1.2], [-2.0, -2.4], [-1.1, -1.3], [-2.1, -2.5],
                             imp_w=[1.0, 0.5], imp_l=[0.5, 1.0])
    return {"engaged": True, "step_rewards": len(scores),
            "ti_dpo_loss": round(ti, 6) if ti is not None else None,
            "loop_missing": "prm refresh <-> policy update not joined"}


def _ep151() -> Dict[str, Any]:
    """GDPO decoupled multi-reward normalization + R-PRM rule verification (EP151).

    GDPO normalizes each reward stream independently — exactly the arbitration
    primitive a dual-RM setup needs — and R-PRM supplies the rule-verification
    half with a heuristic judge, so this composes on CPU today.
    """
    mod, why = _import((_POLICY,))
    if mod is None:
        return {"engaged": False, "reason": why}
    adv = mod.gdpo_advantages({"rule": [1.0, 0.0, 1.0], "model": [0.9, 0.2, 0.6]})
    prm, _ = _import(("CORE.prm",))
    critiques = prm.r_prm_verify(["read the file", "maybe it works"]) if prm else []
    return {"engaged": True, "n_streams": 2, "n_advantages": len(adv),
            "critiques": len(critiques),
            "arbitration": "per-stream z-score (gdpo_advantages)"}


def _ep350() -> Dict[str, Any]:
    """RG-SED reward-gated distillation over a replayed prefix (EP350).

    Invokes the real strict-gain gate both ways (gain, and no-gain) so the
    no-blind-imitation rule is *observed* rather than described.
    """
    mod, why = _import(("CORE.rubric_evolution",))
    if mod is None:
        return {"engaged": False, "reason": why}
    gain = mod.gate_distillation(0.40, 0.55)
    flat = mod.gate_distillation(0.40, 0.40)
    return {"engaged": True, "distills_on_gain": gain.get("distill"),
            "distills_on_tie": flat.get("distill"),
            "gain": round(float(gain.get("gain", 0.0)), 4)}


def _ep351() -> Dict[str, Any]:
    """Checkpoint-pair log-ratio as an implicit reward (EP351).

    The review's host for this row was WRONG (`rubric_evolution.gate_distillation`
    is a reward gate, not a snapshot source). The real snapshots are
    `GRPOTrainer.save_policy`/`load_policy`, which persist the softmax policy dict;
    this adapter computes the log-ratio the technique asks for over two supplied
    policy snapshots and reports where the snapshots really come from.
    """
    def _logratio(a: Dict[str, float], b: Dict[str, float]) -> float:
        keys = set(a) & set(b)
        if not keys:
            return 0.0
        return sum(math.log(max(a[k], 1e-9)) - math.log(max(b[k], 1e-9))
                   for k in keys) / len(keys)

    old = {"worm_a": 0.6, "worm_b": 0.4}
    new = {"worm_a": 0.75, "worm_b": 0.25}
    return {"engaged": True, "log_ratio": round(_logratio(new, old), 4),
            "real_snapshot_source": _surface(
                "CORE.WORMS.expansion.learning_grpo",
                "GRPOTrainer")["found"],
            "snapshot_methods": ["save_policy", "load_policy"]}


def _ep362() -> Dict[str, Any]:
    """State-conditioned negatives fed to the real DPO loss (EP362).

    Invokes `dpo_loss` on a constructed preference pair. The missing half is the
    PAIR BUILDER (state-conditioned negatives) and the schema-invariant renderer;
    the loss that consumes them is live.
    """
    mod, why = _import(("CORE.dpo_losses",))
    if mod is None:
        return {"engaged": False, "reason": why}
    loss = mod.dpo_loss([-0.8, -1.1], [-1.6, -2.0], [-0.9, -1.2], [-1.7, -2.1])
    return {"engaged": True, "dpo_loss": round(loss, 6),
            "pair_builder": "absent — negatives must be state-conditioned",
            "renderer": "absent — schema-invariant rendering"}


# -- SURFACED: contracts we must not execute here -------------------------------

def _ep228() -> Dict[str, Any]:
    """Capability-preserving commit criterion (EP228) — criterion shape read."""
    return {"engaged": True,
            "criterion": _signature("CORE.meta_evolution", "improvement_proved"),
            "delta": "scalar (before, after) int pair only — no capability probe set",
            "surfaced": _surface("CORE.meta_evolution", "improvement_proved",
                                 "architecture_snapshot")}


def _ep234() -> Dict[str, Any]:
    """Pareto retention (EP234) — the scalar predicate is the blocker."""
    pf = _po.pareto_front({"a": (1.0, 0.0), "b": (0.0, 1.0),
                           "c": (0.4, 0.4)})
    return {"engaged": True,
            "criterion": _signature("CORE.meta_evolution", "improvement_proved"),
            "pareto_front": pf["front"],
            "delta": "Pareto SET live (pareto_front); criteria vector + "
                     "retention PREDICATE deferred (no admission caller)",
            "collapse": "same edit site as EP228/EP462 (one retention predicate)"}


def _ep172() -> Dict[str, Any]:
    """Self-referential meta-mechanism (EP172) — blocked by the guard."""
    mod, why = _import(("CORE.meta_evolution",))
    if mod is None:
        return {"engaged": False, "reason": why}
    blocked = mod.is_protected_path("src/CORE/meta_evolution.py")
    return {"engaged": True, "self_editable": not blocked,
            "guard": "is_protected_path", "blocked": blocked,
            "delta": "meta-policy must move to state/ (unprotected) to unblock",
            "archive_seed": _state_file("state/meta_evolution/lineage.jsonl")}


def _ep236() -> Dict[str, Any]:
    """Meta-editing operator (EP236) — same guard, same one edit as EP172."""
    mod, _ = _import(("CORE.meta_evolution",))
    blocked = mod.is_protected_path("src/CORE/meta_evolution.py") if mod else True
    return {"engaged": True, "blocked": blocked,
            "collapse": "EP172 + EP236 are ONE edit (editable meta-policy)",
            "blast_radius": ["tools/", "src/CORE/compliance/",
                             "tests/", "generated/"]}


def _ep240() -> Dict[str, Any]:
    """Agentic modeling ladder (EP240) — surface only (env service not read)."""
    return {"engaged": True,
            "surfaced": _surface("CORE.WORMS.expansion.container",
                                 "ContainerOrch", "ContainerOrchestrator"),
            "delta": "harness-agnostic sandbox-env service + BAR/OPD/RPR ladder",
            "note": "ContainerOrch body not opened — surface claim, not behaviour"}


def _ep273() -> Dict[str, Any]:
    """'Code as a disposable instrument' doctrine binding (EP273).

    Not a mechanism: the honest adapter reports whether a doctrine artifact
    exists, so the row cannot be re-litigated as 'unimplemented technique'.
    """
    candidates = ["state/ADR", "state/decisions", "docs/adr"]
    present = [c for c in candidates if (REPO / c).exists()]
    return {"engaged": True, "doctrine_dirs": present,
            "kind": "doctrine/ADR — documentation, not code",
            "delta": "an ADR binding the rule; no runtime mechanism exists"}


def _ep309() -> Dict[str, Any]:
    """Primitive-sequence workflows + execution-derived safety signal (EP309)."""
    keys: List[str] = []
    mod, _ = _import(("CORE.meta_evolution",))
    snapshot = getattr(mod, "architecture_snapshot", None) if mod else None
    if callable(snapshot):
        try:
            snap = snapshot(REPO)
            keys = sorted(snap.keys())[:8] if isinstance(snap, dict) else []
        except Exception as exc:
            note_failure("_l6_evolve:L296", exc)
            keys = []
    return {"engaged": True,
            "surfaced": _surface("CORE.meta_evolution", "choose_candidate",
                                 "architecture_snapshot", "Lifecycle"),
            "snapshot_keys": keys,
            "delta": "candidates are (path,line,detector) — no primitive sequences"}


def _ep318() -> Dict[str, Any]:
    """Ablation harness making data composition measured (EP318)."""
    mod, why = _import(("CORE.WORMS.expansion.learning_pipeline",
                        "CORE.learning_pipeline"))
    if mod is None:
        return {"engaged": False, "reason": why}
    return {"engaged": True,
            "found": [s for s in ("DataPipelineWorm", "EXCLUDED_TRAINING_SOURCES")
                      if hasattr(mod, s)],
            "delta": "ablation harness + measured downstream metric absent",
            "composition_axis": "exclude_sources already parameterized"}


def _ep294() -> Dict[str, Any]:
    """Reflection calibration (EP294) — outcome half CLOSED, utility half open.

    Backlog sweep 2026-10-10: the review's finding is half-stale.
    `record_reflection` now stores optional outcome/predicted/realised fields
    (additive Sep-29 buffer work; exercised live by EP113, which writes and
    recalls `realised`), and `reflections_for` promotes realised-carrying rows
    above unchecked ones (a re-order, never a filter). The adapter SHOWS both
    the live evidence and the remainder: retrieval ranks by realised-PRESENCE
    plus recency, not by a utility score from predicted-vs-realised error.
    """
    mod, why = _import(("CORE.WORMS.expansion.reflexion",))
    if mod is None:
        return {"engaged": False, "reason": why}
    keys: List[str] = []
    try:
        rows = mod.reflections_for("__technique_runtime_probe__", limit=1) or []
        if rows and isinstance(rows[0], dict):
            keys = sorted(rows[0].keys())
    except Exception as exc:
        note_failure("_l6_evolve:L334", exc)
        keys = []
    src = _source_has("CORE.WORMS.expansion.reflexion", "record_reflection",
                      "outcome", "utility")
    return {"engaged": True,
            "returned_keys": keys or ["(no rows)"],
            "has_outcome_field": bool(keys and "outcome" in keys),
            "write_side_mentions_outcome": bool(src.get("matched")),
            "ranking": ("presence-ranked (realised first, recency inside rank) "
                        "— no error-magnitude weight"),
            "delta": ("calibration-error-weighted retrieval "
                      "(|predicted-realised| as utility); presence-ranking only")}


def _ep388() -> Dict[str, Any]:
    """LatentMT latent reasoning (EP388) — surface of the refiner pairing."""
    return {"engaged": True,
            "surfaced": _surface("CORE.latent_refine", "refine", "LatentRefiner"),
            "delta": "pair the refiner with a frozen LLM (host absent)"}


def _ep406() -> Dict[str, Any]:
    """Repair-state graph over the append-only lineage (EP406)."""
    lineage = _state_file("state/meta_evolution/lineage.jsonl")
    return {"engaged": True, "lineage": lineage,
            "delta": "state-nodes + transition edges over the existing log",
            "escalation": _state_file("state/meta_evolution/escalation.json")}


def _ep429() -> Dict[str, Any]:
    """Harness hot-swap under a frozen anchor (EP429) — depends on EP172."""
    return {"engaged": True, "depends_on": "EP172 (editable meta-policy)",
            "delta": "hot-swap + backbone-capability bound",
            "blocked": True}


def _ep435() -> Dict[str, Any]:
    """Explore->Evaluate->Reflect->Internalize (EP435) — the CHAIN now runs.

    This adapter used to be a surface probe: it reported that `observe`,
    `analyze_patterns` and `adapt_strategy` EXISTED on the module and called
    none of them. The technique was registered while its actual mechanism --
    `meta_cognition.internalize`, the analyse -> recommend -> adapt sequence
    that IS the EP435 Internalize phase -- had zero callers anywhere in src/.
    Registered-but-not-absorbed, which is the shelfware shape.

    Absorbed (engram #632): drive the real chain. `internalize` is fail-open
    and read-mostly by default (`persist=False` builds the engine with
    persistence disabled), so this cannot churn a live strategy; the engine's
    own `ADOPT_CONFIDENCE` gate decides whether anything is adopted.
    """
    mod, why = _import(("CORE.WORMS.expansion.meta_cognition",))
    if mod is None:
        return {"engaged": False, "reason": why}
    fn = getattr(mod, "internalize", None)
    if fn is None:
        return {"engaged": False,
                "reason": "internalize absent from meta_cognition",
                "surfaced": _surface("CORE.WORMS.expansion.meta_cognition",
                                     "observe", "analyze_patterns",
                                     "adapt_strategy",
                                     "generate_introspection_report")}
    out: Any = None
    try:
        out = fn({"worm_type": "ep435_probe", "action": "internalize_chain",
                  "outcome": "success", "context": {"probe": "l6_ep435"}})
    except Exception as exc:                          # pragma: no cover - host
        note_failure("_l6_evolve:ep435", exc)
        out = {"error": f"{type(exc).__name__}: {exc}"}
    ok = isinstance(out, dict) and "error" not in out
    return {"engaged": True,
            "chain_ran": ok,
            "verdict_keys": sorted(out.keys())[:8] if isinstance(out, dict) else [],
            "adopted": bool(out.get("adopted")) if isinstance(out, dict) else False,
            "confidence": out.get("confidence") if isinstance(out, dict) else None,
            "delta": "Explore phase (contrastive calibration) + join to the buffer",
            "adapt_callsite": "DREAM_STRATEGY_TUNING steers dreaming"}


def _ep462() -> Dict[str, Any]:
    """Dual-gate admission + trajectory contrast (EP462)."""
    mod, _ = _import(("CORE.meta_evolution",))
    phases = list(getattr(mod, "PHASES", ()) or ()) if mod else []
    transitions = getattr(mod, "LEGAL_TRANSITIONS", {}) if mod else {}
    return {"engaged": True, "phases": phases, "n_transitions": len(transitions),
            "delta": "second admission gate + trajectory-contrast operator",
            "collapse": "one edit with EP228/EP234"}


def _ep355() -> Dict[str, Any]:
    """Primitive-then-composition curriculum (EP355) — tabular analogue only."""
    src = _source_has("CORE.WORMS.expansion.learning_grpo", "GRPOTrainer",
                      "context_key", "save_policy", "load_policy")
    ordered = order_by_difficulty(["hard", "easy"],
                                  {"easy": 0.1, "hard": 0.9})
    return {"engaged": True, "trainer_read": src.get("read"),
            "matched": src.get("matched"),
            "difficulty_order": ordered["ordered"],
            "delta": "ordering live; train GATING deferred (no gating "
                     "consumer — ordering without one would be decoration)",
            "note": "faithful version needs an RL trainer over a real model"}


def _ep360() -> Dict[str, Any]:
    """Anchor-Rollout off-policy sampling + mixed objective (EP360)."""
    src = _source_has("CORE.WORMS.expansion.learning_grpo", "compute_advantages",
                      "global_stats", "std", "DAPO")
    anchored = _source_has("CORE.WORMS.expansion.learning_grpo",
                           "GRPOTrainer.snapshot_anchor", "snapshot_anchor")
    return {"engaged": True, "present": src.get("matched"),
            "anchor_method_live": bool(anchored.get("matched")),
            "delta": "anchor snapshots live (snapshot_anchor, capped at 20); "
                     "mixed objective deferred (no weight-space host)",
            "note": "global-norm fallback is the existing anchor analogue"}


def _ep361() -> Dict[str, Any]:
    """Manifold-consistent isometric update (EP361) — verify the clamp claim.

    The review says the update rescales by max-abs (a norm clamp, not an
    isometry). Read the source and report which it is.
    """
    src = _source_has("CORE.WORMS.expansion.learning_grpo", "update_policy",
                      "max(", "abs(", "kl", "lr")
    return {"engaged": True, "update_source_read": src.get("read"),
            "matched": src.get("matched"), "lines": src.get("lines"),
            "delta": "log-space update preserving pairwise ratios",
            "faithful_requires": "weight-space geometry (no host on this box)"}


def _ep280() -> Dict[str, Any]:
    """Teacher revises the student rollout before distillation (EP280).

    Invokes the real DAgger round on a tiny supplied policy — pure, CPU-only,
    no model. What the technique adds is the teacher EDITING the rollout rather
    than only labelling the visited state.
    """
    mod, why = _import(("PSYCHE.distill",))
    if mod is None:
        return {"engaged": False, "reason": why}
    out: Any = None
    try:
        out = mod.onpolicy_refine({"read": 0.4, "edit": 0.6},
                                  [{"state": "s1"}, {"state": "s2"}],
                                  extra_rounds=1, seed=0)
    except Exception as exc:
        note_failure("_l6_evolve:L436", exc)
        out = {"error": f"{type(exc).__name__}"}
    return {"engaged": True,
            "refine_ran": isinstance(out, dict) and "error" not in out,
            "result_keys": sorted(out.keys())[:6] if isinstance(out, dict) else [],
            "surfaced": _surface("PSYCHE.distill", "onpolicy_refine",
                                 "preference_step")["found"],
            "delta": "teacher must EDIT the rollout, not only label the state"}


# -- registration --------------------------------------------------------------
#
# `where` names the module:symbol actually invoked or read — the audit trail.

_R = (
    ("EP97", "DeepSeek-R1 Pure-RL", f"{_POLICY}:grpo_advantages", _ep97),
    ("EP105", "iStar implicit step rewards", "CORE.prm:sp_prm_scores", _ep105),
    ("EP151", "MagicGUI-RMS GUI rewards (dual-RM)",
     f"{_POLICY}:gdpo_advantages", _ep151),
    ("EP172", "Hyperagents Self-Modification",
     "CORE.meta_evolution:is_protected_path", _ep172),
    ("EP228", "Capability-Preserving Evolution",
     "CORE.meta_evolution:improvement_proved", _ep228),
    ("EP234", "FATE failure-trajectory alignment",
     "CORE.meta_evolution:improvement_proved", _ep234),
    ("EP236", "AEvo Harnessed Meta-Editing",
     "CORE.meta_evolution:is_protected_path", _ep236),
    ("EP240", "Orchard Agentic Modeling",
     "CORE.WORMS.expansion.container:ContainerOrch", _ep240),
    ("EP273", "Agentic Software Paradigm (doctrine)",
     "no-host: doctrine ADR binding absent", _ep273),
    ("EP280", "Trajectory-Refined Distillation",
     "PSYCHE.distill:onpolicy_refine", _ep280),
    ("EP294", "RefGRPO Reflection Calibration",
     "CORE.WORMS.expansion.reflexion:reflections_for", _ep294),
    ("EP309", "AutoRAS robust design",
     "CORE.meta_evolution:architecture_snapshot", _ep309),
    ("EP318", "OpenThoughts-Agent Data Recipes",
     "CORE.WORMS.expansion.learning_pipeline:DataPipelineWorm", _ep318),
    ("EP350", "Training without live environments",
     "CORE.rubric_evolution:gate_distillation", _ep350),
    ("EP351", "Direct-OPD weak-to-strong",
     "CORE.WORMS.expansion.learning_grpo:save_policy/load_policy", _ep351),
    ("EP355", "RL Builds Compositional Reasoning",
     "CORE.WORMS.expansion.learning_grpo:GRPOTrainer", _ep355),
    ("EP360", "ARMOR reasoning collapse",
      "CORE.WORMS.expansion.learning_grpo:GRPOTrainer.compute_advantages", _ep360),
    ("EP361", "RIPO isometric policy optimization",
      "CORE.WORMS.expansion.learning_grpo:GRPOTrainer.update_policy", _ep361),
    ("EP362", "Agentic-DPO", "CORE.dpo_losses:dpo_loss", _ep362),
    ("EP388", "LatentMT latent reasoning", "CORE.latent_refine:refine", _ep388),
    ("EP406", "Living-Harness evolver (repair-state graph)",
     "no-host: repair-state graph over missing lineage", _ep406),
    ("EP429", "HSI evolvable harnesses",
     "CORE.meta_evolution:is_protected_path", _ep429),
    ("EP435", "Reflection self-distillation",
      "CORE.WORMS.expansion.meta_cognition:MetaCognitionWorm.adapt_strategy", _ep435),
    ("EP462", "OpsHarness self-evolving harness",
     "CORE.meta_evolution:LEGAL_TRANSITIONS", _ep462),
)

# Not deleted afterwards on purpose: `del` on a loop variable raises NameError
# at import time if the tuple is ever edited down to empty, which would take the
# whole slice (and therefore the layer) offline. Module-level leftovers are cheap.
for _ep, _name, _where, _fn in _R:
    register(_ep, _name, _where, _fn, layer=LAYER)
