# coding: utf-8
"""Global test configuration — Python 3.15t free-threading environment constraints.

Loads BEFORE any test module imports. Sets thread-limiting env vars to prevent
C-extension race conditions (OpenBLAS MemoryError, chardet import crashes, etc.)
that occur under free-threading builds.

See: Error #156, Lesson #82. 3.14t was RETIRED on 2026-10-19: production is the
3.15t venv, and the old azazael-fix-python-314t skill is archived.
"""

import os
import pathlib
import sys
import tempfile
import threading

import pytest

# ── Import-time prober pre-warm (collection fix 2026-10-01) ────────
# `dill` probes stdio (incl. nul) at IMPORT time via get_file_type, pulled in
# through torch -> torch.utils.data -> import_dill. The sentinel audit hook
# denies nul opens with no exemptions (user directive), so whichever test
# module imports torch AFTER the guard arms dies at COLLECTION with
# "Unauthorized write to nul" while itself innocent — the face rotates with
# import order (was sentinel/dill, then test_replay_failed). Probing once
# here, before the guard can arm, removes the class for this prober. A new
# third-party import-time nul-prober gets the same one-line treatment.
try:
    import dill  # noqa: F401
except Exception:
    pass

# ── Constrain C-extension thread pools ──────────────────────────
# Free-threading (3.15t, as 3.14t before it) removes the GIL, exposing thread-unsafe
# C-libraries that aggressively pre-allocate memory based on CPU count.
# Force single-threaded mode to prevent allocation failures and deadlocks.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("BLIS_NUM_THREADS", "1")

# ── Production-log quarantine (forensic 2026-09-26) ───────────────
# scripts/evolution_cycle.log() appends to the live production log, and unit
# tests calling cycle helpers wrote tmp-path rows into it — mimicking a
# snapshot-path anomaly in the real log. Route all test-process cycle logging
# to the null device unless explicitly overridden.
os.environ.setdefault("AZAZAEL_CYCLE_LOG", os.devnull)

# ── Morphogenesis containment pen (audit 2026-09-15, finding #3) ────
# `SpineCycleWorm.run_cycle(apply=True)` can stage generated specialist
# source into `CORE/WORMS/expansion/generated/`. Tests must never write
# source into the tree they are testing, so the pen is redirected to a
# session temp dir. This is the same override an installed deployment uses
# when the package directory is read-only.
_GENERATED_DIR_OVERRIDE = None
if not os.environ.get("AZAZAEL_GENERATED_DIR"):
    import tempfile

    _GENERATED_DIR_OVERRIDE = tempfile.mkdtemp(prefix="azazael_generated_")
    os.environ["AZAZAEL_GENERATED_DIR"] = _GENERATED_DIR_OVERRIDE

# ── Entrenchment snapshot (colour queens, 2026-09-17) ───────────────
# `GrayQueenWorm.detect()` persists its census snapshot so the NEXT session can
# report a delta instead of a total. `SpineCycleWorm._governance_stage()` calls
# it on every cycle, so without a redirect a test run would write
# `NEURAL_MESH_ENTRENCHMENT.json` into the repository it is testing — and would
# also overwrite the operator's real baseline, destroying the diff it exists to
# produce. Same redirect, same reason as the containment pen above.
_ENTRENCHMENT_OVERRIDE = None
if not os.environ.get("AZAZAEL_ENTRENCHMENT_PATH"):
    _ENTRENCHMENT_OVERRIDE = pathlib.Path(
        tempfile.mkdtemp(prefix="azazael_entrenchment_")) / "entrenchment.json"
    os.environ["AZAZAEL_ENTRENCHMENT_PATH"] = str(_ENTRENCHMENT_OVERRIDE)

