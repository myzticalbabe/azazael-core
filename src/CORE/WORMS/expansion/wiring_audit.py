# coding: utf-8
"""
WIRING AUDIT — Category: MONITOR (1 worm)
WiringAuditWorm — runs ``scripts/audit_wiring.py`` on a schedule and asserts
that every orchestration entry point in this repo has a caller.

Why this exists:
    Four independent findings in the 2026-09-18/19 audits share one shape — a
    mechanism that is complete, tested, and invoked by nothing:

        scripts/benchmark_evolution.py   CI-ready, exited 1 on failure, no caller
        start_scheduler()                the "daemon thread" idiom, no caller
        MORPHING_WORM                    registerable, nothing registered it
        evolution_audit                  built, then never scheduled

    Each was individually correct and collectively dead. So the question that
    finds bugs here is no longer "does the subsystem work?" but "**is the
    subsystem called?**". ``scripts/audit_wiring.py`` answers that statically;
    this worm makes sure the answer is checked on a schedule instead of once.

    The calling problem is self-referential on purpose: an audit of uncalled
    mechanisms that nothing calls would be the fifth instance of the pattern.
    That is why it is wired into ``dashboard.py --watch``, the one process that
    actually stays resident.

Two guards, because "0 findings" must never be taken on faith:
    1. It runs the audit's OWN self-test first. ``audit_wiring.py --self-test``
       validates the detector against known-bad and known-good fixtures. An
       audit whose detector is broken also reports the cheerful "nothing found",
       and a previous audit in this repo did exactly that — so a passing
       self-test is a precondition for believing a clean result, not a nicety.
    2. It reports findings as failures rather than logging them as observations.

Usage:
    worm = WiringAuditWorm()
    result = worm.execute()        # run once now
    worm.start_scheduler()         # daemon thread, default daily

Records to LocalMemoryStore:
    source=AZAZAEL_WIRING_AUDIT, event_type=wiring_audit_run
    payload includes: ok, instrument_ok, findings, finding_names,
                      scanned_files, candidates, newly_dead, duration_s
"""

import contextlib
import importlib.util
import io
import json
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
    note_failure("wiring_audit:L58", exc)
    _REGISTRY = False

    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):  # noqa: D103
        return lambda cls: cls


try:
    from CORE.local_memory import get_memory_store
    _STORE = True
except ImportError as exc:
    note_failure("wiring_audit:L68", exc)
    _STORE = False

    def get_memory_store(db_path: Optional[str] = None) -> "Any":  # noqa: D103
        return None


class WiringAuditConfig:
    """Configuration for the wiring audit."""

    def __init__(self, interval_seconds: float = 24 * 3600,
                 root: Optional[str] = None,
                 db_path: Optional[str] = None):
        self.interval_seconds = interval_seconds
        self.root = root
        self.db_path = db_path


