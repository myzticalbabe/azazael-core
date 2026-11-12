"""Unit tests for worm_registry.py — validation, thread safety, registration.

Per all inference models' production audit: testing is the #1 gap.
This file provides >80% coverage of worm_registry.py public APIs.
"""
import sys
import os
import threading
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.worm_registry import (
    register_worm,
    WORM_REGISTRY,
    _validate_registry_params,
    _VALID_CATEGORIES,
    _get_correlation_id,
    _log_event,
    get_worms_by_category,
    get_all_categories,
    get_unspawned_worms,
    mark_spawned,
    reset_registry,
    discover_worms,
    SpawnDiversityEngine,
    BaseWormMixin,
)


# ═══════════════════════════════════════════════════════════════════════
# _validate_registry_params — Input validation (per ALL 5 models)
# ═══════════════════════════════════════════════════════════════════════

def test_valid_category():
    """All valid categories should pass validation."""
    for cat in _VALID_CATEGORIES:
        _validate_registry_params(cat, 5, 30.0, None)
        _validate_registry_params(cat, 1, 0.1, None)
        _validate_registry_params(cat, 10, 3600.0, None)


def test_invalid_category():
    """Invalid category should raise ValueError."""
    try:
        _validate_registry_params("not_a_real_category", 5, 30.0, None)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_invalid_priority():
    """Priority outside 1-10 should raise ValueError."""
    for bad in (0, -1, 11, 100):
        try:
            _validate_registry_params("utility", bad, 30.0, None)
            assert False, f"Should have raised ValueError for priority={bad}"
        except ValueError:
            pass


def test_invalid_priority_type():
    """Non-int priority should raise ValueError."""
    try:
        _validate_registry_params("utility", 5.5, 30.0, None)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_invalid_cooldown():
    """Non-positive cooldown should raise ValueError."""
    for bad in (0, -1, -0.1):
        try:
            _validate_registry_params("utility", 5, bad, None)
            assert False, f"Should have raised ValueError for cooldown={bad}"
        except ValueError:
            pass


def test_invalid_tags_type():
    """Non-set tags should raise ValueError."""
    try:
        _validate_registry_params("utility", 5, 30.0, ["tag1", "tag2"])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_tags_with_dangerous_chars():
    """Tags with dangerous characters should raise ValueError."""
    for bad_tag in ('<script>', 'tag"quote', "tag'quote", 'cmd;rm', 'pipe|'):
        try:
            _validate_registry_params("utility", 5, 30.0, {bad_tag})
            assert False, f"Should have raised ValueError for tag={bad_tag}"
        except ValueError:
            pass


def test_valid_tags():
    """Valid tags should pass validation."""
    _validate_registry_params("utility", 5, 30.0, {"networking", "security", "v2"})
    _validate_registry_params("utility", 5, 30.0, set())
    _validate_registry_params("utility", 5, 30.0, None)


# ═══════════════════════════════════════════════════════════════════════
# register_worm decorator — Registration
# ═══════════════════════════════════════════════════════════════════════

def setup_function():
    reset_registry()


def teardown_function():
    """Restore the full registry after tests that wipe it.

    reset_registry() clears the GLOBAL WORM_REGISTRY and clears the
    _discovery_done flag. Without restoration, later test modules in the
    same pytest process see a wiped or partially refilled registry.
    Re-running discovery here reloads from the pickle
    cache in <0.2s and makes the suite order-independent.
    """
    discover_worms()


def test_register_basic():
    """Basic worm registration should populate WORM_REGISTRY."""
    @register_worm(category="diagnostic", priority=7, cooldown_seconds=30.0,
                   description="Test worm")
    class TestWorm:
        VERSION = "1.0.0"
    
    assert "TestWorm" in WORM_REGISTRY
    info = WORM_REGISTRY["TestWorm"]
    assert info["category"] == "diagnostic"
    assert info["priority"] == 7
    assert info["cooldown_seconds"] == 30.0
    assert info["spawn_count"] == 0
    assert info["class"] is TestWorm


def test_register_with_tags():
    """Registration with tags should store them."""
    @register_worm(category="security", priority=9, cooldown_seconds=60.0,
                   tags={"vuln-scan", "network"})
    class SecurityWorm:
        VERSION = "1.0.0"
    
    info = WORM_REGISTRY["SecurityWorm"]
    assert info["tags"] == {"vuln-scan", "network"}


def test_register_invalid_class():
    """Non-class argument should raise TypeError."""
    try:
        register_worm()(lambda: None)
        assert False, "Should have raised TypeError"
    except TypeError:
        pass


def test_register_default_values():
    """Default values should be applied correctly."""
    @register_worm()
    class DefaultWorm:
        VERSION = "1.0.0"
    
    info = WORM_REGISTRY["DefaultWorm"]
    assert info["category"] == "utility"
    assert info["priority"] == 5
    assert info["cooldown_seconds"] == 30.0


# ═══════════════════════════════════════════════════════════════════════
# _get_correlation_id — Thread-local correlation IDs
# ═══════════════════════════════════════════════════════════════════════

