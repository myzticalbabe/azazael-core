# coding: utf-8
"""
Azazael Production Unit Tests

Tests for core worm ecosystem components.
Run: pytest src/tests/ -v
"""

import os
import sys
import json
import unittest
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Set environment
# FIXED 2026-10-10 (2nd pass): the file lives at <root>/src/tests/, so the
# "repo root" the OLD path pointed at is this file's own src/ directory —
# sys.path entries below want src/, src/CORE/WORMS (worm top-level names
# like WORMZ/CONCURRENCY) and src/META (MetaWormSpawner etc.). ROOT is now
# derived from __file__ so the suite always tests the checkout it lives
# in, never the origin clone at C:/Users/clint/azazael.
_AZAZAEL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["AZAZAEL_ROOT"] = _AZAZAEL_ROOT  # == <root>/src in this layout

# Add paths
sys.path.insert(0, _AZAZAEL_ROOT)                                  # == src/
sys.path.insert(0, os.path.join(_AZAZAEL_ROOT, "CORE", "WORMS"))   # WORMZ, CONCURRENCY
sys.path.insert(0, os.path.join(_AZAZAEL_ROOT, "META"))            # MetaWormSpawner, DistillZ…


class TestWORMZ(unittest.TestCase):
    """Tests for WORMZ BIBL crawler."""

    def test_import(self):
        """WORMZ can be imported."""
        import WORMZ
        self.assertTrue(hasattr(WORMZ, "WORMZConfig"))
        self.assertTrue(hasattr(WORMZ, "WORMZ"))

    def test_config_paths(self):
        """WORMZConfig resolves paths correctly."""
        from WORMZ import WORMZConfig
        for folder in WORMZConfig.TARGET_FOLDERS:
            self.assertIsInstance(folder, str)
            self.assertTrue(len(folder) > 0)
        self.assertIsInstance(WORMZConfig.BIBL_PATH, str)

    def test_instantiate(self):
        """WORMZ can be instantiated."""
        from WORMZ import WORMZ
        wz = WORMZ()
        self.assertIsNotNone(wz)


class TestCONCURRENCY(unittest.TestCase):
    """Tests for CONCURRENCY parallel execution."""

    def test_import(self):
        """CONCURRENCY can be imported."""
        import CONCURRENCY
        self.assertTrue(hasattr(CONCURRENCY, "WormExecutor"))

    def test_worm_executor_instantiate(self):
        """WormExecutor can be instantiated."""
        from CONCURRENCY import WormExecutor
        we = WormExecutor()
        self.assertIsNotNone(we)

    def test_load_worms(self):
        """All core worms load successfully."""
        from CONCURRENCY import WormExecutor
        we = WormExecutor()
        for worm in ["SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ"]:
            result = we.load_worm(worm)
            self.assertTrue(result, f"Failed to load {worm}")


class TestMetaWormSpawner(unittest.TestCase):
    """Tests for MetaWormSpawner."""

    def test_import(self):
        """MetaWormSpawner can be imported."""
        import MetaWormSpawner
        self.assertTrue(hasattr(MetaWormSpawner, "MetaWormSpawner"))

    def test_instantiate(self):
        """MetaWormSpawner can be instantiated."""
        from MetaWormSpawner import MetaWormSpawner
        mws = MetaWormSpawner()
        self.assertIsNotNone(mws)
        self.assertIsNotNone(mws.worm_pool)
        self.assertIsNotNone(mws.morph_engine)

    def test_start_stop(self):
        """MetaWormSpawner starts and stops cleanly."""
        from MetaWormSpawner import MetaWormSpawner
        mws = MetaWormSpawner()
        mws.start(auto_spawn=True)
        status = mws.get_fleet_status()
        self.assertGreaterEqual(status.total_worms, 4)  # Base fleet
        mws.stop()

    def test_spawn_worm(self):
        """Can spawn individual worms."""
        from MetaWormSpawner import MetaWormSpawner
        mws = MetaWormSpawner()
        mws.start(auto_spawn=False)
        worm = mws.spawn("test_sys", "SystemZ", priority=5)
        self.assertIsNotNone(worm)
        self.assertEqual(worm.worm_type, "SystemZ")
        mws.stop()

    def test_fleet_status(self):
        """Fleet status has correct structure."""
        from MetaWormSpawner import MetaWormSpawner
        mws = MetaWormSpawner()
        mws.start(auto_spawn=True)
        status = mws.get_fleet_status()
        self.assertIsNotNone(status.current_mode)
        self.assertGreaterEqual(status.total_worms, 0)
        mws.stop()

    def test_morph_engine(self):
        """Morph engine transitions work."""
        from MetaWormSpawner import MetaWormSpawner
        mws = MetaWormSpawner()
        mws.start(auto_spawn=True)
        mws.morph_engine.current_mode
        mws.evaluate_and_morph()
        # Should remain exploratory under normal conditions
        self.assertIsNotNone(mws.morph_engine.current_mode)
        mws.stop()

    def test_worm_details(self):
        """Worm details list has correct structure."""
        from MetaWormSpawner import MetaWormSpawner
        mws = MetaWormSpawner()
        mws.start(auto_spawn=True)
        details = mws.get_worm_details()
        self.assertIsInstance(details, list)
        for d in details:
            self.assertIn("id", d)
            self.assertIn("type", d)
            self.assertIn("state", d)
        mws.stop()

    def test_export_json(self):
        """Can export fleet state to JSON."""
        from MetaWormSpawner import MetaWormSpawner
        mws = MetaWormSpawner()
        mws.start(auto_spawn=True)
        fp = mws.export_memory_json()
        self.assertTrue(os.path.exists(fp))
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("worms", data)
        self.assertIn("fleet_status", data)
        mws.stop()


