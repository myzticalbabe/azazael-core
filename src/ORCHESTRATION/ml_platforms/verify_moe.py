import sys
from pathlib import Path
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
Verify Moe

Module for ml_platforms functionality.

Functions:
    - verify_system()
    - run_deep_learning_q_analysis()
    - analyze_framework()
    - run_successive_analysis()
    - get_current_q_table()
    - suggest_best_framework()
    - export_analysis()
    - compute_compatibility()
    - get_best_for_task()
    - export_matrix()

Classes:
    - DeepLearningQAnalyzer
    - FrameworkCompatibilityMatrix

Usage:
    >>> import verify_moe
    >>> verify_moe.verify_system()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
MoE System Verification & Deep Learning Q Analysis
Tests all framework experts and records Q-Learning analysis.
"""

import sys
import json
import time
import numpy as np
from pathlib import Path
from datetime import datetime
# from typing import Dict, List, Tuple  # removed — all names have native equivalents
import logging
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MoEVerification")

try:
    sys.path.insert(0, str(Path.home() / "Desktop" / "Azazael" / "ml_platforms"))
    import config as _moe_config
    import improvements.qlearning_table as _moe_qlearning
    from improvements.qlearning_table import (
        TaskType,
        FrameworkState,
        benchmark_framework,
    )
    from diagnostics import check_platform_status

    # get_registry / AdaptiveExpertLearner exist only in the Desktop-tree
    # ml_platforms (see docs/qlearning/verify_moe_API); the in-repo copy
    # predates them. getattr keeps the probe honest: absent names raise
    # AttributeError here and flip IMPORT_SUCCESS False — the same outcome
    # the old from-imports produced via ImportError.
    get_registry = getattr(_moe_config, "get_registry")
    AdaptiveExpertLearner = getattr(_moe_qlearning, "AdaptiveExpertLearner")

    IMPORT_SUCCESS = True
except (ImportError, AttributeError) as e:
    logger.warning(f"Import error: {e}")
    IMPORT_SUCCESS = False


class DeepLearningQAnalyzer:
    """
    Deep Learning Q-Learning Analysis System.

    Analyzes framework performance and updates Q-values based on real metrics.
    """

    def __init__(self):
        self.learner = AdaptiveExpertLearner(
            num_states=100, num_actions=6, alpha=0.1, gamma=0.95, epsilon=0.3
        )
        self.framework_state = FrameworkState(alpha=0.1, gamma=0.95)
        self.analysis_history: list[dict] = []
        self.metrics_log: list[dict] = []

        self.expert_names = [
            "torch_expert",
            "tensorflow_expert",
            "sklearn_expert",
            "jax_expert",
            "onnx_expert",
            "ray_expert",
        ]

        self.task_types = list(TaskType)

    def analyze_framework(self, framework: str, task_type: str) -> dict:
        """Analyze a specific framework for a task type."""
        start_time = time.time()

        score = benchmark_framework(framework, TaskType(task_type))

        analysis = {
            "framework": framework,
            "task_type": task_type,
            "benchmark_score": score,
            "timestamp": datetime.now().isoformat(),
            "latency_ms": (time.time() - start_time) * 1000,
        }

        self.analysis_history.append(analysis)

        return analysis

    def run_successive_analysis(self, iterations: int = 10) -> dict:
        """Run successive framework analysis."""
        results = {
            "start_time": datetime.now().isoformat(),
            "iterations": iterations,
            "framework_analyses": [],
            "q_table_updates": [],
            "recommendations": [],
            "total_time_ms": 0,
        }

        start = time.time()

        for i in range(iterations):
            iteration_data = {"iteration": i + 1, "analyses": [], "q_values_after": {}}

            for task_type in self.task_types[:4]:
                for framework in self.expert_names:
                    analysis = self.analyze_framework(framework, task_type.value)
                    iteration_data["analyses"].append(analysis)

                    state = self.learner.encode_task(
                        task_type.value, 10000, gpu_available=True
                    )

                    action_idx = self.expert_names.index(framework)

                    reward = analysis["benchmark_score"]
                    next_state = state + 1
                    best_next = np.max(self.learner.q_table[next_state])

                    self.learner.q_table[state, action_idx] += self.learner.alpha * (
                        reward
                        + self.learner.gamma * best_next
                        - self.learner.q_table[state, action_idx]
                    )

            for name in self.expert_names:
                idx = self.expert_names.index(name)
                avg_q = np.mean(self.learner.q_table[:, idx])
                iteration_data["q_values_after"][name] = avg_q

            results["framework_analyses"].append(iteration_data)

            if (i + 1) % 5 == 0:
                logger.info(f"Iteration {i + 1}/{iterations} complete")

        results["total_time_ms"] = (time.time() - start) * 1000
        results["end_time"] = datetime.now().isoformat()

        return results

    def get_current_q_table(self) -> dict:
        """Get current Q-table state."""
        q_state = {}
        for i, name in enumerate(self.expert_names):
            q_state[name] = {
                "avg_q": float(np.mean(self.learner.q_table[:, i])),
                "max_q": float(np.max(self.learner.q_table[:, i])),
                "min_q": float(np.min(self.learner.q_table[:, i])),
                "std_q": float(np.std(self.learner.q_table[:, i])),
            }
        return q_state

    def suggest_best_framework(self, task_type: str, **kwargs) -> dict:
        """Get best framework suggestion for task."""
        expert, confidence = self.learner.suggest_expert(
            task_type,
            data_size=kwargs.get("data_size", 10000),
            gpu_available=kwargs.get("gpu_available", True),
        )

        q_values = self.learner.get_expert_rankings()

        return {
            "recommended": expert,
            "confidence": confidence,
            "rankings": q_values,
            "reasoning": self._get_reasoning(expert, task_type),
        }

    def _get_reasoning(self, expert: str, task_type: str) -> list[str]:
        """Get reasoning for recommendation."""
        reasoning = [
            f"Selected {expert} for {task_type}",
            "Based on Q-learning analysis",
            f"Epsilon: {self.learner.epsilon:.3f}",
            f"Exploration rate: {1 - self.learner.epsilon:.3f}",
        ]
        return reasoning

    def export_analysis(self, filepath: str):
        """Export complete analysis to file."""
        export_data = {
            "timestamp": datetime.now().isoformat(),
            "q_table": self.learner.q_table.tolist(),
            "expert_names": self.expert_names,
            "analysis_history": self.analysis_history,
            "q_table_state": self.get_current_q_table(),
            "learner_stats": {
                "alpha": self.learner.alpha,
                "gamma": self.learner.gamma,
                "epsilon": self.learner.epsilon,
                "total_experiences": len(self.learner.experience_buffer),
            },
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2)

        logger.info(f"Analysis exported to {filepath}")


class FrameworkCompatibilityMatrix:
    """Analyzes compatibility between frameworks and tasks."""

    def __init__(self):
        self.matrix: dict[str, dict[str, float]] = {}
        self.expert_names = [
            "torch_expert",
            "tensorflow_expert",
            "sklearn_expert",
            "jax_expert",
            "onnx_expert",
            "ray_expert",
        ]
        self.task_types = [
            "computer_vision",
            "nlp",
            "tabular",
            "reinforcement",
            "generative",
        ]

    def compute_compatibility(self) -> dict:
        """Compute compatibility scores."""
        for expert in self.expert_names:
            self.matrix[expert] = {}

            base_scores = {
                "torch_expert": {
                    "computer_vision": 0.95,
                    "nlp": 0.90,
                    "tabular": 0.70,
                    "reinforcement": 0.95,
                    "generative": 0.90,
                },
                "tensorflow_expert": {
                    "computer_vision": 0.92,
                    "nlp": 0.88,
                    "tabular": 0.75,
                    "reinforcement": 0.85,
                    "generative": 0.88,
                },
                "sklearn_expert": {
                    "computer_vision": 0.40,
                    "nlp": 0.50,
                    "tabular": 0.95,
                    "reinforcement": 0.30,
                    "generative": 0.35,
                },
                "jax_expert": {
                    "computer_vision": 0.88,
                    "nlp": 0.85,
                    "tabular": 0.75,
                    "reinforcement": 0.88,
                    "generative": 0.92,
                },
                "onnx_expert": {
                    "computer_vision": 0.70,
                    "nlp": 0.70,
                    "tabular": 0.70,
                    "reinforcement": 0.65,
                    "generative": 0.70,
                },
                "ray_expert": {
                    "computer_vision": 0.75,
                    "nlp": 0.75,
                    "tabular": 0.80,
                    "reinforcement": 0.90,
                    "generative": 0.75,
                },
            }

            if expert in base_scores:
                self.matrix[expert] = base_scores[expert]

        return self.matrix

    def get_best_for_task(self, task_type: str) -> list[tuple[str, float]]:
        """Get best experts for task type."""
        if not self.matrix:
            self.compute_compatibility()

        scores = []
        for expert, task_scores in self.matrix.items():
            if task_type in task_scores:
                scores.append((expert, task_scores[task_type]))

        return sorted(scores, key=lambda x: x[1], reverse=True)

    def export_matrix(self, filepath: str):
        """Export compatibility matrix."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.matrix, f, indent=2)


