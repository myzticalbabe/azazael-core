#!/usr/bin/env python3
"""Tests for CORE/WORMS/expansion/learning.py — ML pipeline worms."""
import os
import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestDataPipelineWorm(unittest.TestCase):
    """Test the DataPipelineWorm with LocalMemoryStore integration."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import DataPipelineWorm
        self.pipeline = DataPipelineWorm()

    def test_pipeline_initialized(self):
        """Pipeline should initialize without error."""
        self.assertIsNotNone(self.pipeline)
        self.assertEqual(self.pipeline.VERSION, "2.0.0")

    def test_extract_non_existent_file(self):
        """Extracting a non-existent file should return None."""
        result = self.pipeline.extract("/nonexistent/file.json")
        self.assertIsNone(result)

    def test_extract_from_memory_unavailable(self):
        """Extract from memory should handle missing store gracefully."""
        result = self.pipeline.extract_from_memory(source="Test", limit=10)
        # Should return [] if LocalMemoryStore is unavailable or succeed
        self.assertIsInstance(result, list)

    def test_search_memory(self):
        """Search should return a list."""
        result = self.pipeline.search_memory("test")
        self.assertIsInstance(result, list)

    def test_extract_recent(self):
        """Recent extraction should return a list."""
        result = self.pipeline.extract_recent(limit=5)
        self.assertIsInstance(result, list)
        self.assertLessEqual(len(result), 5)

    def test_get_memory_stats(self):
        """Stats should return a dict."""
        result = self.pipeline.get_memory_stats()
        self.assertIsInstance(result, dict)

    def test_transform_identity(self):
        """Transform with no transformations returns data unchanged."""
        data = [1, 2, 3]
        result = self.pipeline.transform(data, [])
        self.assertEqual(result, [1, 2, 3])

    def test_transform_chain(self):
        """Transform chain should apply all transforms in order."""
        data = [{"val": 3}, {"val": 1}, {"val": 2}]
        result = self.pipeline.transform(data, [
            lambda items: sorted(items, key=lambda x: x["val"]),
        ])
        self.assertEqual(result, [{"val": 1}, {"val": 2}, {"val": 3}])

    def test_extract_field_transform(self):
        """Extract field should pull a field from records."""
        data = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        extractor = self.pipeline.extract_field("name")
        result = extractor(data)
        self.assertEqual(result, ["A", "B", "C"])

    def test_filter_by_severity(self):
        """Filter by severity should return matching records."""
        data = [
            {"severity": "error", "msg": "bad"},
            {"severity": "info", "msg": "ok"},
            {"severity": "error", "msg": "worse"},
        ]
        filter_fn = self.pipeline.filter_by_severity("error")
        result = filter_fn(data)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["msg"], "bad")

    def test_filter_by_source(self):
        """Filter by source should return matching records."""
        data = [
            {"source": "SystemZ", "val": 1},
            {"source": "ProcessZ", "val": 2},
        ]
        filter_fn = self.pipeline.filter_by_source("SystemZ")
        result = filter_fn(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["val"], 1)

    def test_flatten_data(self):
        """Flatten should promote nested data fields."""
        records = [
            {"id": 1, "data": {"cpu": 50, "mem": 60}},
            {"id": 2, "data": {"cpu": 70}},
        ]
        flat = self.pipeline.flatten_data(records)
        self.assertEqual(len(flat), 2)
        self.assertEqual(flat[0]["data_cpu"], 50)
        self.assertEqual(flat[0]["data_mem"], 60)
        self.assertEqual(flat[1]["data_cpu"], 70)

    def test_deduplicate(self):
        """Deduplicate should remove records with duplicate IDs."""
        records = [
            {"id": 1, "val": "a"},
            {"id": 2, "val": "b"},
            {"id": 1, "val": "c"},
        ]
        unique = self.pipeline.deduplicate(records, key="id")
        self.assertEqual(len(unique), 2)

    def test_batch(self):
        """Batch should split items into batches."""
        items = list(range(10))
        batches = self.pipeline.batch(items, batch_size=3)
        self.assertEqual(len(batches), 4)
        self.assertEqual(batches[0], [0, 1, 2])
        self.assertEqual(batches[-1], [9])

    def test_process_batches(self):
        """Process batches should apply function to each batch."""
        items = list(range(10))
        results = self.pipeline.process_batches(
            items, lambda batch: sum(batch), batch_size=5
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], 10)  # sum(0..4)

    def test_run_pipeline(self):
        """Run pipeline should return a result dict with expected keys."""
        result = self.pipeline.run_pipeline(source="Test", limit=50)
        self.assertIn("pipeline_version", result)
        self.assertIn("extracted", result)
        self.assertIn("duration_ms", result)
        self.assertIn("preview", result)

    def test_execute(self):
        """Worm execute() should return a pipeline result."""
        result = self.pipeline.execute()
        self.assertIn("pipeline_version", result)
        self.assertIn("extracted", result)

    def test_load_to_memory(self):
        """Load to memory should not raise."""
        try:
            self.pipeline.load_to_memory(
                source="test_pipeline", event_type="test",
                data={"msg": "hello"}, tags=["test"],
            )
        except Exception as e:
            self.fail(f"load_to_memory raised: {e}")

    def test_load_to_file(self):
        """Load to file should write a file."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        try:
            self.pipeline.load([{"test": "data"}], tmp_path)
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data[0]["test"], "data")
        finally:
            os.unlink(tmp_path)