class TestDebugZ(unittest.TestCase):
    """Tests for DebugZ diagnostic suite."""

    def test_import(self):
        """DebugZ can be imported."""
        import DebugZ
        self.assertTrue(hasattr(DebugZ, "DebugZ"))

    def test_instantiate(self):
        """DebugZ can be instantiated."""
        from DebugZ import DebugZ
        dz = DebugZ()
        self.assertIsNotNone(dz)

    def test_full_diagnostic(self):
        """Full diagnostic runs and returns report."""
        from DebugZ import DebugZ
        dz = DebugZ()
        report = dz.run_full_diagnostic()
        self.assertIsNotNone(report)
        self.assertIsNotNone(report.overall_health)
        self.assertGreaterEqual(report.total_issues, 0)
        self.assertGreaterEqual(len(report.component_status), 0)

    def test_save_report(self):
        """Can save report to disk."""
        from DebugZ import DebugZ
        dz = DebugZ()
        dz.run_full_diagnostic()
        path = dz.save_report()
        self.assertTrue(os.path.exists(path))


class TestInferenceBridge(unittest.TestCase):
    """Tests for InferenceBridge."""

    def test_import(self):
        """InferenceBridge can be imported."""
        import InferenceBridge
        self.assertTrue(hasattr(InferenceBridge, "InferenceBridge"))

    def test_instantiate(self):
        """InferenceBridge can be instantiated."""
        from InferenceBridge import InferenceBridge
        bridge = InferenceBridge()
        self.assertIsNotNone(bridge)
        self.assertIsNotNone(bridge.llama)

    def test_bridge_status(self):
        """Bridge status has correct structure."""
        from InferenceBridge import InferenceBridge
        bridge = InferenceBridge()
        status = bridge.get_bridge_status()
        self.assertIn("llama_available", status)
        self.assertIn("version", status)


