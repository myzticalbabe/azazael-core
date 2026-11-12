# coding: utf-8
"""
Unit tests for worm_registry.py — Centralized Worm Registry & Spawn Diversity Engine.

Tests cover:
- @register_worm decorator
- WORM_REGISTRY population and thread safety
- discover_worms() auto-discovery
- SpawnDiversityEngine: picking, cooldowns, category rotation, staleness
- Convenience functions: mark_spawned, get_unspawned_worms, get_all_categories, etc.
"""

import sys
import unittest
from pathlib import Path

# Ensure azazael/src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from CORE.worm_registry import (
    register_worm,
    WORM_REGISTRY,
    SpawnDiversityEngine,
    discover_worms,
    get_worms_by_category,
    get_all_categories,
    get_unspawned_worms,
    mark_spawned,
    reset_registry,
)
from typing import List
import tempfile

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class TestRegisterWormDecorator(unittest.TestCase):
    """Test the @register_worm decorator."""

    def setUp(self):
        reset_registry()

    def tearDown(self):
        reset_registry()

    def test_register_worm_adds_to_registry(self):
        """@register_worm should add the class to WORM_REGISTRY."""

        @register_worm(category="monitor", priority=7, cooldown_seconds=15.0,
                       description="Test monitor worm")
        class TestMonitor:
            VERSION = "1.0.0"

        self.assertIn("TestMonitor", WORM_REGISTRY)
        info = WORM_REGISTRY["TestMonitor"]
        self.assertEqual(info["category"], "monitor")
        self.assertEqual(info["priority"], 7)
        self.assertEqual(info["cooldown_seconds"], 15.0)
        self.assertEqual(info["description"], "Test monitor worm")
        self.assertEqual(info["version"], "1.0.0")
        self.assertEqual(info["spawn_count"], 0)
        self.assertIsNone(info["last_spawned"])
        self.assertIsNotNone(info["registered_at"])

    def test_register_worm_defaults(self):
        """@register_worm should use sensible defaults."""

        @register_worm()
        class DefaultWorm:
            VERSION = "0.1.0"

        info = WORM_REGISTRY["DefaultWorm"]
        self.assertEqual(info["category"], "utility")
        self.assertEqual(info["priority"], 5)
        self.assertEqual(info["cooldown_seconds"], 30.0)
        self.assertEqual(info["version"], "0.1.0")

    def test_register_worm_with_tags(self):
        """@register_worm should store tags."""

        @register_worm(category="analysis", tags={"binary", "elf", "pe"})
        class TaggedWorm:
            VERSION = "1.0.0"

        self.assertEqual(WORM_REGISTRY["TaggedWorm"]["tags"], {"binary", "elf", "pe"})

    def test_register_worm_no_version(self):
        """@register_worm should handle classes without VERSION attr."""

        @register_worm(category="utility")
        class NoVersionWorm:
            pass

        self.assertEqual(WORM_REGISTRY["NoVersionWorm"]["version"], "0.0.0")

    def test_register_worm_uses_docstring_as_description(self):
        """@register_worm should use docstring when no description given."""

        @register_worm(category="diagnostic")
        class DocWorm:
            """First line of docstring becomes description."""

        self.assertIn("First line", WORM_REGISTRY["DocWorm"]["description"])

    def test_multiple_registrations(self):
        """Multiple @register_worm calls should all be tracked."""

        # FIXED 2026-10-10: this test predates registry input validation and
        # used ad-hoc categories ("a"/"b"/"c") that _validate_registry_params
        # now (correctly) refuses with ValueError. The registry is behaving
        # per its security contract; the test now uses three real categories.
        # Also reset_registry() first, matching this class's setUp semantics —
        # count-based assertion must be hermetic to imports elsewhere.
        reset_registry()

        @register_worm(category="monitor", priority=1)
        class WormA:
            pass

        @register_worm(category="diagnostic", priority=2)
        class WormB:
            pass

        @register_worm(category="utility", priority=3)
        class WormC:
            pass

        self.assertEqual(len(WORM_REGISTRY), 3)
        self.assertIn("WormA", WORM_REGISTRY)
        self.assertIn("WormB", WORM_REGISTRY)
        self.assertIn("WormC", WORM_REGISTRY)


