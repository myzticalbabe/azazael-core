"""Nightly maintenance entrypoint for Task Scheduler.

Runs CORE.data_retention.run_maintenance(): event TTL, session rotation,
DB quota check, .audit archival, conditional VACUUM, WAL checkpoint.
Appends results to logs/maintenance.log (scheduler captures no output).

CWD-independent: uses absolute paths throughout.
"""
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

LOG = _ROOT / "logs" / "maintenance.log"

# -- Lifecycle-owned collaborators (Lane C redo, 2026-10-22) ----------------
# Script-level declarations: resolved once at startup. get_memory_store is
# the EP414 probe's host binding (no per-pass import); MemoryBus is the
# pre-existing drain binding, lifted with it so the script owns both.
try:
    from CORE.local_memory import get_memory_store as _get_memory_store
    _STORE_OK = True
except ImportError:
    _get_memory_store = None  # type: ignore
    _STORE_OK = False
try:
    from CORE.MemoryBus import MemoryBus as _MemoryBus
    from CORE.MemoryBus import _MEMORYBUS_DB_PATH as _MEMORYBUS_DB_PATH
    _BUS_OK = True
except ImportError:
    _MemoryBus = None  # type: ignore
    _MEMORYBUS_DB_PATH = None  # type: ignore
    _BUS_OK = False


def _pams_healthy(timeout: float = 4.0) -> bool:
    """True when PAMS answers on its real health path (/api/health)."""
    try:
        import urllib.request as _url
        with _url.urlopen("http://127.0.0.1:21434/api/health",
                          timeout=timeout) as _r:
            if _r.status == 200:
                return True
    except Exception:
        pass
    try:
        import json as _json
        _pf = Path(r"G:\source-of-truth\persistent-ai-memory\memory_data"
                   r"\mcp_server_port.json")
        _port = int((_json.loads(_pf.read_text(encoding="utf-8"))
                     .get("active_port") or 0)) if _pf.is_file() else 0
        if _port > 0:
            with _url.urlopen(f"http://127.0.0.1:{_port}/api/health",
                              timeout=timeout) as _r:
                return _r.status == 200
    except Exception:
        pass
    return False


def _ensure_pams(wait_s: int = 60) -> bool:
    """Start pams-server.exe detached if unhealthy; wait for health.

    Returns True when PAMS answers. Never raises -- callers decide.
    """
    if _pams_healthy():
        return True
    try:
        import shutil as _sh, subprocess as _sp
        _exe = _sh.which("pams-server") or _sh.which("pams-server.exe")
        if not _exe:
            return False
        _flags = 0
        if hasattr(_sp, "DETACHED_PROCESS"):
            _flags |= _sp.DETACHED_PROCESS
        if hasattr(_sp, "CREATE_NEW_PROCESS_GROUP"):
            _flags |= _sp.CREATE_NEW_PROCESS_GROUP
        _sp.Popen([_exe], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                  stdin=_sp.DEVNULL, creationflags=_flags,
                  close_fds=True)
    except Exception:
        return False
    _deadline = time.monotonic() + wait_s
    while time.monotonic() < _deadline:
        if _pams_healthy():
            return True
        time.sleep(2)
    return _pams_healthy()


