#!/usr/bin/env python
"""session_start_check.py - One-shot startup verification.

Runs the three core startup checks in a single invocation:
  1. DirectMemory verify    - DB reachable, session_start events recorded
  2. Registry warm          - blocking warm + ready/valid/size status
  3. Telemetry check        - telemetry.json parseable, reports ratio + age

Exit codes:
  0 - all checks passed
  1 - one or more checks failed

Usage:
    python azazael/src/scripts/session_start_check.py
    python azazael/src/scripts/session_start_check.py --timeout 120

Used by: session startup verification (manual or scripted).
Consumes only public APIs: CORE.direct_memory (summary_stats, db_size,
record) and CORE.startup_warm (warm_caches_blocking, cache_status).
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Bootstrap: put azazael/src on sys.path (see Lesson #55 import order) ──
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from CORE.direct_memory import DirectMemory, record as dm_record  # noqa: E402
from CORE.startup_warm import cache_status, warm_caches_blocking  # noqa: E402
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Config ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
TELEMETRY_PATH = SCRIPT_DIR / "telemetry.json"
TELEMETRY_MAX_AGE_HOURS = 48.0


def check_direct_memory() -> tuple[bool, str]:
    """Verify DirectMemory DB is reachable and has recorded session starts."""
    stats = DirectMemory.summary_stats()
    size = DirectMemory.db_size()
    total = stats["total_events"]
    session_starts = stats["by_type"].get("session_start", 0)
    db_mb = size["total_mb"]

    if total == 0 or session_starts == 0:
        return False, (
            f"DirectMemory: FAIL - total_events={total}, "
            f"session_start events={session_starts}, db={db_mb} MB"
        )
    return True, (
        f"DirectMemory: OK - {total} events, "
        f"{session_starts} session_start, {stats['sessions']} sessions, "
        f"db={db_mb} MB, open_sessions={stats['open_sessions']}"
    )


def check_registry_warm(timeout: float) -> tuple[bool, str]:
    """Blocking warm of the worm registry, then report status."""
    t0 = time.time()
    warm_ok = warm_caches_blocking(timeout=timeout)
    elapsed_ms = (time.time() - t0) * 1000
    status = cache_status()

    if not warm_ok or not status.get("ready") or not status.get("registry_valid"):
        return False, (
            f"Registry warm: FAIL - warm_ok={warm_ok}, "
            f"ready={status.get('ready')}, valid={status.get('registry_valid')}, "
            f"size={status.get('registry_size')} ({elapsed_ms:.0f} ms)"
        )
    return True, (
        f"Registry warm: OK - ready=True, valid=True, "
        f"size={status.get('registry_size')} ({elapsed_ms:.0f} ms)"
    )


def check_telemetry() -> tuple[bool, str]:
    """Verify telemetry.json exists, parses, and is fresh enough."""
    if not TELEMETRY_PATH.exists():
        return False, f"Telemetry: FAIL - {TELEMETRY_PATH} not found"
    try:
        data = json.loads(TELEMETRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        note_failure("session_start_check:L85", e)
        return False, f"Telemetry: FAIL - unreadable: {e}"

    ts_raw = data.get("timestamp")
    age_hours = None
    if ts_raw:
        try:
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        except ValueError as exc:
            note_failure("session_start_check:L96", exc)

    ratio = data.get("compliance_ratio")
    parts = [
        f"Telemetry: OK - compliance_ratio={ratio}",
        f"parallel={data.get('parallel_batches')}",
        f"sequential_violations={data.get('sequential_violations')}",
    ]
    if age_hours is not None:
        parts.append(f"age={age_hours:.1f}h")
        if age_hours > TELEMETRY_MAX_AGE_HOURS:
            parts.append(f"(WARNING: older than {TELEMETRY_MAX_AGE_HOURS}h)")
    return True, ", ".join(parts)


def main() -> int:
    timeout = 60.0
    if "--timeout" in sys.argv:
        idx = sys.argv.index("--timeout")
        if idx + 1 < len(sys.argv):
            timeout = float(sys.argv[idx + 1])

    print("session_start_check - startup verification")
    print("=" * 60)

    results = [
        check_direct_memory(),
        check_registry_warm(timeout),
        check_telemetry(),
    ]

    all_ok = True
    for ok, message in results:
        print(message)
        if not ok:
            all_ok = False

    print("=" * 60)

    # Record the verification result to DirectMemory (audit trail).
    try:
        dm_record(
            "tooling_verify",
            {
                "action": "tooling: session_start_check "
                f"({'all passed' if all_ok else 'FAILURES detected'})",
                "tools_found": sum(1 for ok, _ in results if ok),
                "tools_missing": sum(1 for ok, _ in results if not ok),
                "details": {
                    "script": "session_start_check.py",
                    "checks": [m for _, m in results],
                },
            },
            severity="info" if all_ok else "warning",
            tags=["tooling", "startup", "session_start_check"],
        )
    except Exception as e:  # never fail the script on audit-trail issues
        print(f"(note: could not record to DirectMemory: {e})")

    if all_ok:
        print("RESULT: ALL CHECKS PASSED")
        return 0
    print("RESULT: FAILURES DETECTED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
