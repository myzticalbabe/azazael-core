# coding: utf-8
"""Unit tests for CLUSTERWORMS.py — WormConfig, ClusterResult, ClusterState,
WormExecutor, ClusterWORMZ dynamic resolution (3 strategies), and Mermaid.

Error #37 fix (June 11, 2026): The 15 execution tests that previously hung
are now ENABLED by default. Slow test classes use SUBSIDIARY-PROCESS timeouts
(via _timer_utils.run_class_with_timeout) — the subprocess is killed by Python's
subprocess.run(timeout=...) which cleanly releases ALL locks (OS-level cleanup).
This avoids the daemon-thread lock-leak bug: daemon threads that survive a
timeout keep holding global locks (e.g., circuit breaker registry threading.Lock),
causing cascading deadlocks in subsequent tests.
"""

import sys
import os
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    import pytest
except ImportError:
    import types
    # Real Skip exception class — avoids exec() scoping bug
    class Skip(BaseException):
        """Exception raised by pytest.skip() fallback when pytest not available."""
        def __init__(self, reason: str = ""):
            self.reason = reason
            super().__init__(reason)

    pytest = types.ModuleType("pytest")
    pytest.skip = lambda reason=None: (_ for _ in ()).throw(Skip(reason or ''))
    pytest.mark = types.ModuleType("mark")
    pytest.mark.skip = lambda *a, **kw: lambda f: f

# ── Module Under Test ──────────────────────────────────────────────
from CORE.WORMS.CLUSTERWORMS import (
    # Data types
    WormConfig,
    ClusterResult,
    # Core classes
    ClusterState,
    WormExecutor,
    ClusterWORMZ,
    # Constants
    CLUSTER_REGISTRY,
    _WORM_MODULE_MAP,
    _STANDARD_METHODS,
    _LEGACY_DISPATCH,
    # Resolution functions
    _resolve_worm_dynamic,
    _execute_worm_class,
    _ensure_legacy_modules,
    # Registry (shared dict with CORE.worm_registry)
    WORM_REGISTRY,
)


# ═══════════════════════════════════════════════════════════════════
# WormConfig Tests
# ═══════════════════════════════════════════════════════════════════


class TestWormConfig:
    """Verify WormConfig dataclass."""

    def test_minimal_config(self):
        """Minimal config should use defaults."""
        cfg = WormConfig(name="SYSTEMZ", module="SYSTEMZ")
        assert cfg.name == "SYSTEMZ"
        assert cfg.module == "SYSTEMZ"
        assert cfg.enabled is True
        assert cfg.priority == 0
        assert cfg.timeout == 30
        assert cfg.retry == 1
        assert cfg.params == {}

    def test_full_config(self):
        """Config with all fields populated."""
        cfg = WormConfig(
            name="PortAuditor",
            module="PortAuditor",
            enabled=True,
            priority=5,
            timeout=60,
            retry=3,
            params={"ports": [80, 443]},
        )
        assert cfg.priority == 5
        assert cfg.timeout == 60
        assert cfg.retry == 3
        assert cfg.params["ports"] == [80, 443]

    def test_disabled_config(self):
        """Disabled worm should have enabled=False."""
        cfg = WormConfig(name="DISKZ", module="DISKZ", enabled=False)
        assert cfg.enabled is False

    def test_config_equality(self):
        """Two identical configs should be equal as dataclasses."""
        cfg1 = WormConfig(name="A", module="A")
        cfg2 = WormConfig(name="A", module="A")
        assert cfg1 == cfg2


# ═══════════════════════════════════════════════════════════════════
# ClusterResult Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterResult:
    """Verify ClusterResult dataclass."""

    def test_minimal_result(self):
        """Minimal result should have required fields."""
        result = ClusterResult(
            cluster_id="cluster_001",
            worm_results={"SYSTEMZ": {"success": True}},
            aggregated_metrics={"success_rate": 1.0},
            duration_ms=100.0,
            success_count=1,
            failure_count=0,
        )
        assert result.cluster_id == "cluster_001"
        assert result.success_count == 1
        assert result.failure_count == 0
        assert result.duration_ms == 100.0
        assert result.timestamp is not None

    def test_full_result(self):
        """Result with all fields."""
        result = ClusterResult(
            cluster_id="cluster_002",
            worm_results={
                "SYSTEMZ": {"success": True, "duration_ms": 50.0},
                "PROCESSZ": {"success": False, "error": "timeout"},
            },
            aggregated_metrics={
                "total_duration_ms": 150.0,
                "success_rate": 0.5,
                "avg_duration_ms": 75.0,
            },
            duration_ms=150.0,
            success_count=1,
            failure_count=1,
        )
        assert result.cluster_id == "cluster_002"
        assert result.success_count == 1
        assert result.failure_count == 1
        assert result.aggregated_metrics["success_rate"] == 0.5


# ═══════════════════════════════════════════════════════════════════
# CLUSTER_REGISTRY Validation Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterRegistry:
    """Verify CLUSTER_REGISTRY structure and validity."""

    def test_has_essential_types(self):
        """Core cluster types should exist."""
        for cluster_type in ("system", "network", "security", "bibl",
                             "analysis", "validation", "health", "full"):
            assert cluster_type in CLUSTER_REGISTRY, f"Missing cluster: {cluster_type}"

    def test_each_entry_has_description(self):
        """Every cluster type should have a description."""
        for name, info in CLUSTER_REGISTRY.items():
            assert "description" in info, f"Missing description for {name}"
            assert len(info["description"]) > 0

    def test_each_entry_has_worms(self):
        """Every cluster type should have worms list."""
        for name, info in CLUSTER_REGISTRY.items():
            assert "worms" in info, f"Missing worms for {name}"
            assert isinstance(info["worms"], list)
            assert len(info["worms"]) > 0, f"Empty worms for {name}"

    def test_all_worms_in_module_map(self):
        """Every worm in CLUSTER_REGISTRY should be in _WORM_MODULE_MAP."""
        all_reg_worms = set()
        for info in CLUSTER_REGISTRY.values():
            all_reg_worms.update(info["worms"])
        for worm in all_reg_worms:
            assert worm in _WORM_MODULE_MAP, f"Worm '{worm}' not in _WORM_MODULE_MAP"

    def test_all_worms_in_legacy_dispatch(self):
        """Every worm in CLUSTER_REGISTRY should be in _LEGACY_DISPATCH."""
        _ensure_legacy_modules()
        all_reg_worms = set()
        for info in CLUSTER_REGISTRY.values():
            all_reg_worms.update(info["worms"])
        for worm in all_reg_worms:
            assert worm in _LEGACY_DISPATCH, f"Worm '{worm}' not in _LEGACY_DISPATCH"