def test_correlation_id_unique():
    """Each thread should get a unique correlation ID."""
    ids = set()
    lock = threading.Lock()
    
    def _get_id():
        cid = _get_correlation_id()
        with lock:
            ids.add(cid)
    
    threads = [threading.Thread(target=_get_id) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    assert len(ids) == 10, f"Expected 10 unique IDs, got {len(ids)}"


def test_correlation_id_persistent():
    """Same thread should get the same ID on repeated calls."""
    cid1 = _get_correlation_id()
    cid2 = _get_correlation_id()
    assert cid1 == cid2


# ═══════════════════════════════════════════════════════════════════════
# get_worms_by_category — Thread-safe registry reads
# ═══════════════════════════════════════════════════════════════════════

def test_get_worms_by_category():
    """Should return only worms in the specified category."""
    reset_registry()
    
    @register_worm(category="diagnostic", priority=5, cooldown_seconds=30.0)
    class DiagWorm:
        VERSION = "1.0.0"
    
    @register_worm(category="monitor", priority=5, cooldown_seconds=30.0)
    class MonWorm:
        VERSION = "1.0.0"
    
    diag_worms = get_worms_by_category("diagnostic")
    assert "DiagWorm" in diag_worms
    assert "MonWorm" not in diag_worms


def test_get_all_categories():
    """Should return all unique categories."""
    reset_registry()
    
    @register_worm(category="diagnostic", priority=5, cooldown_seconds=30.0)
    class CatAWorm:
        VERSION = "1.0.0"
    
    @register_worm(category="monitor", priority=5, cooldown_seconds=30.0)
    class CatBWorm:
        VERSION = "1.0.0"
    
    cats = get_all_categories()
    assert "diagnostic" in cats
    assert "monitor" in cats


def test_get_unspawned_worms():
    """Newly registered worms should appear as unspawned."""
    reset_registry()
    
    @register_worm(category="utility", priority=5, cooldown_seconds=30.0)
    class FreshWorm:
        VERSION = "1.0.0"
    
    unspawned = get_unspawned_worms()
    assert "FreshWorm" in unspawned


# ═══════════════════════════════════════════════════════════════════════
# mark_spawned — Spawn tracking
# ═══════════════════════════════════════════════════════════════════════

def test_mark_spawned_increments():
    """mark_spawned should increment spawn_count."""
    reset_registry()
    
    @register_worm(category="utility", priority=5, cooldown_seconds=30.0)
    class SpawnWorm:
        VERSION = "1.0.0"
    
    assert WORM_REGISTRY["SpawnWorm"]["spawn_count"] == 0
    mark_spawned("SpawnWorm")
    assert WORM_REGISTRY["SpawnWorm"]["spawn_count"] == 1
    mark_spawned("SpawnWorm")
    assert WORM_REGISTRY["SpawnWorm"]["spawn_count"] == 2


def test_mark_spawned_sets_timestamp():
    """mark_spawned should set last_spawned to a timestamp."""
    reset_registry()
    
    @register_worm(category="utility", priority=5, cooldown_seconds=30.0)
    class StampWorm:
        VERSION = "1.0.0"
    
    assert WORM_REGISTRY["StampWorm"]["last_spawned"] is None
    mark_spawned("StampWorm")
    assert WORM_REGISTRY["StampWorm"]["last_spawned"] is not None


# ═══════════════════════════════════════════════════════════════════════
# SpawnDiversityEngine — Spawn diversity
# ═══════════════════════════════════════════════════════════════════════

def test_pick_next_spawn():
    """Should return a spawn recommendation with type and category."""
    reset_registry()
    
    @register_worm(category="diagnostic", priority=7, cooldown_seconds=30.0)
    class DivWorm:
        VERSION = "1.0.0"
    
    sde = SpawnDiversityEngine()
    pick = sde.pick_next_spawn()
    assert pick is not None
    assert "type" in pick
    assert "category" in pick
    assert pick["type"] == "DivWorm"
    assert pick["category"] == "diagnostic"


def test_pick_next_spawn_empty_registry():
    """Empty registry should return None."""
    reset_registry()
    sde = SpawnDiversityEngine()
    pick = sde.pick_next_spawn()
    assert pick is None


def test_staleness_report():
    """Staleness report should list worms and their spawn counts."""
    reset_registry()
    
    @register_worm(category="utility", priority=5, cooldown_seconds=30.0)
    class StaleWorm:
        VERSION = "1.0.0"
    
    sde = SpawnDiversityEngine()
    report = sde.get_staleness_report()
    assert len(report) > 0
    assert any(w["type"] == "StaleWorm" for w in report)


def test_category_rotation():
    """Get category rotation order."""
    reset_registry()
    
    @register_worm(category="diagnostic", priority=5, cooldown_seconds=30.0)
    class RotWorm:
        VERSION = "1.0.0"
    
    sde = SpawnDiversityEngine()
    rotation = sde.get_category_rotation_order()
    assert "diagnostic" in rotation


# ═══════════════════════════════════════════════════════════════════════
# BaseWormMixin — get_summary and get_status
# ═══════════════════════════════════════════════════════════════════════

def test_base_worm_mixin_get_summary_no_schema():
    """BaseWormMixin without explicit schema should return basic info."""
    class SimpleWorm(BaseWormMixin):
        VERSION = "1.0.0"
    
    w = SimpleWorm()
    summary = w.get_summary()
    assert summary["success"] is True
    assert summary["result"]["class"] == "SimpleWorm"
    assert summary["result"]["version"] == "1.0.0"


def test_base_worm_mixin_get_status():
    """get_status should return class info and schema details."""
    class StatusWorm(BaseWormMixin):
        VERSION = "2.0.0"
    
    w = StatusWorm()
    status = w.get_status()
    assert status["class"] == "StatusWorm"
    assert status["version"] == "2.0.0"


# ═══════════════════════════════════════════════════════════════════════
# Thread safety — Concurrent registry operations
# ═══════════════════════════════════════════════════════════════════════

def test_concurrent_registration():
    """Multiple threads should be able to register worms concurrently."""
    reset_registry()
    errors = []
    lock = threading.Lock()
    
    def _register(i):
        try:
            ns = {"register_worm": register_worm}
            exec(f"""
@register_worm(category="utility", priority=5, cooldown_seconds=30.0)
class ThreadWorm{i}:
    VERSION = "1.0.0"
""", ns)
        except Exception as e:
            with lock:
                errors.append(str(e))
    
    threads = [threading.Thread(target=_register, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    assert len(errors) == 0, f"Errors: {errors}"
    assert len(WORM_REGISTRY) >= 20


def test_concurrent_registry_reads():
    """Multiple threads should be able to read WORM_REGISTRY concurrently."""
    reset_registry()
    
    @register_worm(category="diagnostic", priority=7, cooldown_seconds=30.0)
    class ReadTestWorm:
        VERSION = "1.0.0"
    
    results = []
    lock = threading.Lock()
    
    def _read():
        cats = get_all_categories()
        worms = get_worms_by_category("diagnostic")
        unspawned = get_unspawned_worms()
        with lock:
            results.append((cats, len(worms), len(unspawned)))
    
    threads = [threading.Thread(target=_read) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    assert len(results) == 20
    for cats, wcount, ucount in results:
        assert "diagnostic" in cats
        assert wcount >= 1


def test_every_registered_class_can_report_its_state():
    """The registry's summary contract holds for every entry (2026-09-21).

    Measured before the fix: 174 of 218 registered classes had no `get_summary`
    at all, so `tests/test_worm_smoke.py` could only report `returned NoneType`.
    The registry now attaches `BaseWormMixin.get_summary` to a class that lacks
    one, which is the only single place that can cover 145 unrelated
    `object` subclasses.
    """
    discover_worms()
    missing = [n for n, e in WORM_REGISTRY.items()
               if not callable(getattr(e.get("class"), "get_summary", None))]
    assert not missing, f"{len(missing)} registered classes cannot be summarized: {missing[:10]}"


def test_summary_provenance_is_recorded():
    """A default summary must be distinguishable from a declared one.

    Without this, "every worm can be summarized" would go green while 179 organs
    reported an identical metadata stub, and the difference between reporting
    real state and satisfying a signature would be invisible in the ledger.
    """
    discover_worms()
    sources = {e.get("summary_source") for e in WORM_REGISTRY.values()}
    assert sources <= {"declared", "default", "unavailable"}, (
        f"unexpected summary_source values: {sources}"
    )
    declared = [n for n, e in WORM_REGISTRY.items()
                if e.get("summary_source") == "declared"]
    assert len(declared) >= 20, (
        f"only {len(declared)} worms declare their own get_summary — the default "
        f"is meant for classes that have no state to report, not the fleet"
    )


def test_default_summary_is_side_effect_free():
    """The attached default must not invoke worm work.

    `tests/test_worm_smoke.py` skips RLTrainLoop and ScheduledTrainingWorm because
    calling into them is expensive. A default that reached for `run()`/`execute()`
    through `_DISCOVERY_CANDIDATES` would have turned 174 fast failures into 174
    10s timeouts, so the metadata branch is the contract, not an implementation
    detail.
    """
    from CORE.worm_registry import BaseWormMixin

    class _Probe:  # no schema, no side effects available to call
        def run(self):
            raise AssertionError("the default summary must not call run()")

    _Probe.get_summary = BaseWormMixin.get_summary
    _Probe.__summary_method__ = ""
    _Probe.__summary_fields__ = []
    summary = _Probe().get_summary()
    assert isinstance(summary, dict)
    assert summary["result"]["class"] == "_Probe"


if __name__ == "__main__":
    # Run all tests manually
    import inspect
    failures = 0
    tests = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            tests += 1
            try:
                setup = globals().get("setup_function")
                if setup:
                    setup()
                fn()
                print(f"  PASS {name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{tests - failures}/{tests} tests passed")
    sys.exit(1 if failures > 0 else 0)