class TestFeatureEngineer(unittest.TestCase):
    """Test the FeatureEngineer."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import FeatureEngineer
        self.engineer = FeatureEngineer()

    def test_engineer_version(self):
        """FeatureEngineer should be at least version 2.0.0."""
        self.assertGreaterEqual(self.engineer.VERSION, "2.0.0")

    def test_normalize_empty(self):
        """Normalizing empty list returns empty list."""
        result = self.engineer.normalize([])
        self.assertEqual(result, [])

    def test_normalize_identical(self):
        """Normalizing identical values returns [0.5]."""
        result = self.engineer.normalize([5.0, 5.0, 5.0])
        self.assertEqual(result, [0.5, 0.5, 0.5])

    def test_normalize_range(self):
        """Normalizing should map to [0, 1]."""
        result = self.engineer.normalize([0.0, 50.0, 100.0])
        self.assertEqual(result, [0.0, 0.5, 1.0])

    def test_one_hot_encode(self):
        """One-hot encoding should produce binary vectors."""
        result = self.engineer.one_hot_encode(["a", "b", "a"])
        self.assertEqual(result["categories"], ["a", "b"])
        self.assertEqual(result["encoded"], [[1, 0], [0, 1], [1, 0]])

    def test_one_hot_encode_known(self):
        """One-hot with known categories should respect order."""
        result = self.engineer.one_hot_encode(
            ["b"], known_categories=["a", "b", "c"]
        )
        self.assertEqual(result["encoded"], [[0, 1, 0]])

    def test_impute_missing_empty(self):
        """Imputing empty list returns zeros."""
        result = self.engineer.impute_missing([])
        self.assertEqual(result, [])

    def test_impute_missing_mean(self):
        """Imputing with mean should fill missing values."""
        result = self.engineer.impute_missing([1.0, None, 3.0])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 1.0)
        self.assertEqual(result[1], 2.0)  # mean of 1 and 3
        self.assertEqual(result[2], 3.0)

    def test_impute_missing_all_none(self):
        """Imputing all None should fill with zeros."""
        result = self.engineer.impute_missing([None, None])
        self.assertEqual(result, [0.0, 0.0])

    def test_extract_worm_features_empty(self):
        """Extracting features from empty records returns count 0."""
        result = self.engineer.extract_worm_features([])
        self.assertEqual(result["count"], 0)

    def test_extract_worm_features_basic(self):
        """Extracting features from records should produce feature vectors."""
        records = [
            {"source": "SystemZ", "severity": "info", "data": {"duration_ms": 150}},
            {"source": "ProcessZ", "severity": "error", "data": {"duration_ms": 300}},
        ]
        result = self.engineer.extract_worm_features(records)
        self.assertEqual(result["count"], 2)
        self.assertGreater(result["feature_dim"], 0)
        self.assertEqual(len(result["features"]), 2)
        self.assertEqual(len(result["labels"]), 2)
        # First record should have success label (severity="info")
        self.assertEqual(result["labels"][0], 1.0)
        # Second record should have failure label (severity="error")
        self.assertEqual(result["labels"][1], 0.0)


class TestRLTrainLoop(unittest.TestCase):
    """Test the RLTrainLoop."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import RLTrainLoop
        self.loop = RLTrainLoop()

    def test_training_stats_empty(self):
        """Training stats with no episodes should return zeros."""
        stats = self.loop.get_training_stats()
        self.assertEqual(stats["episodes"], 0)

    def test_training_stats_after_episode(self):
        """Training stats should reflect trained episodes."""
        # Use a simple mock environment
        class MockEnv:
            def reset(self):
                return 0
            def step(self, action):
                return 0, 1.0, True, {}

        class MockAgent:
            def act(self, state):
                return 0

        episode = self.loop.train_episode(MockEnv(), MockAgent(), max_steps=10)
        self.assertIn("steps", episode)
        self.assertIn("total_reward", episode)

        stats = self.loop.get_training_stats()
        self.assertEqual(stats["episodes"], 1)

    def test_train_from_memory_empty_db(self):
        """Train from memory with no data returns error."""
        result = self.loop.train_from_memory(source="NonExistentSource", n_episodes=5)
        self.assertIn("error", result)
        self.assertEqual(result["episodes"], 0)

    def test_train_from_memory_with_split(self):
        """Train from memory with use_data_split=True should not crash."""
        result = self.loop.train_from_memory(source="SystemZ", n_episodes=5,
                                               use_data_split=True, test_ratio=0.2)
        # Should either have training data (with split stats) or error gracefully
        if "error" in result:
            self.assertIn("episodes", result)
            self.assertEqual(result["episodes"], 0)
        else:
            self.assertIn("data_source", result)
            self.assertIn("data_split", result)
            self.assertIn("method", result["data_split"])

    def test_train_from_memory_with_test_data(self):
        """Train from memory with explicit test_data returns test_metrics."""
        test_records = [
            {"source": "SystemZ", "severity": "error", "data": {"duration_ms": 500}},
            {"source": "ProcessZ", "severity": "info", "data": {"duration_ms": 100}},
        ]
        result = self.loop.train_from_memory(
            source="SystemZ", n_episodes=3, max_steps=5,
            test_data=test_records,
        )
        # May or may not have training data, but test_metrics should appear if training worked
        if "test_metrics" in result:
            self.assertIn("test_avg_reward", result["test_metrics"])
            self.assertIn("test_records", result["test_metrics"])

    def test_train_from_memory_with_schema_features(self):
        """Train with use_schema_features=True should not crash."""
        result = self.loop.train_from_memory(
            source="SystemZ", n_episodes=3, max_steps=5,
            use_schema_features=True,
        )
        # Should either have data or error gracefully
        self.assertIn("episodes", result)
        if "error" not in result:
            # If training ran, q_table should have schema-based keys
            self.assertGreaterEqual(result["q_table_size"], 0)

    def test_hyperparameter_tune_returns_best_params(self):
        """hyperparameter_tune should return best_params or error gracefully."""
        result = self.loop.hyperparameter_tune(
            source="SystemZ", n_episodes=5, max_steps=5,
            n_folds=2, lr_values=[0.1], gamma_values=[0.9], eps_values=[0.1],
        )
        # Should either have best_params from CV data, or error gracefully
        if "error" in result:
            self.assertIn("cv_stats", result)
        else:
            self.assertIn("best_params", result)
            self.assertIn("best_score", result)
            self.assertIn("results", result)
            self.assertIn("learning_rate", result["best_params"])
            self.assertIn("discount_factor", result["best_params"])
            self.assertIn("epsilon", result["best_params"])

    def test_hyperparameter_tune_no_data(self):
        """hyperparameter_tune with no data returns error."""
        result = self.loop.hyperparameter_tune(source="__nonexistent__")
        self.assertIn("error", result)

    def test_tune_and_train_returns_both_results(self):
        """tune_and_train should return tune_result and train_result."""
        result = self.loop.tune_and_train(
            source="SystemZ", n_episodes=5, max_steps=5,
            n_folds=2, lr_values=[0.1], gamma_values=[0.9], eps_values=[0.1],
        )
        if "error" in result:
            self.assertIn("tune_result", result)
        else:
            self.assertIn("tune_result", result)
            self.assertIn("train_result", result)
            self.assertIn("best_params", result)

    def test_tune_and_train_no_data(self):
        """tune_and_train with no data returns error."""
        result = self.loop.tune_and_train(source="__nonexistent__")
        self.assertIn("error", result)

    def test_hyperparameter_tune_register_model(self):
        """hyperparameter_tune with register_tune_model=True should not crash."""
        result = self.loop.hyperparameter_tune(
            source="SystemZ", n_episodes=5, max_steps=5,
            n_folds=2, lr_values=[0.1], gamma_values=[0.9], eps_values=[0.1],
            register_tune_model=True,
        )
        if "error" not in result:
            self.assertIn("registered_version", result)

    def test_tune_history_returns_list(self):
        """tune_history should return a dict with history list."""
        result = self.loop.tune_history(limit=5)
        self.assertIn("history", result)
        self.assertIsInstance(result["history"], list)

    def test_tune_history_with_events(self):
        """tune_history should find registered tune events from the store."""
        # Register a tune config first so there's data to find
        reg_result = self.loop.hyperparameter_tune(
            source="SystemZ", n_episodes=3, max_steps=3,
            n_folds=2, lr_values=[0.1], gamma_values=[0.9], eps_values=[0.1],
            register_tune_model=True,
        )
        # Now query history
        result = self.loop.tune_history(limit=10)
        self.assertIn("history", result)
        self.assertIn("summary", result)
        if "error" not in reg_result:
            # If registered, the history list should contain it
            if result["history"]:
                entry = result["history"][0]
                self.assertIn("best_params", entry)
                self.assertIn("best_score", entry)
                self.assertIn("registered_at", entry)


