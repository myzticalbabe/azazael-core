# coding: utf-8
"""EV-8 bounded-probe guard for PerformanceMonitor.

Regression this pins (measured 2026-09-28)
------------------------------------------
The full suite's chunk 4 timed out at its 600 s budget. Root-caused with a
faulthandler stack dump, twice, to two independent unbounded waits inside
`PerformanceMonitor`, reached from the hot path
`SessionMemory.start_session` / `end_session` -> `capture()`:

1. `capture_watched_processes` asked for `open_files` through
   `psutil.process_iter(attrs=[...])`. On Windows answering `open_files`
   makes psutil build the ENTIRE system handle table, once PER PROCESS, so
   the loop cost O(processes x handles): **10.5 s for 277 processes on an
   idle box**, >40 s when the suite ran other workers, even though only ~13
   of those processes are ever watched.

2. `capture_service_health` probed its four services serially with
   `timeout=5`, and a single float does NOT bound `socket.getaddrinfo`, so a
   slow resolver blocked the caller indefinitely (a dump caught it inside
   `urllib3 ... create_connection`). Even healthy, three dead localhost ports
   cost ~1.8 s EACH, serial: 5.3 s per call, on every session start.

Fixes pinned here: filter on the cheap attributes BEFORE asking for the
expensive one; give every probe a hard thread-enforced deadline; probe the
services concurrently so the set is bounded by one ceiling, not N.
"""

import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = str(_PROJECT_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

pm_module = pytest.importorskip("performance_monitor")
PerformanceMonitor = pm_module.PerformanceMonitor


# ── fakes ──────────────────────────────────────────────────────────────

class _FakeMemInfo:
    rss = 12 * 1024 * 1024
    vms = 40 * 1024 * 1024


class _FakeProcess:
    """Stands in for `psutil.Process`, recording how it was used."""

    def __init__(self, pid):
        self.pid = pid
        self.calls = []

    @contextmanager
    def oneshot(self):
        yield

    def memory_info(self):
        self.calls.append("memory_info")
        return _FakeMemInfo()

    def cpu_percent(self):
        self.calls.append("cpu_percent")
        return 3.5

    def num_threads(self):
        self.calls.append("num_threads")
        return 7

    def create_time(self):
        self.calls.append("create_time")
        return time.time() - 60

    def open_files(self):
        self.calls.append("open_files")
        return ["a", "b", "c"]


class _FakeIterProc:
    def __init__(self, info):
        self.info = info


def _install_fake_psutil(monkeypatch, names):
    """Fake `process_iter` / `Process`, capturing the attrs requested."""
    seen = {"attrs": None}
    children = {}

    def fake_process_iter(attrs=None, *a, **kw):
        seen["attrs"] = attrs
        out = []
        for pid, name in names:
            children[pid] = _FakeProcess(pid)
            out.append(_FakeIterProc({"pid": pid, "name": name}))
        return out

    def fake_process(pid):
        return children[pid]

    monkeypatch.setattr(pm_module.psutil, "process_iter", fake_process_iter)
    monkeypatch.setattr(pm_module.psutil, "Process", fake_process)
    return seen, children


# ── process scan is filtered before it is made expensive ───────────────

class TestProcessScanIsFilteredFirst:
    def test_pass_one_never_requests_open_files(self, monkeypatch):
        seen, _ = _install_fake_psutil(
            monkeypatch, [(101, "python.exe"), (102, "explorer.exe")])
        pm = PerformanceMonitor()
        pm.capture_watched_processes(include_open_files=False)
        assert seen["attrs"] == ["pid", "name"], (
            "open_files must not ride along on the scan of every process: "
            "on Windows that is O(processes x handles)")
        assert "open_files" not in (seen["attrs"] or [])

    def test_expiring_attribute_read_only_for_watched_processes(self,
                                                               monkeypatch):
        seen, children = _install_fake_psutil(
            monkeypatch, [(101, "python.exe"), (102, "explorer.exe")])
        pm = PerformanceMonitor()
        snaps = pm.capture_watched_processes(include_open_files=True)
        assert [s.pid for s in snaps] == [101]
        assert "open_files" in children[101].calls
        assert "open_files" not in children[102].calls, (
            "an unwatched process must never pay for the handle-table walk")
        assert snaps[0].open_files == 3

    def test_open_files_can_be_switched_off_entirely(self, monkeypatch):
        _, children = _install_fake_psutil(monkeypatch, [(101, "python.exe")])
        pm = PerformanceMonitor()
        snaps = pm.capture_watched_processes(include_open_files=False)
        assert snaps[0].open_files == 0
        assert "open_files" not in children[101].calls


# ── service probes are deadline-bounded and concurrent ─────────────────

class _HangingGet:
    """A `_requests.get` that blocks until released, ignoring its timeout."""

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Semaphore(0)
        self.live = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, url, timeout=None):
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        self.entered.release()
        try:
            self.release.wait(60)
            raise RuntimeError("released")
        finally:
            with self._lock:
                self.live -= 1


