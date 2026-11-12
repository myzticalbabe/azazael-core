"""doctor.py — Self-diagnostic for Azazael systems.

Covers followups: #3 (doctor), #5 (process watchdog), #6 (startup validation),
#14 (DB integrity check), #25 (query timeout), #30 (index verification),
#31 (file permission validation), #34 (env var validation).

Usage:
    python -m CORE.doctor          # Full diagnostic
    python -m CORE.doctor --quick   # Quick health check
"""


__all__ = [
    "check_db_integrity",
    "check_disk_space",
    "check_env_vars",
    "check_file_permissions",
    "check_index_usage",
    "check_registry_importable",
    "cmd_doctor",
    "cmd_verify",
    "get_db_size",
    "run_all",
]


import os
import sys
import json
import time
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def check_db_integrity() -> dict:
    """Run PRAGMA integrity_check on the DB."""
    from CORE.direct_memory import DB_PATH
    start = time.perf_counter()
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        elapsed = (time.perf_counter() - start) * 1000
        conn.close()
        return {"ok": result == "ok", "result": result, "elapsed_ms": round(elapsed, 1)}
    except Exception as e:
        note_failure("doctor:L48", e)
        return {"ok": False, "error": str(e)}


def check_index_usage() -> list:
    """Check which indexes are used by common queries (EXPLAIN QUERY PLAN)."""
    from CORE.direct_memory import DB_PATH
    queries = [
        ("SELECT * FROM memories WHERE timestamp > '2020-01-01'", "memories(timestamp)"),
        ("SELECT * FROM memories WHERE session_id = 'test' AND event_type = 'test'", "memories(session_id, event_type)"),
        ("SELECT * FROM memories WHERE event_uuid = 'test'", "memories(event_uuid)"),
        ("SELECT * FROM session_mappings WHERE dm_session_id = 'test'", "idx_mappings_dm"),
    ]
    results = []
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        for query, expected_index in queries:
            plan = conn.execute(f"EXPLAIN QUERY PLAN {query}").fetchall()
            plan_str = " ".join(str(r[2]) if len(r) > 2 else str(r) for r in plan)
            uses_index = expected_index in plan_str
            results.append({"query": query[:60], "expected_index": expected_index, "uses_index": uses_index, "plan": plan_str[:800]})
        conn.close()
    except Exception as exc:
        note_failure("doctor:L70", exc)
    return results


def get_db_size() -> dict:
    """Get database file size and WAL size."""
    from CORE.direct_memory import DB_PATH
    db = Path(str(DB_PATH))
    wal = db.with_suffix(".db-wal")
    shm = db.with_suffix(".db-shm")
    sizes: dict[str, float] = {"db": db.stat().st_size if db.exists() else 0}
    if wal.exists():
        sizes["wal"] = wal.stat().st_size
    if shm.exists():
        sizes["shm"] = shm.stat().st_size
    sizes["total_mb"] = round(sum(sizes.values()) / (1024 * 1024), 2)
    sizes["db_mb"] = round(sizes["db"] / (1024 * 1024), 2)
    return sizes


def check_file_permissions() -> list:
    """Verify .audit/ files are read-only after close."""
    import stat
    from CORE.audit_trail import AUDIT_DIR
    issues = []
    if not AUDIT_DIR.exists():
        return issues
    for fp in sorted(AUDIT_DIR.glob("session_*.jsonl")):
        try:
            mode = os.stat(str(fp)).st_mode
            is_writable = bool(mode & stat.S_IWRITE)
            if is_writable:
                issues.append(f"{fp.name}: writable (should be read-only)")
        except Exception as exc:
            note_failure("doctor:L104", exc)
    return issues


def check_env_vars() -> list:
    """Validate required environment variables."""
    warnings = []
    if not os.environ.get("OLLAMA_HOST", ""):
        pass  # Optional — InferencePool uses default
    mem_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    if mem_dir and not Path(mem_dir).exists():
        warnings.append(f"AZAZAEL_MEMORY_DIR={mem_dir}: directory does not exist")
    return warnings