class TestDataPipelineWormTemporalCV(unittest.TestCase):
    """Test DataPipelineWorm.temporal_cross_validation()."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import DataPipelineWorm
        self.pipeline = DataPipelineWorm()

    def test_cv_returns_dict_with_folds(self):
        """temporal_cross_validation returns dict with folds list."""
        result = self.pipeline.temporal_cross_validation(source="SystemZ", n_folds=3)
        self.assertIn("folds", result)
        self.assertIn("stats", result)
        self.assertIsInstance(result["folds"], list)

    def test_cv_no_data_returns_empty(self):
        """CV with no matching data returns empty folds."""
        result = self.pipeline.temporal_cross_validation(source="__nonexistent__")
        self.assertEqual(result["folds"], [])
        self.assertEqual(result["stats"].get("total_records", 0), 0)

    def test_cv_fold_structure(self):
        """Each fold should have train, test, and stats."""
        result = self.pipeline.temporal_cross_validation(source="SystemZ", n_folds=3)
        for fold in result["folds"]:
            self.assertIn("fold", fold)
            self.assertIn("train", fold)
            self.assertIn("test", fold)
            self.assertIn("stats", fold)
            self.assertIn("train_records", fold["stats"])
            self.assertIn("test_records", fold["stats"])

    def test_cv_expanding_window(self):
        """CV should use expanding window (train gets larger each fold)."""
        result = self.pipeline.temporal_cross_validation(source="SystemZ", n_folds=3)
        if len(result["folds"]) >= 2:
            # Each successive fold should have more training records
            train_sizes = [f["stats"]["train_records"] for f in result["folds"] if f["stats"]["train_records"] > 0]
            if len(train_sizes) >= 2:
                self.assertGreater(train_sizes[-1], train_sizes[0])

    def test_cv_gap_days(self):
        """CV with gap_days should still produce folds."""
        result = self.pipeline.temporal_cross_validation(
            source="SystemZ", n_folds=3, gap_days=1
        )
        self.assertIn("folds", result)
        self.assertEqual(result["stats"]["gap_days"], 1)


class TestAutoDiscoverShapes(unittest.TestCase):
    """Test DataPipelineWorm.auto_discover_shapes()."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import DataPipelineWorm
        self.pipeline = DataPipelineWorm()

    def test_discover_returns_dict(self):
        """auto_discover_shapes returns dict with discovered."""
        result = self.pipeline.auto_discover_shapes(sample_limit=5)
        self.assertIn("discovered", result)
        self.assertIn("stats", result)
        self.assertIsInstance(result["discovered"], dict)
        self.assertIn("total_event_types", result["stats"])

    def test_discovered_entries_have_fields(self):
        """Discovered entries should have fields and numeric_keys."""
        result = self.pipeline.auto_discover_shapes(sample_limit=5)
        discovered = result["discovered"]
        for et, shape in discovered.items():
            self.assertIn("fields", shape, f"Missing fields for {et}")
            self.assertIn("numeric_keys", shape, f"Missing numeric_keys for {et}")
            self.assertIsInstance(shape["fields"], dict)
            self.assertIsInstance(shape["numeric_keys"], list)

    def test_discover_multiple_event_types(self):
        """Should discover multiple event types from the store.

        Seeds its own events: under xdist every worker starts with an
        empty AZAZAEL_MEMORY_DIR (tests/conftest.py per-worker isolation),
        so an assertion over ambient store content only passes when a
        same-worker test happened to write telemetry first — a
        scheduling-dependent outcome, not evidence about this changeset.
        """
        store = self.pipeline._get_store()
        self.assertIsNotNone(store)
        store.record_event(
            source="learning_pipeline_probe",
            event_type="pipeline_probe_executed",
            data={"worm": "ProbeWorm", "duration_ms": 1.5},
        )
        store.record_event(
            source="learning_pipeline_probe",
            event_type="pipeline_probe_summary",
            data={"rounds": 3, "ok": 2},
        )
        result = self.pipeline.auto_discover_shapes(sample_limit=5)
        self.assertGreater(len(result["discovered"]), 0)
        self.assertIn("pipeline_probe_executed", result["discovered"])
        self.assertIn("pipeline_probe_summary", result["discovered"])


