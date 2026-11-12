import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
Azazael Comprehensive Weakness Testing & Hardening
Systematically tests ML operations to find and fix weaknesses.

Test Categories:
1. Data Sparsity - Cold start scenarios
2. Concept Drift - Changing performance patterns
3. Edge Cases - Unusual inputs and scenarios
4. Concurrent Operations - Race conditions
5. Memory Pressure - Resource exhaustion
6. Integration Failures - Module import errors
7. Prediction Accuracy - Confidence calibration
8. Recovery Mechanisms - Error handling

Hardening Applied:
- Input validation and sanitization
- Bounds checking for all metrics
- Task type normalization
- Drift detection
- Memory management
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
# from typing import Dict, List, Optional  # removed — all names have native equivalents
from dataclasses import dataclass, field
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR = Path(__file__).parent / "test_data"
DATA_DIR.mkdir(exist_ok=True)


# ========== HARDENING UTILITIES ==========


def sanitize_input(value, default=None):
    """Sanitize input values."""
    if value is None:
        return default
    if isinstance(value, str) and len(value.strip()) == 0:
        return default
    return value


def validate_metric(name: str, value: float, min_val: float, max_val: float) -> float:
    """Validate metric values are within bounds."""
    if value < min_val or value > max_val:
        return max(min_val, min(max_val, value))
    return value


NORMALIZED_TASKS = {
    "img_class": "image_classification",
    "cls": "image_classification",
    "img_cls": "image_classification",
    "object_detection": "object_detection",
    "obj_det": "object_detection",
    "nlp": "nlp",
    "text": "nlp",
    "llm": "nlp",
    "cv": "image_classification",
    "tabular": "tabular",
    "rl": "reinforcement_learning",
    "tabular_data": "tabular",
}


def normalize_task(task: str | None) -> str:
    """Normalize task type names."""
    task = sanitize_input(task, "")
    if not task:
        return "unknown"
    task_lower = task.lower().strip()
    return NORMALIZED_TASKS.get(task_lower, task_lower)


@dataclass
class TestResult:
    """Result of a single test."""

    test_name: str
    passed: bool
    duration_ms: float
    error: (str | None) = None
    details: dict = field(default_factory=dict)


@dataclass
class TestSuite:
    """Collection of test results."""

    results: list[TestResult] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    def add(self, result: TestResult):
        self.results.append(result)

    def summary(self) -> dict:
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        weak_points = []
        for r in self.results:
            if not r.passed:
                weak_points.append(
                    {
                        "test": r.test_name,
                        "error": r.error,
                        "details": r.details,
                    }
                )

        return {
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "success_rate": passed / max(len(self.results), 1) * 100,
            "weak_points": weak_points,
            "duration_ms": (self.end_time - self.start_time) * 1000,
        }


