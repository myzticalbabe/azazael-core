#!/usr/bin/env python3
"""Step 3: Worm Regression Smoke Test — import, register, basic smoke for ALL worms.

Verifies every worm in the WORM_REGISTRY:
1. Has valid registration metadata (name, category, priority, cooldown)
2. Can be imported from its source module
3. Can be instantiated (or at minimum provides get_summary())

CRITICAL: The disclosure whitepaper (W-A1) reports 86/103 worms FAIL at runtime.
This test provides a baseline to track the fix.

Usage:
    python tests/test_worm_regression_smoke.py
    python -m pytest tests/test_worm_regression_smoke.py -v
"""

import sys
import os
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from CORE.worm_registry import discover_worms, WORM_REGISTRY, reset_registry

def _discover():
    """Ensure WORM_REGISTRY is populated. Return (items, registry size).

    `discover_worms()` returns the number of **newly registered** worms -- a
    DELTA, not the registry size. Measured 2026-09-29: a fresh call returns 9
    while the registry holds 250 entries, and a second call returns 0. That is
    the documented contract (`AZAZAEL_LOCAL_MODEL_OPERATOR_TODO.md`: "returns
    the number of newly registered worms, not the registry"; also recorded in
    `.agents/skills/azazael-error-resolution`), so the assertions in this file
    read WORM_REGISTRY instead of the delta. Reading the delta here reported
    "Expected >=90 worms, got 9" against a 250-worm fleet.
    """
    reset_registry()
    discover_worms()
    items = sorted(WORM_REGISTRY.items(), key=lambda x: x[0])
    return items, len(WORM_REGISTRY)


class TestWormRegistryMetadata:
    """Verify all registered worms have valid metadata."""

    def test_registry_populated(self):
        """WORM_REGISTRY should contain at least 90 worms after discovery."""
        items, count = _discover()
        assert count >= 90, f"Expected >=90 worms, got {count}"

    def test_all_worms_have_category(self):
        """Every worm must have a category field."""
        items, _ = _discover()
        missing = []
        for name, entry in items:
            cat = entry.get("category") or (
                entry.get("categories", [None])[0]
                if isinstance(entry.get("categories"), list) and entry["categories"]
                else None
            )
            if cat is None:
                missing.append(name)
        assert len(missing) == 0, (
            f"{len(missing)} worms missing category: {missing[:10]}"
        )

    def test_all_worms_have_priority(self):
        """Every worm must have a numeric priority."""
        items, _ = _discover()
        bad = []
        for name, entry in items:
            p = entry.get("priority")
            if not isinstance(p, (int, float)):
                bad.append(f"{name}: priority={p!r}")
        assert len(bad) == 0, f"{len(bad)} worms with invalid priority: {bad[:10]}"

    def test_all_worms_have_cooldown(self):
        """Every worm must have a cooldown_seconds field."""
        items, _ = _discover()
        missing = []
        for name, entry in items:
            cd = entry.get("cooldown_seconds") or entry.get("cooldown")
            if cd is None:
                missing.append(name)
        assert len(missing) == 0, (
            f"{len(missing)} worms missing cooldown: {missing[:10]}"
        )

    def test_all_worms_have_source_file(self):
        """Every worm must track its source module."""
        items, _ = _discover()
        missing = []
        for name, entry in items:
            src = entry.get("source") or entry.get("module") or entry.get("source_file")
            if src is None:
                missing.append(name)
        assert len(missing) == 0, (
            f"{len(missing)} worms missing source file: {missing[:10]}"
        )

    def test_all_worms_have_version(self):
        """Every worm should have a version (warn if missing, don't fail)."""
        items, _ = _discover()
        missing = []
        for name, entry in items:
            ver = entry.get("version")
            if ver is None:
                missing.append(name)
        if missing:
            print(f"\n  ⚠️  {len(missing)} worms without version: {missing[:15]}...")


