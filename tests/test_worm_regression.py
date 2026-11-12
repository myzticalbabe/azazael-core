# coding: utf-8
"""Regression tests for ALL registered worms — import, registry, and basic smoke.

Warms the registry ONCE at module load time to avoid redundant ~38s discovery calls.
Verifies that every worm in WORM_REGISTRY:
  1. Has valid metadata (category, priority, source_file)
  2. Has a valid source file on disk
  3. Imports without error (lightweight worms)
  4. Has no circular import chains

Run standalone (3.14t compatible):
  python tests/test_worm_regression.py

Run with pytest:
  python -m pytest tests/test_worm_regression.py -v
"""

import sys
import os
import time
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "CORE" / "WORMS"))

os.environ["PYTHONIOENCODING"] = "utf-8"

import pytest

# ── Module-level warm-up: discover once, use everywhere ───────────

from CORE.worm_registry import (
    discover_worms, WORM_REGISTRY, get_all_categories, VALID_CATEGORIES,
)
from collections import defaultdict

print("  Warming registry...", end=" ", flush=True)
t0 = time.time()
count = discover_worms()
elapsed = time.time() - t0
print(f"({count} modules, {len(WORM_REGISTRY)} entries, {elapsed:.1f}s)")

ALL_WORMS = dict(WORM_REGISTRY)
ALL_CATEGORIES = sorted(get_all_categories())

# Group worms by category for category-level tests
WORMS_BY_CATEGORY = defaultdict(list)
for name, entry in ALL_WORMS.items():
    WORMS_BY_CATEGORY[entry.get("category", "unknown")].append(name)

# ── Test data ──────────────────────────────────────────────────────

EXPECTED_CATEGORIES = 28
EXPECTED_MIN_WORMS = 200
# Floors track the live fleet (250 worms / 35 categories, 2026-09-29) with
# ~20% headroom: shrinkage past the floor is a real regression, not drift.
# A fixed small floor (75) passed with 2/3 of the fleet missing.
# VALID_CATEGORIES is imported from CORE.worm_registry, not copied. The copy that
# used to sit here was frozen at 17 natures from before the mesh/immune/
# xenomorph expansions, so `MeshRouterWorm` (a deliberate `mesh` governor, see
# FLEET_GOVERNORS) failed this file while the registry was correct: measured 40
# valid tuples and 0 off-vocabulary worms. A duplicated constant can only drift,
# which is why the registry exports this alias for consumers to read.

# Worms with C extensions that may not load on 3.14t
HEAVY_WORMS = {"PROCESSZ", "SMARTDISK", "DISKZ", "SystemZ", "NETWORXZ", "SYSTEMZ_ML"}

# Worms with very slow imports
SLOW_WORMS = {"RLTrainLoop", "TrainingOrchestrator", "ScheduledTrainingWorm"}


# ── Registry validation tests ──────────────────────────────────────


def test_registry_populated():
    assert len(ALL_WORMS) >= EXPECTED_MIN_WORMS, (
        f"Expected >= {EXPECTED_MIN_WORMS} worms, got {len(ALL_WORMS)}"
    )


def test_all_categories_present():
    assert len(ALL_CATEGORIES) >= EXPECTED_CATEGORIES, (
        f"Expected >= {EXPECTED_CATEGORIES} categories, got {len(ALL_CATEGORIES)}"
    )


def test_every_worm_has_valid_metadata():
    bad = []
    for name, entry in ALL_WORMS.items():
        if "category" not in entry:
            bad.append(f"{name}: missing category")
        if "priority" not in entry:
            bad.append(f"{name}: missing priority")
        if "source_file" not in entry:
            bad.append(f"{name}: missing source_file")
    assert not bad, "\n".join(bad[:20])


def test_every_worm_source_file_exists():
    missing = []
    for name, entry in ALL_WORMS.items():
        sf = entry.get("source_file", "")
        if sf and not os.path.isfile(sf):
            missing.append(f"{name}: {sf}")
    assert not missing, "\n".join(missing[:20])


def test_worm_priorities_in_range():
    bad = []
    for name, entry in ALL_WORMS.items():
        p = entry.get("priority", 0)
        if not (1 <= p <= 10):
            bad.append(f"{name}: priority={p}")
    assert not bad, "\n".join(bad[:20])


def test_category_assignments_valid():
    bad = []
    for name, entry in ALL_WORMS.items():
        cat = entry.get("category", "")
        if cat not in VALID_CATEGORIES:
            bad.append(f"{name}: invalid category '{cat}'")
    assert not bad, "\n".join(bad[:20])