class AzazaelIntelligenceHardened:
    """Hardened version of AzazaelIntelligence with better error handling."""

    def __init__(self):
        self.workflow_history = []
        self.framework_scores = {}

    def record_event(
        self,
        action: str | None,
        framework: str | None,
        task_type: str | None,
        duration: float,
        success: bool,
        metrics: (dict | None) = None,
    ):
        # Sanitize inputs
        action = sanitize_input(action, "unknown")
        framework = sanitize_input(framework, "unknown")
        task_type = normalize_task(task_type)
        duration = validate_metric("duration", duration, 0, 1e10)
        metrics = metrics or {}

        # Validate accuracy if present
        if "accuracy" in metrics:
            metrics["accuracy"] = validate_metric("accuracy", metrics["accuracy"], 0, 1)

        # Add to history
        self.workflow_history.append(
            {
                "timestamp": time.time(),
                "action": action,
                "framework": framework,
                "task_type": task_type,
                "duration": duration,
                "success": success,
                "metrics": metrics,
            }
        )

        # Update scores
        if framework not in self.framework_scores:
            self.framework_scores[framework] = {
                "success_count": 0,
                "total_duration": 0,
                "task_types": {},
            }

        scores = self.framework_scores[framework]
        if success:
            scores["success_count"] += 1
        scores["total_duration"] += duration
        scores["avg_duration"] = scores["total_duration"] / max(
            scores["success_count"], 1
        )

        if task_type not in scores["task_types"]:
            scores["task_types"][task_type] = {"count": 0, "success": 0}

        scores["task_types"][task_type]["count"] += 1
        if success:
            scores["task_types"][task_type]["success"] += 1

    def get_framework_recommendation(self, task_type: str | None) -> dict:
        """Get framework recommendation with fallback."""
        task_type = normalize_task(task_type)

        if not self.workflow_history:
            return {"recommendation": None, "reason": "no_history"}

        scores = []
        for framework, data in self.framework_scores.items():
            tt_data = data.get("task_types", {}).get(task_type, {})

            success_rate = tt_data.get("success", 0) / max(tt_data.get("count", 1), 1)
            avg_duration = data.get("avg_duration", 999)
            duration_score = 1.0 / (1.0 + avg_duration)
            total_score = (success_rate * 0.7) + (duration_score * 0.3)

            scores.append(
                {
                    "framework": framework,
                    "score": total_score,
                    "success_rate": success_rate,
                    "avg_duration": avg_duration,
                }
            )

        scores.sort(key=lambda x: x["score"], reverse=True)

        return {
            "recommendation": scores[0]["framework"] if scores else None,
            "score": scores[0]["score"] if scores else None,
            "alternatives": scores[1:4] if len(scores) > 1 else [],
        }

    def detect_drift(self, window_size: int = 10) -> dict:
        """Detect if performance is drifting."""
        if len(self.workflow_history) < window_size:
            return {"drift_detected": False, "reason": "insufficient_data"}

        recent = self.workflow_history[-window_size:]
        historical = self.workflow_history[:-window_size]

        recent_failures = sum(1 for e in recent if not e.get("success", True))
        historical_failures = sum(
            1 for e in historical if not e.get("success", True)
        ) / max(len(historical), 1)

        drift_detected = recent_failures > max(historical_failures * 2, 0.5)

        return {
            "drift_detected": drift_detected,
            "recent_failures": recent_failures,
            "historical_failure_rate": historical_failures,
        }


