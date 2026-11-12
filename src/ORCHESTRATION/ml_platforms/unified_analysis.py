# coding: utf-8
"""
Unified Analysis

Module for ml_platforms functionality.

Functions:
    - register_framework()
    - jax_analysis()
    - numpy_analysis()
    - sklearn_analysis()
    - pytorch_analysis()
    - tensorflow_analysis()
    - optuna_analysis()
    - ray_analysis()
    - onnx_analysis()
    - mlflow_analysis()

Usage:
    >>> import unified_analysis
    >>> unified_analysis.register_framework()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
Unified Multi-Framework Cross-Platform Analysis
Each of 10 ML frameworks analyzes all 27 platforms.
Correlates results and suggests improvements.

Frameworks:
1. JAX - Functional programming perspective
2. NumPy - API consistency perspective
3. scikit-learn - Pipeline/API patterns perspective
4. PyTorch - Autograd/computation graph perspective
5. TensorFlow - Graph optimization perspective
6. Optuna - Hyperparameter optimization perspective
7. Ray - Distributed computing perspective
8. ONNX - Interoperability perspective
9. MLflow - MLOps perspective
10. Shap - Explainability perspective
"""

from collections import defaultdict, Counter
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


PLATFORMS = [
    {"name": "PyTorch", "category": "deep_learning"},
    {"name": "TensorFlow", "category": "deep_learning"},
    {"name": "scikit-learn", "category": "classical_ml"},
    {"name": "Keras", "category": "deep_learning"},
    {"name": "XGBoost", "category": "gradient_boosting"},
    {"name": "LightGBM", "category": "gradient_boosting"},
    {"name": "Optuna", "category": "optimization"},
    {"name": "LangChain", "category": "llm_frameworks"},
    {"name": "Transformers", "category": "nlp"},
    {"name": "spaCy", "category": "nlp"},
    {"name": "OpenCV", "category": "computer_vision"},
    {"name": "Stable-Baselines3", "category": "reinforcement_learning"},
    {"name": "Ray", "category": "distributed_ml"},
    {"name": "SHAP", "category": "explainability"},
    {"name": "PyTorch Lightning", "category": "deep_learning"},
    {"name": "PyTorch Geometric", "category": "graph_learning"},
    {"name": "ONNX", "category": "interoperability"},
    {"name": "MLflow", "category": "mlops"},
    {"name": "Weights & Biases", "category": "mlops"},
    {"name": "CatBoost", "category": "gradient_boosting"},
    {"name": "FastAPI", "category": "deployment"},
    {"name": "FAISS", "category": "vector_search"},
    {"name": "HuggingFace Hub", "category": "model_registry"},
    {"name": "NLTK", "category": "nlp"},
    {"name": "Gymnasium", "category": "reinforcement_learning"},
    {"name": "PettingZoo", "category": "multi_agent_rl"},
    {"name": "Dask", "category": "distributed_computing"},
]


FRAMEWORKS = {}


def register_framework(name: str):
    """Decorator to register analysis frameworks."""

    def decorator(func):
        FRAMEWORKS[name] = func
        return func

    return decorator