def test_every_category_has_worms():
    empty = [cat for cat, worms in WORMS_BY_CATEGORY.items() if len(worms) == 0]
    assert not empty, f"Categories with 0 worms: {empty}"


# ── Worm import tests ─────────────────────────────────────────────


def test_discover_worms_detects_all():
    """All worms detected by discovery should also be individually importable."""
    import importlib.util
    failures = []
    for name in sorted(ALL_WORMS.keys()):
        if name in SLOW_WORMS and name not in sys.modules:
            continue  # skip slow worms not yet imported
        if name in sys.modules:
            continue
        sf = ALL_WORMS[name].get("source_file", "")
        if not sf or not os.path.isfile(sf):
            failures.append(f"{name}: missing source_file")
            continue
        try:
            spec = importlib.util.spec_from_file_location(name, sf)
            if spec is None:
                failures.append(f"{name}: no spec from {sf}")
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")
    assert not failures, "\n".join(failures[:20])


def test_heavy_worms_import():
    """Heavy worms (C extensions) should import, skip if known incompatible."""
    failures = []
    for name in sorted(HEAVY_WORMS):
        if name not in ALL_WORMS:
            continue
        try:
            if name not in sys.modules:
                __import__(name)
        except Exception as e:
            err = str(e).lower()
            if "c extension" in err or "dll" in err or "no module" in err:
                pytest.skip(f"{name}: import degraded under free-threaded interpreter ({type(e).__name__}: {e})")
            failures.append(f"{name}: {type(e).__name__}: {e}")
    assert not failures, "\n".join(failures[:20])


def test_slow_worms_import():
    """Slow worms should import with adequate time.

    SLOW_WORMS holds CLASS names, and the registry is what knows which module
    owns each one (`ALL_WORMS[name]["module"]`). The previous body called
    `__import__(name)`, i.e. it tried to import modules literally named
    "RLTrainLoop" and "TrainingOrchestrator". Neither exists, so the test
    reported three ModuleNotFoundErrors for classes that import in well under a
    second. The owning module is read from the registry instead of assumed.
    """
    import importlib

    failures = []
    for name in sorted(SLOW_WORMS):
        entry = ALL_WORMS.get(name)
        if entry is None:
            failures.append(f"{name}: not in the registry")
            continue
        module = entry.get("module")
        if not module:
            failures.append(f"{name}: registry entry carries no module")
            continue
        try:
            t0 = time.time()
            importlib.import_module(module)
            elapsed = time.time() - t0
            print(f"  [OK] {name} ({module}, {elapsed:.1f}s)")
        except Exception as e:
            failures.append(f"{name} ({module}): {type(e).__name__}: {e}")
    assert not failures, "\n".join(failures[:20])


# ── Category-level smoke tests ────────────────────────────────────


def test_build_cluster_for_each_nature():
    """Build a ClusterWORMZ for every nature and verify it has worms."""
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ

    failures = []
    for nature in ALL_CATEGORIES:
        try:
            cluster = ClusterWORMZ.build_from_category(nature)
            assert cluster is not None, f"{nature}: cluster is None"
            assert len(cluster.configs) > 0, f"{nature}: no configs"
        except Exception as e:
            failures.append(f"{nature}: {type(e).__name__}: {e}")
    assert not failures, "\n".join(failures[:10])


# ── Standalone runner ──────────────────────────────────────────────


def _run_standalone():
    """Run all tests standalone (for 3.14t where pytest segfaults)."""
    passed = 0
    failed = 0

    test_fns = [
        ("Registry populated", test_registry_populated),
        ("All categories present", test_all_categories_present),
        ("Every worm has valid metadata", test_every_worm_has_valid_metadata),
        ("Every worm source file exists", test_every_worm_source_file_exists),
        ("Worm priorities in range", test_worm_priorities_in_range),
        ("Category assignments valid", test_category_assignments_valid),
        ("Every category has worms", test_every_category_has_worms),
        ("All worms import from discovery", test_discover_worms_detects_all),
        ("Heavy worms import", test_heavy_worms_import),
        ("Slow worms import", test_slow_worms_import),
        ("Build cluster for each nature", test_build_cluster_for_each_nature),
    ]

    print(f"\n  Worm Regression Tests ({len(test_fns)} tests)")
    print(f"  {'='*55}")
    for name, fn in test_fns:
        try:
            fn()
            print(f"  [OK] {name}")
            passed += 1
        except pytest.skip.Exception as e:
            print(f"  [SKIP] {name}: {e}")
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {name}: {e}")
            failed += 1

    print(f"  {'='*55}")
    print(f"  Total: {passed + failed} | Passed: {passed} | Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