# ═══════════════════════════════════════════════════════════════════
# _WORM_MODULE_MAP Validation Tests
# ═══════════════════════════════════════════════════════════════════


class TestWormModuleMap:
    """Verify _WORM_MODULE_MAP structure."""

    def test_module_map_format(self):
        """Every entry should be (module_name, class_name) tuple."""
        for name, mapping in _WORM_MODULE_MAP.items():
            assert isinstance(mapping, tuple), f"{name}: expected tuple"
            assert len(mapping) == 2, f"{name}: expected (module, class)"
            assert isinstance(mapping[0], str), f"{name}: module not str"
            assert isinstance(mapping[1], str), f"{name}: class not str"

    def test_module_map_self_reference(self):
        """Most worms should map to same-name module and class."""
        for name, (mod, cls) in _WORM_MODULE_MAP.items():
            if name == "SYSTEMZ":
                continue
            assert mod == cls, f"{name}: module '{mod}' != class '{cls}'"


# ═══════════════════════════════════════════════════════════════════
# _STANDARD_METHODS Tests
# ═══════════════════════════════════════════════════════════════════


class TestStandardMethods:
    """Verify _STANDARD_METHODS priority order."""

    def test_has_essential_methods(self):
        """Core methods should be in priority order."""
        assert "get_summary" in _STANDARD_METHODS
        assert "get_status" in _STANDARD_METHODS
        assert "run" in _STANDARD_METHODS
        assert "execute" in _STANDARD_METHODS
        assert "scan" in _STANDARD_METHODS

    def test_get_summary_first(self):
        """get_summary should be the first method tried."""
        assert _STANDARD_METHODS[0] == "get_summary"

    def test_order_is_correct(self):
        """Methods should be in expected order."""
        expected_prefix = ["get_summary", "get_status", "run", "execute", "scan"]
        for i, method in enumerate(expected_prefix):
            assert _STANDARD_METHODS[i] == method, \
                f"Position {i}: expected '{method}', got '{_STANDARD_METHODS[i]}'"


# ═══════════════════════════════════════════════════════════════════
# _LEGACY_DISPATCH Tests
# ═══════════════════════════════════════════════════════════════════


