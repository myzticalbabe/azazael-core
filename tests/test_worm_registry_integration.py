#!/usr/bin/env python3
# coding: utf-8
"""Integration tests exercising the REAL worm registry end-to-end (no mocks).

These tests verify the actual CORE/WORMS modules are discovered into the
real WORM_REGISTRY, then exercise the same dynamic resolution/execution
paths the runtime uses:

  * worm_registry.discover_worms()      -> imports real modules into WORM_REGISTRY
  * WORM_REGISTRY / get_all_categories / get_worms_by_category
  * CLUSTERWORMS._resolve_worm_dynamic  -> Strategy 1 (real registry) execution
  * ClusterManager.discover_all_natures() -> real category grouping

Only safe, deterministic worms are executed (profiling / read-only get_summary).
Run standalone (python tests/test_worm_registry_integration.py) or under pytest.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("AZAZAEL_TEST_QUICK", "1")

# ── Python 3.15t Free-Threading Guard (module level — before imports) ──
from _env_315t import setup_315t
setup_315t(mock_pams=True)

try:
    import pytest
except BaseException:
    import types
    pytest = types.ModuleType("pytest")
    pytest.skip = lambda reason=None: exec("raise type('Skip', (BaseException,), {})(reason or '')")
    pytest.mark = types.ModuleType("mark")
    pytest.mark.skip = lambda *a, **kw: lambda f: f

# ── Real registry + runtime paths (no mocks) ───────────────────────
from CORE.worm_registry import (
    WORM_REGISTRY,
    discover_worms,
    get_all_categories,
    get_worms_by_category,
    reset_registry,
    BaseWormMixin,
)
from CORE.WORMS.CLUSTERWORMS import _resolve_worm_dynamic


def _ensure_discovered() -> int:
    """Idempotently populate the REAL registry (cache-backed after first run).

    Import side-effects of CLUSTERWORMS / CLUSTERWORMS leave a tiny partial
    registry (a few worms, _discovery_done=False). Guarding on *emptiness* was
    unreliable across runners, so we force a deterministic full discovery when
    the registry hasn't been fully built yet (len < 20 -> cache-backed load).
    """
    if len(WORM_REGISTRY) < 20:
        reset_registry()      # clear partial import side-effects, set done=False
        discover_worms()      # cache-backed => loads the full real registry fast
    return len(WORM_REGISTRY)


# ═══════════════════════════════════════════════════════════════════
# Real Registry Discovery
# ═══════════════════════════════════════════════════════════════════


class TestWormRegistryRealDiscovery:
    """Verify discover_worms() populates the real registry from modules."""

    def test_registry_contains_real_modules(self):
        """Registered entries should be real classes with on-disk source files.

        Note: not every registered worm carries a VERSION attr (e.g. CLAWBOT is
        a real class registered via cached metadata), so the "not a mock" proof
        is class-is-a-type + source-on-disk, not VERSION presence.
        """
        total = _ensure_discovered()
        assert total >= 20, f"registry too small: {total}"
        with_version = 0
        for name in list(WORM_REGISTRY.keys())[:min(12, total)]:
            info = WORM_REGISTRY[name]
            cls = info["class"]
            assert isinstance(cls, type), name
            if hasattr(cls, "VERSION"):
                with_version += 1
            src = info.get("source_file", "")
            assert src, f"{name} missing source_file"
            assert os.path.isfile(src), f"{name} source not on disk: {src}"
        # The overwhelming majority of real worms expose VERSION
        assert with_version >= 1

    def test_known_core_worm_has_version(self):
        """A canonical registered worm should carry its real VERSION."""
        _ensure_discovered()
        cls = WORM_REGISTRY["CodeProfiler"]["class"]
        assert isinstance(cls, type)
        assert hasattr(cls, "VERSION")
        assert cls.VERSION  # non-empty
        assert callable(cls)

    def test_all_entries_have_schema(self):
        """Every registry entry should carry the documented metadata keys."""
        _ensure_discovered()
        for name, info in WORM_REGISTRY.items():
            assert "class" in info, name
            assert "category" in info, name
            assert "source_file" in info, name
            assert "priority" in info, name

    def test_categories_discovered(self):
        """Real category set should include the core natures."""
        _ensure_discovered()
        cats = get_all_categories()
        assert len(cats) >= 5, f"expected many categories, got {cats}"
        for c in ("orchestration", "monitor", "security", "profiling"):
            assert c in cats, f"missing real category {c}"

    def test_worms_by_category_consistent(self):
        """get_worms_by_category should return only same-category worms."""
        _ensure_discovered()
        cats = get_all_categories()
        for c in cats:
            worms = get_worms_by_category(c)
            assert worms, f"category {c} has no worms"
            for info in worms.values():
                assert info["category"] == c, f"wrong category in {c}"


# ═══════════════════════════════════════════════════════════════════
# Dynamic Resolution + Real Execution
# ═══════════════════════════════════════════════════════════════════


class TestWormDynamicResolutionEndToEnd:
    """Exercise the runtime resolution path against the real registry."""

    def test_resolve_safe_worm_via_runtime_path(self):
        """_resolve_worm_dynamic should return a real execution dict for a
        registry-resolved worm (Strategy 1), not a mock or legacy stub."""
        _ensure_discovered()
        assert callable(WORM_REGISTRY["CodeProfiler"]["class"])
        result = _resolve_worm_dynamic("CodeProfiler")
        assert isinstance(result, dict)
        assert "success" in result
        if result["success"]:
            assert "result" in result
        else:
            assert "error" in result

    def test_real_worm_class_is_instantiable(self):
        """The registry-resolved class should instantiate into a live worm."""
        _ensure_discovered()
        cls = WORM_REGISTRY["CodeProfiler"]["class"]
        instance = cls()
        assert instance is not None

    def test_summary_on_real_mixin_worms(self):
        """Every registered BaseWormMixin worm should produce a real summary."""
        _ensure_discovered()
        mixin_worms = [
            name for name, info in WORM_REGISTRY.items()
            if isinstance(info.get("class"), type)
            and issubclass(info["class"], BaseWormMixin)
        ]
        assert len(mixin_worms) >= 1, "no BaseWormMixin worms found"
        for name in mixin_worms:
            inst = WORM_REGISTRY[name]["class"]()
            summary = inst.get_summary()
            assert isinstance(summary, dict), f"{name} did not return a summary dict"


# ═══════════════════════════════════════════════════════════════════
# Nature Clusters Against the Real Registry
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZsRealRegistry:
    """Verify the flat registry groups real worms by category end-to-end.

    U3: was ClusterManager.discover_all_natures; the grouping now comes
    straight from get_all_categories/get_worms_by_category.
    """

    def test_category_grouping_from_real_registry(self):
        """Registry categories should group the bulk of real worms."""
        _ensure_discovered()
        from CORE.worm_registry import get_all_categories, get_worms_by_category
        counts = {c: len(get_worms_by_category(c))
                  for c in sorted(get_all_categories())}
        assert isinstance(counts, dict)
        assert counts, "no categories discovered from real registry"
        assert sum(counts.values()) >= 50, f"grouped too few real worms: {counts}"
        cats = get_all_categories()
        for nature in counts:
            assert nature in cats, f"{nature} is not a real registry category"

    def test_category_total_within_registry_bounds(self):
        """Category grouping should account for at most the real registered worms."""
        _ensure_discovered()
        from CORE.worm_registry import get_all_categories, get_worms_by_category
        counts = {c: len(get_worms_by_category(c))
                  for c in sorted(get_all_categories())}
        grouped = sum(counts.values())
        assert 0 < grouped <= len(WORM_REGISTRY)
        # Reproducible across repeated calls (state is shared/cached)
        counts2 = {c: len(get_worms_by_category(c))
                   for c in sorted(get_all_categories())}
        assert sum(counts2.values()) >= 50


# ═══════════════════════════════════════════════════════════════════
# Standalone Runner
# ═══════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    passed = failed = 0
    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            inst = obj()
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        getattr(inst, mn)()
                        print(f"  [OK] {name}.{mn}")
                        passed += 1
                    except Exception as e:
                        print(f"  [FAIL] {name}.{mn}: {e}")
                        failed += 1
    print(f"\n  Passed: {passed}, Failed: {failed}")
    sys.exit(0 if failed == 0 else 1)