"""
Eve Unleashed — TERENCE v2 Self-Healing Codebase (Phase 2, V8.0).

LLM-powered fix generation (via InferenceBridge / llama-server) layered on
the existing TERENCE v1 pattern matcher. Auto-applies low-risk fixes
(syntax, imports, encoding); proposes high-risk fixes (logic changes,
refactors) for human review. Tracks fix success rate per pattern.

Fail-open: if llama-server is down, falls back to TERENCE v1 patterns only.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass


LOW_RISK = {"syntax", "import", "encoding", "indentation"}
HIGH_RISK = {"logic", "refactor", "architecture"}


@dataclass
class FixProposal:
    """A proposed fix; auto_apply True means safe to apply without review."""
    file: str
    line: int
    error_type: str
    risk: str                      # low | high
    original: str
    fixed: str
    description: str
    auto_apply: bool
    pattern: str = ""              # TERENCE v1 pattern that matched, if any
    model_used: str = ""           # which model generated it


@dataclass
class HealStats:
    files_scanned: int = 0
    errors_found: int = 0
    fixes_proposed: int = 0
    fixes_applied: int = 0
    fixes_rejected: int = 0
    successes: int = 0
    failures: int = 0
    duration_ms: float = 0.0
    by_pattern: Dict[str, Dict[str, int]] = field(default_factory=dict)


class TerenceV2:
    """Self-healing: LLM fix generation + risk-gated auto-apply + success tracking."""

    def __init__(self, router=None, bridge=None):
        self.router = router
        self.bridge = bridge
        self.stats = HealStats()
        self._review_queue: List[FixProposal] = []
        self._applied: List[FixProposal] = []
        self._v1 = None  # lazy TERENCE v1

    # -- subsystem access ---------------------------------------------------

    def _get_bridge(self):
        if self.bridge is not None:
            return self.bridge
        try:
            from META.InferenceBridge import InferenceBridge
            self.bridge = InferenceBridge()
            return self.bridge
        except Exception as exc:
            note_failure("terence_v2:bridge", exc)
            return None

    def _get_router(self):
        if self.router is not None:
            return self.router
        try:
            from .model_router import get_router
            self.router = get_router()
            return self.router
        except Exception as exc:
            note_failure("terence_v2:router", exc)
            return None

    def _llama_available(self) -> bool:
        bridge = self._get_bridge()
        if bridge is None:
            return False
        try:
            return bool(bridge.get_bridge_status().get("llama_available", False))
        except Exception as exc:
            note_failure("terence_v2:bridge_status", exc)
            return False

    # -- v1 fallback --------------------------------------------------------

    def _get_v1(self):
        if self._v1 is not None:
            return self._v1
        try:
            from UTILITIES.TERENCE.TERENCE import TERENCE
            self._v1 = TERENCE()
            return self._v1
        except Exception as exc:
            note_failure("terence_v2:v1_import", exc)
            return None

    # -- fix proposal -------------------------------------------------------

    def propose_fix(self, file: str, line: int, error_type: str,
                    context: str, suggested_type: str = "logic") -> FixProposal:
        """Generate a fix for an error; LLM when available, v1 otherwise."""
        start = time.time()
        risk = "low" if suggested_type in LOW_RISK else "high"
        auto_apply = risk == "low"
        fixed = ""
        model_used = ""

        bridge = self._get_bridge()
        if bridge is not None and self._llama_available():
            router = self._get_router()
            decision = router.route_for_type("code") if router else None
            model = decision.model if decision else ""
            prompt = (
                "Fix the following Python error. Return ONLY the corrected "
                "code block, no explanation.\n"
                f"File: {file}:{line}\nError: {error_type}\n"
                f"Context:\n{context}\n"
            )
            try:
                fixed = bridge.send_to_llama(prompt)
                model_used = model or "llama-server"
            except Exception as exc:
                note_failure("terence_v2:llama_fix", exc)

        if not fixed:
            # TERENCE v1 pattern fallback
            v1 = self._get_v1()
            if v1 is not None:
                try:
                    # v1 exposes fix_by_error_type(file, line, error_type, context)
                    fn = getattr(v1, "fix_by_error_type", None)
                    if fn:
                        fixed = fn(file, line, error_type, context) or ""
                    else:
                        fixed = ""
                except Exception as exc:
                    note_failure("terence_v2:v1_fix", exc)
            model_used = model_used or "terence_v1_pattern"

        proposal = FixProposal(
            file=file, line=line, error_type=error_type, risk=risk,
            original=context, fixed=fixed, description=f"Fix {error_type} at {file}:{line}",
            auto_apply=auto_apply, model_used=model_used,
        )
        self.stats.fixes_proposed += 1
        self.stats.by_pattern.setdefault(error_type, {"proposed": 0, "applied": 0,
                                                      "success": 0, "failure": 0})
        self.stats.by_pattern[error_type]["proposed"] += 1

        if auto_apply:
            self._applied.append(proposal)
            self.stats.fixes_applied += 1
            self.stats.by_pattern[error_type]["applied"] += 1
        else:
            self._review_queue.append(proposal)

        try:
            self.stats.duration_ms += (time.time() - start) * 1000
        except Exception as exc:
            note_failure("terence_v2:duration_update", exc)
            pass
        return proposal

    # -- review queue -------------------------------------------------------

    def review_queue(self) -> List[FixProposal]:
        return list(self._review_queue)

    def approve(self, index: int) -> bool:
        """Approve a queued high-risk fix (moves it to applied)."""
        if 0 <= index < len(self._review_queue):
            prop = self._review_queue.pop(index)
            self._applied.append(prop)
            self.stats.fixes_applied += 1
            self.stats.fixes_rejected = max(0, self.stats.fixes_rejected)
            self.stats.by_pattern[prop.error_type]["applied"] += 1
            return True
        return False

    def reject(self, index: int) -> bool:
        """Reject a queued high-risk fix."""
        if 0 <= index < len(self._review_queue):
            prop = self._review_queue.pop(index)
            self.stats.fixes_rejected += 1
            return True
        return False

    # -- outcome tracking ---------------------------------------------------

    def record_outcome(self, error_type: str, success: bool) -> None:
        """Record whether a previously applied fix succeeded (caller-verified)."""
        if success:
            self.stats.successes += 1
        else:
            self.stats.failures += 1
        if error_type in self.stats.by_pattern:
            key = "success" if success else "failure"
            self.stats.by_pattern[error_type][key] += 1

    def snapshot(self) -> Dict[str, Any]:
        return {
            "version": "2.0.0",
            "files_scanned": self.stats.files_scanned,
            "errors_found": self.stats.errors_found,
            "fixes_proposed": self.stats.fixes_proposed,
            "fixes_applied": self.stats.fixes_applied,
            "fixes_rejected": self.stats.fixes_rejected,
            "successes": self.stats.successes,
            "failures": self.stats.failures,
            "review_pending": len(self._review_queue),
            "success_rate": (self.stats.successes /
                             max(1, self.stats.successes + self.stats.failures)),
            "by_pattern": dict(self.stats.by_pattern),
        }


__all__ = ["TerenceV2", "FixProposal", "HealStats", "LOW_RISK", "HIGH_RISK"]