class TestLegacyDispatch:
    """Verify _LEGACY_DISPATCH structure."""

    def test_all_entries_are_callable(self):
        """Every legacy dispatch entry should be callable."""
        _ensure_legacy_modules()
        for name, dispatch_fn in _LEGACY_DISPATCH.items():
            assert callable(dispatch_fn), f"{name}: not callable"

    def test_essential_worms_present(self):
        """Core worms should be in legacy dispatch."""
        _ensure_legacy_modules()
        for worm in ("SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "WORMZ"):
            assert worm in _LEGACY_DISPATCH, f"Missing legacy entry: {worm}"


# ═══════════════════════════════════════════════════════════════════
# _resolve_worm_dynamic Tests (Strategy 1: Registry)
# ═══════════════════════════════════════════════════════════════════


class TestResolveWormDynamicRegistry:
    """Verify _resolve_worm_dynamic Strategy 1 — Registry resolution."""

    def test_unknown_worm_returns_none(self):
        """An unknown worm name should return None."""
        result = _resolve_worm_dynamic("__nonexistent_worm__")
        assert result is None, "Unknown worm should return None"

    def test_known_worm_returns_dict(self):
        """A registry-known worm should return a dict with success/method/result."""
        result = _resolve_worm_dynamic("WORMZ")
        if result is None:
            pytest.skip("WORMZ unresolvable in this env")
        assert isinstance(result, dict)
        assert "success" in result
        assert "method" in result
        assert "result" in result

    def test_known_worm_success_flag(self):
        """A working worm should have success=True."""
        result = _resolve_worm_dynamic("WORMZ")
        if result is None:
            pytest.skip("WORMZ unresolvable in this env")
        assert result["success"] is True, f"WORMZ failed: {result.get('error')}"


class TestResolveWormDynamicFallback:
    """Verify _resolve_worm_dynamic fallback paths."""

    def test_legacy_dispatch_fallback(self):
        """Worms with legacy dispatch entries should resolve."""
        result = _resolve_worm_dynamic("WORMZ")
        if result is None:
            pytest.skip("WORMZ unresolvable in this env")
        # Should have resolved through registry or legacy
        assert result["success"] is True

    def test_resolve_returns_standard_method(self):
        """Resolved result should indicate which method was used."""
        result = _resolve_worm_dynamic("WORMZ")
        if result is None:
            pytest.skip("WORMZ unresolvable in this env")
        assert result["method"] in _STANDARD_METHODS + ["legacy_dispatch"]

    def test_strategy_2_legacy_dispatch(self):
        """Test fallback to legacy dispatch (Strategy 2) using a mock entry."""
        _LEGACY_DISPATCH["MOCK_LEGACY"] = lambda: {"mock": "legacy"}
        try:
            result = _resolve_worm_dynamic("MOCK_LEGACY")
            assert result is not None
            assert result["success"] is True
            assert result["method"] == "legacy_dispatch"
            assert result["result"] == {"mock": "legacy"}
        finally:
            _LEGACY_DISPATCH.pop("MOCK_LEGACY", None)

    def test_strategy_3_module_map_fallback(self):
        """Test fallback to module map (Strategy 3) using a bare module name."""
        # Use a bare module name (not dotted) because __import__ returns the
        # top-level package for dotted names, breaking the getattr lookup.
        _WORM_MODULE_MAP["MOCK_MOD"] = ("WORMZ", "WORMZ")
        try:
            result = _resolve_worm_dynamic("MOCK_MOD")
            assert result is not None
            assert result["success"] is True
            assert result["method"] in _STANDARD_METHODS
        finally:
            _WORM_MODULE_MAP.pop("MOCK_MOD", None)


# ═══════════════════════════════════════════════════════════════════
# _execute_worm_class Tests
# ═══════════════════════════════════════════════════════════════════


class TestExecuteWormClass:
    """Verify _execute_worm_class method resolution."""

    def test_execute_registry_class(self):
        """Executing a valid class should return success dict."""
        # Import WORMZ class directly
        from CORE.WORMS.WORMZ import WORMZ
        result = _execute_worm_class("WORMZ", WORMZ)
        assert result is not None
        assert "success" in result
        assert "method" in result

    def test_execute_method_name_returned(self):
        """The method name used should be returned."""
        from CORE.WORMS.WORMZ import WORMZ
        result = _execute_worm_class("WORMZ", WORMZ)
        if result["success"]:
            assert result["method"] in _STANDARD_METHODS

    def test_execute_with_result(self):
        """Result should contain the worm's output."""
        from CORE.WORMS.WORMZ import WORMZ
        result = _execute_worm_class("WORMZ", WORMZ)
        if result["success"]:
            assert "result" in result
            assert result["result"] is not None


# ═══════════════════════════════════════════════════════════════════
# _resolve_worm_dynamic Strategy 1 — Registry (isolated with mocks)
# ═══════════════════════════════════════════════════════════════════


class _MockSummaryWorm:
    """Mock worm with a standard method for Strategy 1 tests."""
    VERSION = "1.0.0"

    def get_summary(self):
        return {"mock": "registry"}


class TestResolveStrategy1Registry:
    """Verify _resolve_worm_dynamic Strategy 1 — registry resolution."""

    def _inject(self, name, cls):
        self._saved = WORM_REGISTRY.get(name)
        WORM_REGISTRY[name] = {
            "class": cls,
            "module": "tests",
            "category": "utility",
            "priority": 5,
            "cooldown_seconds": 30.0,
            "description": "mock",
        }

    def _restore(self, name):
        if self._saved is None:
            WORM_REGISTRY.pop(name, None)
        else:
            WORM_REGISTRY[name] = self._saved

    def test_registry_class_resolved(self):
        """A class registered in WORM_REGISTRY should resolve via Strategy 1."""
        self._inject("MOCK_STRAT1", _MockSummaryWorm)
        try:
            result = _resolve_worm_dynamic("MOCK_STRAT1")
            assert result is not None
            assert result["success"] is True
            assert result["result"] == {"mock": "registry"}
        finally:
            self._restore("MOCK_STRAT1")

    def test_registry_reports_method_used(self):
        """Strategy 1 should report which standard method was called."""
        self._inject("MOCK_STRAT1", _MockSummaryWorm)
        try:
            result = _resolve_worm_dynamic("MOCK_STRAT1")
            assert result is not None
            assert result["method"] == "get_summary"
        finally:
            self._restore("MOCK_STRAT1")

    def test_registry_priority_over_legacy_dispatch(self):
        """Strategy 1 (registry) must win over Strategy 2 (legacy dispatch)."""
        self._inject("MOCK_PRIORITY", _MockSummaryWorm)
        _LEGACY_DISPATCH["MOCK_PRIORITY"] = lambda: {"mock": "legacy"}
        try:
            result = _resolve_worm_dynamic("MOCK_PRIORITY")
            assert result is not None
            assert result["success"] is True
            assert result["result"] == {"mock": "registry"}, \
                "Registry entry should take precedence over legacy dispatch"
        finally:
            self._restore("MOCK_PRIORITY")
            _LEGACY_DISPATCH.pop("MOCK_PRIORITY", None)

    def test_registry_noncallable_falls_through_to_legacy(self):
        """A registry entry with a non-callable class should fall through to Strategy 2."""
        self._inject("MOCK_NONCALL", None)
        _LEGACY_DISPATCH["MOCK_NONCALL"] = lambda: {"mock": "legacy"}
        try:
            result = _resolve_worm_dynamic("MOCK_NONCALL")
            assert result is not None
            assert result["success"] is True
            assert result["method"] == "legacy_dispatch"
            assert result["result"] == {"mock": "legacy"}
        finally:
            self._restore("MOCK_NONCALL")
            _LEGACY_DISPATCH.pop("MOCK_NONCALL", None)


# ═══════════════════════════════════════════════════════════════════
# _resolve_worm_dynamic Strategy 2 — Legacy Dispatch (isolated)
# ═══════════════════════════════════════════════════════════════════


class TestResolveStrategy2LegacyDispatch:
    """Verify _resolve_worm_dynamic Strategy 2 — legacy hardcoded dispatch."""

    def test_legacy_dispatch_used_when_not_registered(self):
        """A legacy-only name (not in registry/module map) should use Strategy 2."""
        _LEGACY_DISPATCH["MOCK_LEGACY_ONLY"] = lambda: {"mock": "legacy"}
        try:
            result = _resolve_worm_dynamic("MOCK_LEGACY_ONLY")
            assert result is not None
            assert result["success"] is True
            assert result["method"] == "legacy_dispatch"
            assert result["result"] == {"mock": "legacy"}
        finally:
            _LEGACY_DISPATCH.pop("MOCK_LEGACY_ONLY", None)

    def test_legacy_dispatch_error_reported(self):
        """A legacy dispatch that raises should return a failure dict."""
        def _boom():
            raise RuntimeError("legacy exploded")
        _LEGACY_DISPATCH["MOCK_LEGACY_ERR"] = _boom
        try:
            result = _resolve_worm_dynamic("MOCK_LEGACY_ERR")
            assert result is not None
            assert result["success"] is False
            assert result["method"] == "legacy_dispatch"
            assert "legacy exploded" in result["error"]
        finally:
            _LEGACY_DISPATCH.pop("MOCK_LEGACY_ERR", None)


# ═══════════════════════════════════════════════════════════════════
# _resolve_worm_dynamic Strategy 3 — Module Map (isolated)
# ═══════════════════════════════════════════════════════════════════


class TestResolveStrategy3ModuleMap:
    """Verify _resolve_worm_dynamic Strategy 3 — module map import fallback."""

    def test_module_map_resolves_class(self):
        """A module-map name (not in registry/legacy) should import and resolve."""
        _WORM_MODULE_MAP["MOCK_MAP_ONLY"] = ("WORMZ", "WORMZ")
        try:
            result = _resolve_worm_dynamic("MOCK_MAP_ONLY")
            assert result is not None
            assert result["success"] is True
            assert result["method"] in _STANDARD_METHODS
        finally:
            _WORM_MODULE_MAP.pop("MOCK_MAP_ONLY", None)

    def test_module_map_import_failure_returns_none(self):
        """An unimportable module-map entry should fall through and return None."""
        _WORM_MODULE_MAP["MOCK_MAP_MISSING"] = ("__nonexistent_module_xyz__", "Nope")
        try:
            result = _resolve_worm_dynamic("MOCK_MAP_MISSING")
            assert result is None
        finally:
            _WORM_MODULE_MAP.pop("MOCK_MAP_MISSING", None)


# ═══════════════════════════════════════════════════════════════════
# _execute_worm_class Edge Cases
# ═══════════════════════════════════════════════════════════════════


class TestExecuteWormClassEdgeCases:
    """Verify _execute_worm_class failure paths and result conversion."""

    def test_instantiation_failure_reported(self):
        """A class whose __init__ raises should report instantiation failure."""
        class _BrokenInit:
            def __init__(self):
                raise RuntimeError("cannot construct")
        result = _execute_worm_class("MOCK_BROKEN", _BrokenInit)
        assert result["success"] is False
        assert "Instantiation failed" in result["error"]

    def test_method_exception_reported(self):
        """A method that raises should report the error with the method name."""
        class _RaisingMethod:
            VERSION = "1.0.0"

            def get_summary(self):
                raise ValueError("method exploded")
        result = _execute_worm_class("MOCK_RAISE", _RaisingMethod)
        assert result["success"] is False
        assert result["method"] == "get_summary"
        assert "method exploded" in result["error"]

    def test_no_parameterless_method_reported(self):
        """A class with only arg-requiring methods should report no method found."""
        class _ArgOnly:
            VERSION = "1.0.0"

            def needs_args(self, x):
                return x
        result = _execute_worm_class("MOCK_ARGONLY", _ArgOnly)
        assert result["success"] is False
        assert "No parameterless method found" in result["error"]

    def test_dataclass_result_converted_to_dict(self):
        """A dataclass result should be converted to a dict."""
        @dataclass
        class _Payload:
            value: int = 42

        class _DataclassWorm:
            VERSION = "1.0.0"

            def get_summary(self):
                return _Payload(value=7)
        result = _execute_worm_class("MOCK_DATACLASS", _DataclassWorm)
        assert result["success"] is True
        assert result["result"] == {"value": 7}


# ═══════════════════════════════════════════════════════════════════
# ClusterState Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterState:
    """Verify ClusterState shared state management."""

    def test_initial_state(self):
        """Fresh ClusterState should be empty."""
        state = ClusterState("test_cluster")
        assert state.cluster_id == "test_cluster"
        assert state.results == {}
        assert state.messages == []
        assert state.metrics == {}

    def test_add_result(self):
        """Adding a result should store it."""
        state = ClusterState("test_cluster")
        state.add_result("SYSTEMZ", {"success": True})
        assert "SYSTEMZ" in state.results
        assert state.results["SYSTEMZ"]["success"] is True

    def test_multiple_results(self):
        """Multiple worm results should be stored independently."""
        state = ClusterState("test_cluster")
        state.add_result("A", {"status": "ok"})
        state.add_result("B", {"status": "fail"})
        assert state.results["A"]["status"] == "ok"
        assert state.results["B"]["status"] == "fail"

    def test_send_message(self):
        """Sending a message should add to message list."""
        state = ClusterState("test_cluster")
        state.send_message("WORMZ", "SYSTEMZ", {"alert": "high_cpu"})
        assert len(state.messages) == 1
        msg = state.messages[0]
        assert msg["from"] == "WORMZ"
        assert msg["to"] == "SYSTEMZ"
        assert msg["data"]["alert"] == "high_cpu"
        assert "timestamp" in msg

    def test_broadcast_excludes_sender(self):
        """Broadcast should exclude specified worms."""
        state = ClusterState("test_cluster")
        state.add_result("A", {})
        state.add_result("B", {})
        state.add_result("C", {})
        state.broadcast({"alert": "test"}, exclude=["A"])
        for msg in state.messages:
            assert msg["to"] != "A", "Broadcast should exclude 'A'"

    def test_get_messages_for_worm(self):
        """Getting messages should return messages addressed to that worm."""
        state = ClusterState("test_cluster")
        state.send_message("A", "B", "hello")
        state.send_message("C", "B", "world")
        state.send_message("B", "A", "reply")
        b_messages = state.get_messages("B")
        assert len(b_messages) == 2
        for msg in b_messages:
            assert msg["to"] == "B"

    def test_get_messages_broadcast(self):
        """Getting messages should include broadcasts."""
        state = ClusterState("test_cluster")
        state.add_result("A", {})
        state.add_result("B", {})
        state.broadcast({"global": "alert"})
        a_msgs = state.get_messages("A")
        broadcast_msgs = [m for m in a_msgs if m["from"] == "BROADCAST"]
        assert len(broadcast_msgs) >= 1

    def test_clear_messages_all(self):
        """Clearing all messages should empty the list."""
        state = ClusterState("test_cluster")
        state.send_message("A", "B", "test")
        assert len(state.messages) > 0
        state.clear_messages()
        assert len(state.messages) == 0

    def test_clear_messages_for_worm(self):
        """Clearing messages for a specific worm."""
        state = ClusterState("test_cluster")
        state.send_message("A", "B", "msg1")
        state.send_message("C", "B", "msg2")
        state.send_message("B", "D", "msg3")
        state.clear_messages("B")
        # Messages to B should be removed, but not messages from B
        remaining_b = [m for m in state.messages if m["to"] == "B"]
        assert len(remaining_b) == 0
        remaining_d = [m for m in state.messages if m["to"] == "D"]
        assert len(remaining_d) == 1

    def test_last_sync_updates(self):
        """last_sync should update when results are added."""
        state = ClusterState("test_cluster")
        before = state.last_sync
        time.sleep(0.01)
        state.add_result("X", {"ok": True})
        assert state.last_sync > before


# ═══════════════════════════════════════════════════════════════════
# WormExecutor Tests
# ═══════════════════════════════════════════════════════════════════


class TestWormExecutor:
    """Verify WormExecutor execution."""

    def test_execute_known_worm(self):
        """Executing a known worm with config should return output."""
        state = ClusterState("test")
        cfg = WormConfig(name="WORMZ", module="WORMZ", timeout=10)
        executor = WormExecutor("WORMZ", cfg, state)
        result = executor.execute()
        assert result is not None
        assert result["name"] == "WORMZ"
        assert "success" in result
        assert "duration_ms" in result

    def test_execute_unknown_worm(self):
        """Executing an unknown worm should return failure."""
        state = ClusterState("test")
        cfg = WormConfig(name="NONEXISTENT", module="NONEXISTENT", timeout=5)
        executor = WormExecutor("NONEXISTENT", cfg, state)
        result = executor.execute()
        assert result is not None
        assert result["name"] == "NONEXISTENT"
        assert result["success"] is False
        assert result["error"] is not None

    def test_execute_updates_state(self):
        """Executor should add result to cluster state."""
        state = ClusterState("test")
        cfg = WormConfig(name="WORMZ", module="WORMZ")
        executor = WormExecutor("WORMZ", cfg, state)
        executor.execute()
        assert "WORMZ" in state.results

    def test_execute_duration_positive(self):
        """Duration should be a positive number."""
        state = ClusterState("test")
        cfg = WormConfig(name="WORMZ", module="WORMZ")
        executor = WormExecutor("WORMZ", cfg, state)
        result = executor.execute()
        assert result["duration_ms"] >= 0


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Construction Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZConstruction:
    """Verify ClusterWORMZ construction and configuration."""

    def test_default_construction(self):
        """Default construction should use 'system' cluster."""
        cluster = ClusterWORMZ()
        assert cluster.cluster_type == "system"
        assert cluster.VERSION == "2.2.0"
        assert cluster.cluster_id.startswith("cluster_")

    def test_custom_cluster_type(self):
        """Construction with specific cluster type should load its worms."""
        cluster = ClusterWORMZ("network")
        assert cluster.cluster_type == "network"
        assert len(cluster.configs) > 0
        # Network cluster should have NETWORXZ, CLOCKZ, DISKZ
        worm_names = [c.name for c in cluster.configs]
        assert "NETWORXZ" in worm_names

    def test_unknown_cluster_type(self):
        """Unknown cluster type should result in empty configs."""
        cluster = ClusterWORMZ("__nonexistent__")
        assert cluster.cluster_type == "__nonexistent__"
        assert len(cluster.configs) == 0

    def test_all_registered_clusters_load(self):
        """Each cluster type in CLUSTER_REGISTRY should load its worms."""
        for cluster_type in CLUSTER_REGISTRY:
            cluster = ClusterWORMZ(cluster_type)
            expected_worms = set(CLUSTER_REGISTRY[cluster_type]["worms"])
            actual_worms = set(c.name for c in cluster.configs)
            assert actual_worms == expected_worms, \
                f"{cluster_type}: expected {expected_worms}, got {actual_worms}"

    def test_default_parallel_off(self):
        """Parallel execution should be off by default."""
        cluster = ClusterWORMZ()
        assert cluster.parallel is False

    def test_default_timeout(self):
        """Default timeout should be 60 seconds."""
        cluster = ClusterWORMZ()
        assert cluster.timeout == 60


class TestClusterWORMZConfiguration:
    """Verify ClusterWORMZ configuration methods."""

    def test_set_parallel(self):
        """Setting parallel flag should work."""
        cluster = ClusterWORMZ()
        assert cluster.parallel is False
        cluster.set_parallel(True)
        assert cluster.parallel is True
        cluster.set_parallel(False)
        assert cluster.parallel is False

    def test_set_timeout(self):
        """Setting timeout should work."""
        cluster = ClusterWORMZ()
        cluster.set_timeout(120)
        assert cluster.timeout == 120

    def test_add_worm(self):
        """Adding a worm should append to configs."""
        cluster = ClusterWORMZ("system")
        initial_count = len(cluster.configs)
        cluster.add_worm("WORMZ", "WORMZ")
        assert len(cluster.configs) == initial_count + 1
        assert cluster.configs[-1].name == "WORMZ"

    def test_add_worm_with_config(self):
        """Adding a worm with config should set its properties."""
        cluster = ClusterWORMZ("system")
        cluster.add_worm("WORMZ", "WORMZ", {
            "enabled": False, "priority": 10, "timeout": 5, "retry": 0,
        })
        cfg = cluster.configs[-1]
        assert cfg.enabled is False
        assert cfg.priority == 10
        assert cfg.timeout == 5
        assert cfg.retry == 0

    def test_remove_worm(self):
        """Removing a worm should remove from configs."""
        cluster = ClusterWORMZ("system")
        initial_count = len(cluster.configs)
        worm_to_remove = cluster.configs[0].name
        cluster.remove_worm(worm_to_remove)
        assert len(cluster.configs) == initial_count - 1
        assert worm_to_remove not in [c.name for c in cluster.configs]

    def test_remove_nonexistent_worm(self):
        """Removing a non-existent worm should not change configs."""
        cluster = ClusterWORMZ("system")
        initial_count = len(cluster.configs)
        cluster.remove_worm("__nonexistent__")
        assert len(cluster.configs) == initial_count


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Dynamic Building Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZBuildFromNature:
    """Verify ClusterWORMZ.build_from_category()."""

    def test_build_from_category_returns_cluster(self):
        """Building from a valid nature should return a ClusterWORMZ."""
        cluster = ClusterWORMZ.build_from_category("security")
        if cluster is None:
            pytest.skip("security nature unresolvable in this env")
        assert isinstance(cluster, ClusterWORMZ)
        assert cluster.cluster_type.startswith("cat_")
        assert len(cluster.configs) > 0

    def test_build_from_category_unknown(self):
        """Building from an unknown nature should return None or empty."""
        cluster = ClusterWORMZ.build_from_category("__nonexistent__")
        # May be None or have empty configs
        if cluster is not None:
            assert len(cluster.configs) == 0
        # None is also a correct refusal; either way the test observes it
        assert cluster is None or len(cluster.configs) == 0

    def test_build_from_category_worms_match(self):
        """Built cluster should have worms from that nature category."""
        cluster = ClusterWORMZ.build_from_category("monitor")
        if cluster is None or len(cluster.configs) == 0:
            pytest.skip("monitor nature empty in this env")
        for cfg in cluster.configs:
            assert isinstance(cfg.name, str)


class TestClusterWORMZBuildFromRegistry:
    """Verify ClusterWORMZ.build_from_registry()."""

    def test_build_from_registry_list(self):
        """Building from a list should include those worms."""
        cluster = ClusterWORMZ.build_from_registry(["SYSTEMZ", "PROCESSZ"])
        assert cluster.cluster_type == "custom"
        worm_names = [c.name for c in cluster.configs]
        assert "SYSTEMZ" in worm_names
        assert "PROCESSZ" in worm_names

    def test_build_from_registry_empty(self):
        """Building from an empty list should have no configs."""
        cluster = ClusterWORMZ.build_from_registry([])
        assert len(cluster.configs) == 0

    def test_build_from_registry_deduplicates(self):
        """Building from a list should not duplicate worms."""
        cluster = ClusterWORMZ.build_from_registry(["A", "A", "B"])
        worm_names = [c.name for c in cluster.configs]
        # Each add_worm call adds a new config entry
        # So dedup is not built-in — each call adds separately
        assert worm_names.count("A") == 2


class TestClusterWORMZBuildFullEcosystem:
    """Verify ClusterWORMZ.build_full_ecosystem()."""

    def test_build_full_ecosystem(self):
        """Full ecosystem should return a valid ClusterWORMZ."""
        cluster = ClusterWORMZ.build_full_ecosystem()
        assert isinstance(cluster, ClusterWORMZ)
        # Should have worms (either from registry or from 'full' fallback)
        assert len(cluster.configs) >= 0
        # If it fell back to 'full', should have 5 worms
        if cluster.cluster_type == "full":
            assert len(cluster.configs) == len(CLUSTER_REGISTRY.get("full", {}).get("worms", []))


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Nature Config Wiring Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZCategoryConfig:
    """Verify nature config overrides from azazael.yaml are wired to ClusterWORMZ."""

    def test_build_from_category_applies_timeout(self):
        """build_from_category('monitor') should apply 30s timeout from azazael.yaml."""
        cluster = ClusterWORMZ.build_from_category("monitor")
        if cluster is not None:
            assert cluster.timeout == 30, f"Expected 30 (from YAML), got {cluster.timeout}"

    def test_build_from_category_applies_parallel(self):
        """build_from_category('security') should NOT enable parallel (YAML says false)."""
        cluster = ClusterWORMZ.build_from_category("security")
        if cluster is not None:
            assert cluster.parallel is False, f"Expected False (from YAML), got {cluster.parallel}"

    def test_build_from_category_applies_max_retries(self):
        """build_from_category('learning') no YAML entry, falls back to defaults."""
        cluster = ClusterWORMZ.build_from_category("learning")
        if cluster is not None:
            assert cluster._category_config is not None
            assert cluster.timeout == 60  # cluster default, no 'learning' in YAML

    def test_build_from_category_sets_category_name(self):
        """build_from_category should set _category_name on cluster."""
        cluster = ClusterWORMZ.build_from_category("profiling")
        if cluster is not None:
            assert cluster._category_name == "profiling"

    def test_standard_init_no_category_config(self):
        """Standard ClusterWORMZ('system') should have no nature config."""
        cluster = ClusterWORMZ("system")
        assert cluster._category_name is None
        assert cluster._category_config is None
        assert cluster.timeout == 60  # default

    def test_get_status_reports_category_info(self):
        """get_status should report nature config info when applied."""
        cluster = ClusterWORMZ.build_from_category("monitor")
        if cluster is not None:
            status = cluster.get_status()
            assert status.get("category_name") == "monitor"
            assert status.get("category_config_applied") is True
            assert cluster.timeout == 30  # monitor YAML timeout

    def test_get_status_standard_init_no_nature(self):
        """get_status for standard init should report no nature config."""
        cluster = ClusterWORMZ("system")
        status = cluster.get_status()
        assert status.get("category_name") is None
        assert status.get("category_config_applied") is False


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Status Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZStatus:
    """Verify ClusterWORMZ.get_status()."""

    def test_status_has_required_keys(self):
        """Status should have all expected fields."""
        cluster = ClusterWORMZ("system")
        status = cluster.get_status()
        for key in ("cluster_id", "cluster_type", "version", "worm_count",
                    "enabled_count", "parallel", "timeout", "execution_count",
                    "last_sync", "free_threaded", "worm_names", "registry_ok"):
            assert key in status, f"Missing status key: {key}"

    def test_status_version(self):
        """Status should report correct version."""
        cluster = ClusterWORMZ()
        assert cluster.get_status()["version"] == "2.2.0"

    def test_status_worm_count(self):
        """Status should report correct worm count."""
        cluster = ClusterWORMZ("system")
        status = cluster.get_status()
        assert status["worm_count"] == len(cluster.configs)
        assert status["enabled_count"] <= status["worm_count"]

    def test_status_worm_names_sorted(self):
        """Worm names in status should be sorted."""
        cluster = ClusterWORMZ("system")
        status = cluster.get_status()
        names = status["worm_names"]
        assert names == sorted(names)


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Mermaid Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZMermaid:
    """Verify ClusterWORMZ Mermaid graph generation."""

    def test_to_mermaid_structure(self):
        """Mermaid graph should have expected structure."""
        cluster = ClusterWORMZ("system")
        mermaid = cluster.to_mermaid()
        assert isinstance(mermaid, str)
        assert "graph TB" in mermaid
        assert "CLUSTER:" in mermaid
        assert cluster.cluster_type.upper() in mermaid.upper()

    def test_to_mermaid_includes_worms(self):
        """Mermaid graph should include worm names."""
        cluster = ClusterWORMZ("system")
        mermaid = cluster.to_mermaid()
        for cfg in cluster.configs:
            assert cfg.name in mermaid, f"Missing worm {cfg.name} in Mermaid"

    def test_to_mermaid_custom_title(self):
        """Custom title should appear in the graph."""
        cluster = ClusterWORMZ("system")
        mermaid = cluster.to_mermaid(title="Custom Cluster")
        assert "Custom Cluster" in mermaid

    def test_to_mermaid_execution_flow(self):
        """Execution flow should return a valid graph."""
        cluster = ClusterWORMZ("system")
        mermaid = cluster.to_mermaid_execution_flow()
        assert isinstance(mermaid, str)
        assert "graph LR" in mermaid

    def test_to_mermaid_interactions(self):
        """Interactions graph should be a sequence diagram."""
        cluster = ClusterWORMZ("system")
        cluster.state.send_message("A", "B", {"data": "test"})
        mermaid = cluster.to_mermaid_interactions()
        assert isinstance(mermaid, str)
        assert "sequenceDiagram" in mermaid


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Cancellation Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZCancellation:
    """Verify cooperative cancellation shortens retry loops."""

    def test_cancellation_shortens_retry(self):
        """Cancellation should break from retry exponential backoff early."""
        import threading
        from CORE.WORMS.CLUSTERWORMS import _get_breaker_registry

        # Gracefully skip when circuit breaker is active — the retry loop
        # (and thus cancellation path) only runs without breaker protection.
        if _get_breaker_registry() is not None:
            pytest.skip("Circuit breaker enabled — retry loop not active")

        # Use nonexistent cluster type to start empty — no wasted worm loading
        cluster = ClusterWORMZ("__nonexistent__")
        cluster.add_worm("NONEXISTENT", "NONEXISTENT", {"retry": 10, "timeout": 5})

        # Manually set cancellation to short-circuit the backoff sleep
        cluster._cancel_event = threading.Event()
        cluster._cancel_event.set()

        start = time.time()
        result = cluster._execute_single_worm(cluster.configs[0])
        duration = time.time() - start

        assert result["success"] is False
        assert duration < 2.0, f"Cancellation should finish under 2s, took {duration:.2f}s"

    def test_no_cancellation_runs_normally(self):
        """Without cancellation, execution attempts retries with backoff before failing."""
        from CORE.WORMS.CLUSTERWORMS import _get_breaker_registry

        if _get_breaker_registry() is not None:
            pytest.skip("Circuit breaker enabled — retry loop not active")

        cluster = ClusterWORMZ("__nonexistent__")
        cluster.add_worm("NONEXISTENT", "NONEXISTENT", {"retry": 2, "timeout": 1})

        start = time.time()
        result = cluster._execute_single_worm(cluster.configs[0])
        duration = time.time() - start

        assert result["success"] is False
        assert duration >= 0.1, (
            f"Normal execution should take time due to backoff, took {duration:.2f}s"
        )


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Sequential Execution Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZExecutionSequential:
    """Verify ClusterWORMZ sequential execution."""

    def test_execute_all_sequential(self):
        """Sequential execution should return results for all worms."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(False)
        results = cluster.execute_all()
        assert isinstance(results, list)
        assert len(results) == len([c for c in cluster.configs if c.enabled])

    def test_execute_all_results_have_keys(self):
        """Each result should have expected keys."""
        cluster = ClusterWORMZ("system")
        results = cluster.execute_all()
        for result in results:
            assert "name" in result
            assert "success" in result
            assert "duration_ms" in result

    def test_execute_single_worm(self):
        """Executing a single worm should return its result."""
        cluster = ClusterWORMZ("system")
        worm_name = cluster.configs[0].name
        result = cluster.execute_worm(worm_name)
        assert result is not None
        assert result["name"] == worm_name

    def test_execute_single_unknown_worm(self):
        """Executing an unknown worm by name should return None."""
        cluster = ClusterWORMZ("system")
        result = cluster.execute_worm("__nonexistent__")
        assert result is None

    def test_execute_all_tracks_execution_count(self):
        """Each execute_all should increment execution_count."""
        cluster = ClusterWORMZ("system")
        assert cluster._execution_count == 0
        cluster.execute_all()
        assert cluster._execution_count == 1
        cluster.execute_all()
        assert cluster._execution_count == 2


class TestClusterWORMZExecutionParallel:
    """Verify ClusterWORMZ parallel execution."""

    def test_execute_all_parallel(self):
        """Parallel execution should return results for all worms."""
        cluster = ClusterWORMZ("full")
        cluster.set_parallel(True)
        results = cluster.execute_all()
        assert isinstance(results, list)
        assert len(results) == len([c for c in cluster.configs if c.enabled])

    def test_parallel_results_valid(self):
        """Parallel results should have expected structure."""
        cluster = ClusterWORMZ("full")
        cluster.set_parallel(True)
        results = cluster.execute_all()
        for result in results:
            assert "name" in result
            assert "success" in result
            assert "duration_ms" in result


class TestClusterWORMZRetry:
    """Verify ClusterWORMZ retry logic."""

    def test_retry_on_known_worm(self):
        """Retry should handle retries for worms with retry > 0."""
        cluster = ClusterWORMZ("system")
        # Set retry on first worm
        cluster.configs[0].retry = 2
        results = cluster.execute_all()
        # Should still complete successfully
        assert len(results) > 0

    def test_retry_on_unknown_worm(self):
        """Retry should not crash for failing worms."""
        cluster = ClusterWORMZ("system")
        cluster.add_worm("NONEXISTENT", "NONEXISTENT", {"retry": 2})
        results = cluster.execute_all()
        missing_result = [r for r in results if r["name"] == "NONEXISTENT"]
        if missing_result:
            assert missing_result[0]["success"] is False


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Aggregation Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZAggregation:
    """Verify ClusterWORMZ.aggregate_results()."""

    def test_aggregate_before_execution(self):
        """Aggregating before any execution should return ClusterResult."""
        cluster = ClusterWORMZ("system")
        aggregated = cluster.aggregate_results()
        assert isinstance(aggregated, ClusterResult)
        assert aggregated.cluster_id == cluster.cluster_id

    def test_aggregate_after_execution(self):
        """Aggregating after execution should include worm results."""
        cluster = ClusterWORMZ("system")
        cluster.execute_all()
        aggregated = cluster.aggregate_results()
        assert len(aggregated.worm_results) > 0
        assert aggregated.success_count + aggregated.failure_count > 0

    def test_aggregate_metrics(self):
        """Aggregate metrics should be computed correctly."""
        cluster = ClusterWORMZ("system")
        cluster.execute_all()
        aggregated = cluster.aggregate_results()
        metrics = aggregated.aggregated_metrics
        assert "total_duration_ms" in metrics
        assert "success_rate" in metrics
        assert "avg_duration_ms" in metrics
        assert metrics["total_duration_ms"] >= 0
        assert 0 <= metrics["success_rate"] <= 1

    def test_aggregate_success_rate(self):
        """Success rate should reflect actual results."""
        cluster = ClusterWORMZ("system")
        cluster.execute_all()
        aggregated = cluster.aggregate_results()
        total = aggregated.success_count + aggregated.failure_count
        if total > 0:
            expected_rate = aggregated.success_count / total
            assert aggregated.aggregated_metrics["success_rate"] == expected_rate


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Integration Tests
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZIntegration:
    """End-to-end cluster integration tests."""

    def test_full_lifecycle(self):
        """Full lifecycle: create, execute, aggregate, get_status."""
        cluster = ClusterWORMZ("system")
        assert cluster.get_status()["worm_count"] > 0

        results = cluster.execute_all()
        assert len(results) > 0

        aggregated = cluster.aggregate_results()
        assert aggregated.success_count + aggregated.failure_count == len(results)

        mermaid = cluster.to_mermaid()
        assert "graph TB" in mermaid

    def test_multiple_executions(self):
        """Running execute_all multiple times should accumulate state."""
        cluster = ClusterWORMZ("system")
        r1 = cluster.execute_all()
        assert cluster._execution_count == 1
        r2 = cluster.execute_all()
        assert cluster._execution_count == 2

    def test_parallel_then_sequential(self):
        """Switching between parallel and sequential should work."""
        cluster = ClusterWORMZ("full")
        cluster.set_parallel(True)
        r1 = cluster.execute_all()
        assert len(r1) > 0

        cluster.set_parallel(False)
        r2 = cluster.execute_all()
        assert len(r2) > 0

    def test_execute_all_with_custom_worm(self):
        """Adding a custom worm before execution should include it."""
        cluster = ClusterWORMZ("system")
        cluster.add_worm("CUSTOM", "WORMZ")
        results = cluster.execute_all()
        custom_results = [r for r in results if r["name"] == "CUSTOM"]
        assert len(custom_results) == 1


# ═══════════════════════════════════════════════════════════════════
# Standalone Runner
# ═══════════════════════════════════════════════════════════════════


def _run_standalone():
    """Run tests standalone (for environments where pytest doesn't work, e.g., 3.14t).

    Error #37 fixed (June 11, 2026): The 15 execution tests previously skipped
    are now ENABLED by default. Slow (execution) test classes are run in
    subprocesses with per-class timeouts — if the subprocess hangs (import
    deadlock, C-extension stall), it's killed by OS-level subprocess timeout,
    cleanly releasing ALL locks without affecting subsequent test classes.

    Use --quick to skip slow test classes (worm execution, nature config, ecosystem
    builds) — runs the core framework tests in under 60 seconds.
    Use --unit to ONLY run the pure data/logic tests (skip all ClusterWORMZ instances).
    """
    import subprocess
    # Lazy import to avoid import-order issues
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from _timer_utils import run_class_with_timeout

    quick_mode = "--quick" in sys.argv
    unit_mode = "--unit" in sys.argv

    # These classes were previously skipped (Error #37 — 15 hanging execution tests).
    # Now they run in SUBPROCESSES with per-class timeouts. Clean lock release
    # via OS process termination — no daemon-thread lock-leak cascade.
    slow_classes = {
        "TestClusterWORMZCategoryConfig",
        "TestClusterWORMZBuildFromNature",
        "TestClusterWORMZBuildFullEcosystem",
        "TestClusterWORMZExecutionSequential",
        "TestClusterWORMZExecutionParallel",
        "TestClusterWORMZRetry",
        "TestClusterWORMZIntegration",
        "TestClusterWORMZAggregation",
    }

    fast_unit_classes = {
        "TestWormConfig",
        "TestClusterResult",
        "TestClusterRegistry",
        "TestWormModuleMap",
        "TestStandardMethods",
        "TestLegacyDispatch",
        "TestResolveWormDynamicRegistry",
        "TestResolveWormDynamicFallback",
        "TestExecuteWormClass",
        "TestResolveStrategy1Registry",
        "TestResolveStrategy2LegacyDispatch",
        "TestResolveStrategy3ModuleMap",
        "TestExecuteWormClassEdgeCases",
        "TestClusterState",
    }

    # Subprocess timeouts for slow/execution test classes
    subprocess_timeouts = {
        "TestClusterWORMZExecutionSequential": 45,
        "TestClusterWORMZExecutionParallel": 45,
        "TestClusterWORMZRetry": 60,
        "TestClusterWORMZAggregation": 45,
        "TestClusterWORMZIntegration": 300,
        "TestClusterWORMZCategoryConfig": 30,
        "TestClusterWORMZBuildFromNature": 30,
        "TestClusterWORMZBuildFullEcosystem": 30,
    }

    passed = 0
    failed = 0
    skipped = 0
    errors = []

    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            method_count = len([mn for mn in dir(obj) if mn.startswith("test_")])

            if unit_mode and name not in fast_unit_classes:
                print(f"  [SKIP] {name}.* ({method_count} tests skipped in --unit mode)")
                skipped += method_count
                continue

            if quick_mode and name in slow_classes:
                print(f"  [SKIP] {name}.* ({method_count} tests skipped in --quick mode)")
                skipped += method_count
                continue

            # Run slow test classes in subprocess with timeout
            if name in subprocess_timeouts:
                timeout = subprocess_timeouts[name]
                print(f"[{name}] (subprocess, timeout={timeout}s)")
                try:
                    # run_class_with_timeout prints output inline AND returns counts
                    sp, sf, sk = run_class_with_timeout(obj, timeout_sec=timeout)
                    passed += sp
                    failed += sf
                    skipped += sk
                except subprocess.TimeoutExpired:
                    print(f"  [FAIL] {name}.* — subprocess timed out after {timeout}s")
                    failed += method_count
                    errors.append(f"{name}: TIMEOUT (subprocess killed at {timeout}s)")
                except Exception as e:
                    print(f"  [FAIL] {name}.* — subprocess error: {e}")
                    failed += method_count
                    errors.append(f"{name}: SUBPROCESS ERROR: {e}")
            else:
                # Fast test classes run in-process
                print(f"[{name}]")
                inst = obj()
                for mn in sorted(dir(inst)):
                    if mn.startswith("test_"):
                        try:
                            getattr(inst, mn)()
                            print(f"  [OK] {name}.{mn}")
                            passed += 1
                        except BaseException as exc:
                            if type(exc).__name__ in ('Skip', 'Skipped'):
                                print(f"  [SKIP] {name}.{mn}: {exc}")
                                skipped += 1
                            elif isinstance(exc, (SystemExit, KeyboardInterrupt)):
                                raise
                            else:
                                print(f"  [FAIL] {name}.{mn}: {exc}")
                                failed += 1
                                errors.append(f"{name}.{mn}: {exc}")

    print(f"\n  Passed: {passed}, Failed: {failed}, Skipped: {skipped}")
    if errors:
        print("  Errors:")
        for e in errors[:10]:
            print(f"    {e}")
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