@register_framework("JAX")
def jax_analysis(target: dict) -> list[dict]:
    """JAX perspective: Functional programming, transformations."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    if category not in ["deep_learning", "optimization"]:
        issues.append(
            {
                "issue": "Missing automatic differentiation",
                "severity": "high",
                "recommendation": "Support jax.grad composition for gradient-based optimization",
            }
        )

    if category not in ["deep_learning"]:
        issues.append(
            {
                "issue": "No JIT/XLA compilation support",
                "severity": "medium",
                "recommendation": "Add XLA compilation for graph optimization",
            }
        )

    if category in ["mlops", "llm_frameworks", "nlp"]:
        issues.append(
            {
                "issue": "State mutation detected",
                "severity": "high",
                "recommendation": "Use immutable data structures, tree-map updates",
            }
        )

    issues.append(
        {
            "issue": "No vectorization (jax.vmap)",
            "severity": "medium",
            "recommendation": "Support batch operations across dimensions",
        }
    )

    if not issues:
        critiques.append(
            {
                "framework": "JAX",
                "target": name,
                "verdict": "PASS",
                "message": "Compatible with JAX transformations",
                "issues": [],
            }
        )
    else:
        critiques.append(
            {
                "framework": "JAX",
                "target": name,
                "verdict": "NEEDS_IMPROVEMENT",
                "message": "JAX compatibility gaps identified",
                "issues": issues,
            }
        )

    return critiques


@register_framework("NumPy")
def numpy_analysis(target: dict) -> list[dict]:
    """NumPy perspective: API consistency, array semantics."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    if category not in ["deep_learning", "classical_ml", "gradient_boosting"]:
        issues.append(
            {
                "issue": "Deviates from NumPy array API",
                "severity": "high",
                "recommendation": "Align with np.ndarray semantics and broadcasting rules",
            }
        )

    if category in ["computer_vision", "nlp", "vector_search"]:
        issues.append(
            {
                "issue": "Non-standard tensor types",
                "severity": "medium",
                "recommendation": "Support numpy.ndarray as interchange format",
            }
        )

    issues.append(
        {
            "issue": "Inconsistent dtype handling",
            "severity": "medium",
            "recommendation": "Follow NumPy dtype promotion rules",
        }
    )

    if category == "llm_frameworks":
        issues.append(
            {
                "issue": "No standardized text tensors",
                "severity": "medium",
                "recommendation": "Implement text-to-numpy conversion pipeline",
            }
        )

    critiques.append(
        {
            "framework": "NumPy",
            "target": name,
            "verdict": "PARTIAL" if issues else "PASS",
            "message": f"NumPy API alignment: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


@register_framework("scikit-learn")
def sklearn_analysis(target: dict) -> list[dict]:
    """scikit-learn perspective: Pipeline patterns, fit/predict API."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    if category == "deep_learning":
        issues.append(
            {
                "issue": "No sklearn-compatible fit/predict API",
                "severity": "high",
                "recommendation": "Implement sklearn.base.BaseEstimator mixin",
            }
        )
        issues.append(
            {
                "issue": "Missing Pipeline integration",
                "severity": "medium",
                "recommendation": "Support sklearn.Pipeline composition",
            }
        )

    if category == "nlp":
        issues.append(
            {
                "issue": "No sklearn-style text classification",
                "severity": "medium",
                "recommendation": "Implement sklearn.feature_extraction compatibility",
            }
        )

    if category == "computer_vision":
        issues.append(
            {
                "issue": "No sklearn image preprocessing",
                "severity": "medium",
                "recommendation": "Add sklearn.preprocessing.TransformMixin",
            }
        )

    if category == "gradient_boosting":
        issues.append(
            {
                "issue": "Partial sklearn API support",
                "severity": "low",
                "recommendation": "Complete sklearn API parity",
            }
        )

    if category in ["mlops", "deployment"]:
        issues.append(
            {
                "issue": "No sklearn model export",
                "severity": "medium",
                "recommendation": "Support sklearn joblib pickle format",
            }
        )

    critiques.append(
        {
            "framework": "scikit-learn",
            "target": name,
            "verdict": "NEEDS_IMPROVEMENT" if issues else "PASS",
            "message": f"sklearn API compliance: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


@register_framework("PyTorch")
def pytorch_analysis(target: dict) -> list[dict]:
    """PyTorch perspective: Autograd, tensors, CUDA."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    if category not in ["deep_learning"]:
        issues.append(
            {
                "issue": "No native tensor type",
                "severity": "high",
                "recommendation": "Implement torch.Tensor interface or conversion",
            }
        )

    if category in ["computer_vision", "nlp"]:
        issues.append(
            {
                "issue": "Missing CUDA support",
                "severity": "medium",
                "recommendation": "Add GPU acceleration via torch.cuda",
            }
        )

    if category == "gradient_boosting":
        issues.append(
            {
                "issue": "No native GPU tensor support",
                "severity": "medium",
                "recommendation": "Add torch.Tensor conversion for GPU training",
            }
        )

    if category == "nlp":
        issues.append(
            {
                "issue": "No torchtext integration",
                "severity": "medium",
                "recommendation": "Replace deprecated torchtext with native torch",
            }
        )

    critiques.append(
        {
            "framework": "PyTorch",
            "target": name,
            "verdict": "NEEDS_IMPROVEMENT" if issues else "PASS",
            "message": f"PyTorch tensor compatibility: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


@register_framework("TensorFlow")
def tensorflow_analysis(target: dict) -> list[dict]:
    """TensorFlow perspective: Graphs, SavedModel, TFX."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    if category == "deep_learning":
        issues.append(
            {
                "issue": "No TFX pipeline integration",
                "severity": "medium",
                "recommendation": "Support tfx.Example, tf.Data pipelines",
            }
        )
        issues.append(
            {
                "issue": "No SavedModel export",
                "severity": "medium",
                "recommendation": "Implement tf.saved_model.save format",
            }
        )

    if category in ["nlp", "computer_vision"]:
        issues.append(
            {
                "issue": "No TF Lite conversion",
                "severity": "medium",
                "recommendation": "Add TFLite model export for edge deployment",
            }
        )

    critiques.append(
        {
            "framework": "TensorFlow",
            "target": name,
            "verdict": "PARTIAL" if issues else "PASS",
            "message": f"TensorFlow ecosystem integration: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


@register_framework("Optuna")
def optuna_analysis(target: dict) -> list[dict]:
    """Optuna perspective: Hyperparameter optimization."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    if category in ["deep_learning", "classical_ml"]:
        issues.append(
            {
                "issue": "No Optuna study integration",
                "severity": "medium",
                "recommendation": "Implement optuna.trial.Trial interface",
            }
        )

    if category in ["nlp", "computer_vision"]:
        issues.append(
            {
                "issue": "No pruning callbacks",
                "severity": "low",
                "recommendation": "Add optuna.pruners support",
            }
        )

    critiques.append(
        {
            "framework": "Optuna",
            "target": name,
            "verdict": "PARTIAL" if issues else "PASS",
            "message": f"Optuna HMO integration: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


@register_framework("Ray")
def ray_analysis(target: dict) -> list[dict]:
    """Ray perspective: Distributed computing."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    issues.append(
        {
            "issue": "No Ray actor/task support",
            "severity": "medium",
            "recommendation": "Add @ray.remote decorator support",
        }
    )

    if category in ["deep_learning", "nlp"]:
        issues.append(
            {
                "issue": "No Ray Train integration",
                "severity": "medium",
                "recommendation": "Implement ray.train scaling API",
            }
        )

    if category in ["reinforcement_learning"]:
        issues.append(
            {
                "issue": "No Ray RLlib integration",
                "severity": "low",
                "recommendation": "Add RLLib environment wrapper",
            }
        )

    critiques.append(
        {
            "framework": "Ray",
            "target": name,
            "verdict": "NEEDS_IMPROVEMENT",
            "message": f"Ray distributed computing: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


@register_framework("ONNX")
def onnx_analysis(target: dict) -> list[dict]:
    """ONNX perspective: Model interchange."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    if category == "deep_learning":
        issues.append(
            {
                "issue": "ONNX export unstable",
                "severity": "high",
                "recommendation": "Fix custom layer and control flow export",
            }
        )

    if category in ["nlp", "computer_vision"]:
        issues.append(
            {
                "issue": "No ONNX operators coverage",
                "severity": "medium",
                "recommendation": "Add missing ONNX operator support",
            }
        )

    if category == "gradient_boosting":
        issues.append(
            {
                "issue": "No ONNX export",
                "severity": "medium",
                "recommendation": "Implement onnx.convert sklearn API",
            }
        )

    critiques.append(
        {
            "framework": "ONNX",
            "target": name,
            "verdict": "NEEDS_IMPROVEMENT",
            "message": f"ONNX interoperability: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


@register_framework("MLflow")
def mlflow_analysis(target: dict) -> list[dict]:
    """MLflow perspective: MLOps, tracking, deployment."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    issues.append(
        {
            "issue": "No MLflow tracking integration",
            "severity": "medium",
            "recommendation": "Add mlflow.start_run context manager support",
        }
    )

    if category in ["deep_learning", "classical_ml"]:
        issues.append(
            {
                "issue": "No MLflow model registry",
                "severity": "medium",
                "recommendation": "Implement mlflow.<name>.log_model API",
            }
        )

    if category in ["deployment"]:
        issues.append(
            {
                "issue": "No MLflow model serving",
                "severity": "low",
                "recommendation": "Support mlflow models serve",
            }
        )

    critiques.append(
        {
            "framework": "MLflow",
            "target": name,
            "verdict": "NEEDS_IMPROVEMENT",
            "message": f"MLflow MLOps integration: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


@register_framework("SHAP")
def shap_analysis(target: dict) -> list[dict]:
    """SHAP perspective: Explainability."""
    name = target["name"]
    category = target["category"]

    critiques = []
    issues = []

    if category in ["deep_learning", "gradient_boosting"]:
        issues.append(
            {
                "issue": "No SHAP explainer integration",
                "severity": "medium",
                "recommendation": "Implement shap.Explainer interface",
            }
        )

    if category == "nlp":
        issues.append(
            {
                "issue": "No SHAP text explainability",
                "severity": "low",
                "recommendation": "Add shap.Explainer for token-level attributions",
            }
        )

    critiques.append(
        {
            "framework": "SHAP",
            "target": name,
            "verdict": "PARTIAL" if issues else "PASS",
            "message": f"SHAP explainability: {len(issues)} gaps",
            "issues": issues,
        }
    )

    return critiques


def run_full_analysis():
    """Run all framework analyses and correlate results."""
    all_critiques = []
    platform_issues = defaultdict(list)

    print("\n" + "=" * 80)
    print("UNIFIED MULTI-FRAMEWORK CROSS-PLATFORM ANALYSIS")
    print("=" * 80)
    print(
        f"\nAnalyzing {len(PLATFORMS)} platforms with {len(FRAMEWORKS)} frameworks..."
    )

    for target in PLATFORMS:
        for framework_name, framework_func in FRAMEWORKS.items():
            critiques = framework_func(target)
            all_critiques.extend(critiques)
            for c in critiques:
                if c["issues"]:
                    platform_issues[target["name"]].extend(c["issues"])

    print("\n" + "=" * 80)
    print("CORRELATION ANALYSIS")
    print("=" * 80)

    issue_counts = Counter()
    severity_counts = Counter()
    recommendation_buckets = defaultdict(list)

    for platform, issues in platform_issues.items():
        for issue in issues:
            issue_counts[issue["issue"]] += 1
            severity_counts[issue["severity"]] += 1
            recommendation_buckets[issue["recommendation"][:50]].append(platform)

    print("\nISSUE FREQUENCY (most common problems):")
    print("-" * 60)
    for issue, count in issue_counts.most_common(15):
        print(f"  {count:3}x {issue}")

    print("\nSEVERITY DISTRIBUTION:")
    print("-" * 60)
    for severity, count in severity_counts.items():
        print(f"  {severity:8}: {count:3} issues")

    print("\n" + "=" * 80)
    print("IMPROVEMENT RECOMMENDATIONS (Prioritized)")
    print("=" * 80)

    improvements = []

    for platform, issues in platform_issues.items():
        for issue in issues:
            improvements.append(
                {
                    "platform": platform,
                    "issue": issue["issue"],
                    "severity": issue["severity"],
                    "recommendation": issue["recommendation"],
                }
            )

    improvements.sort(
        key=lambda x: (
            {"high": 0, "medium": 1, "low": 2}[x["severity"]],
            -issue_counts[x["issue"]],
        )
    )

    print("\nTOP 20 PRIORITIZED IMPROVEMENTS:")
    print("-" * 80)
    for i, imp in enumerate(improvements[:20], 1):
        print(f"\n{i}. [{imp['severity'].upper()}] {imp['platform']}")
        print(f"   Issue: {imp['issue']}")
        print(f"   Fix: {imp['recommendation']}")

    print("\n" + "=" * 80)
    print("FRAMEWORK-SPECIFIC RECOMMENDATIONS")
    print("=" * 80)

    for framework_name in FRAMEWORKS.keys():
        framework_issues = [
            c for c in all_critiques if c["framework"] == framework_name
        ]
        needs_work = [
            c for c in framework_issues if c["verdict"] == "NEEDS_IMPROVEMENT"
        ]
        partial = [c for c in framework_issues if c["verdict"] == "PARTIAL"]
        passing = [c for c in framework_issues if c["verdict"] == "PASS"]

        print(f"\n{framework_name}:")
        print(
            f"  Needs work: {len(needs_work)} | Partial: {len(partial)} | Passing: {len(passing)}"
        )

    print("\n" + "=" * 80)
    print("PLATFORM HEALTH SCORES")
    print("=" * 80)

    platform_scores = {}
    for platform in PLATFORMS:
        name = platform["name"]
        issues = platform_issues.get(name, [])
        high = sum(1 for i in issues if i["severity"] == "high")
        medium = sum(1 for i in issues if i["severity"] == "medium")
        low = sum(1 for i in issues if i["severity"] == "low")

        base_score = 100
        base_score -= high * 15
        base_score -= medium * 8
        base_score -= low * 3
        platform_scores[name] = max(0, base_score)

    sorted_platforms = sorted(platform_scores.items(), key=lambda x: -x[1])

    print(f"\n{'Platform':<25} {'Score':<8} {'Grade':<6} {'Critical Issues'}")
    print("-" * 60)
    for name, score in sorted_platforms:
        grade = (
            "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"
        )
        issues = platform_issues.get(name, [])
        critical = sum(1 for i in issues if i["severity"] == "high")
        print(f"{name:<25} {score:<8} {grade:<6} {critical} high-severity")

    return {
        "platforms": PLATFORMS,
        "critiques": all_critiques,
        "platform_issues": dict(platform_issues),
        "improvements": improvements[:20],
        "scores": platform_scores,
    }


if __name__ == "__main__":
    results = run_full_analysis()