# ── Memory store isolation (audit 2026-09-21) ───────────────────────
# The SAME hole the spine-state fixture below was written for, still open for
# the memory store. `_record_cluster_worm_telemetry` calls
# `get_memory_store()`, so `tests/test_clusterworms_unit.py` and
# `tests/test_parallel_integration.py` — which execute worms literally named
# `NONEXISTENT` and `NONEXISTENT_RETRY` — wrote their failures into the
# DEVELOPER'S live `~/.azazael/memory_store.db` as if they were real fleet
# telemetry. Measured 2026-09-21: 45 such rows (36 + 9, all failing) sitting in
# the production outcome tap, plus a `TAPTEST` row, where any learner reading
# per-worm reliability would grade them as genuine executions.
#
# Must be set BEFORE `CORE.local_memory` is imported, because that module
# computes `DB_PATH` from this env var at import time — the same
# set-at-collection-reason as the two redirects above. Global rather than
# opt-in, for the reason already stated there: "remember to patch this" is not
# a mechanism.
_MEMORY_DIR_OVERRIDE = None
# Per-worker isolation under xdist: each worker process must get its own
# memory dir. The controller sets AZAZAEL_MEMORY_DIR and all workers inherit
# the same value, so without this override every worker shares one store and
# races on it (wal_autocheckpoint_proof, local_memory, etc.). A per-worker
# (or per-process, when not under xdist) dir removes the shared-state crash.
_worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
if os.environ.get("PYTEST_XDIST_WORKER"):
    _MEMORY_DIR_OVERRIDE = tempfile.mkdtemp(prefix="azazael_memory_%s_" % _worker_id)
    os.environ["AZAZAEL_MEMORY_DIR"] = _MEMORY_DIR_OVERRIDE
elif not os.environ.get("AZAZAEL_MEMORY_DIR"):
    _MEMORY_DIR_OVERRIDE = tempfile.mkdtemp(prefix="azazael_memory_")
    os.environ["AZAZAEL_MEMORY_DIR"] = _MEMORY_DIR_OVERRIDE


# ── Memory DB path restore (audit 2026-09-22) ───────────────────────
# The override above pins `AZAZAEL_MEMORY_DIR` for the session, but every module
# that derives a DB path from it computes that path ONCE, at import, into a
# module global (`CORE/direct_memory.py:73  DB_PATH = _get_db_path()`, same shape
# in `local_memory`, `record_session`, `MemoryBus`). Legacy unittest-style suites
# redirect themselves by mutating BOTH:
#
#     os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)      # setUp
#     direct_memory.DB_PATH = direct_memory._get_db_path()
#     ...
#     shutil.rmtree(str(tmpdir))                          # tearDown
#
# ...and restore only the env var. The global is left pointing INTO THE DELETED
# temp dir, so every later `DirectMemory()` in the process dies:
#
#   RuntimeError: Failed to initialize database at
#   C:\Users\clint\AppData\Local\Temp\tmp_1cocc4t\memory_store.db:
#   unable to open database file
#
# Measured 2026-09-22 on the aggregate suite (52 failed / 3,628 passed): four
# files leak this way (`test_cli`, `test_doctor`, `test_concurrency_200`,
# `test_perf*`) while the failures land in a DIFFERENT file
# (`test_cross_domain_integration::test_cd1_record_event`,
# `test_worm_smoke::test_every_worm_instantiable` -> ParallelInferenceWorm).
# Reproduced in 18s with:
#
#   pytest tests/test_cli.py tests/test_doctor.py \
#          tests/test_cross_domain_integration.py::test_cd1_record_event
#
# Every one of those files passes when run alone, which is why per-file suites
# and per-file CI never saw it. Restoring after EVERY test — autouse, not
# opt-in — is the same mechanism as the other isolation blocks in this file, for
# the same reason: "remember to patch this" is not a mechanism.
_MEMORY_DIR_BASELINE = os.environ.get("AZAZAEL_MEMORY_DIR", "")

#: (module, that module's own zero-arg resolver, the global it fills).
_MEMORY_DB_RESOLVERS = (
    ("CORE.direct_memory", "_get_db_path", "DB_PATH"),
    ("CORE.local_memory", "_get_store_path", "DB_PATH"),
    ("CORE.record_session", "_get_db_path", "DB_PATH"),
    ("CORE.MemoryBus", "_get_memorybus_db_path", "_MEMORYBUS_DB_PATH"),
    ("CORE.PamsRecorder", "_get_db_path", "DB_PATH"),
)