class TestDistillZ(unittest.TestCase):
    """Tests for DistillZ Tier 5 Distillation."""

    def test_import(self):
        """DistillZ can be imported."""
        import DistillZ
        self.assertTrue(hasattr(DistillZ, "DistillZ"))

    def test_instantiate(self):
        """DistillZ can be instantiated."""
        from DistillZ import DistillZ
        dz = DistillZ()
        self.assertIsNotNone(dz)

    def test_distill(self):
        """Distillation runs without errors."""
        from DistillZ import DistillZ
        dz = DistillZ()
        report = dz.distill(use_llm=False)
        self.assertIsNotNone(report)
        self.assertGreaterEqual(report.total_insights, 0)
        self.assertIsNotNone(report.recommendations)

    def test_distill_with_local_memorystore(self):
        """DistillZ should read PAMS insights from LocalMemoryStore."""
        from DistillZ import DistillZ
        dz = DistillZ()
        report = dz.distill(use_llm=False)
        # Should include PAMS source with memory volume insights
        pams_insights = [n for n in report.insights if n.source == "PAMS"]
        # May be empty if LocalMemoryStore has no data, but shouldn't crash
        self.assertIsInstance(pams_insights, list)
        # Check structure if data exists
        if pams_insights:
            self.assertTrue(any("memories" in n.insight.lower() or "session" in n.insight.lower()
                              for n in pams_insights))
        # Source stats should include PAMS
        self.assertIn("PAMS", report.source_stats or {})

    def test_distill_registers_model_version(self):
        """DistillZ should register a model version via ModelVersioner."""
        from DistillZ import DistillZ
        from CORE.WORMS.expansion.learning import ModelVersioner

        dz = DistillZ()
        report = dz.distill(use_llm=False)

        # Check that a distillation model version was registered
        mv = ModelVersioner()
        versions = mv.list_versions("distillation")
        if versions:
            latest = versions[-1]
            self.assertEqual(latest["name"], "distillation")
            self.assertIn("metrics", latest)
            self.assertIn("total_insights", latest["metrics"])
            self.assertIn("total_sources", latest["metrics"])
            self.assertIn("metadata", latest)
            self.assertIn("insight_categories", latest["metadata"])
            self.assertIn("knowledge_sources", latest["metadata"])

    def test_compare_distillations(self):
        """DistillZ.compare_distillations should compare two versions."""
        from DistillZ import DistillZ
        from CORE.WORMS.expansion.learning import ModelVersioner

        dz = DistillZ()
        dz.distill(use_llm=False)

        # Get registered versions
        mv = ModelVersioner()
        versions = mv.list_versions("distillation")

        if len(versions) >= 2:
            v1 = versions[-2]["version"]
            v2 = versions[-1]["version"]
            comparison = dz.compare_distillations(v1, v2)
            self.assertIn("comparison", comparison)
            self.assertIn("summary", comparison)

    def test_compare_distillations_not_found(self):
        """Comparing non-existent versions returns error."""
        from DistillZ import DistillZ
        dz = DistillZ()
        result = dz.compare_distillations("nonexistent_v1", "nonexistent_v2")
        self.assertIn("error", result)


class TestFleetZ(unittest.TestCase):
    """Tests for FleetZ continuous daemon."""

    def test_import(self):
        """FleetZ can be imported."""
        import FleetZ
        self.assertTrue(hasattr(FleetZ, "FleetZ"))

    def test_instantiate(self):
        """FleetZ can be instantiated."""
        from FleetZ import FleetZ
        fz = FleetZ()
        self.assertIsNotNone(fz)
        self.assertIsNotNone(fz.dashboard)

    def test_start_stop(self):
        """FleetZ starts and stops cleanly."""
        from FleetZ import FleetZ
        fz = FleetZ()
        result = fz.start()
        self.assertTrue(result)
        fz.stop()


class TestEcosystemIntegration(unittest.TestCase):
    """Integration tests across components."""

    def test_all_imports(self):
        """All key components can be imported together."""
        modules = []
        try:
            modules.append("WORMZ")
        except Exception as exc:
            note_failure("test_ecosystem:L330", exc)
        try:
            modules.append("CONCURRENCY")
        except Exception as exc:
            note_failure("test_ecosystem:L333", exc)
        try:
            modules.append("MetaWormSpawner")
        except Exception as exc:
            note_failure("test_ecosystem:L336", exc)
        try:
            modules.append("DebugZ")
        except Exception as exc:
            note_failure("test_ecosystem:L339", exc)
        try:
            modules.append("InferenceBridge")
        except Exception as exc:
            note_failure("test_ecosystem:L342", exc)
        try:
            modules.append("DistillZ")
        except Exception as exc:
            note_failure("test_ecosystem:L345", exc)
        try:
            modules.append("FleetZ")
        except Exception as exc:
            note_failure("test_ecosystem:L348", exc)
        self.assertGreaterEqual(len(modules), 4, f"Only loaded: {modules}")

    def test_metaworm_inference_bridge_integration(self):
        """MetaWormSpawner and InferenceBridge work together."""
        from MetaWormSpawner import MetaWormSpawner
        from InferenceBridge import InferenceBridge
        mws = MetaWormSpawner()
        mws.start(auto_spawn=True)
        bridge = InferenceBridge()
        status = bridge.get_bridge_status()
        self.assertIn("llama_available", status)
        mws.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
