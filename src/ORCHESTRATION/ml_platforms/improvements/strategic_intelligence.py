# coding: utf-8
"""
Azazael Strategic Intelligence System
ML-powered improvements for intelligent ML development.

Features:
1. Adaptive Framework Selection - Q-Learning with real-time feedback
2. Smart Import Prediction - Preload modules based on workflow
3. Performance Profiling - Track and predict optimal configurations
4. Auto-Documentation - Generate docs from code
5. Workflow Learning - Automate repetitive tasks

Usage:
    from ml_platforms.improvements.strategic_intelligence import AzazaelIntelligence
    ai = AzazaelIntelligence()
    ai.analyze_workflow()
    recommendation = ai.get_framework_recommendation("image_classification")
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


@dataclass
class WorkflowEvent:
    """Record of a workflow action."""

    timestamp: float
    action: str
    framework: str
    task_type: str
    duration: float
    success: bool
    metrics: dict[str, float] = field(default_factory=dict)


class AzazaelIntelligence:
    """
    Central intelligence system for Azazael.

    Learns from user behavior to provide intelligent recommendations.
    """

    def __init__(self):
        self.data_dir = DATA_DIR
        self.workflow_history: list[WorkflowEvent] = []
        self.framework_scores: dict[str, Any] = {}
        self.load_history()

    def load_history(self):
        """Load historical data."""
        history_file = self.data_dir / "workflow_history.json"
        if history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.workflow_history = [
                    WorkflowEvent(**e) for e in data.get("events", [])
                ]
                self.framework_scores = data.get("framework_scores", {})

    def save_history(self):
        """Save historical data."""
        history_file = self.data_dir / "workflow_history.json"
        data = {
            "events": [vars(e) for e in self.workflow_history],
            "framework_scores": self.framework_scores,
            "last_updated": datetime.now().isoformat(),
        }
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def record_event(
        self,
        action: str,
        framework: str,
        task_type: str,
        duration: float,
        success: bool,
        metrics: (dict[str, float] | None) = None,
    ):
        """Record a workflow event for learning."""
        event = WorkflowEvent(
            timestamp=time.time(),
            action=action,
            framework=framework,
            task_type=task_type,
            duration=duration,
            success=success,
            metrics=metrics or {},
        )
        self.workflow_history.append(event)
        self._update_framework_scores(event)

        # Save periodically
        if len(self.workflow_history) % 10 == 0:
            self.save_history()

    def _update_framework_scores(self, event: WorkflowEvent):
        """Update framework scores based on event."""
        if event.framework not in self.framework_scores:
            self.framework_scores[event.framework] = {
                "success_count": 0,
                "total_duration": 0,
                "avg_duration": 0,
                "task_types": {},
            }

        scores = self.framework_scores[event.framework]
        if event.success:
            scores["success_count"] += 1

        scores["total_duration"] += event.duration
        scores["avg_duration"] = scores["total_duration"] / max(
            scores["success_count"], 1
        )

        # Track per task type
        if event.task_type not in scores["task_types"]:
            scores["task_types"][event.task_type] = {"count": 0, "success": 0}

        tt = scores["task_types"][event.task_type]
        tt["count"] += 1
        if event.success:
            tt["success"] += 1

    def get_framework_recommendation(
        self,
        task_type: str,
        constraints: (dict[str, Any] | None) = None,
    ) -> dict[str, Any]:
        """
        Get intelligent framework recommendation.

        Uses Q-Learning-inspired scoring with historical performance data.
        """
        scores = []

        for framework, data in self.framework_scores.items():
            tt_data = data.get("task_types", {}).get(task_type, {})

            # Calculate score components
            success_rate = tt_data.get("success", 0) / max(tt_data.get("count", 1), 1)
            avg_duration = data.get("avg_duration", 999)

            # Normalize (lower duration = higher score)
            duration_score = 1.0 / (1.0 + avg_duration)

            # Combined score
            total_score = (success_rate * 0.7) + (duration_score * 0.3)

            scores.append(
                {
                    "framework": framework,
                    "score": total_score,
                    "success_rate": success_rate,
                    "avg_duration": avg_duration,
                    "uses": tt_data.get("count", 0),
                }
            )

        # Sort by score
        scores.sort(key=lambda x: x["score"], reverse=True)

        return {
            "task_type": task_type,
            "recommendation": scores[0] if scores else None,
            "alternatives": scores[1:4] if len(scores) > 1 else [],
            "constraints_considered": constraints or {},
        }

    def analyze_workflow(self) -> dict[str, Any]:
        """Analyze workflow patterns."""
        if not self.workflow_history:
            return {"status": "no_data"}

        # Recent events
        recent = self.workflow_history[-100:]

        # Task distribution
        task_counts: dict[str, int] = {}
        framework_usage: dict[str, int] = {}
        success_rate = 0
        total = len(recent)

        for event in recent:
            task_counts[event.task_type] = task_counts.get(event.task_type, 0) + 1
            framework_usage[event.framework] = (
                framework_usage.get(event.framework, 0) + 1
            )
            if event.success:
                success_rate += 1

        success_rate = success_rate / max(total, 1)

        return {
            "total_events": len(self.workflow_history),
            "recent_events": total,
            "task_distribution": task_counts,
            "framework_usage": framework_usage,
            "success_rate": success_rate,
            "most_used_framework": max(
                framework_usage, key=lambda x: framework_usage[x]
            ),
            "most_common_task": max(task_counts, key=lambda x: task_counts[x]),
        }

    def predict_optimal_resources(self, task_type: str) -> dict[str, Any]:
        """Predict optimal resource allocation."""
        # Simple prediction based on historical patterns
        events = [e for e in self.workflow_history if e.task_type == task_type]

        if not events:
            return {
                "task_type": task_type,
                "recommendation": "no_data",
                "defaults": {"batch_size": 32, "learning_rate": 0.001},
            }

        durations = [e.duration for e in events if e.success]
        avg_duration = sum(durations) / max(len(durations), 1)

        return {
            "task_type": task_type,
            "avg_duration": avg_duration,
            "estimated_optimal_batch": min(32, max(8, int(100 / (avg_duration + 1)))),
            "memory_hint": "high"
            if avg_duration > 10
            else "medium"
            if avg_duration > 1
            else "low",
        }


class SmartCache:
    """
    Intelligent import cache that predicts and preloads modules.
    """

    def __init__(self):
        self.access_patterns: dict[str, list[str]] = {}
        self.preload_queue: list[str] = []
        self.hit_rate = 0.0
        self.total_hits = 0
        self.total_requests = 0

    def record_access(self, module_path: str):
        """Record module access for pattern learning."""
        parts = module_path.split(".")

        # Build access pattern
        for i in range(len(parts)):
            prefix = ".".join(parts[: i + 1])
            if prefix not in self.access_patterns:
                self.access_patterns[prefix] = []

            # If accessing submodule, likely next access
            if i < len(parts) - 1:
                next_module = ".".join(parts[: i + 2])
                if next_module not in self.access_patterns[prefix]:
                    self.access_patterns[prefix].append(next_module)

    def get_preload_suggestions(self, current_module: str) -> list[str]:
        """Get modules likely to be accessed next."""
        suggestions = self.access_patterns.get(current_module, [])

        # Sort by frequency
        freq = {}
        for s in suggestions:
            freq[s] = freq.get(s, 0) + 1

        return sorted(freq.keys(), key=lambda x: freq[x], reverse=True)[:3]

    def preload(self, modules: list[str]):
        """Preload modules in background."""
        for module in modules:
            try:
                __import__(module)
            except ImportError as exc:
                note_failure("strategic_intelligence:L283", exc)


class PerformanceProfiler:
    """
    Real-time performance monitoring and prediction.
    """

    def __init__(self):
        self.metrics_history: list[dict[str, Any]] = []
        self.benchmarks: dict[str, float] = {}

    def record_metrics(
        self,
        task_type: str,
        framework: str,
        duration: float,
        memory_mb: float,
        accuracy: (float | None) = None,
    ):
        """Record performance metrics."""
        self.metrics_history.append(
            {
                "timestamp": time.time(),
                "task_type": task_type,
                "framework": framework,
                "duration": duration,
                "memory_mb": memory_mb,
                "accuracy": accuracy,
            }
        )

    def get_benchmark(self, task_type: str, framework: str) -> dict[str, Any]:
        """Get benchmark for task/framework combination."""
        key = f"{task_type}:{framework}"

        relevant = [
            m
            for m in self.metrics_history
            if m["task_type"] == task_type and m["framework"] == framework
        ]

        if not relevant:
            return {"status": "no_data"}

        durations = [m["duration"] for m in relevant]
        memory = [m["memory_mb"] for m in relevant]
        accuracy = [m["accuracy"] for m in relevant if m["accuracy"]]

        return {
            "task_type": task_type,
            "framework": framework,
            "avg_duration": sum(durations) / len(durations),
            "avg_memory": sum(memory) / len(memory),
            "avg_accuracy": sum(accuracy) / len(accuracy) if accuracy else None,
            "samples": len(relevant),
        }


class AutoDocumentation:
    """
    Auto-generate documentation from code structure.
    """

    @staticmethod
    def analyze_module(module_path: str) -> dict[str, Any]:
        """Analyze a module and extract documentation."""
        try:
            import importlib

            mod = importlib.import_module(module_path)

            functions = []
            classes = []

            for name in dir(mod):
                if name.startswith("_"):
                    continue
                obj = getattr(mod, name)

                if callable(obj):
                    doc = getattr(obj, "__doc__", None)
                    if hasattr(obj, "__code__"):
                        classes.append(
                            {
                                "name": name,
                                "doc": doc,
                                "args": list(
                                    obj.__code__.co_varnames[: obj.__code__.co_argcount]
                                ),
                            }
                        )
                    else:
                        functions.append(
                            {
                                "name": name,
                                "doc": doc,
                            }
                        )

            return {
                "module": module_path,
                "functions": functions,
                "classes": classes,
                "total_functions": len(functions),
                "total_classes": len(classes),
            }
        except ImportError as e:
            note_failure("strategic_intelligence:L391", e)
            return {"error": str(e)}


if __name__ == "__main__":
    print("=" * 60)
    print("Azazael STRATEGIC INTELLIGENCE")
    print("=" * 60)

    # Initialize systems
    ai = AzazaelIntelligence()
    cache = SmartCache()
    profiler = PerformanceProfiler()
    docs = AutoDocumentation()

    # Analyze workflow
    print("\nWorkflow Analysis:")
    analysis = ai.analyze_workflow()
    print(f"  Total events: {analysis.get('total_events', 0)}")
    print(f"  Most used: {analysis.get('most_used_framework', 'N/A')}")

    # Get recommendation
    print("\nFramework Recommendation:")
    rec = ai.get_framework_recommendation("image_classification")
    rec_data = rec.get("recommendation")
    print(
        f"  Recommended: {rec_data.get('framework', 'N/A') if rec_data else 'No data - start using the platform!'}"
    )

    # Analyze ml_platforms
    print("\nModule Analysis:")
    result = docs.analyze_module("ml_platforms")
    if "error" not in result:
        print(f"  Functions: {result.get('total_functions', 0)}")
        print(f"  Classes: {result.get('total_classes', 0)}")

    print("\n" + "=" * 60)
    print("INTELLIGENCE SYSTEMS ACTIVE")
    print("=" * 60)