#: Modules that cache MORE than one value from the env dir. The third element
#: names every global that must be snapshotted and restored, because one
#: resolver cannot express it: `CORE/backup.py:45 _reload_paths` recomputes
#: `_backup_base` and `BACKUP_DIR`, `CORE/cd3_exporter.py:79 _reload_paths`
#: recomputes `_DB_DIR` and `DB_PATH`.
_MEMORY_DB_RELOADERS = (
    ("CORE.backup", "_reload_paths", ("_backup_base", "BACKUP_DIR")),
    ("CORE.cd3_exporter", "_reload_paths", ("_DB_DIR", "DB_PATH", "TELEMETRY_DB_PATH")),
)

#: Modules that cache a path read from `AZAZAEL_MEMORY_DIR` at import time.
#: `tests/test_memory_dir_caching.py` rediscovers this set by AST and FAILS when
#: a module is missing here, so the list cannot silently rot.
MEMORY_DIR_CACHING_MODULES = tuple(
    [entry[0] for entry in _MEMORY_DB_RESOLVERS]
    + [entry[0] for entry in _MEMORY_DB_RELOADERS]
)


_MISSING = object()


def _snapshot_memory_globals():
    """Return [(module, attr, value)] for every cached path global present now."""
    snap = []
    for _mod_name, _resolver, _attr in _MEMORY_DB_RESOLVERS:
        _module = sys.modules.get(_mod_name)
        if _module is not None:
            snap.append((_module, _attr, getattr(_module, _attr, _MISSING)))
    for _mod_name, _reloader, _attrs in _MEMORY_DB_RELOADERS:
        _module = sys.modules.get(_mod_name)
        if _module is None:
            continue
        for _attr in _attrs:
            snap.append((_module, _attr, getattr(_module, _attr, _MISSING)))
    return snap


@pytest.fixture(autouse=True)
def _restore_memory_db_globals():
    r"""Snapshot the memory dir + derived DB-path globals, then restore them.

    Snapshot-and-restore, NOT recompute-from-env. The first version of this
    fixture recomputed each global from the env var after every test, which
    silently *overwrote a redirect the test module had deliberately made*:
    `tests/test_mutation.py:13` and `tests/test_dm_advanced.py:13` both point
    `direct_memory.DB_PATH` at their own temp file in `setup_module()`, so the
    recompute put it back on the SHARED session store after the very first test.
    Measured 2026-09-22, two symptoms, one cause:

      * `test_mutation.py::teardown_module` then saw `DB_PATH.exists()` True and
        called `.unlink()` on the live session store -- `PermissionError:
        [WinError 32] ... azazael_memory_fd_dtgau\memory_store.db` reported as an
        ERROR at teardown of `TestMutationDetection.test_mapping_summary_structure`,
        and only the open handle prevented the run from deleting its own store.
      * `test_dm_advanced.py::test_replay_session` then built its `DirectMemory`
        against a store full of other tests' rows, so `_find_recent_session()`
        (the Phase-2 reconnect merge, `direct_memory.py:537`) adopted an existing
        session id and no `session_start` event was recorded for it.

    Restoring the value that was in effect at test start repairs a leak (the
    Error #196 shape: a test leaves `DB_PATH` pointing into a directory it then
    deletes) *and* preserves a deliberate redirect. Modules imported DURING the
    test have no snapshot, so for those the env-derived recompute is still the
    right answer -- that is the only case the old behaviour was ever correct for.
    """
    _snap = _snapshot_memory_globals()
    _known = {(id(_m), _a) for _m, _a, _v in _snap}
    yield
    if _MEMORY_DIR_BASELINE:
        os.environ["AZAZAEL_MEMORY_DIR"] = _MEMORY_DIR_BASELINE
    else:
        os.environ.pop("AZAZAEL_MEMORY_DIR", None)
    for _module, _attr, _value in _snap:
        if _value is not _MISSING:
            setattr(_module, _attr, _value)
    # Late imports: never had a deliberate redirect to preserve, so recompute.
    for _mod_name, _resolver, _attr in _MEMORY_DB_RESOLVERS:
        _module = sys.modules.get(_mod_name)
        if _module is None or (id(_module), _attr) in _known:
            continue
        _fn = getattr(_module, _resolver, None)
        if _fn is not None:
            setattr(_module, _attr, _fn())
    for _mod_name, _reloader, _attrs in _MEMORY_DB_RELOADERS:
        _module = sys.modules.get(_mod_name)
        if _module is None or all((id(_module), _a) in _known for _a in _attrs):
            continue
        _fn = getattr(_module, _reloader, None)
        if _fn is not None:
            _fn()