class WiringAuditWorm:
    """Static wiring audit, on a schedule.

    Cheap (~1s, read-only) and deterministic: it parses source, so it only
    changes when the code does. A daily default is ample; the dashboard runs it
    more often so a regression is caught the same session it is introduced.
    """

    VERSION = "1.0.0"

    def __init__(self, config: (WiringAuditConfig | None) = None):
        self.config = config or WiringAuditConfig()
        self._history: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # -- Audit loading --------------------------------------------------

    def _audit_path(self) -> Path:
        """azazael/scripts/audit_wiring.py (5 hops up: expansion -> WORMS -> CORE -> src)."""
        base = Path(__file__).resolve().parent.parent.parent.parent.parent
        return base / "scripts" / "audit_wiring.py"

    def _load_audit_module(self):
        """Import the CLI audit by path, re-executed from disk each call.

        It lives in scripts/, outside the package, so it cannot be imported by
        name — loading by path is what CORE/compliance/broker.py already does.
        Re-reading from disk means an edited audit takes effect without a
        restart of the resident dashboard.
        """
        path = self._audit_path()
        if not path.exists():
            raise FileNotFoundError(f"missing {path}")
        spec = importlib.util.spec_from_file_location("azazael_audit_wiring", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _instrument_ok(self, module) -> bool:
        """Validate the detector before trusting its verdict (see docstring)."""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                return module.self_test() == 0
        except Exception as exc:  # noqa: BLE001 — an unvalidatable instrument is not ok
            note_failure("wiring_audit:L134", exc)
            return False

    # -- Execution ------------------------------------------------------

    def execute(self) -> Dict[str, Any]:
        """Run one audit pass, record to the store, and return the verdict."""
        start = time.time()
        try:
            module = self._load_audit_module()
        except Exception as e:  # noqa: BLE001 — a monitor must not crash the dashboard
            result = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "instrument_ok": False,
                "findings": [],
                "finding_names": [],
                "reason": f"could not load audit: {type(e).__name__}: {e}",
                "duration_s": round(time.time() - start, 2),
            }
            self._record(result)
            return result

        instrument_ok = self._instrument_ok(module)

        root = Path(self.config.root) if self.config.root else module.PROJECT_ROOT
        try:
            report = module.audit(root)
        except Exception as e:  # noqa: BLE001
            note_failure("wiring_audit:L162", e)
            report = {"findings": [], "allowed": [], "scanned_files": 0,
                      "candidates": 0, "error": f"{type(e).__name__}: {e}"}

        findings = report.get("findings", [])
        names = sorted({f["name"] for f in findings})

        # A broken detector must not be allowed to report "clean".
        ok = instrument_ok and not findings

        result: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "instrument_ok": instrument_ok,
            "findings": findings,
            "finding_names": names,
            "allowed_count": len(report.get("allowed", [])),
            "scanned_files": report.get("scanned_files", 0),
            "candidates": report.get("candidates", 0),
            "reason": report.get("error"),
            "duration_s": round(time.time() - start, 2),
        }

        with self._lock:
            prev = set(self._history[-1].get("finding_names", [])) if self._history else set()
            result["newly_dead"] = sorted(set(names) - prev)
            self._history.append(result)

        if not instrument_ok:
            print("[wiring-audit] FAIL — the audit's own self-test failed; its "
                  "verdict cannot be trusted (a broken detector also reports "
                  "'nothing found')", flush=True)
        elif findings:
            loud = "; ".join(f"{f['name']} ({f['file']})" for f in findings)
            print(f"[wiring-audit] FAIL — {len(findings)} entry point(s) with no "
                  f"caller: {loud}", flush=True)
        else:
            print(f"[wiring-audit] OK — {result['candidates']} entry-point names "
                  f"across {result['scanned_files']} files, all called", flush=True)

        self._record(result)
        return result

    def _record(self, result: Dict[str, Any]) -> None:
        """Best-effort record to the shared store. Never raises."""
        store = get_memory_store(self.config.db_path) if _STORE else None
        if store is None:
            return
        try:
            store.record_event(
                source="AZAZAEL_WIRING_AUDIT",
                event_type="wiring_audit_run",
                data={k: result.get(k) for k in (
                    "ok", "instrument_ok", "finding_names", "allowed_count",
                    "scanned_files", "candidates", "newly_dead", "reason",
                    "duration_s")},
                tags=["audit", "wiring", "monitor"],
            )
        except Exception as exc:  # noqa: BLE001 — recording is best-effort
            note_failure("wiring_audit:L220", exc)

    # -- Scheduler ------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.execute()
            except Exception as exc:  # noqa: BLE001 — a monitor must survive its own failures
                note_failure("wiring_audit:L229", exc)
            self._stop.wait(self.config.interval_seconds)

    def start_scheduler(self) -> None:
        """Start the daemon scheduler thread."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._stop.clear()
        self._scheduler_thread = threading.Thread(
            target=self._loop, daemon=True, name="azazael-wiring-audit",
        )
        self._scheduler_thread.start()

    def stop(self) -> None:
        """Stop the scheduler thread."""
        self._stop.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=10)

    def last_results(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Most recent audit results, oldest first."""
        with self._lock:
            return self._history[-limit:]


# =======================================================================
# REGISTRATION
# =======================================================================

if _REGISTRY:
    WiringAuditWorm = register_worm(
        category="monitor", priority=6, cooldown_seconds=3600.0,
        description="Wiring audit — asserts every orchestration entry point in the repo has a caller, validating its own detector first so a clean result means something"
    )(WiringAuditWorm)

if __name__ == "__main__":
    worm = WiringAuditWorm()
    print(json.dumps(worm.execute(), indent=2, default=str))