def _hang_free(hanging):
    hanging.release.set()


class TestServiceProbesAreBounded:
    def test_a_wedged_probe_cannot_block_the_caller(self, monkeypatch):
        hanging = _HangingGet()
        monkeypatch.setattr(pm_module, "_requests", type(
            "R", (), {"get": staticmethod(hanging)}))
        pm = PerformanceMonitor(services=[("dead", "http://localhost:1/")])
        pm.SERVICE_PROBE_DEADLINE_S = 0.3
        try:
            start = time.perf_counter()
            svcs = pm.capture_service_health()
            elapsed = time.perf_counter() - start
        finally:
            _hang_free(hanging)

        assert elapsed < 3.0, (
            "a wedged probe must not block the caller; took %.1fs" % elapsed)
        assert len(svcs) == 1
        assert svcs[0].reachable is False
        assert "deadline" in svcs[0].error

    def test_services_are_probed_concurrently(self, monkeypatch):
        hanging = _HangingGet()
        monkeypatch.setattr(pm_module, "_requests", type(
            "R", (), {"get": staticmethod(hanging)}))
        pm = PerformanceMonitor(services=[("s%d" % i, "http://localhost:1/")
                                          for i in range(4)])
        pm.SERVICE_PROBE_DEADLINE_S = 0.4
        try:
            start = time.perf_counter()
            svcs = pm.capture_service_health()
            elapsed = time.perf_counter() - start
        finally:
            _hang_free(hanging)

        assert len(svcs) == 4
        assert all(s.reachable is False for s in svcs)
        assert hanging.peak >= 2, (
            "probes must overlap; peak concurrency was %d" % hanging.peak)
        # Serial probing would need >= 4 x 0.4 s.
        assert elapsed < 1.2, (
            "4 concurrent 0.4s probes took %.2fs; they look serial" % elapsed)

    def test_results_keep_declaration_order(self, monkeypatch):
        hanging = _HangingGet()
        monkeypatch.setattr(pm_module, "_requests", type(
            "R", (), {"get": staticmethod(hanging)}))
        names = ["alpha", "beta", "gamma", "delta"]
        pm = PerformanceMonitor(services=[(n, "http://localhost:1/")
                                          for n in names])
        pm.SERVICE_PROBE_DEADLINE_S = 0.2
        try:
            svcs = pm.capture_service_health()
        finally:
            _hang_free(hanging)
        assert [s.name for s in svcs] == names, (
            "concurrency must not reorder the report")

    def test_empty_service_list_is_cheap_and_safe(self):
        # NB: `__init__` treats an empty `services` as "unset" and falls back
        # to DEFAULT_SERVICES, so the empty case has to be set directly.
        pm = PerformanceMonitor()
        pm.services = []
        assert pm.capture_service_health() == []

    def test_explicit_empty_list_falls_back_to_defaults(self):
        """Pins the existing `services or DEFAULT_SERVICES` behaviour."""
        pm = PerformanceMonitor(services=[])
        assert pm.services == PerformanceMonitor.DEFAULT_SERVICES