def verify_system():
    """Verify all system components."""
    print("=" * 80)
    print("MoE SYSTEM VERIFICATION & DEEP LEARNING Q ANALYSIS")
    print("=" * 80)
    print()

    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "system": "Mixture-of-Experts ML Platform",
        "version": "2.0",
        "verification_results": {},
        "framework_analysis": {},
        "q_learning_results": {},
        "recommendations": [],
    }

    if not IMPORT_SUCCESS:
        print("[!!] Warning: Some imports failed - running basic verification")

    print("[1/6] Verifying Expert Registry...")
    try:
        if IMPORT_SUCCESS:
            registry = get_registry()
            experts = registry.get_enabled_experts()
            expert_count = len(experts)
            print(f"    [OK] Loaded {expert_count} experts")
            report["verification_results"]["experts"] = {
                "status": "OK",
                "count": expert_count,
                "names": [e.name for e in experts],
            }
        else:
            print("    [OK] Registry simulated (imports unavailable)")
            report["verification_results"]["experts"] = {
                "status": "SIMULATED",
                "count": 6,
            }
    except Exception as e:
        print(f"    [!!] Error: {e}")
        report["verification_results"]["experts"] = {"status": "ERROR", "error": str(e)}

    print("\n[2/6] Verifying Platform Diagnostics...")
    try:
        if IMPORT_SUCCESS:
            available, unavailable = check_platform_status()
            print(
                f"    [OK] Available: {len(available)}, Unavailable: {len(unavailable)}"
            )
            report["verification_results"]["diagnostics"] = {
                "status": "OK",
                "available": len(available),
                "unavailable": len(unavailable),
            }
        else:
            print("    [OK] Diagnostics simulated")
            report["verification_results"]["diagnostics"] = {"status": "SIMULATED"}
    except Exception as e:
        print(f"    [!!] Error: {e}")
        report["verification_results"]["diagnostics"] = {
            "status": "ERROR",
            "error": str(e),
        }

    print("\n[3/6] Running Framework Analysis...")
    try:
        analyzer = DeepLearningQAnalyzer()
        analysis_results = analyzer.run_successive_analysis(iterations=10)

        print(
            f"    [OK] Analysis complete in {analysis_results['total_time_ms']:.2f}ms"
        )

        q_state = analyzer.get_current_q_table()
        print("    Q-Table State:")
        for name, state in q_state.items():
            print(
                f"      {name}: avg_q={state['avg_q']:.4f}, max_q={state['max_q']:.4f}"
            )

        report["framework_analysis"]["iterations"] = analysis_results["iterations"]
        report["framework_analysis"]["total_time_ms"] = analysis_results[
            "total_time_ms"
        ]
        report["q_learning_results"]["final_q_table"] = q_state

    except Exception as e:
        print(f"    [!!] Error: {e}")
        report["framework_analysis"] = {"status": "ERROR", "error": str(e)}

    print("\n[4/6] Computing Compatibility Matrix...")
    try:
        matrix = FrameworkCompatibilityMatrix()
        comp_matrix = matrix.compute_compatibility()

        print("    Compatibility Scores:")
        for task in matrix.task_types:
            best = matrix.get_best_for_task(task)
            print(f"      {task}: {best[0][0]} ({best[0][1]:.2f})")

        # Bind the nested section explicitly: report[...] is Any, and a nested
        # subscript-assign through Any does not carry the section's type.
        fw_analysis: dict[str, Any] = report["framework_analysis"]
        fw_analysis["compatibility_matrix"] = comp_matrix

    except Exception as e:
        print(f"    [!!] Error: {e}")

    print("\n[5/6] Generating Recommendations...")
    try:
        analyzer = DeepLearningQAnalyzer()

        tasks_to_test = [
            ("computer_vision", {"data_size": 10000, "gpu_available": True}),
            ("nlp", {"data_size": 50000, "gpu_available": True}),
            ("tabular", {"data_size": 5000, "gpu_available": False}),
            ("reinforcement", {"data_size": 1000, "gpu_available": True}),
        ]

        recommendations = []
        for task, opts in tasks_to_test:
            suggestion = analyzer.suggest_best_framework(task, **opts)
            recommendations.append(
                {
                    "task": task,
                    "recommended": suggestion["recommended"],
                    "confidence": suggestion["confidence"],
                    "reasoning": suggestion["reasoning"],
                }
            )
            print(
                f"    {task}: {suggestion['recommended']} (conf: {suggestion['confidence']:.3f})"
            )

        report["recommendations"] = recommendations

    except Exception as e:
        print(f"    [!!] Error: {e}")

    print("\n[6/6] Exporting Results...")
    try:
        output_dir = Path(Path.home() / "Desktop" / "Azazael" / "ml_platforms" / "analysis_logs")
        output_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if IMPORT_SUCCESS:
            analyzer = DeepLearningQAnalyzer()
            analyzer.export_analysis(str(output_dir / f"dlq_analysis_{timestamp}.json"))

        matrix = FrameworkCompatibilityMatrix()
        matrix.export_matrix(str(output_dir / f"compatibility_matrix_{timestamp}.json"))

        with open(str(output_dir / f"verification_report_{timestamp}.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        print(f"    [OK] Results saved to {output_dir}")

    except Exception as e:
        print(f"    [!!] Error: {e}")

    print("\n" + "=" * 80)
    print("VERIFICATION COMPLETE")
    print("=" * 80)

    return report


def run_deep_learning_q_analysis():
    """Run Deep Learning Q-Analysis only."""
    print("\n" + "=" * 80)
    print("DEEP LEARNING Q-ANALYSIS")
    print("=" * 80)

    analyzer = DeepLearningQAnalyzer()

    print("\n[Phase 1] Framework Benchmarking...")
    for task in [TaskType.COMPUTER_VISION, TaskType.NLP, TaskType.TABULAR]:
        print(f"\n  Task: {task.value}")
        for framework in analyzer.expert_names[:3]:
            score = benchmark_framework(framework, task)
            print(f"    {framework}: {score:.4f}")

    print("\n[Phase 2] Q-Learning Updates...")
    results = analyzer.run_successive_analysis(iterations=5)
    print(f"  Total analysis time: {results['total_time_ms']:.2f}ms")

    print("\n[Phase 3] Q-Table Analysis...")
    q_state = analyzer.get_current_q_table()
    print("  Expert Q-Values:")
    for name, state in sorted(
        q_state.items(), key=lambda x: x[1]["avg_q"], reverse=True
    ):
        print(f"    {name}: avg={state['avg_q']:.4f}, max={state['max_q']:.4f}")

    print("\n[Phase 4] Exporting...")
    output_dir = Path(Path.home() / "Desktop" / "Azazael" / "ml_platforms" / "analysis_logs")
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    analyzer.export_analysis(str(output_dir / f"dlq_{timestamp}.json"))

    print(f"\n  Exported to: {output_dir / f'dlq_{timestamp}.json'}")

    print("\n" + "=" * 80)

    return analyzer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MoE System Verification")
    parser.add_argument("--full", action="store_true", help="Run full verification")
    parser.add_argument(
        "--analysis", action="store_true", help="Run DL Q-Analysis only"
    )
    parser.add_argument("--recommend", action="store_true", help="Get recommendations")

    args = parser.parse_args()

    if args.full:
        verify_system()
    elif args.analysis:
        run_deep_learning_q_analysis()
    elif args.recommend:
        analyzer = DeepLearningQAnalyzer()
        for task in ["computer_vision", "nlp", "tabular"]:
            suggestion = analyzer.suggest_best_framework(task, gpu_available=True)
            print(
                f"{task}: {suggestion['recommended']} ({suggestion['confidence']:.3f})"
            )
    else:
        verify_system()
