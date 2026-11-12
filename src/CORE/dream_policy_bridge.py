# coding: utf-8
"""dream_policy_bridge.py - Close the dream->policy loop.

Applies dream suggestions through MetaCognitionWorm.adapt_strategy (the
designed policy channel: confidence-weighted recommendations persisted as
strategy_adaptation rows), one suggestion at a time.

How adaptation now actually happens:
- Each suggestion names the strategy to try (strategies / recovery_action).
- The bridge calls adapt_strategy() with that strategy passed as the
  explicit `recommended` argument plus the suggestion's confidence. The
  policy channel in adapt_strategy() adopts a governed recommendation,
  switches the per-worm current strategy, and persists it (save_state).
- A detailed audit row (per suggestion + run summary) is recorded as a
  policy_applied event so results are observable, not just counts.

Safety:
- CurativePool governance per suggestion; blocked items are skipped.
- Minimum confidence gate (default 0.01; the generator's tepid band
  starts at 0.01 — CurativePool remains the real safety).
- Fail-closed: governance/import errors skip items, never raise.
- State is saved after each run so the next 5-minute cycle resumes from
  the updated strategy instead of a stale snapshot.

This module is the `policy_fn` that dreaming.integrate_best() accepts but
no production caller currently passes.
"""

from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Minimum confidence gate (2026-09-08: 0.05 -> 0.01). The dream generator
# deliberately emits tepid low-confidence strategy_exploration suggestions
# (confidence = max(0.05, creativity) * U(0.2, 0.5), i.e. a 0.01-0.10 band
# at low creativity) — the old 0.05 gate sat inside that band and rejected
# every suggestion, so applied stayed 0 forever. CurativePool governance
# below remains the real safety; this gate only filters numerical noise.
MIN_CONFIDENCE = 0.01


def _governance_blocked(suggestion: Dict[str, Any]) -> bool:
    """True when CurativePool explicitly blocks. Fail-closed on errors."""
    try:
        from META.MetaWormSpawner import CurativePool
        pool = CurativePool()
        result = pool.evaluate_action(
            {"context": "dream_policy_bridge",
             "params": {"suggestion": str(suggestion)[:300]}},
            {"action_type": "execute", "output_size": 100,
             "feature": "dream_policy"},
            [{"role": "user", "content": "apply dream policy suggestion"}],
            [{"id": "dream_policy", "archetype": "The Artist"}],
        )
        return result.get("decision") == "blocked"
    except Exception as exc:
        note_failure("dream_policy_bridge:L54", exc)
        return True


def apply_dream_policy(suggestions: Optional[List[Dict[str, Any]]],
                       patterns: Optional[List[Dict[str, Any]]] = None,
                       min_confidence: float = MIN_CONFIDENCE) -> Dict[str, Any]:
    """Apply dream suggestions to live strategy policy. Returns report dict."""
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    mc = None
    try:
        from CORE.WORMS.expansion.meta_cognition import MetaCognitionWorm
        mc = MetaCognitionWorm()
    except Exception as e:
        note_failure("dream_policy_bridge:L68", e)
        return {"applied": applied,
                "skipped": [{"reason": f"mc-unavailable: {e}"}],
                "governed": False}

    for s in suggestions or []:
        try:
            if not isinstance(s, dict) or s.get("confidence", 0) < min_confidence:
                skipped.append({"reason": "low-confidence"})
                continue
            if _governance_blocked(s):
                skipped.append({"reason": "governance-blocked"})
                continue
            worm = str(s.get("source") or "global")
            actions = [a for a in (s.get("strategies") or
                                   [s.get("recovery_action")]) if a]
            if not actions:
                skipped.append({"reason": "no-actionable-strategy"})
                continue
            conf = float(s.get("confidence", 0.0)) or 0.0
            current = mc.get_current_strategy(worm)
            res = mc.adapt_strategy(
                worm, current, actions,
                recommended=actions[0], confidence=conf,
            )
            applied.append({
                "worm": worm,
                "current_strategy": current,
                "recommended": actions[0],
                "confidence": round(conf, 4),
                "should_adapt": res.get("should_adapt"),
                "rationale": res.get("rationale"),
                "all_scores": res.get("all_scores"),
            })
        except Exception as e:
            note_failure("dream_policy_bridge:L102", e)
            skipped.append({"reason": f"error: {e}"})

    # Persist state so the next cycle resumes from the updated strategy.
    if mc is not None:
        try:
            mc.save_state()
        except Exception as exc:
            note_failure("dream_policy_bridge:L109", exc)

    try:
        from CORE.direct_memory import DirectMemory
        DirectMemory().record("policy_applied", {
            "action": "dream policy bridge run",
            "applied": len(applied),
            "skipped": len(skipped),
            "details": {"applied": applied, "skipped": skipped},
            "governance_min_confidence": min_confidence,
        }, tags=["policy", "dream", "bridge"])
    except Exception as exc:
        note_failure("dream_policy_bridge:L121", exc)
    return {"applied": applied, "skipped": skipped}