def main() -> int:
    from CORE.data_retention import run_maintenance
    LOG.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    # Chain-quiet guard (2026-09-27): bulk writes (cap trims, quota
    # batches, VACUUM) race the chain's 8 writers for the DB lock. If a
    # chain job fired within the last 15 minutes, run the light subset
    # (TTL + caps are small indexed deletes); the heavy passes wait for a
    # quiet window. Never fail the task on this decision.
    heavy_ok = True
    try:
        import json as _json
        _lf = _ROOT / "state" / "chain_controller_last_fire.json"
        _at = (_json.loads(_lf.read_text(encoding="utf-8")).get("at", "")
               if _lf.is_file() else "")
        if _at:
            from datetime import datetime as _dt
            _parsed = _dt.fromisoformat(_at)
            if _parsed.tzinfo is None:
                # 2026-09-28: chain writes naive local isoformat; comparing
                # against aware now() raised (naive-vs-aware) and the check
                # failed open every night. Assume local wall time.
                _parsed = _parsed.astimezone()
            _age = (_dt.now().astimezone() - _parsed).total_seconds()
            if _age < 900:
                heavy_ok = False
                print("chain active %.0fs ago: light pass (no vacuum/quota)" % _age)
    except Exception as _e:
        print("chain-quiet check failed open: %s" % str(_e)[:120])
    try:
        results = run_maintenance(light=not heavy_ok)
    except Exception as e:  # never fail the scheduled task loudly
        results = {"fatal": str(e)[:200]}
    elapsed = time.perf_counter() - t0
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"=== {time.strftime('%Y-%m-%dT%H:%M:%S')} ({elapsed:.0f}s) ===\n")
        for task, result in results.items():
            status = "OK" if "error" not in result else "FAIL"
            f.write(f"  [{status}] {task}: {result}\n")
    # Drain the PAMS fallback queue on the live path (2026-09-27): the
    # dashboard-thread drain never runs (no --watch process), so 5k rows
    # sat unflushed since 09-24. Flush marks only PAMS-confirmed rows and
    # no-ops when PAMS is down — safe to run every night.
    # 2026-09-28: start PAMS FIRST so the drain has something to drain into.
    # A down server made every nightly drain a silent no-op (queue hit 5k+
    # rows / 214MB). Fail-soft throughout: a PAMS that won't start must
    # never fail upkeep.
    try:
        _ensure_pams()
        if _MemoryBus is None:
            raise RuntimeError("memorybus-unavailable")
        drained = _MemoryBus().flush_local_queue()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"  bus_drain: {drained} rows flushed\n")
            # Backlog visibility (2026-09-28): an unflushed backlog that
            # only shrinks is fine; one that grows while PAMS is up means
            # posts are failing again. Oldest-row age is the tripwire.
            try:
                import sqlite3 as _sq
                _qp = _MEMORYBUS_DB_PATH
                _c = _sq.connect(str(_qp), timeout=10)
                _row = _c.execute(
                    "SELECT COUNT(*), MIN(timestamp) FROM memorybus_queue"
                    " WHERE flushed=0").fetchone()
                _c.close()
                f.write(f"  bus_backlog: {_row[0]} unflushed, "
                        f"oldest={_row[1]}\n")
            except Exception as _e2:
                f.write(f"  bus_backlog: unreadable ({str(_e2)[:80]})\n")
    except Exception as _e:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"  bus_drain: SKIPPED ({str(_e)[:120]})\n")
    # The pass itself is worth remembering: which caps/TTLs fired and what
    # the quota backstop did. Fail-soft — a dead store must not fail upkeep.
    try:
        store = _get_memory_store() if _STORE_OK else None
        if store is not None:
            summary = {k: (v if isinstance(v, (int, float, str, bool))
                           else str(v)[:300])
                       for k, v in (results or {}).items()}
            # EP414: generation-based compaction state rides the pass record —
            # which compaction family is active is maintenance-relevant state.
            # Read-only probe; a probe failure records the gap, never the pass.
            try:
                compaction = store.generative_compaction_status()
            except Exception:
                compaction = {"verified": False,
                              "reason": "probe-failed"}
            store.record_event(source="retention", event_type="retention_pass",
                               data={"tasks": summary,
                                     "elapsed_s": round(elapsed, 1),
                                     "compaction": {
                                         "verified": compaction.get(
                                             "verified"),
                                         "generation_based": compaction.get(
                                             "generation_based"),
                                         "rollup": compaction.get("rollup"),
                                         "budget": compaction.get("budget")},
                                     },
                               severity="info",
                               tags=["retention", "maintenance"])
            print("  compaction: generation_based=%s rollup=%s" % (
                compaction.get("generation_based"),
                compaction.get("rollup")))
    except Exception:
        pass
    print(f"maintenance done in {elapsed:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