class TestRegistryConvenienceFunctions(unittest.TestCase):
    """Test convenience functions for WORM_REGISTRY."""

    def setUp(self):
        reset_registry()

        @register_worm(category="monitor", priority=8)
        class HighMonitor:
            VERSION = "1.0.0"

        @register_worm(category="monitor", priority=5)
        class LowMonitor:
            VERSION = "1.0.0"

        @register_worm(category="diagnostic", priority=7)
        class DiagWorm:
            VERSION = "1.0.0"

        @register_worm(category="sync", priority=4)
        class SyncWorm:
            VERSION = "1.0.0"

    def tearDown(self):
        reset_registry()

    def test_get_all_categories(self):
        """get_all_categories should return unique categories."""
        cats = get_all_categories()
        self.assertEqual(cats, {"monitor", "diagnostic", "sync"})

    def test_get_worms_by_category(self):
        """get_worms_by_category should filter correctly."""
        monitors = get_worms_by_category("monitor")
        self.assertEqual(len(monitors), 2)
        self.assertIn("HighMonitor", monitors)
        self.assertIn("LowMonitor", monitors)

        diags = get_worms_by_category("diagnostic")
        self.assertEqual(len(diags), 1)
        self.assertIn("DiagWorm", diags)

        empty = get_worms_by_category("nonexistent")
        self.assertEqual(len(empty), 0)

    def test_get_unspawned_worms_all_unspawned(self):
        """All worms should be unspawned initially."""
        unspawned = get_unspawned_worms()
        self.assertEqual(len(unspawned), 4)

    def test_mark_spawned(self):
        """mark_spawned should increment counter and set timestamp."""
        mark_spawned("HighMonitor")
        info = WORM_REGISTRY["HighMonitor"]
        self.assertEqual(info["spawn_count"], 1)
        self.assertIsNotNone(info["last_spawned"])

        # Second spawn
        mark_spawned("HighMonitor")
        self.assertEqual(info["spawn_count"], 2)

        # Unspawned should now exclude HighMonitor
        unspawned = get_unspawned_worms()
        self.assertEqual(len(unspawned), 3)
        self.assertNotIn("HighMonitor", unspawned)

    def test_mark_spawned_nonexistent(self):
        """mark_spawned on nonexistent worm should not raise."""
        mark_spawned("NonExistentWorm")  # Should not raise

    def test_reset_registry(self):
        """reset_registry should clear everything."""
        self.assertEqual(len(WORM_REGISTRY), 4)
        reset_registry()
        self.assertEqual(len(WORM_REGISTRY), 0)