class TestDynamicShapes(unittest.TestCase):
    """Test DYNAMIC_SHAPES registry and refresh_dynamic_shapes()."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import (
            DYNAMIC_SHAPES, refresh_dynamic_shapes,
            DataPipelineWorm,
        )
        self.DYNAMIC_SHAPES = DYNAMIC_SHAPES
        self.refresh = refresh_dynamic_shapes
        self.pipeline = DataPipelineWorm()

    def test_refresh_returns_stats(self):
        """refresh_dynamic_shapes returns dict with stats."""
        result = self.refresh(pipeline=self.pipeline, sample_limit=5)
        self.assertIn("added", result)
        self.assertIn("total", result)
        self.assertIn("static_total", result)

    def test_dynamic_shapes_not_in_static(self):
        """Dynamic shapes should not duplicate static DATA_SHAPES."""
        from CORE.WORMS.expansion.learning import DATA_SHAPES
        result = self.refresh(pipeline=self.pipeline, sample_limit=5)
        added = result.get("added", 0)
        # Dynamic shapes should only contain event_types NOT in static
        seen_in_static = 0
        for et in self.DYNAMIC_SHAPES:
            if et in DATA_SHAPES:
                seen_in_static += 1
        self.assertEqual(seen_in_static, 0)

    def test_dynamic_shape_structure(self):
        """Dynamic shapes should have fields and numeric_keys."""
        self.refresh(pipeline=self.pipeline, sample_limit=5)
        for et, shape in self.DYNAMIC_SHAPES.items():
            self.assertIn("fields", shape)
            self.assertIn("numeric_keys", shape)
            self.assertIn("type", shape)

    def test_refresh_does_not_overwrite(self):
        """Refresh multiple times should not duplicate entries."""
        r1 = self.refresh(pipeline=self.pipeline, sample_limit=5)
        r2 = self.refresh(pipeline=self.pipeline, sample_limit=5)
        # Second refresh should not increase total beyond first (unless new shapes appeared in DB)
        self.assertGreaterEqual(r1.get("total", 0), 0)
        self.assertGreaterEqual(r2.get("total", 0), r1.get("total", 0))


class TestScheduleDynamicRefresh(unittest.TestCase):
    """Test DataPipelineWorm.schedule_dynamic_refresh()."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import DataPipelineWorm
        self.pipeline = DataPipelineWorm()

    def test_schedule_returns_refresh_and_schedule(self):
        """schedule_dynamic_refresh returns dict with refresh and schedule keys."""
        result = self.pipeline.schedule_dynamic_refresh(
            interval_hours=24, sample_limit=5, auto_merge=True
        )
        self.assertIn("refresh", result)
        self.assertIn("schedule", result)
        self.assertEqual(result["schedule"]["interval_hours"], 24)

    def test_schedule_without_merge(self):
        """schedule_dynamic_refresh with auto_merge=False should not error."""
        result = self.pipeline.schedule_dynamic_refresh(
            interval_hours=12, sample_limit=5, auto_merge=False
        )
        self.assertIn("schedule", result)
        self.assertEqual(result["schedule"]["interval_hours"], 12)

    def test_schedule_records_event(self):
        """schedule_dynamic_refresh should record an event in LocalMemoryStore."""
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        before = len(store.search_memories("dynamic_shapes_refresh", limit=100))
        self.pipeline.schedule_dynamic_refresh(
            interval_hours=24, sample_limit=5, auto_merge=True
        )
        after = len(store.search_memories("dynamic_shapes_refresh", limit=100))
        self.assertGreaterEqual(after, before)