def pytest_unconfigure(config):
    """Flush telemetry, then remove all session-scoped redirected stores."""
    import shutil

    # OpenTelemetry's provider is process-global and its BatchSpanProcessor can
    # flush during interpreter shutdown.  That is AFTER this hook deletes the
    # redirected memory directory, so the exporter raises
    # ``sqlite3.OperationalError: unable to open database file`` even when every
    # test passed.  Flush while the isolated store still exists, then release the
    # cached exporter.  This is teardown ordering, not an exporter retry: a
    # production exporter must not silently recreate a store an operator removed.
    try:
        from opentelemetry import trace as _otel_trace
        _provider = _otel_trace.get_tracer_provider()
        _shutdown = getattr(_provider, "shutdown", None)
        if callable(_shutdown):
            _shutdown()
    except Exception:
        pass
    try:
        from CORE import cd3_exporter as _cd3_exporter
        _cd3_exporter._default_exporter = None
    except Exception:
        pass

    if _GENERATED_DIR_OVERRIDE:
        shutil.rmtree(_GENERATED_DIR_OVERRIDE, ignore_errors=True)
    if _ENTRENCHMENT_OVERRIDE:
        shutil.rmtree(str(_ENTRENCHMENT_OVERRIDE.parent), ignore_errors=True)
    if _MEMORY_DIR_OVERRIDE:
        shutil.rmtree(_MEMORY_DIR_OVERRIDE, ignore_errors=True)


# ── Spine state isolation (audit 2026-09-15) ────────────────────────
# `spine_registry._STATE_FILE` defaults to the DEVELOPER'S live store at
# `~/.azazael/spine_state.json`. Isolation was opt-in per test file, so any
# test that recorded an outcome and then called `save_spine_state()` wrote the
# real store. Verified for real: running `tests/test_spine_mesh.py` against a
# live 76-record homeostat replaced it with synthetic fixtures (`mesh:W0`,
# `learning:W1`) — hours of accumulated learning, gone, with no error.
#
# The redirect is global rather than opt-in for the same reason the
# containment pen above is: a test must not be able to damage the machine it
# is testing, and "remember to patch this" is not a mechanism.
#
# Note this patches the module attributes, so a test that opens the real path
# by string (e.g. `test_aoa_routing_telemetry`'s read-only provenance check)
# still reads the real file, as intended.
@pytest.fixture(autouse=True)
def _isolate_spine_state(tmp_path_factory, monkeypatch):
    """Redirect the spine's durable store to a per-test temp dir."""
    try:
        from CORE import spine_registry as _sr
    except Exception:  # spine not importable in this environment
        yield
        return
    base = tmp_path_factory.mktemp("spine_state")
    monkeypatch.setattr(_sr, "_STATE_FILE", base / "spine_state.json")
    monkeypatch.setattr(_sr, "_GOSSIP_DIR", base / "gossip")
    yield


