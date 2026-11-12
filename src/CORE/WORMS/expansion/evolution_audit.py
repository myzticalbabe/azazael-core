# coding: utf-8
"""
EVOLUTION INTEGRITY AUDIT — Category: MONITOR (1 worm)
EvolutionIntegrityWorm — runs the evolution benchmark on a schedule and asserts
the standing invariant that the promoted champion is the genome production
actually trains with, recording both as a measurable series.

Why this exists (2026-09-18 / 2026-09-19 audit):
    ``scripts/benchmark_evolution.py`` check [7] ("Promotion integrity") sat RED on
    disk for three days while the adaptation loop was a silent no-op: the champion
    in ``state/`` was rejected by a stale range check, so production trained on bare
    built-in defaults. Nothing ran the benchmark, and nothing asserted the equality
    between "champion on disk" and "config in use". A guard nobody runs is not a
    guard.

    This worm deliberately runs the REAL benchmark as a subprocess instead of
    re-implementing its checks. The 2026-09-18 audit (defect A4) found three guards
    that validated the wrong thing because each held its own copy of the logic;
    duplicating the benchmark here would repeat that mistake. The benchmark already
    returns 0/1, so it is CI-ready — it was simply never wired.

Usage:
    auditor = EvolutionIntegrityWorm()
    result = auditor.execute()       # run once now
    auditor.start_scheduler()        # daemon thread, default daily

Records to LocalMemoryStore:
    source=AZAZAEL_EVOLUTION_AUDIT, event_type=evolution_benchmark_run
    payload includes: checks_passed, checks_total, failed_checks,
                      champion_present, champion_applied, champion_in_use, genes
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
    note_failure("evolution_audit:L46", exc)
    _REGISTRY = False

    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):  # noqa: D103
        return lambda cls: cls


try:
    from CORE.local_memory import get_memory_store
    _STORE = True
except ImportError as exc:
    note_failure("evolution_audit:L56", exc)
    _STORE = False

    def get_memory_store(db_path: Optional[str] = None) -> "Any":  # noqa: D103
        return None


#: Genes a ScheduleConfig can actually apply. The promotion file may carry more
#: (structural mutations grow the genome); those are reported as ignored by
#: validate_genes and are not invariant violations.
APPLICABLE_GENES = ("learning_rate", "epsilon", "discount", "epsilon_decay")

#: Failures that mean "this machine has no live history yet", not "something
#: regressed". A pristine checkout has no `evolution_result` artifacts and an
#: empty pool, so these checks fail by construction — measured at 7/9 with
#: AZAZAEL_MEMORY_DIR pointed at an empty directory. They are reported
#: separately so the alarm does not cry wolf on a fresh install.
ENVIRONMENTAL_MARKERS = (
    "no artifacts yet",
    "pool too sparse",
    "only 0 states",
    "current-pool fitness 0.000",
)


class EvolutionAuditConfig:
    """Configuration for the evolution integrity audit."""

    def __init__(self, interval_seconds: float = 24 * 3600,
                 max_duration_seconds: float = 900.0,
                 db_path: Optional[str] = None):
        self.interval_seconds = interval_seconds
        self.max_duration_seconds = max_duration_seconds
        self.db_path = db_path


class EvolutionIntegrityWorm:
    """Benchmark + champion-in-use invariant, on a schedule.

    Two independent signals, both recorded:

    1. ``benchmark_ok``   — the full benchmark_evolution.py suite passes (the
       script's own exit code, not a re-derivation).
    2. ``champion_in_use`` — a valid promotion file exists AND
       ``ScheduleConfig.apply_evolved_overrides()`` both accepted it and produced
       exactly the genes the file declares. This is the equality that was silently
       false for three days.
    """

    VERSION = "1.0.0"

    def __init__(self, config: (EvolutionAuditConfig | None) = None):
        self.config = config or EvolutionAuditConfig()
        self._history: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # -- Benchmark ------------------------------------------------------

    def _benchmark_path(self) -> Path:
        """azazael/scripts/benchmark_evolution.py (4 hops up: expansion -> WORMS -> CORE -> src)."""
        base = Path(__file__).resolve().parent.parent.parent.parent.parent
        return base / "scripts" / "benchmark_evolution.py"

    def _run_benchmark(self) -> Dict[str, Any]:
        """Run the real benchmark and parse its own PASS/FAIL summary."""
        script = self._benchmark_path()
        if not script.exists():
            return {"ran": False, "ok": False, "reason": f"missing {script}"}
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.config.max_duration_seconds,
                cwd=str(script.parent.parent),
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("evolution_audit:L134", exc)
            return {"ran": False, "ok": False,
                    "reason": f"timed out after {self.config.max_duration_seconds}s"}
        except Exception as exc:  # noqa: BLE001 — the audit must never crash the caller
            note_failure("evolution_audit:L137", exc)
            return {"ran": False, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}

        out = (proc.stdout or "") + (proc.stderr or "")
        passed = total = 0
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("RESULT:"):
                # "RESULT: 12/13 checks passed"
                try:
                    frag = line.split("RESULT:", 1)[1].strip().split()[0]
                    passed_s, total_s = frag.split("/")
                    passed, total = int(passed_s), int(total_s)
                except (IndexError, ValueError) as exc:
                    note_failure("evolution_audit:L150", exc)
        # Take failures from the inline `[FAIL] name - detail` lines, NOT the
        # `RESULT:`/`FAILED:` summary. The summary carries only the check NAME, so
        # a marker looking for detail text ("no artifacts yet") could never match
        # it — the environmental/regression split would silently never fire.
        failed = [ln.strip()[len("[FAIL]"):].strip()
                  for ln in out.splitlines() if ln.strip().startswith("[FAIL]")]
        return {
            "ran": True,
            "ok": proc.returncode == 0 and total > 0 and passed == total,
            "returncode": proc.returncode,
            "checks_passed": passed,
            "checks_total": total,
            "failed_checks": failed,
        }

    # -- Champion-in-use invariant --------------------------------------

    def _champion_in_use(self) -> Dict[str, Any]:
        """Assert champion-on-disk == config-in-use, using the production reader.

        Uses ``apply_evolved_overrides()`` itself rather than re-validating, so
        this cannot drift from the code path that actually trains.
        """
        try:
            from CORE.WORMS.expansion.scheduling import ScheduleConfig
            from CORE.WORMS.expansion.rl_gene_bounds import validate_genes
        except ImportError as exc:
            note_failure("evolution_audit:L178", exc)
            return {"champion_present": None, "champion_applied": None,
                    "champion_in_use": None, "reason": f"import failed: {exc}"}

        paths = list(ScheduleConfig.evolved_param_paths())
        present = [p for p in paths if p.exists()]
        if not present:
            # Legitimate pre-first-promotion state. Not a violation — but not a
            # pass either, so it is reported as None rather than True.
            return {"champion_present": False, "champion_applied": False,
                    "champion_in_use": None, "reason": "no promotion file on disk"}

        # The most-preferred VALID file is the one production must adopt.
        target = None
        problems: List[str] = []
        for path in present:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                note_failure("evolution_audit:L196", exc)
                problems.append(f"{path.parent.name}/{path.name}: {type(exc).__name__}")
                continue
            check = validate_genes(data.get("genes"))
            if check.ok:
                target = (path, check)
                break
            problems.append(f"{path.parent.name}/{path.name}: {'; '.join(check.problems)}")

        config = ScheduleConfig()
        applied = config.apply_evolved_overrides()

        if target is None:
            # Every promotion file present is invalid. Production running bare
            # defaults is then correct behaviour, so this is not a violation.
            return {"champion_present": True, "champion_applied": applied,
                    "champion_in_use": None,
                    "reason": "all promotion files invalid: " + " | ".join(problems)}

        path, check = target
        want = {k: float(check.genes[k]) for k in APPLICABLE_GENES if k in check.genes}
        got = {
            "learning_rate": float(config.learning_rate),
            "epsilon": float(config.epsilon),
            "discount": float(config.discount_factor),
            "epsilon_decay": float(config.epsilon_decay),
        }
        mismatched = {k: {"file": want[k], "in_use": got[k]}
                      for k in want if abs(got[k] - want[k]) > 1e-12}

        return {
            "champion_present": True,
            "champion_file": f"{path.parent.name}/{path.name}",
            "champion_applied": bool(applied),
            "champion_in_use": bool(applied) and not mismatched,
            "genes": want,
            "mismatched": mismatched,
            "ignored_genes": sorted(check.ignored),
        }

    # -- Execution ------------------------------------------------------

    def execute(self) -> Dict[str, Any]:
        """Run one audit pass, record to the store, and return the combined verdict."""
        start = time.time()
        bench = self._run_benchmark()
        champion = self._champion_in_use()

        failed = list(bench.get("failed_checks", []))
        environmental = [f for f in failed
                         if any(m in f for m in ENVIRONMENTAL_MARKERS)]
        regressions = [f for f in failed if f not in environmental]

        # A missing-history failure is not a regression, so it does not make the
        # audit unhealthy. A champion that is present-but-ignored always does.
        ok = not regressions and champion.get("champion_in_use") is not False
        result: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "benchmark_ran": bench.get("ran"),
            "benchmark_ok": bench.get("ok"),
            "checks_passed": bench.get("checks_passed", 0),
            "checks_total": bench.get("checks_total", 0),
            "failed_checks": failed,
            "regressions": regressions,
            "environmental_gaps": environmental,
            "benchmark_reason": bench.get("reason"),
            "champion_present": champion.get("champion_present"),
            "champion_file": champion.get("champion_file"),
            "champion_applied": champion.get("champion_applied"),
            "champion_in_use": champion.get("champion_in_use"),
            "champion_genes": champion.get("genes"),
            "champion_mismatched": champion.get("mismatched"),
            "reason": champion.get("reason"),
            "duration_s": round(time.time() - start, 2),
        }

        # Cross-run delta: a check that newly went red is the signal that matters.
        with self._lock:
            prev_failed = set(self._history[-1].get("failed_checks", [])) if self._history else set()
            newly_failed = sorted(set(result["failed_checks"]) - prev_failed)
            result["newly_failed_checks"] = newly_failed
            self._history.append(result)

        if not ok:
            loud = regressions or [result.get("benchmark_reason") or "champion not in use"]
            print(f"[evolution-audit] FAIL — {'; '.join(str(x) for x in loud if x)}", flush=True)
            if result["champion_in_use"] is False:
                print(f"[evolution-audit] champion on disk is NOT the config in use: "
                      f"{result['champion_file']} vs {result['champion_mismatched']}", flush=True)
        elif environmental:
            # Healthy, but say so out loud: a monitor that silently absorbs
            # missing history is how you stop noticing that history went missing.
            print(f"[evolution-audit] {len(environmental)} check(s) unattributable — "
                  f"no live history yet: {'; '.join(environmental)}", flush=True)

        store = get_memory_store(self.config.db_path) if _STORE else None
        if store is not None:
            try:
                store.record_event(
                    source="AZAZAEL_EVOLUTION_AUDIT",
                    event_type="evolution_benchmark_run",
                    data={k: result[k] for k in (
                        "ok", "benchmark_ok", "checks_passed", "checks_total",
                        "failed_checks", "regressions", "environmental_gaps",
                        "newly_failed_checks", "champion_present",
                        "champion_applied", "champion_in_use", "champion_file",
                        "champion_genes", "duration_s")},
                    tags=["audit", "evolution", "monitor"],
                )
            except Exception as exc:  # noqa: BLE001 — recording is best-effort
                note_failure("evolution_audit:L306", exc)

        return result

    # -- Scheduler ------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.execute()
            except Exception as exc:  # noqa: BLE001 — a monitor must survive its own failures
                note_failure("evolution_audit:L317", exc)
            self._stop.wait(self.config.interval_seconds)

    def start_scheduler(self) -> None:
        """Start the daemon scheduler thread."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._stop.clear()
        self._scheduler_thread = threading.Thread(
            target=self._loop, daemon=True, name="azazael-evolution-audit",
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
    EvolutionIntegrityWorm = register_worm(
        category="monitor", priority=7, cooldown_seconds=3600.0,
        description="Evolution integrity audit — runs benchmark_evolution.py and asserts the promoted champion is the config production trains with, recorded as a monitorable series"
    )(EvolutionIntegrityWorm)

if __name__ == "__main__":
    worm = EvolutionIntegrityWorm()
    print(json.dumps(worm.execute(), indent=2, default=str))