def check_disk_space() -> dict:
    """Check available disk space on DB and audit directories."""
    from CORE.direct_memory import DB_PATH
    from CORE.audit_trail import AUDIT_DIR
    result = {}
    for label, path in [("db", Path(str(DB_PATH)).parent), ("audit", AUDIT_DIR)]:
        try:
            usage = shutil.disk_usage(str(path))
            free_gb = round(usage.free / (1024**3), 1)
            total_gb = round(usage.total / (1024**3), 1)
            pct = round(usage.used / usage.total * 100, 1)
            result[label] = {"free_gb": free_gb, "total_gb": total_gb, "used_pct": pct}
        except Exception as exc:
            note_failure("doctor:L132", exc)
    return result


def _check_ok(result) -> bool:
    """Single predicate for "did this check pass".

    Defect fixed 2026-10-03: `run_all()` computed health from
    `.get("ok", True)` while `cmd_verify()` counted issues from
    `.get("ok", result.get("healthy", True))`. The ollama check returns
    `healthy` and no `ok`, so the two disagreed on the SAME data: doctor
    printed `[FAIL] ollama`, verify printed `PASS — 1 issues`, and the
    aggregate said `HEALTHY`. Two predicates over one dataset is the bug.
    `healthy` is now honoured everywhere, so the per-check label, the issue
    count, and the aggregate all answer the same question the same way.
    """
    if not isinstance(result, dict):
        return True
    return bool(result.get("ok", result.get("healthy", True)))


def check_registry_importable() -> dict:
    """Validate every registered worm's module is importable.

    Prefers the result warmed by CORE.startup_warm at boot (avoids re-running
    cold discovery / validation). Falls back to a direct call if the warm
    pipeline hasn't populated it yet. Non-fatal: a broken worm is reported,
    never raised.

    Returns:
        dict with:
            valid: bool              — True if no broken entries
            total: int               — registry entries checked
            checked: int             — entries confirmed importable
            broken_count: int        — broken entries
            broken: list             — of {name, module, error}
            source: str              — "warm" or "direct"
            error: optional str      — set if validation itself failed
    """
    # Prefer the startup_warm cached result (validated right after discovery).
    try:
        from CORE.startup_warm import get_registry_validation
        cached = get_registry_validation()
        if cached is not None:
            out = dict(cached)
            out["broken_count"] = len(cached.get("broken") or [])
            out["source"] = "warm"
            out["ok"] = bool(cached.get("valid"))
            return out
    except Exception as exc:
        note_failure("doctor:L182", exc)

    try:
        # Ensure the registry is populated so validation isn't vacuous.
        try:
            from CORE.startup_warm import ensure_registry_warm
            ensure_registry_warm()
        except Exception as exc:
            note_failure("doctor:L190", exc)
        from CORE.worm_registry import validate_registry_importable
        res = validate_registry_importable()
        out = dict(res)
        out["broken_count"] = len(res.get("broken") or [])
        # Auto-repair: if broken entries remain, re-run discovery to rebuild
        # the registry from current source, then re-validate.
        if not res.get("valid") and (res.get("broken") or []):
            try:
                from CORE.worm_registry import repair_registry
                repair = repair_registry()
                out["repaired"] = repair.get("repaired", 0)
                out["repair_attempted"] = repair.get("attempt", False)
                out["repair_valid"] = repair.get("valid")
                out["repair_still_broken"] = repair.get("still_broken") or []
                if repair.get("valid"):
                    out["valid"] = True
                    out["broken"] = []
                    out["broken_count"] = 0
                    out["checked"] = repair.get("checked", out.get("checked", 0))
                    out["total"] = repair.get("total", out.get("total", 0))
            except Exception as re:
                note_failure("doctor:L212", re)
                out["repair_error"] = str(re)
        out["source"] = "direct"
        out["ok"] = bool(out.get("valid"))
        return out
    except Exception as e:
        note_failure("doctor:L217", e)
        return {
            "valid": False,
            "total": 0,
            "checked": 0,
            "broken_count": 0,
            "broken": [],
            "source": "direct",
            "ok": False,
            "error": str(e),
        }