@pytest.fixture(autouse=True)
def _quiesce_spine_heart():
    """Stop any spine heartbeat a previous test left running.

    The same shape as the HLC singleton below: process-wide state that leaks
    across tests and makes the failure order-dependent. `startup_warm.py`
    starts a daemon heart thread from `warm_caches_now()` (line 268), so any
    test that warms caches synchronously leaves a thread beating on its own
    interval for the rest of the session.

    A beat holds the process-global cycle gate (`_GLOBAL_CYCLE_GATE` in
    `CORE/WORMS/expansion/spine.py`) for its whole duration. The next
    same-process caller is therefore refused with
    `skipped: cycle_already_running_global` -- and that is *earlier* in
    `run_cycle()` than the min-interval throttle, so it returns without setting
    `last_run_at`. Measured: `test_spine_mesh.py::
    test_spine_cycle_throttle_and_reentry_guard` passes alone (63 passed) but
    failed at `second.get("skipped") == "min_interval"` when the file ran after
    `test_startup_warm_wiring.py`. The first cycle was refused by a heartbeat
    instead of running, so nothing was throttled and the assertion saw None.

    Teardown-only: the leak is created *by* a test, so it is cleaned after it.
    `stop_spine_heart()` already waits for an in-flight beat (its own
    docstring records why), so this cannot race the thread it is stopping, and
    it returns immediately when no heart is alive -- which is the case for the
    ~2000 tests that never start one, so the cost is zero.
    """
    yield
    try:
        from CORE import startup_warm as _sw
    except Exception:  # startup_warm not importable in this environment
        return
    try:
        if _sw._SPINE_HEART_ALIVE.is_set():
            _sw.stop_spine_heart(timeout=30.0)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_hlc_clock(monkeypatch):
    """Give every test a pristine hybrid logical clock.

    `CORE/hlc.py` is a process-wide singleton, and it *adopts* a peer's physical
    time whenever that peer is within `MAX_DRIFT_MS` — which is correct HLC
    behaviour, not a bug. The consequence for a test session is that the clock
    advances and then *stays* advanced: once a test observes a stamp ~200s ahead
    (the clock-trust tests do exactly that, on purpose), the singleton's
    `_physical` sits ~200s ahead of wall clock, so a later test that publishes an
    incumbent and then offers a stamp "2 seconds in the future" is offering one
    that is genuinely *older* than the incumbent. It then loses the merge, and
    the failure is order-dependent:

        test_mesh_fabric_gaps.py::test_fabric_state_merges_by_recency  ->  passes
          alone, fails when the clock-trust tests run first in the session.

    `reset_remote_view()` cannot fix this: it deliberately clears only the
    observability fields, because un-monotoning the logical clock is the exact
    failure mode the module exists to prevent. Installing a fresh instance is the
    honest isolation — the assertion holds on a clock that has seen nothing.

    Global rather than opt-in for the same reason as the fixtures above.
    """
    try:
        from CORE import hlc as _H
    except Exception:  # hlc not importable in this environment
        yield
        return
    monkeypatch.setattr(_H, "_HLC", _H.HybridLogicalClock(), raising=False)
    yield


# ── Sentinel write-guard isolation (audit 2026-09-16) ───────────────
# `CORE/sentinel_enforcer.py` installs a *process-global* `sys.addaudithook` at
# import time that raises PermissionError on any write whose mode contains
# w/a/+/x unless a thread-local token is set. `CORE/compliance/orchestrator.py`
# imports it in order to arm the hook, so the first suite that builds a
# ComplianceOrchestrator arms the guard for the remainder of the session and
# every later test that writes a temp file dies. Verified for real:
#
#   pytest tests/test_compliance_step45_init.py tests/test_compliance_auditor.py
#     -> test_halt_flag_file: PermissionError: CRITICAL: Unauthorized write
#
# The same failure reproduces against the pre-change copy of that suite, so it
# is an isolation defect rather than a regression. The guard exists to gate the
# assistant's tool writes; a test writing a temp file is not what it guards.
# Nothing under tests/ asserts that the hook blocks, so holding the token open
# cannot mask a real check. The previous value is restored afterwards, so a test
# that wants to assert blocking can switch it off and still see the guard.
#
# Deliberately lazy: it only engages once the hook has actually been armed, so a
# suite that never touches compliance code keeps running exactly as before.
class _SharedWriteToken:
    """Process-wide stand-in for `sentinel_enforcer._GATED_CALL`.

    The real token is `threading.local()` (`sentinel_enforcer.py:40`) and the
    audit hook reads it per thread (`:53`). This one is deliberately SHARED, so a
    permit granted on the main thread also covers threads the code under test
    starts. A test that wants to observe blocking still can: it sets
    `_GATED_CALL.is_active = False` on this same object.
    """

    is_active = True


#: `AZAZAEL_TEST_SHARED_PERMIT=0` keeps the REAL `threading.local()` token, so
#: the production permit-carrying is what makes worker writes pass.
_SHARED_PERMIT_ENABLED = os.environ.get(
    "AZAZAEL_TEST_SHARED_PERMIT", "1").strip().lower() not in ("0", "false", "no")