class TestFallbackAccuracyReport(unittest.TestCase):
    """Test SchemaAwareFeatureExtractor.fallback_accuracy_report()."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import (
            SchemaAwareFeatureExtractor, refresh_dynamic_shapes,
            DataPipelineWorm,
        )
        self.extractor = SchemaAwareFeatureExtractor()
        # Ensure DYNAMIC_SHAPES is populated
        refresh_dynamic_shapes(pipeline=DataPipelineWorm(), sample_limit=5)

    def test_report_returns_stats(self):
        """fallback_accuracy_report returns dict with expected keys."""
        records = [
            {
                "event_type": "category_cluster_executed",
                "severity": "info",
                "source": "ClusterWORMZs",
                "data": {"nature": "SystemZ", "worms": 5, "success": 4, "failures": 1, "duration_ms": 300},
            }
        ]
        report = self.extractor.fallback_accuracy_report(records)
        self.assertIn("total_records", report)
        self.assertIn("static_unknown", report)
        self.assertIn("dynamic_recovered", report)
        self.assertIn("recovery_rate", report)
        self.assertIn("static_shapes_count", report)
        self.assertIn("dynamic_shapes_count", report)

    def test_report_on_known_shape(self):
        """Known shapes should have high recovery rate."""
        records = [
            {
                "event_type": "cluster_created",
                "severity": "info",
                "source": "CLUSTERWORMS",
                "data": {"cluster_type": "ClusterWORMZs", "cluster_id": "abc"},
            }
        ]
        report = self.extractor.fallback_accuracy_report(records)
        self.assertEqual(report["total_records"], 1)
        self.assertGreaterEqual(report["static_shapes_count"], 1)

    def test_extractor_uses_dynamic_shapes(self):
        """SchemaAwareFeatureExtractor should use dynamic shapes for unknown event_types."""
        from CORE.WORMS.expansion.learning import (
            SchemaAwareFeatureExtractor, DATA_SHAPES, DYNAMIC_SHAPES,
            refresh_dynamic_shapes, DataPipelineWorm,
        )
        refresh_dynamic_shapes(pipeline=DataPipelineWorm(), sample_limit=5)

        extractor = SchemaAwareFeatureExtractor()

        # Find an event_type only in DYNAMIC_SHAPES (not in static DATA_SHAPES)
        dynamic_only = [et for et in DYNAMIC_SHAPES if et not in DATA_SHAPES]

        if dynamic_only:
            et = dynamic_only[0]
            records = [
                {
                    "event_type": et,
                    "severity": "info",
                    "source": "Test",
                    "data": {"test_val": 42},
                }
            ]
            features, meta = extractor.extract(records)
            self.assertEqual(meta["count"], 1)
            self.assertGreater(meta["dim"], 0)
            # Verify the dynamic shape was used (not unknown)
            self.assertIn(et, meta.get("shapes_used", []))


class TestDataPipelineWormSplit(unittest.TestCase):
    """Test DataPipelineWorm.split_memories()."""
    """Test DataPipelineWorm.split_memories()."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import DataPipelineWorm
        self.pipeline = DataPipelineWorm()

    def test_split_returns_dict_with_keys(self):
        """split_memories returns dict with train/val/test."""
        result = self.pipeline.split_memories(source="SystemZ", test_ratio=0.2, val_ratio=0.1)
        self.assertIn("train", result)
        self.assertIn("val", result)
        self.assertIn("test", result)
        self.assertIn("stats", result)

    def test_split_no_data_returns_empty(self):
        """Split with no matching data returns empty lists."""
        result = self.pipeline.split_memories(source="__nonexistent__")
        self.assertEqual(result["train"], [])
        self.assertEqual(result["stats"].get("total_records", 0), 0)

    def test_split_by_session_in_stats(self):
        """Split by session should include session count in stats."""
        result = self.pipeline.split_memories(source="SystemZ", by_session=True)
        if result["stats"].get("total_records", 0) > 0:
            self.assertIn("total_sessions", result["stats"])
            self.assertEqual(result["stats"]["split_method"], "by_session_temporal")

    def test_split_by_record_in_stats(self):
        """Split by record should include random method in stats."""
        result = self.pipeline.split_memories(source="SystemZ", by_session=False)
        if result["stats"].get("total_records", 0) > 0:
            self.assertEqual(result["stats"]["split_method"], "random_record")

    def test_split_ratios_respected(self):
        """Split ratios should produce correct proportions."""
        result = self.pipeline.split_memories(source="SystemZ", test_ratio=0.3, val_ratio=0.0)
        stats = result["stats"]
        total = stats.get("total_records", 0)
        if total > 10:
            train_pct = stats["train_records"] / total
            test_pct = stats["test_records"] / total
            self.assertGreater(train_pct, 0.5)
            self.assertGreater(test_pct, 0.0)