def run_all(quick: bool = False) -> dict:
    """Run all diagnostic checks and return report."""
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "checks": {}}

    # DB integrity (skip in quick mode)
    if not quick:
        report["checks"]["db_integrity"] = check_db_integrity()
        report["checks"]["index_usage"] = check_index_usage()

    # DB size
    report["checks"]["db_size"] = get_db_size()

    # File permissions
    report["checks"]["file_permissions"] = check_file_permissions()

    # Environment variables
    report["checks"]["env_vars"] = check_env_vars()

    # Disk space
    report["checks"]["disk_space"] = check_disk_space()

    # Ollama connectivity
    try:
        from CORE.inference_pool import InferencePool
        pool = InferencePool(max_concurrent=1)
        report["checks"]["ollama"] = pool.health()
    except Exception as e:
        note_failure("doctor:L256", e)
        report["checks"]["ollama"] = {"healthy": False, "error": str(e)}

    # Audit trail health
    try:
        from CORE.audit_trail import verify_on_startup
        broken = verify_on_startup()
        report["checks"]["audit"] = {"broken_files": len(broken), "broken": [b.get("file", "")[-40:] for b in broken]}
    except Exception as e:
        note_failure("doctor:L264", e)
        report["checks"]["audit"] = {"error": str(e)}

    # Registry importability (every registered worm must resolve)
    report["checks"]["registry_importable"] = check_registry_importable()

    # Session stats
    try:
        from CORE.direct_memory import DirectMemory
        report["checks"]["sessions"] = {
            "open": DirectMemory.get_open_sessions_count(),
            "total_events": DirectMemory.summary_stats().get("total_events", 0),
        }
    except Exception as e:
        note_failure("doctor:L277", e)
        report["checks"]["sessions"] = {"error": str(e)}

    # Overall health — same predicate as the per-check label and the verify
    # issue count, so the three can never disagree (see `_check_ok`).
    all_ok = all(_check_ok(c) for c in report["checks"].values())
    report["healthy"] = all_ok
    return report


def cmd_doctor():
    """Print diagnostic report to stdout."""
    report = run_all()
    cols = shutil.get_terminal_size().columns
    print("=" * cols)
    print(f"  AZAZAEL Doctor -- {'HEALTHY' if report.get('healthy', False) else 'ISSUES FOUND'}")
    print("=" * cols)
    for check_name, result in report["checks"].items():
        if isinstance(result, dict):
            status = "[OK]" if _check_ok(result) else "[FAIL]"
            print(f"\n  {status} {check_name}")
            for k, v in result.items():
                if k not in ("ok", "healthy", "error"):
                    print(f"      {k}: {v}")
            if "error" in result:
                print(f"      error: {result['error']}")
            if isinstance(result.get("broken"), list) and result["broken"]:
                for b in result["broken"]:
                    print(f"      broken: {b}")
            if isinstance(result.get("warnings"), list) and result["warnings"]:
                for w in result["warnings"]:
                    print(f"      warning: {w}")
        elif isinstance(result, list):
            if result:
                print(f"\n  [WARN] {check_name}")
                for item in result:
                    print(f"      {item}")
    print(f"\n  Overall: {'[OK] HEALTHY' if report.get('healthy') else '[FAIL] ISSUES FOUND'}")
    return report


def cmd_verify():
    """Run verification checks and print summary."""
    report = run_all(quick=True)
    all_ok = report.get("healthy", False)
    issues = sum(1 for c in report["checks"].values() if not _check_ok(c))
    print(f"Verify: {'PASS' if all_ok else 'FAIL'} — {issues} issues")
    return report


if __name__ == "__main__":
    import sys
    if "--json" in sys.argv:
        print(json.dumps(run_all(), indent=2))
    elif "--quick" in sys.argv:
        cmd_verify()
    else:
        cmd_doctor()