def _permit_sentinel_writes() -> bool:
    """Latch the sentinel write token open if the guard is armed.

    Returns True when the guard was found (so the caller can distinguish
    "permitted" from "not armed yet"). The latch is deliberately not restored:
    see `pytest_runtest_setup` below.
    """
    module = sys.modules.get("CORE.sentinel_enforcer")
    token = getattr(module, "_GATED_CALL", None) if module is not None else None
    if token is None:
        return False
    token.is_active = True
    # The latch above only covers the THREAD that ran it. Any worker thread the
    # code under test starts arrives with `is_active` unset and is blocked.
    # Measured 2026-09-22: `fs_probe.bounded_subprocess_output` runs its child on
    # a `bounded-subprocess` thread and opens `tempfile.TemporaryFile()` there:
    #
    #   {"ok": false, "out": "", "timeout": false,
    #    "error": "CRITICAL: Unauthorized write to
    #              C:\\Users\\clint\\AppData\\Local\\Temp. All edits MUST route..."}
    #
    # so every delegated probe answered `_UNANSWERED` and returned None. That is
    # the whole of `test_fs_probe_backend`'s 10 aggregate failures, reproduced in
    # 4 seconds with `pytest tests/test_compliance_step45_init.py
    # tests/test_fs_probe_backend.py` (10 failed / 28 passed) while every one of
    # those tests passes alone. Swapping the thread-local for a shared token makes
    # the session permit hold for every thread. It is a test-environment change;
    # the guard's own semantics are untouched, and the production consequence of
    # the thread-locality is recorded separately (Error #197).
    # Belt-and-braces, and now provably redundant for the probe path:
    # `fs_probe._carried_permit` carries the starter's permit onto the worker
    # (production fix, guarded by `tests/test_sentinel_thread_permit.py`), so the
    # delegated probes answer without this swap. Set
    # `AZAZAEL_TEST_SHARED_PERMIT=0` to run the suite against the real
    # thread-local token and prove it. The swap stays ON by default until a full
    # run with it off is green, because other legacy suites write from worker
    # threads through paths this fix does not cover.
    if _SHARED_PERMIT_ENABLED and isinstance(token, threading.local):
        module._GATED_CALL = _SharedWriteToken()
    return True


# ── Sentinel write-guard: armed BEFORE any fixture of any scope ─────
# The block above fixes the guard leaking into per-test temp writes, but its
# fixture is FUNCTION-scoped, and pytest sets up module/class-scoped fixtures
# FIRST. So a module-scoped fixture that imports a script which opens its log
# file at import time (e.g. `scripts/evolution_cycle.py:48`) is blocked before
# the function-scoped permit ever runs. Measured 2026-09-22 on a full-suite run:
# 207 `PermissionError: CRITICAL: Unauthorized write` — 69 collection errors in
# `test_evolution_gates.py` (61) and `test_import_map.py` (8) and a share of the
# 72 failures, while every one of those files passed when run alone.
#
# `pytest_runtest_setup` is called before fixture setup of ANY scope, which is
# the earliest hook a conftest gets, and the latch is left ON for the session:
# the guard exists to gate the assistant's tool writes, not a test process's
# temp/log files, and nothing under `tests/` asserts that it blocks. A test that
# wants to observe blocking can still set `_GATED_CALL.is_active = False` itself.
def pytest_runtest_setup(item):  # noqa: ARG001 - pytest hook signature
    _permit_sentinel_writes()


def pytest_collectstart(collector):  # noqa: ARG001 - pytest hook signature
    """Permit the write guard during COLLECTION too, not only test setup.

    `pytest_runtest_setup` is too late for collection: pytest imports every
    test module up front, before a single test runs. A module imported during
    collection that arms the sentinel hook (any compliance import -- the hook
    is armed at `CORE/sentinel_enforcer.py` import) then blocks the
    import-time writes of every module collected AFTER it. Measured
    2026-10-02: `tests/test_monitor_artifact_paths.py` collected cleanly in the
    full suite but errored with `PermissionError: CRITICAL: Unauthorized write`
    whenever a compliance-importing module preceded it, because collection
    order differs once the suite is split. Nothing under `tests/` asserts that
    the hook blocks, so latching it here cannot mask a real check; a test that
    wants to observe blocking still sets `_GATED_CALL.is_active = False`.
    """
    _permit_sentinel_writes()