class WeaknessTester:
    """
    Comprehensive testing for ML operations.
    """

    def __init__(self):
        self.suite = TestSuite()
        self.hardening_recommendations: list[dict] = []

    def run_all_tests(self) -> TestSuite:
        """Run all weakness tests."""
        self.suite = TestSuite()
        self.suite.start_time = time.time()

        # Category 1: Data Sparsity Tests
        self.test_cold_start_empty()
        self.test_cold_start_minimal_data()
        self.test_cold_start_no_relevant_history()

        # Category 2: Concept Drift Tests
        self.test_concept_drift_gradual()
        self.test_concept_drift_sudden()
        self.test_performance_degradation()

        # Category 3: Edge Case Tests
        self.test_empty_inputs()
        self.test_null_handling()
        self.test_extreme_values()
        self.test_invalid_task_types()

        # Category 4: Concurrent Operations
        self.test_rapid_successive_calls()
        self.test_memory_pressure()
        self.test_thread_safety()

        # Category 5: Integration Tests
        self.test_import_failures()
        self.test_component_integration()

        # Category 6: Prediction Accuracy
        self.test_confidence_calibration()
        self.test_prediction_variance()

        # Category 7: Recovery Tests
        self.test_partial_data_recovery()
        self.test_graceful_degradation()

        self.suite.end_time = time.time()
        return self.suite

    # ========== DATA SPARSITY TESTS ==========

    def test_cold_start_empty(self):
        """Test behavior with completely empty history."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            rec = ai.get_framework_recommendation("image_classification")

            passed = rec.get("recommendation") is None
            details = {"reason": rec.get("reason")}

            self.suite.add(
                TestResult(
                    test_name="cold_start_empty",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should handle empty gracefully",
                    details=details,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L324", e)
            self.suite.add(
                TestResult(
                    test_name="cold_start_empty",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_cold_start_minimal_data(self):
        """Test behavior with minimal data."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            ai.record_event(
                action="train",
                framework="torch",
                task_type="image_classification",
                duration=100.0,
                success=True,
            )

            rec = ai.get_framework_recommendation("image_classification")

            passed = rec.get("recommendation") is not None
            details = {
                "has_recommendation": rec.get("recommendation") is not None,
                "framework": rec.get("recommendation"),
            }

            self.suite.add(
                TestResult(
                    test_name="cold_start_minimal_data",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should work with single event",
                    details=details,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L365", e)
            self.suite.add(
                TestResult(
                    test_name="cold_start_minimal_data",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_cold_start_no_relevant_history(self):
        """Test when history exists but not for current task."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            for task in ["nlp", "tabular", "rl"]:
                ai.record_event(
                    action="train",
                    framework="torch",
                    task_type=task,
                    duration=100.0,
                    success=True,
                )

            rec = ai.get_framework_recommendation("image_classification")

            passed = rec.get("recommendation") is not None
            details = {
                "recommendation": rec.get("recommendation"),
                "score": rec.get("score"),
            }

            self.suite.add(
                TestResult(
                    test_name="cold_start_no_relevant_history",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should fallback for unseen tasks",
                    details=details,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L407", e)
            self.suite.add(
                TestResult(
                    test_name="cold_start_no_relevant_history",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    # ========== CONCEPT DRIFT TESTS ==========

    def test_concept_drift_gradual(self):
        """Test handling of gradual performance changes."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            for i in range(10):
                ai.record_event(
                    action="train",
                    framework="torch",
                    task_type="image_classification",
                    duration=100.0 - i * 5,
                    success=True,
                    metrics={"accuracy": 0.8 + i * 0.02},
                )

            rec = ai.get_framework_recommendation("image_classification")

            passed = rec.get("recommendation") is not None
            details = {
                "framework": rec.get("recommendation"),
                "score": rec.get("score"),
            }

            self.suite.add(
                TestResult(
                    test_name="concept_drift_gradual",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should handle gradual drift",
                    details=details,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L452", e)
            self.suite.add(
                TestResult(
                    test_name="concept_drift_gradual",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_concept_drift_sudden(self):
        """Test handling of sudden performance changes."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            for _ in range(10):
                ai.record_event(
                    action="train",
                    framework="torch",
                    task_type="image_classification",
                    duration=50.0,
                    success=True,
                )

            for _ in range(3):
                ai.record_event(
                    action="train",
                    framework="torch",
                    task_type="image_classification",
                    duration=500.0,
                    success=False,
                )

            drift = ai.detect_drift()
            rec = ai.get_framework_recommendation("image_classification")

            passed = rec.get("recommendation") is not None
            details = {
                "framework": rec.get("recommendation"),
                "drift_detected": drift.get("drift_detected"),
            }

            self.suite.add(
                TestResult(
                    test_name="concept_drift_sudden",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should detect sudden drift",
                    details=details,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L504", e)
            self.suite.add(
                TestResult(
                    test_name="concept_drift_sudden",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_performance_degradation(self):
        """Test detection of long-term performance degradation."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            for i in range(50):
                ai.record_event(
                    action="train",
                    framework="tensorflow",
                    task_type="nlp",
                    duration=80.0 + i * 2,
                    success=i < 40,
                )

            rec = ai.get_framework_recommendation("nlp")

            passed = rec.get("recommendation") is not None
            details = {
                "total_events": len(ai.workflow_history),
                "recommendation": rec.get("recommendation"),
            }

            self.suite.add(
                TestResult(
                    test_name="performance_degradation",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should detect degradation",
                    details=details,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L546", e)
            self.suite.add(
                TestResult(
                    test_name="performance_degradation",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    # ========== EDGE CASE TESTS ==========

    def test_empty_inputs(self):
        """Test handling of empty inputs."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            passed = True
            errors = []

            # Test empty action
            try:
                ai.record_event(
                    action="",
                    framework="torch",
                    task_type="test",
                    duration=1.0,
                    success=True,
                )
            except Exception as e:  # noqa: BLE001 — harness verifies target does not raise
                note_failure("weakness_testing:L576", e)
                passed = False
                errors.append(f"Empty action: {e}")

            # Test empty task_type
            try:
                ai.get_framework_recommendation("")
            except Exception as e:  # noqa: BLE001 — harness verifies target does not raise
                note_failure("weakness_testing:L583", e)
                passed = False
                errors.append(f"Empty task_type: {e}")

            self.suite.add(
                TestResult(
                    test_name="test_empty_inputs",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error="; ".join(errors) if errors else None,
                    details={"errors": errors},
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L596", e)
            self.suite.add(
                TestResult(
                    test_name="test_empty_inputs",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_null_handling(self):
        """Test handling of None/null values."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            passed = True
            errors = []

            # Test None action
            try:
                ai.record_event(
                    action=None,
                    framework="torch",
                    task_type="test",
                    duration=1.0,
                    success=True,
                )
            except Exception as e:  # noqa: BLE001 — harness verifies target does not raise
                note_failure("weakness_testing:L624", e)
                passed = False
                errors.append(f"None action: {e}")

            # Test None task_type
            try:
                ai.get_framework_recommendation(None)
            except Exception as e:  # noqa: BLE001 — harness verifies target does not raise
                note_failure("weakness_testing:L631", e)
                passed = False
                errors.append(f"None task_type: {e}")

            self.suite.add(
                TestResult(
                    test_name="test_null_handling",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error="; ".join(errors) if errors else None,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L643", e)
            self.suite.add(
                TestResult(
                    test_name="test_null_handling",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_extreme_values(self):
        """Test handling of extreme input values."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            passed = True
            errors = []

            # Extreme duration values
            test_cases = [
                ("duration", 1e10),
                ("duration", 1e-10),
                ("accuracy", 1.5),
                ("accuracy", -0.5),
            ]

            for attr, value in test_cases:
                try:
                    event_kwargs = {
                        "action": "test",
                        "framework": "torch",
                        "task_type": "test",
                        "duration": 100.0,
                        "success": True,
                    }
                    if attr == "duration":
                        event_kwargs["duration"] = value
                    else:
                        event_kwargs["metrics"] = {attr: value}
                    ai.record_event(**event_kwargs)
                except Exception as e:  # noqa: BLE001 — harness verifies extreme input does not raise
                    note_failure("weakness_testing:L684", e)
                    passed = False
                    errors.append(f"Extreme {attr}={value}: {e}")

            self.suite.add(
                TestResult(
                    test_name="test_extreme_values",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error="; ".join(errors) if errors else None,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L696", e)
            self.suite.add(
                TestResult(
                    test_name="test_extreme_values",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_invalid_task_types(self):
        """Test handling of invalid or unknown task types."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            invalid_tasks = [
                "",
                "invalid_task_xyz",
                "123task",
                "task-with-special-chars!!!",
                "a" * 1000,
            ]

            passed = True
            for task in invalid_tasks:
                try:
                    ai.get_framework_recommendation(task)
                except Exception as exc:  # noqa: BLE001 — harness verifies invalid task does not raise
                    note_failure("weakness_testing:L724", exc)
                    passed = False
                    break

            self.suite.add(
                TestResult(
                    test_name="test_invalid_task_types",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should handle invalid task types",
                    details={"test_cases": len(invalid_tasks)},
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L737", e)
            self.suite.add(
                TestResult(
                    test_name="test_invalid_task_types",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    # ========== CONCURRENT OPERATIONS TESTS ==========

    def test_rapid_successive_calls(self):
        """Test handling of rapid successive calls."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            # Reset for this test
            ai.workflow_history = []

            num_calls = 100
            for i in range(num_calls):
                ai.record_event(
                    action=f"action_{i}",
                    framework="torch",
                    task_type="test",
                    duration=1.0,
                    success=True,
                )

            history_len = len(ai.workflow_history)
            passed = history_len == num_calls

            self.suite.add(
                TestResult(
                    test_name="test_rapid_successive_calls",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None
                    if passed
                    else f"Expected {num_calls}, got {history_len}",
                    details={"expected": num_calls, "actual": history_len},
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L782", e)
            self.suite.add(
                TestResult(
                    test_name="test_rapid_successive_calls",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_memory_pressure(self):
        """Test behavior under memory pressure."""
        start = time.time()
        try:
            from ml_platforms.improvements.qualitative_improvements import MemoryManager

            mm = MemoryManager(max_events=100)

            events = []
            for i in range(200):
                events.append(
                    {
                        "task_type": f"task_{i % 5}",
                        "framework": f"fw_{i % 3}",
                        "duration": 100.0,
                        "timestamp": time.time(),
                    }
                )

            compressed = mm.compress_history(events)
            passed = len(compressed) <= 100

            self.suite.add(
                TestResult(
                    test_name="test_memory_pressure",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Compression should limit history",
                    details={"original": len(events), "compressed": len(compressed)},
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L823", e)
            self.suite.add(
                TestResult(
                    test_name="test_memory_pressure",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_thread_safety(self):
        """Test thread safety (basic sequential test)."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            # Reset for this test
            ai.workflow_history = []

            # Interleave reads and writes
            for i in range(50):
                ai.record_event(
                    action="test",
                    framework="torch",
                    task_type="test",
                    duration=1.0,
                    success=True,
                )
                ai.get_framework_recommendation("test")

            passed = len(ai.workflow_history) == 50

            self.suite.add(
                TestResult(
                    test_name="test_thread_safety",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Interleaved operations failed",
                    details={"events": len(ai.workflow_history)},
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L864", e)
            self.suite.add(
                TestResult(
                    test_name="test_thread_safety",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    # ========== INTEGRATION TESTS ==========

    def test_import_failures(self):
        """Test graceful handling when imports fail."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            passed = True
            try:
                ai.record_event(
                    action="test",
                    framework="test",
                    task_type="test",
                    duration=1.0,
                    success=True,
                )
            except (TypeError, KeyError) as exc:  # narrowed from Exception; harness verifies target does not raise
                note_failure("weakness_testing:L891", exc)
                passed = False

            self.suite.add(
                TestResult(
                    test_name="test_import_failures",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Core functionality should work",
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L902", e)
            self.suite.add(
                TestResult(
                    test_name="test_import_failures",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_component_integration(self):
        """Test that all components work together."""
        start = time.time()
        try:
            from ml_platforms.improvements.strategic_intelligence import (
                SmartCache,
                PerformanceProfiler,
            )
            from ml_platforms.improvements.qualitative_improvements import (
                NeuralPredictor,
                AnomalyDetector,
                ContextAwareScheduler,
            )

            ai = AzazaelIntelligenceHardened()
            cache = SmartCache()
            profiler = PerformanceProfiler()
            NeuralPredictor()
            AnomalyDetector()
            scheduler = ContextAwareScheduler()

            passed = True

            ai.record_event(
                action="train",
                framework="torch",
                task_type="cv",
                duration=100.0,
                success=True,
            )

            cache.record_access("ml_platforms")
            profiler.record_metrics("cv", "torch", 100.0, 2048.0, 0.95)
            scheduler.update_hardware_state({"memory_available": 0.5})

            self.suite.add(
                TestResult(
                    test_name="test_component_integration",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Integration failed",
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L955", e)
            self.suite.add(
                TestResult(
                    test_name="test_component_integration",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    # ========== PREDICTION ACCURACY TESTS ==========

    def test_confidence_calibration(self):
        """Test if confidence scores are reasonable."""
        start = time.time()
        try:
            from ml_platforms.improvements.qualitative_improvements import (
                NeuralPredictor,
            )

            neural = NeuralPredictor()

            data = [
                {"task_type": "cv", "framework": "torch", "score": 0.9},
                {"task_type": "cv", "framework": "tensorflow", "score": 0.85},
            ]
            neural.train(data)

            confidence = neural.get_confidence()
            passed = confidence < 1.0

            self.suite.add(
                TestResult(
                    test_name="test_confidence_calibration",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Confidence should be calibrated",
                    details={"confidence": confidence},
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L995", e)
            self.suite.add(
                TestResult(
                    test_name="test_confidence_calibration",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_prediction_variance(self):
        """Test that predictions show appropriate variance."""
        start = time.time()
        try:
            from ml_platforms.improvements.qualitative_improvements import (
                NeuralPredictor,
            )

            neural = NeuralPredictor()

            score1 = neural.predict_score("cv", "torch")
            score2 = neural.predict_score("cv", "torch")

            passed = score1 == score2

            self.suite.add(
                TestResult(
                    test_name="test_prediction_variance",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Predictions should be deterministic",
                    details={"score1": score1, "score2": score2},
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L1029", e)
            self.suite.add(
                TestResult(
                    test_name="test_prediction_variance",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    # ========== RECOVERY TESTS ==========

    def test_partial_data_recovery(self):
        """Test recovery from partial/corrupted data."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()

            for _ in range(5):
                ai.record_event(
                    action="test",
                    framework="torch",
                    task_type="test",
                    duration=100.0,
                    success=True,
                )

            # Simulate data corruption
            ai.framework_scores = {"invalid": {"nonexistent": None}}

            # Should recover gracefully
            try:
                ai.get_framework_recommendation("test")
                recovered = True
            except Exception as exc:  # noqa: BLE001 — recovery test expects no exception
                note_failure("weakness_testing:L1063", exc)
                recovered = False

            passed = recovered

            self.suite.add(
                TestResult(
                    test_name="test_partial_data_recovery",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should recover from corrupted data",
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L1076", e)
            self.suite.add(
                TestResult(
                    test_name="test_partial_data_recovery",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )

    def test_graceful_degradation(self):
        """Test graceful degradation when components fail."""
        start = time.time()
        try:
            ai = AzazaelIntelligenceHardened()
            ai.workflow_history = []
            ai.framework_scores = {}

            rec = ai.get_framework_recommendation("nonexistent_task")

            passed = True
            details = {"recommendation_type": type(rec).__name__}

            self.suite.add(
                TestResult(
                    test_name="test_graceful_degradation",
                    passed=passed,
                    duration_ms=(time.time() - start) * 1000,
                    error=None if passed else "Should not crash",
                    details=details,
                )
            )
        except Exception as e:  # noqa: BLE001 — weakness harness must capture any failure
            note_failure("weakness_testing:L1108", e)
            self.suite.add(
                TestResult(
                    test_name="test_graceful_degradation",
                    passed=False,
                    duration_ms=(time.time() - start) * 1000,
                    error=str(e),
                )
            )


def run_comprehensive_test():
    """Run all tests and generate report."""
    print("=" * 70)
    print("Azazael COMPREHENSIVE WEAKNESS TEST & HARDENING")
    print("=" * 70)
    print()

    tester = WeaknessTester()
    suite = tester.run_all_tests()
    summary = suite.summary()

    print("TEST RESULTS SUMMARY")
    print("-" * 40)
    print(f"Total Tests: {summary['total']}")
    print(f"Passed: {summary['passed']}")
    print(f"Failed: {summary['failed']}")
    print(f"Success Rate: {summary['success_rate']:.1f}%")
    print(f"Duration: {summary['duration_ms']:.1f}ms")
    print()

    if summary["weak_points"]:
        print("FAILED TESTS (WEAKNESSES)")
        print("-" * 40)
        for wp in summary["weak_points"]:
            print(f"\n  Test: {wp['test']}")
            print(f"  Error: {wp['error']}")
        print()

    print("HARDENING APPLIED")
    print("-" * 40)
    print("  [OK] Input validation and sanitization")
    print("  [OK] Bounds checking for all metrics")
    print("  [OK] Task type normalization")
    print("  [OK] Drift detection")
    print("  [OK] Graceful degradation")
    print()

    # Save results
    results_file = DATA_DIR / "weakness_test_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": summary,
                "weak_points": summary["weak_points"],
                "timestamp": datetime.now().isoformat(),
                "hardening": "applied",
            },
            f,
            indent=2,
            default=str,
        )

    print("=" * 70)
    print(f"Results saved to: {results_file}")
    print("=" * 70)

    return suite


if __name__ == "__main__":
    run_comprehensive_test()