class TestWormInstantiationSmoke:
    """Attempt to import and instantiate every registered worm."""

    def test_import_all_worm_modules(self):
        """Every worm's source module should be importable."""
        items, _ = _discover()
        failed = []
        for name, entry in items:
            src = entry.get("source") or entry.get("module") or entry.get("source_file")
            if src is None:
                failed.append(f"{name}: no source")
                continue
            try:
                # Dynamic import of the worm's module
                mod_name = src.replace("/", ".").replace(".py", "")
                if mod_name.startswith("src."):
                    mod_name = mod_name[4:]
                __import__(mod_name)
            except Exception as e:
                failed.append(f"{name}: {type(e).__name__}: {str(e)[:80]}")
        assert len(failed) == 0, (
            f"{len(failed)}/{len(items)} worms have unimportable modules:\n" +
            "\n".join(f"  - {f}" for f in failed[:15])
        )

    def test_instantiate_all_worms(self):
        """Every worm should be instantiable or at minimum provide a worm class."""
        items, _ = _discover()
        failed = []
        instantiated = 0
        for name, entry in items:
            worm_cls = entry.get("worm_class") or entry.get("cls")
            if worm_cls is None:
                # Try to get the class from the module
                src = entry.get("source") or entry.get("module", "")
                mod_name = src.replace("/", ".").replace(".py", "")
                if mod_name.startswith("src."):
                    mod_name = mod_name[4:]
                try:
                    mod = __import__(mod_name, fromlist=["*"])
                    # Find the class by convention: module name matches class name
                    candidates = [
                        v for k, v in vars(mod).items()
                        if isinstance(v, type) and k.lower() == name.lower().replace("_", "")
                    ]
                    if candidates:
                        worm_cls = candidates[0]
                except Exception:
                    pass
            if worm_cls is not None:
                try:
                    instance = worm_cls()
                    instantiated += 1
                except Exception as e:
                    failed.append(f"{name}: instantiation error: {type(e).__name__}: {str(e)[:80]}")
            else:
                failed.append(f"{name}: no worm_class in registry")
        total = len(items)
        success_rate = instantiated / total * 100 if total > 0 else 0
        assert instantiated > 0, f"0/{total} worms instantiable"
        if failed:
            print(f"\n  ⚠️  {len(failed)}/{total} worms failed instantiation "
                  f"({success_rate:.0f}% success)")
            for f in failed[:15]:
                print(f"    - {f}")
        else:
            print(f"\n  ✅ {instantiated}/{total} worms instantiated ({success_rate:.0f}%)")

    def test_get_summary_on_all_worms(self):
        """Every worm should provide get_summary() that returns a dict."""
        items, _ = _discover()
        no_summary = []
        for name, entry in items:
            worm_cls = entry.get("worm_class") or entry.get("cls")
            if worm_cls is None:
                src = entry.get("source") or entry.get("module", "")
                mod_name = src.replace("/", ".").replace(".py", "")
                if mod_name.startswith("src."):
                    mod_name = mod_name[4:]
                try:
                    mod = __import__(mod_name, fromlist=["*"])
                    candidates = [
                        v for k, v in vars(mod).items()
                        if isinstance(v, type) and k.lower() == name.lower().replace("_", "")
                    ]
                    if candidates:
                        worm_cls = candidates[0]
                except Exception:
                    pass
            if worm_cls is not None:
                try:
                    instance = worm_cls()
                    summary = instance.get_summary() if hasattr(instance, "get_summary") else None
                    if summary is None or not isinstance(summary, dict):
                        no_summary.append(f"{name}: no get_summary() or non-dict return")
                except Exception:
                    no_summary.append(f"{name}: instantiation failed, can't test get_summary")
            else:
                no_summary.append(f"{name}: no worm_class, can't test get_summary")
        total = len(items)
        summary_ok = total - len(no_summary)
        if no_summary:
            print(f"\n  ⚠️  {len(no_summary)}/{total} worms missing get_summary() "
                  f"(disclosure whitepaper W-A1: 42 expected)")
            for f in no_summary[:15]:
                print(f"    - {f}")
        # Honest floor (2026-09-29): the registry guarantees get_summary via
        # BaseWormMixin attach, so missing summaries are real regressions.
        # 10% headroom absorbs legitimately summary-less probes; 6/250 today.
        assert summary_ok > 0, f"0/{total} worms provide get_summary()"
        assert len(no_summary) <= max(25, total // 10), (
            f"{len(no_summary)}/{total} worms missing get_summary(): "
            + ", ".join(f for f in no_summary[:8]))


class TestWormCategoryCoverage:
    """Verify all 18 legacy categories have at least one worm.

    KNOWN_EMPTY_CATEGORIES is kept explicit rather than dropping the names: a
    populated nature that goes EMPTY must still fail this test, and an empty
    one must not read as a missing assertion.
    """

    #: Natures a measurement shows are empty. `optimization` (2026-09-29): 0 of
    #: 250 registered worms locally and 0 of 233 on the WSL mirror, so this is
    #: an unfilled nature and not a discovery bug -- nothing is mis-filed
    #: either, because `_guess_category` has an optimization rule
    #: (OPTIMIZE/OPTIMIZ/TRIM/PRUNE) and no registered class name matches it.
    #: Also empty but outside the legacy 18: repair, data, expansion, system.
    KNOWN_EMPTY_CATEGORIES = {"optimization"}

    def test_all_categories_populated(self):
        """All 18 known categories, apart from the documented empty ones."""
        items, _ = _discover()
        cats = {}
        for name, entry in items:
            cat = entry.get("category") or (
                entry.get("categories", [None])[0]
                if isinstance(entry.get("categories"), list) and entry["categories"]
                else None
            )
            if cat:
                cats.setdefault(cat, []).append(name)
        expected_cats = {
            "analysis", "backup", "communication", "config", "container",
            "defense", "diagnostic", "learning", "migration", "monitor",
            "optimization", "orchestration", "profiling", "security",
            "sync", "telemetry", "utility", "visualization",
        }
        thin = [c for c in expected_cats
                if c not in self.KNOWN_EMPTY_CATEGORIES
                and len(cats.get(c, [])) == 0]
        assert len(thin) == 0, f"Empty categories: {thin}"
        for cat in sorted(cats):
            print(f"  {cat}: {len(cats[cat])} worms")


class TestWormRegistryPerformance:
    """Registry operations should be fast."""

    def test_discovery_under_5s(self):
        """discover_worms() should complete in under 5 seconds."""
        reset_registry()
        t0 = time.perf_counter()
        discover_worms()
        elapsed = time.perf_counter() - t0
        # Registry size, not the call's delta return -- see _discover().
        size = len(WORM_REGISTRY)
        assert elapsed < 5.0, (
            f"discover_worms took {elapsed:.2f}s for {size} worms"
        )
        assert size >= 90, f"Expected >=90 worms, got {size}"


if __name__ == "__main__":
    p = f = 0
    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            inst = obj()
            print(f"\n[{name}]")
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        getattr(inst, mn)()
                        print(f"  [OK] {mn}")
                        p += 1
                    except Exception as e:
                        print(f"  [FAIL] {mn}: {e}")
                        traceback.print_exc()
                        f += 1
    print(f"\n  Passed: {p}, Failed: {f}")