@pytest.fixture(autouse=True)
def _permit_sentinel_test_writes():
    """Hold the sentinel write token open for the duration of each test."""
    _permit_sentinel_writes()
    yield

# ── Standalone-script runner (audit 2026-09-21) ─────────────────────
# Seven files under `tests/` are named `test_*.py` and are STANDALONE SCRIPTS:
# every assertion in them sits under `if __name__ == "__main__"`, with `PASS`/
# `FAIL` counters and `sys.exit(1)` on failure. pytest therefore collected ZERO
# tests from them and reported `rc=5`, "no tests collected" -- measured
# 2026-09-21, `test_algorithm_coverage`, `test_cloud_tab_error_handling`,
# `test_full_integration`, `test_observability`, `test_provider_failover`,
# `test_secrets_manager_thread_safety` and `test_calc`.
#
# The consequence is worth stating plainly: **none of those checks has ever run
# in CI**, and the files look green because they fail to collect rather than
# because they pass. Rewriting each script body into test functions would be six
# separate refactors of working code; running the script the way its author
# intended, and asserting its exit code, makes the existing assertions execute
# today and does not touch the logic.
@pytest.fixture
def run_standalone_script():
    """Run a `tests/test_*.py` script and return its CompletedProcess."""
    import subprocess

    def _run(path: str, timeout: float = 300.0):
        return subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=str(pathlib.Path(__file__).parent.parent),
        )

    return _run


# ── Single-file tool loader (2026-11-03) ──────────────────────────────
# Three test files re-implement the same importlib dance to load a one-file
# production tool BY PATH (test_sword_2600.py, test_flaming_sword_reaper.py,
# tests/test_pmb_floor_apply.py): spec_from_file_location -> module_from_spec
# -> sys.modules[name] -> exec_module. One loadable copy of that dance lives
# here, session-cached, because two files loading the same path under the same
# registered name should not exec it twice per session. The reaper and floor
# tests consume it now; the next declared-root tool (sword_2600 included when
# it is next touched) joins by replacing its local `load_tool()`.
@pytest.fixture(scope="session")
def load_tool_by_path():
    """Load a single-file production tool by path; cached per (name, path)."""
    import importlib.util

    _cache: dict = {}

    def _load(path, name: str):
        path = pathlib.Path(path)
        assert path.exists(), "tool not found: %s" % path
        key = (name, str(path))
        if key not in _cache:
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec and spec.loader, path
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            _cache[key] = module
        return _cache[key]

    return _load


# ── Detect free-threading (3.15t production build) ──────────────────
try:
    _IS_FREE_THREADED = not sys._is_gil_enabled()
except Exception:
    _IS_FREE_THREADED = "t" in sys.version.lower()


def is_314t() -> bool:
    """Return True if running under a free-threading build.

    Name kept for its existing callers; the check was always version-agnostic
    (``sys._is_gil_enabled()``), so it is correct on 3.15t. Prefer
    ``is_free_threaded`` in new code.
    """
    return _IS_FREE_THREADED


# ── CD4: nuitka_incompatible marker registration ────────────────────
def pytest_configure(config):
    """Register custom pytest markers.

    Registers nuitka_incompatible marker per CD4 spec for tests that
    cannot run under Nuitka compiled (.exe or --module) mode.
    Stage 3 of the CD4 pipeline automatically skips these tests via:
        pytest -m "not nuitka_incompatible"
    """
    try:
        from CORE.cd4_pipeline import register_nuitka_marker
        register_nuitka_marker(config)
    except ImportError:
        # Fallback registration if cd4_pipeline module not importable
        config.addinivalue_line(
            "markers",
            "nuitka_incompatible: Mark test as incompatible with Nuitka "
            "compiled execution. Skipped in CD4 pipeline Stage 3.",
        )
