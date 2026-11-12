# coding: utf-8
"""
CODEBASE AUDIT — Category: MONITOR (1 worm)
WeeklyCodeAuditWorm — runs the pyflakes/compile scan over src+scripts,
records error deltas (found -> fixed) as a measurable series, and registers
new reasoning traces for any error class it discovers so the pattern library
grows automatically.

Usage:
    auditor = WeeklyCodeAuditWorm()
    result = auditor.execute()   # run once now
    auditor.start_scheduler()    # daemon thread, default weekly

Records to LocalMemoryStore:
    source=AZAZAEL_CODE_AUDIT, event_type=code_audit_run
    payload includes: errors_found, errors_fixed_prev, delta, per-class counts
"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("code_audit:L32", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

try:
    from CORE.local_memory import get_memory_store
    _STORE = True
except ImportError as exc:
    note_failure("code_audit:L40", exc)
    _STORE = False
    def get_memory_store(db_path: Optional[str] = None) -> "Any":  # noqa: D103
        return None


class CodeAuditConfig:
    """Configuration for the weekly code audit."""

    def __init__(self, interval_seconds: float = 7 * 24 * 3600,
                 max_duration_seconds: float = 600.0,
                 roots: Optional[List[str]] = None,
                 db_path: Optional[str] = None):
        self.interval_seconds = interval_seconds
        self.max_duration_seconds = max_duration_seconds
        self.roots = roots or ["src", "scripts"]
        self.db_path = db_path


class WeeklyCodeAuditWorm:
    """Static error scan + reasoning-trace registration on a schedule."""

    VERSION = "1.0.0"

    def __init__(self, config: (CodeAuditConfig | None) = None):
        self.config = config or CodeAuditConfig()
        self._history: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # -- Scan -----------------------------------------------------------

    def _scan(self) -> Dict[str, Any]:
        """Run pyflakes over roots; classify undefined-name errors."""
        base = Path(__file__).resolve().parent.parent.parent.parent  # azazael/
        classes: Dict[str, int] = {}
        undefined: List[str] = []
        total = 0
        for root in self.config.roots:
            rp = base / root
            if not rp.exists():
                continue
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pyflakes", str(rp)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=self.config.max_duration_seconds,
                    env={**os.environ, "PYTHONUTF8": "1"},
                )
            except Exception as exc:
                note_failure("code_audit:L90", exc)
                continue
            for line in (proc.stdout or "").splitlines():
                total += 1
                if "undefined name" in line:
                    undefined.append(line)
                    if "undefined name" in line:
                        # classify by message tail, e.g. "... undefined name 'np'"
                        try:
                            name = line.rsplit("undefined name ", 1)[1].strip().strip("'\"")
                        except Exception as exc:
                            note_failure("code_audit:L100", exc)
                            name = "?"
                        classes[name] = classes.get(name, 0) + 1
        return {"total_issues": total, "undefined_total": len(undefined),
                "undefined_classes": classes, "undefined_examples": undefined[:20]}

    # -- Reasoning-trace registration ----------------------------------

    def _register_trace(self, name: str, count: int) -> None:
        """Register a reasoning trace for a recurring undefined-name class."""
        store = get_memory_store(self.config.db_path) if _STORE else None
        if store is None:
            return
        try:
            from CORE.embedding_backend import embed_text
            problem = (f"pyflakes reports undefined name '{name}' recurring {count} times "
                       "in the codebase scan")
            evidence = f"undefined name '{name}' appeared in {count} locations"
            solution = ("Check import position (docstring trap), aliased imports, "
                        "conditional imports, and dynamic globals before adding an import")
            vec = embed_text(f"{problem} {evidence} {solution}")
            store.register_pattern(
                source_agent="WeeklyCodeAuditWorm",
                pattern_type="code_repair_reasoning",
                embedding=vec,
                metadata={
                    "class": "recurring_undefined_name",
                    "trace_id": f"AUDIT-{name}",
                    "problem": problem,
                    "evidence": evidence,
                    "decision_procedure": "1. grep the name; 2. check import position relative to docstrings; 3. check aliases; 4. check dynamic globals; 5. fix + py_compile",
                    "solution": solution,
                    "verification": "py_compile + pyflakes re-run shows 0 occurrences",
                    "generalization": "Recurring undefined-name classes share import-position root causes",
                    "count": count,
                },
                confidence=min(1.0, 0.5 + 0.05 * count),
            )
        except Exception as exc:
            note_failure("code_audit:L138", exc)

    # -- Execution ------------------------------------------------------

    def execute(self) -> Dict[str, Any]:
        """Run one audit pass, record to store, register traces, return stats."""
        start = time.time()
        scan = self._scan()
        store = get_memory_store(self.config.db_path) if _STORE else None
        delta = 0
        if self._history:
            prev = self._history[-1].get("undefined_total", 0)
            delta = prev - scan["undefined_total"]
        result = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "undefined_total": scan["undefined_total"],
            "total_issues": scan["total_issues"],
            "delta": delta,
            "classes": scan["undefined_classes"],
            "duration_s": round(time.time() - start, 2),
        }
        with self._lock:
            self._history.append(result)
        # register a reasoning trace per recurring undefined-name class
        for name, count in scan["undefined_classes"].items():
            if count >= 2:
                self._register_trace(name, count)
        # record the run event
        if store is not None:
            try:
                store.record_event(
                    source="AZAZAEL_CODE_AUDIT",
                    event_type="code_audit_run",
                    data={k: result[k] for k in
                          ("undefined_total", "total_issues", "delta",
                           "duration_s", "classes")},
                    tags=["audit", "training"],
                )
            except Exception as exc:
                note_failure("code_audit:L177", exc)
        return result

    # -- Scheduler ------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.execute()
            except Exception as exc:
                note_failure("code_audit:L187", exc)
            self._stop.wait(self.config.interval_seconds)

    def start_scheduler(self) -> None:
        """Start the daemon scheduler thread."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._stop.clear()
        self._scheduler_thread = threading.Thread(
            target=self._loop, daemon=True, name="azazael-weekly-audit",
        )
        self._scheduler_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=10)

    def last_results(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            return self._history[-limit:]


# =======================================================================
# REGISTRATION
# =======================================================================

if _REGISTRY:
    WeeklyCodeAuditWorm = register_worm(
        category="monitor", priority=7, cooldown_seconds=3600.0,
        description="Weekly codebase audit — pyflakes scan, error-delta series, and automatic reasoning-trace registration for recurring error classes"
    )(WeeklyCodeAuditWorm)

if __name__ == "__main__":
    worm = WeeklyCodeAuditWorm()
    r = worm.execute()
    print(json.dumps(r, indent=2, default=str))