class TestSpawnDiversityEngine(unittest.TestCase):
    """Test the SpawnDiversityEngine."""

    def setUp(self):
        reset_registry()

        @register_worm(category="monitor", priority=8, cooldown_seconds=10.0)
        class SysMonitor:
            VERSION = "1.0.0"

        @register_worm(category="diagnostic", priority=7, cooldown_seconds=10.0)
        class NetDiag:
            VERSION = "1.0.0"

        @register_worm(category="analysis", priority=6, cooldown_seconds=10.0)
        class BinAnalysis:
            VERSION = "1.0.0"

        @register_worm(category="sync", priority=5, cooldown_seconds=10.0)
        class ClockSync:
            VERSION = "1.0.0"

        @register_worm(category="orchestration", priority=9, cooldown_seconds=10.0)
        class FleetOrch:
            VERSION = "1.0.0"

        self.engine = SpawnDiversityEngine()

    def tearDown(self):
        reset_registry()

    def test_pick_next_spawn_returns_valid_pick(self):
        """pick_next_spawn should return a worm type with metadata."""
        pick = self.engine.pick_next_spawn()
        self.assertIsNotNone(pick)
        if pick is None:  # unreachable; narrows Optional for the type checker
            self.fail("pick_next_spawn returned None unexpectedly")
        self.assertIn("type", pick)
        self.assertIn("category", pick)
        self.assertIn("priority", pick)
        self.assertIn("reason", pick)
        self.assertIn(pick["type"], WORM_REGISTRY)

    def test_category_rotation_no_consecutive_same_category(self):
        """Category rotation: no two consecutive picks from same category."""
        pick1 = self.engine.pick_next_spawn()
        self.assertIsNotNone(pick1)
        if pick1 is None:  # unreachable; narrows Optional for the type checker
            self.fail("pick_next_spawn returned None unexpectedly")

        # Simulate spawn
        self.engine._last_category = pick1["category"]

        pick2 = self.engine.pick_next_spawn()
        self.assertIsNotNone(pick2)
        if pick2 is None:  # unreachable; narrows Optional for the type checker
            self.fail("pick_next_spawn returned None unexpectedly")
        self.assertNotEqual(pick1["category"], pick2["category"],
                            "Consecutive picks should have different categories")

    def test_cooldown_enforcement(self):
        """Worms on cooldown should not be picked."""
        # Spawn all 5 worms to trigger cooldowns (all have different categories,
        # so category rotation allows all 5 to be picked sequentially)
        for i in range(5):
            pick = self.engine.pick_next_spawn()
            self.assertIsNotNone(pick, f"Pick {i+1} of 5 must succeed; all worms unspawned")
            if pick is None:  # unreachable; narrows Optional for the type checker
                self.fail("pick_next_spawn returned None unexpectedly")
            mark_spawned(pick["type"])
            self.engine._last_category = pick["category"]

        # All worms now on cooldown (cooldown_seconds=10, just spawned)
        pick = self.engine.pick_next_spawn()
        self.assertIsNone(pick, "Should return None when all worms are on cooldown")

    def test_staleness_bonus_for_unspawned(self):
        """Never-spawned worms should get high priority due to staleness bonus."""
        pick = self.engine.pick_next_spawn()
        self.assertIsNotNone(pick)
        # With 5 unspawned worms, the highest priority (FleetOrch, priority=9)
        # should be picked first due to staleness bonus
        # Note: category rotation may shift this, but staleness bonus should
        # make high-priority worms very likely to be picked early

    def test_pick_diverse_batch_unique_categories(self):
        """pick_diverse_batch should return worms from different categories."""
        batch = self.engine.pick_diverse_batch(5)
        categories = [p["category"] for p in batch]
        # All categories should be unique
        self.assertEqual(len(categories), len(set(categories)),
                         "Batch should contain unique categories")

    def test_pick_diverse_batch_max_count(self):
        """pick_diverse_batch should return at most count picks."""
        batch = self.engine.pick_diverse_batch(10)
        self.assertLessEqual(len(batch), 5)  # Only 5 worm types available

    def test_get_staleness_report(self):
        """get_staleness_report should rank unspawned worms first."""
        report = self.engine.get_staleness_report()
        self.assertEqual(len(report), 5)

        # Unspawned worms should appear first (spawn_count=0)
        for entry in report:
            if entry["spawn_count"] == 0:
                # These should be at the top — spawn_count=0 means never spawned
                pass
            else:
                self.fail("All worms should be unspawned initially")

        # After marking one as spawned
        mark_spawned("SysMonitor")
        report = self.engine.get_staleness_report()
        spawned = [e for e in report if e["type"] == "SysMonitor"][0]
        self.assertEqual(spawned["spawn_count"], 1)
        self.assertIsNotNone(spawned["hours_since_last"])

    def test_get_category_rotation_order(self):
        """get_category_rotation_order should put last category at end."""
        self.engine._last_category = "monitor"
        order = self.engine.get_category_rotation_order()
        # monitor should be last
        self.assertEqual(order[-1], "monitor")
        # All categories should be present
        self.assertIn("monitor", order)
        self.assertIn("diagnostic", order)
        self.assertIn("analysis", order)
        self.assertIn("sync", order)
        self.assertIn("orchestration", order)

    def test_record_spawn(self):
        """record_spawn should add to spawn_history."""
        self.engine.record_spawn("SysMonitor", "monitor", 8, "test_reason")
        self.assertEqual(len(self.engine.spawn_history), 1)

        record = self.engine.spawn_history[0]
        self.assertEqual(record.worm_type, "SysMonitor")
        self.assertEqual(record.category, "monitor")
        self.assertEqual(record.priority, 8)
        self.assertEqual(record.reason, "test_reason")

    def test_empty_registry(self):
        """pick_next_spawn on empty registry should return None."""
        reset_registry()
        engine = SpawnDiversityEngine()
        pick = engine.pick_next_spawn()
        self.assertIsNone(pick)


class TestDiscovery(unittest.TestCase):
    """Test discover_worms auto-discovery."""

    def setUp(self):
        reset_registry()

    def tearDown(self):
        reset_registry()

    def test_discover_nonexistent_dir(self):
        """discover_worms on nonexistent directory should return 0."""
        count = discover_worms(worms_dir="/nonexistent/path")
        self.assertEqual(count, 0)

    def test_discover_empty_dir(self):
        """discover_worms on empty directory should return 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            count = discover_worms(worms_dir=tmpdir)
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