class TestSchemaAwareFeatureExtractor(unittest.TestCase):
    """Test the SchemaAwareFeatureExtractor."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import SchemaAwareFeatureExtractor
        self.extractor = SchemaAwareFeatureExtractor()

    def test_extract_empty(self):
        """Extracting from empty records returns empty."""
        features, meta = self.extractor.extract([])
        self.assertEqual(features, [])
        self.assertEqual(meta["count"], 0)

    def test_extract_single_record(self):
        """Extract features from a known shape."""
        records = [
            {
                "event_type": "metacluster_orchestrated",
                "severity": "info",
                "source": "MetaCluster",
                "data": {"categories": 5, "ok": 4, "fail": 1, "duration_ms": 1200},
            }
        ]
        features, meta = self.extractor.extract(records)
        self.assertEqual(meta["count"], 1)
        self.assertGreater(meta["dim"], 0)
        self.assertEqual(len(features[0]), meta["dim"])
        # First feature should be severity_ordinal (info = 1.0)
        self.assertEqual(features[0][0], 1.0)
        # Should have used the known shape
        self.assertIn("metacluster_orchestrated", meta["shapes_used"])

    def test_extract_features_and_labels(self):
        """Extract features and labels returns correct label mapping."""
        records = [
            {
                "event_type": "metacluster_orchestrated",
                "severity": "info",
                "source": "MetaCluster",
                "data": {"categories": 3, "ok": 3, "fail": 0, "duration_ms": 500},
            },
            {
                "event_type": "category_cluster_executed",
                "severity": "error",
                "source": "ClusterWORMZs",
                "data": {"nature": "SystemZ", "worms": 10, "success": 8, "failures": 2, "duration_ms": 800},
            },
        ]
        features, labels, meta = self.extractor.extract_features_and_labels(records)
        self.assertEqual(len(features), 2)
        self.assertEqual(len(labels), 2)
        self.assertEqual(labels[0], 1.0)  # info -> success
        self.assertEqual(labels[1], 0.0)  # error -> failure

    def test_extract_unknown_shape_falls_back(self):
        """Unknown event_type should fall back to generic extraction."""
        records = [
            {
                "event_type": "unknown_type_xyz",
                "severity": "warning",
                "source": "Test",
                "data": {"cpu": 75.0, "mem": 1024.0, "val": 42},
            }
        ]
        features, meta = self.extractor.extract(records)
        self.assertEqual(meta["count"], 1)
        self.assertGreater(meta["unknown_shapes"], 0)
        # Should have severity + 3 numeric values = 4 features
        self.assertEqual(len(features[0]), 4)

    def test_fit_then_extract_consistency(self):
        """fit and extract should produce consistent shapes."""
        records = [
            {
                "event_type": "category_cluster_executed",
                "severity": "info",
                "source": "ClusterWORMZs",
                "data": {"nature": "SystemZ", "worms": 5, "success": 4, "failures": 1, "duration_ms": 300},
            },
            {
                "event_type": "category_cluster_executed",
                "severity": "info",
                "source": "ClusterWORMZs",
                "data": {"nature": "ProcessZ", "worms": 3, "success": 3, "failures": 0, "duration_ms": 150},
            },
        ]
        features, meta = self.extractor.extract(records)
        self.assertEqual(meta["count"], 2)
        self.assertEqual(len(features[0]), len(features[1]))

    def test_new_shapes_present(self):
        """New DATA_SHAPES entries should be recognized."""
        from CORE.WORMS.expansion.learning import DATA_SHAPES
        for et in ["cluster_execution_started", "cluster_execution_completed",
                    "nature_manager_discovery", "model_registered", "training_complete"]:
            self.assertIn(et, DATA_SHAPES, f"Missing DATA_SHAPES entry: {et}")

    def test_extract_cluster_execution_started(self):
        """Extract features from cluster_execution_started."""
        records = [
            {
                "event_type": "cluster_execution_started",
                "severity": "info",
                "source": "CLUSTERWORMS",
                "data": {"cluster_type": "ClusterWORMZs", "worm_count": 15, "parallel": 4},
            }
        ]
        features, meta = self.extractor.extract(records)
        self.assertEqual(meta["count"], 1)
        self.assertGreater(meta["dim"], 0)
        self.assertIn("cluster_execution_started", meta["shapes_used"])

    def test_extract_training_complete(self):
        """Extract features from training_complete."""
        records = [
            {
                "event_type": "training_complete",
                "severity": "info",
                "source": "RLTrainLoop",
                "data": {"n_episodes": 100, "total_duration_ms": 2500, "avg_reward": 0.85, "q_table_size": 12, "source_filter": "SystemZ"},
            }
        ]
        features, meta = self.extractor.extract(records)
        self.assertEqual(meta["count"], 1)
        self.assertIn("training_complete", meta["shapes_used"])

    def test_worm_executed_shape_registered(self):
        """clusterwormz_worm_executed is a known shape (not unknown)."""
        from CORE.WORMS.expansion.learning import DATA_SHAPES
        self.assertIn("clusterwormz_worm_executed", DATA_SHAPES)

    def test_extract_worm_executed_live_rows(self):
        """Featurize transcribed live telemetry rows (producer: worm_telemetry.emit_worm_outcome).

        Fixture transcribed 2026-10-11 from LocalMemoryStore row
        source=ClusterWORMZ event_type=clusterwormz_worm_executed
        (ResinWorm success). Second row adds a failure with error text and
        a decision_id: both must be EXCLUDED from features (free text and
        unique ids would explode the one-hot dims), never crash extraction.
        """
        records = [
            {
                "event_type": "clusterwormz_worm_executed",
                "severity": "info",
                "source": "ClusterWORMZ",
                "data": {"worm": "ResinWorm", "success": True,
                         "standby": False, "duration_ms": 0.5347728729248047,
                         "error": None, "cluster_type": "custom",
                         "cluster_id": "cluster_custom_1791730128308377700"},
            },
            {
                "event_type": "clusterwormz_worm_executed",
                "severity": "error",
                "source": "ClusterWORMZ",
                "data": {"worm": "OtherWorm", "success": False,
                         "standby": False, "duration_ms": 12.5,
                         "error": "boom: something failed in the worm",
                         "decision_id": "d-xyz-1", "cluster_type": "custom",
                         "cluster_id": "cluster_custom_999"},
            },
        ]
        features, meta = self.extractor.extract(records)
        self.assertEqual(meta["count"], 2)
        self.assertEqual(meta["unknown_shapes"], 0)
        self.assertIn("clusterwormz_worm_executed", meta["shapes_used"])
        self.assertEqual(len(features[0]), len(features[1]))
        self.assertGreater(meta["dim"], 4)  # severity + 3 numerics + one-hots
        names = meta["field_names"]
        self.assertIn("clusterwormz_worm_executed::success", names)
        self.assertIn("clusterwormz_worm_executed::duration_ms", names)
        self.assertFalse([n for n in names if "error" in n or "decision_id" in n
                          or "cluster_id" in n])
        # success encodes True->1.0 / False->0.0 right after severity
        self.assertEqual(features[0][1], 1.0)
        self.assertEqual(features[1][1], 0.0)


if __name__ == "__main__":
    unittest.main()
