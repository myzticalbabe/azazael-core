#!/usr/bin/env python3
"""Integration tests for CLUSTERWORMS — import, creation, sequential execution."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/azazael-core/src"))

import pytest


class TestClusterWORMZImport:
    """Verify CLUSTERWORMS module imports cleanly."""

    def test_import_clusterworms(self):
        """from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ should not hang."""
        t0 = time.time()
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        elapsed = time.time() - t0
        assert elapsed < 5.0, f"Import took {elapsed:.2f}s — possible deadlock"

    def test_import_all_exports(self):
        """All public exports should be importable."""
        from CORE.WORMS.CLUSTERWORMS import (
            ClusterWORMZ, ClusterResult, ClusterState,
            WormConfig, WormExecutor,
            CLUSTER_REGISTRY, _LEGACY_MODULE_NAMES,
        )
        assert CLUSTER_REGISTRY is not None
        assert len(_LEGACY_MODULE_NAMES) > 0

    def test_module_has_version(self):
        """ClusterWORMZ.VERSION should be defined."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        assert hasattr(ClusterWORMZ, "VERSION")
        assert ClusterWORMZ.VERSION.startswith("2.")


class TestClusterWORMZCreation:
    """Verify cluster creation works with various cluster types."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def test_create_system_cluster(self):
        """Creating a system cluster should include the 3 system worms."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        c = ClusterWORMZ("system")
        names = [cfg.name for cfg in c.configs]
        assert "SYSTEMZ" in names
        assert "PROCESSZ" in names
        assert "DISKZ" in names

    def test_create_nonexistent_cluster(self):
        """Creating a cluster with unknown type should produce 0 configs."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        c = ClusterWORMZ("nonexistent_type_xyz")
        assert len(c.configs) == 0
        assert c.cluster_type == "nonexistent_type_xyz"

    def test_create_all_known_clusters(self):
        """All known cluster types should create without error."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ, CLUSTER_REGISTRY
        for cluster_type in CLUSTER_REGISTRY:
            t0 = time.time()
            c = ClusterWORMZ(cluster_type)
            elapsed = time.time() - t0
            assert elapsed < 10.0, f"Creation of '{cluster_type}' took {elapsed:.2f}s"
            assert len(c.configs) > 0

    def test_cluster_id_is_unique(self):
        """Each cluster should have a unique ID."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        c1 = ClusterWORMZ("system")
        c2 = ClusterWORMZ("system")
        assert c1.cluster_id != c2.cluster_id

    def test_cluster_has_correct_type(self):
        """Cluster type should match the argument."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        c = ClusterWORMZ("network")
        assert c.cluster_type == "network"


class TestClusterWORMZSequentialExecution:
    """Verify sequential execution produces results."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def test_sequential_execution_returns_results(self):
        """Sequential execute_all() should return results for each worm."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        c = ClusterWORMZ("system")
        results = c.execute_all()
        assert len(results) == 3
        for r in results:
            assert "name" in r
            assert "success" in r
            assert "duration_ms" in r

    def test_sequential_execution_timeout(self):
        """Sequential execution should complete within reasonable time."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        c = ClusterWORMZ("system")
        c.set_timeout(30)
        t0 = time.time()
        results = c.execute_all()
        elapsed = time.time() - t0
        assert elapsed < 15.0, f"Sequential execution took {elapsed:.2f}s — possible hang"

    def test_execution_does_not_crash(self):
        """Sequential execution should not raise exceptions."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        c = ClusterWORMZ("system")
        try:
            results = c.execute_all()
        except Exception as e:
            pytest.fail(f"execute_all() raised: {e}")

    def test_aggregation_after_execution(self):
        """Aggregate results should have valid metrics after execution."""
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        c = ClusterWORMZ("system")
        results = c.execute_all()
        agg = c.aggregate_results()
        assert agg.success_count >= 0
        assert agg.failure_count >= 0
        assert agg.success_count + agg.failure_count == len(results)
        assert agg.duration_ms > 0


class TestClusterWORMZLegacyDispatch:
    """Verify legacy dispatch works as fallback."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def test_legacy_execute_returns_dict(self):
        """_legacy_execute should return a result dict for known worms."""
        from CORE.WORMS.CLUSTERWORMS import _legacy_execute, _ensure_legacy_modules
        _ensure_legacy_modules()
        result = _legacy_execute("SYSTEMZ")
        if result is not None:
            assert isinstance(result, dict)

    def test_legacy_execute_returns_none_for_unknown(self):
        """_legacy_execute should return None for unknown worms."""
        from CORE.WORMS.CLUSTERWORMS import _legacy_execute, _ensure_legacy_modules
        _ensure_legacy_modules()
        result = _legacy_execute("UNKNOWN_WORM")
        assert result is None


class TestClusterWORMZRegistry:
    """Verify WORM_REGISTRY integration."""

    def test_discover_worms_works(self):
        """discover_worms should populate the registry."""
        from CORE.worm_registry import discover_worms, WORM_REGISTRY, reset_registry
        reset_registry()
        count = discover_worms()
        assert count > 0 or len(WORM_REGISTRY) > 0


if __name__ == "__main__":
    p = f = 0
    for name, obj in sorted(globals().items()):
        if isinstance(obj, type) and name.startswith("Test"):
            inst = obj()
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        inst.setup_method() if hasattr(inst, "setup_method") else None
                        getattr(inst, mn)()
                        print(f"  [OK] {name}.{mn}")
                        p += 1
                    except Exception as e:
                        print(f"  [FAIL] {name}.{mn}: {e}")
                        import traceback
                        traceback.print_exc()
                        f += 1
    print(f"\n  Passed: {p}, Failed: {f}")
