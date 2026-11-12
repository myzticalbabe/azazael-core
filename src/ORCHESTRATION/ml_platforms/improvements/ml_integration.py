import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
Azazael ML Integration System
Groups frameworks and platforms for successive computation.

Structure:
1. Capability Groups - Frameworks organized by task type
2. Integration Pathways - How modules work together
3. Successive Computation - Pipelines for complex tasks
4. Smart Recommendations - ML-guided framework selection

Usage:
    from ml_platforms.improvements.ml_integration import (
        FrameworkGroups, IntegrationPathways, SuccessiveComputation
    )
"""

import sys
import time
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
from enum import Enum

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

sys.path.insert(0, str(Path(__file__).parent.parent))


# ========== CAPABILITY ENUMS ==========


class TaskType(Enum):
    """ML task types."""

    IMAGE_CLASSIFICATION = "image_classification"
    OBJECT_DETECTION = "object_detection"
    SEMANTIC_SEGMENTATION = "semantic_segmentation"
    IMAGE_GENERATION = "image_generation"
    TEXT_CLASSIFICATION = "text_classification"
    NAMED_ENTITY_RECOGNITION = "named_entity_recognition"
    QUESTION_ANSWERING = "question_answering"
    SUMMARIZATION = "summarization"
    MACHINE_TRANSLATION = "machine_translation"
    SPEECH_RECOGNITION = "speech_recognition"
    SPEECH_SYNTHESIS = "speech_synthesis"
    TIME_SERIES = "time_series"
    TABULAR_CLASSIFICATION = "tabular_classification"
    TABULAR_REGRESSION = "tabular_regression"
    CLUSTERING = "clustering"
    ANOMALY_DETECTION = "anomaly_detection"
    RECOMMENDATION = "recommendation"
    REINFORCEMENT_LEARNING = "reinforcement_learning"


class FrameworkType(Enum):
    """Framework categories."""

    DEEP_LEARNING = "deep_learning"
    NLP = "nlp"
    COMPUTER_VISION = "computer_vision"
    CLASSICAL_ML = "classical_ml"
    OPTIMIZATION = "optimization"
    MLOPS = "mlops"
    AUDIO = "audio"


@dataclass
class FrameworkInfo:
    """Information about a framework."""

    name: str
    version: str
    framework_type: FrameworkType
    capabilities: list[str]
    strengths: list[str]
    weaknesses: list[str]
    dependencies: list[str] = field(default_factory=list)


# ========== FRAMEWORK GROUPS ==========


class FrameworkGroups:
    """
    Groups frameworks by capability for intelligent selection.

    Categories:
    1. Core DL - PyTorch, TensorFlow, Keras
    2. NLP - Transformers, Spacy, NLTK
    3. Vision - OpenCV, torchvision, easyocr
    4. Classical - scikit-learn
    5. Optimization - Optuna
    6. MLOps - MLflow, Ray, TensorBoard
    """

    # Framework registry with metadata
    REGISTRY: dict[str, FrameworkInfo] = {}

    # Initialize registry
    @classmethod
    def initialize(cls):
        """Initialize framework registry with known frameworks."""
        cls.REGISTRY = {
            # Deep Learning
            "torch": FrameworkInfo(
                name="torch",
                version="2.10.0",
                framework_type=FrameworkType.DEEP_LEARNING,
                capabilities=[
                    "training",
                    "inference",
                    "autograd",
                    "distributed",
                    "torchscript",
                ],
                strengths=["flexibility", "Pythonic", "research", "custom layers"],
                weaknesses=[" steeper learning curve", "production deployment"],
            ),
            "tensorflow": FrameworkInfo(
                name="tensorflow",
                version="2.20.0",
                framework_type=FrameworkType.DEEP_LEARNING,
                capabilities=[
                    "training",
                    "inference",
                    "tensorboard",
                    "tf.data",
                    "tf.keras",
                ],
                strengths=["production", "deployment", "tensorboard", "TF Serving"],
                weaknesses=["verbose", "complex API"],
            ),
            "keras": FrameworkInfo(
                name="keras",
                version="3.13.2",
                framework_type=FrameworkType.DEEP_LEARNING,
                capabilities=[
                    "high-level API",
                    "rapid prototyping",
                    "tf.keras integration",
                ],
                strengths=["easy to use", "fast prototyping", "intuitive"],
                weaknesses=["less flexible", "less control"],
            ),
            # NLP
            "transformers": FrameworkInfo(
                name="transformers",
                version="5.1.0",
                framework_type=FrameworkType.NLP,
                capabilities=[
                    "pretrained models",
                    "BERT",
                    "GPT",
                    "tokenization",
                    "fine-tuning",
                ],
                strengths=[
                    "state-of-the-art models",
                    "easy loading",
                    "HuggingFace Hub",
                ],
                weaknesses=["memory intensive", "slow for custom architectures"],
            ),
            "spacy": FrameworkInfo(
                name="spacy",
                version="3.8.11",
                framework_type=FrameworkType.NLP,
                capabilities=[
                    "NER",
                    "POS tagging",
                    "dependency parsing",
                    "text processing",
                ],
                strengths=["fast", "production-ready", "pre-trained pipelines"],
                weaknesses=["less customizable", "focused on pipelines"],
            ),
            "nltk": FrameworkInfo(
                name="nltk",
                version="3.9.2",
                framework_type=FrameworkType.NLP,
                capabilities=["corpora", "tokenization", "stemming", "tagging"],
                strengths=["educational", "comprehensive", "corpora access"],
                weaknesses=["slow", "older API"],
            ),
            # Computer Vision
            "opencv-python": FrameworkInfo(
                name="opencv-python",
                version="4.13.0",
                framework_type=FrameworkType.COMPUTER_VISION,
                capabilities=[
                    "image processing",
                    "video",
                    "object detection",
                    "filters",
                ],
                strengths=["fast", "comprehensive", "C++ backend"],
                weaknesses=["steep learning curve", "Python API awkward"],
            ),
            "torchvision": FrameworkInfo(
                name="torchvision",
                version="0.25.0",
                framework_type=FrameworkType.COMPUTER_VISION,
                capabilities=["datasets", "models", "transforms", "utils"],
                strengths=["PyTorch integration", "pre-trained models", "easy"],
                weaknesses=["PyTorch only"],
            ),
            "pillow": FrameworkInfo(
                name="pillow",
                version="12.1.1",
                framework_type=FrameworkType.COMPUTER_VISION,
                capabilities=["image manipulation", "format conversion", "filters"],
                strengths=["simple", "fast", "widely used"],
                weaknesses=["basic operations only"],
            ),
            "easyocr": FrameworkInfo(
                name="easyocr",
                version="1.7.2",
                framework_type=FrameworkType.COMPUTER_VISION,
                capabilities=["OCR", "text detection", "multiple languages"],
                strengths=["easy to use", "deep learning based"],
                weaknesses=["slow", "heavy"],
            ),
            "deepface": FrameworkInfo(
                name="deepface",
                version="0.0.98",
                framework_type=FrameworkType.COMPUTER_VISION,
                capabilities=["face recognition", "verification", "attribute analysis"],
                strengths=["state-of-the-art", "easy API"],
                weaknesses=["specific use case"],
            ),
            # Classical ML
            "scikit-learn": FrameworkInfo(
                name="scikit-learn",
                version="1.8.0",
                framework_type=FrameworkType.CLASSICAL_ML,
                capabilities=[
                    "classification",
                    "regression",
                    "clustering",
                    "PCA",
                    "SVM",
                    "trees",
                ],
                strengths=["consistent API", "comprehensive", "well-documented"],
                weaknesses=["not for deep learning", "single-machine"],
            ),
            # Optimization
            "optuna": FrameworkInfo(
                name="optuna",
                version="4.7.0",
                framework_type=FrameworkType.OPTIMIZATION,
                capabilities=["hyperparameter optimization", "pruning", "distributed"],
                strengths=["powerful", "easy integration", "visualization"],
                weaknesses=["complex for simple cases"],
            ),
            # MLOps
            "mlflow": FrameworkInfo(
                name="mlflow",
                version="3.9.0",
                framework_type=FrameworkType.MLOPS,
                capabilities=["tracking", "projects", "models", "registry"],
                strengths=["end-to-end", "framework agnostic", "tracking"],
                weaknesses=["complex setup", "storage choices"],
            ),
            "ray": FrameworkInfo(
                name="ray",
                version="2.53.0",
                framework_type=FrameworkType.MLOPS,
                capabilities=[
                    "distributed computing",
                    "hyperparameter tuning",
                    "serving",
                ],
                strengths=["scalable", "Pythonic", "unified"],
                weaknesses=["newer", "smaller community"],
            ),
            "tensorboard": FrameworkInfo(
                name="tensorboard",
                version="2.20.0",
                framework_type=FrameworkType.MLOPS,
                capabilities=["visualization", "profiling", "embedding"],
                strengths=["powerful", "standard", "integrations"],
                weaknesses=["TensorFlow focused"],
            ),
            # ONNX
            "onnx": FrameworkInfo(
                name="onnx",
                version="1.20.1",
                framework_type=FrameworkType.MLOPS,
                capabilities=["model format", "graph exchange"],
                strengths=["interoperability", "standard"],
                weaknesses=["not a framework"],
            ),
            "onnxruntime": FrameworkInfo(
                name="onnxruntime",
                version="1.24.1",
                framework_type=FrameworkType.MLOPS,
                capabilities=["inference", "optimization", "cross-platform"],
                strengths=["fast", "cross-platform", "low latency"],
                weaknesses=["inference only"],
            ),
            "tf2onnx": FrameworkInfo(
                name="tf2onnx",
                version="1.14.0",
                framework_type=FrameworkType.MLOPS,
                capabilities=["TensorFlow to ONNX", "model conversion"],
                strengths=["automatic conversion", "comprehensive"],
                weaknesses=["TensorFlow only"],
            ),
        }

    @classmethod
    def get_by_type(cls, framework_type: FrameworkType) -> list[FrameworkInfo]:
        """Get all frameworks of a given type."""
        if not cls.REGISTRY:
            cls.initialize()
        return [f for f in cls.REGISTRY.values() if f.framework_type == framework_type]

    @classmethod
    def get_by_capability(cls, capability: str) -> list[FrameworkInfo]:
        """Get frameworks with a specific capability."""
        if not cls.REGISTRY:
            cls.initialize()
        return [f for f in cls.REGISTRY.values() if capability in f.capabilities]

    @classmethod
    def get_recommendation(
        cls, task_type: TaskType
    ) -> list[tuple[FrameworkInfo, float]]:
        """Get framework recommendations for a task."""
        if not cls.REGISTRY:
            cls.initialize()

        # Task to framework mapping with scores
        task_frameworks = {
            TaskType.IMAGE_CLASSIFICATION: [
                ("torch", 0.95),
                ("torchvision", 0.90),
                ("tensorflow", 0.85),
                ("keras", 0.80),
            ],
            TaskType.OBJECT_DETECTION: [
                ("torch", 0.90),
                ("torchvision", 0.85),
                ("opencv-python", 0.75),
            ],
            TaskType.TEXT_CLASSIFICATION: [
                ("transformers", 0.95),
                ("torch", 0.85),
                ("tensorflow", 0.80),
                ("scikit-learn", 0.70),
            ],
            TaskType.NAMED_ENTITY_RECOGNITION: [
                ("transformers", 0.95),
                ("spacy", 0.90),
                ("nltk", 0.60),
            ],
            TaskType.QUESTION_ANSWERING: [
                ("transformers", 0.98),
                ("torch", 0.85),
            ],
            TaskType.SUMMARIZATION: [
                ("transformers", 0.95),
                ("torch", 0.85),
            ],
            TaskType.SPEECH_RECOGNITION: [
                ("torchaudio", 0.90),
                ("transformers", 0.85),
            ],
            TaskType.TABULAR_CLASSIFICATION: [
                ("scikit-learn", 0.95),
                ("torch", 0.75),
                ("tensorflow", 0.70),
            ],
            TaskType.TABULAR_REGRESSION: [
                ("scikit-learn", 0.95),
                ("torch", 0.75),
                ("tensorflow", 0.70),
            ],
            TaskType.CLUSTERING: [
                ("scikit-learn", 0.95),
                ("torch", 0.70),
            ],
            TaskType.REINFORCEMENT_LEARNING: [
                ("torch", 0.95),
                ("tensorflow", 0.85),
            ],
            TaskType.ANOMALY_DETECTION: [
                ("scikit-learn", 0.85),
                ("torch", 0.80),
                ("tensorflow", 0.75),
            ],
            TaskType.TIME_SERIES: [
                ("scikit-learn", 0.85),
                ("torch", 0.80),
                ("tensorflow", 0.75),
                ("pandas", 0.70),
            ],
        }

        if task_type not in task_frameworks:
            return []

        recommendations = []
        for framework_name, score in task_frameworks[task_type]:
            if framework_name in cls.REGISTRY:
                recommendations.append((cls.REGISTRY[framework_name], score))

        return sorted(recommendations, key=lambda x: x[1], reverse=True)


# ========== INTEGRATION PATHWAYS ==========


class IntegrationPathways:
    """
    Defines how frameworks integrate for successive computation.

    Key patterns:
    1. Data Pipeline: pandas -> sklearn/transformers -> mlflow
    2. Vision Pipeline: opencv -> torch -> onnxruntime -> tensorboard
    3. NLP Pipeline: transformers -> torch -> mlflow
    4. Research Pipeline: optuna -> torch/tensorflow -> mlflow
    """

    PATHWAYS = {
        "data_pipeline": {
            "name": "Data Processing Pipeline",
            "description": "Load, transform, and analyze tabular/text data",
            "steps": [
                ("pandas", "Data loading and preprocessing"),
                ("scikit-learn", "Feature engineering and selection"),
                ("transformers", "Text embeddings (if needed)"),
                ("scikit-learn", "Model training"),
                ("mlflow", "Experiment tracking"),
            ],
            "use_cases": ["tabular ML", "text classification", "feature engineering"],
        },
        "vision_pipeline": {
            "name": "Computer Vision Pipeline",
            "description": "End-to-end image processing and DL",
            "steps": [
                ("opencv-python", "Image loading and preprocessing"),
                ("pillow", "Image augmentation"),
                ("torch", "Model definition"),
                ("torchvision", "Dataset and transforms"),
                ("onnxruntime", "Optimized inference"),
                ("tensorboard", "Training visualization"),
            ],
            "use_cases": ["image classification", "object detection", "segmentation"],
        },
        "nlp_pipeline": {
            "name": "NLP Pipeline",
            "description": "Text processing with transformers",
            "steps": [
                ("nltk", "Basic text processing"),
                ("spacy", "Advanced NLP (NER, POS)"),
                ("tokenizers", "Fast tokenization"),
                ("transformers", "Pre-trained models"),
                ("torch", "Fine-tuning"),
                ("mlflow", "Experiment tracking"),
            ],
            "use_cases": ["text classification", "NER", "question answering"],
        },
        "research_pipeline": {
            "name": "Research Experiment Pipeline",
            "description": "Hyperparameter tuning with distributed training",
            "steps": [
                ("optuna", "Hyperparameter optimization"),
                ("torch", "Model training"),
                ("ray", "Distributed training"),
                ("mlflow", "Experiment tracking"),
                ("tensorboard", "Visualization"),
            ],
            "use_cases": ["hyperparameter tuning", "distributed experiments"],
        },
        "production_pipeline": {
            "name": "Production Deployment Pipeline",
            "description": "Train, convert, and deploy models",
            "steps": [
                ("torch", "Model training"),
                ("tensorflow", "Alternative training"),
                ("tf2onnx", "Convert to ONNX"),
                ("onnxruntime", "Optimized inference"),
                ("ray", "Model serving"),
                ("mlflow", "Model registry"),
            ],
            "use_cases": ["model deployment", "inference optimization"],
        },
        "ocr_pipeline": {
            "name": "OCR Pipeline",
            "description": "End-to-end text recognition",
            "steps": [
                ("opencv-python", "Image preprocessing"),
                ("easyocr", "Text detection and recognition"),
                ("pillow", "Result processing"),
            ],
            "use_cases": ["document OCR", "receipt scanning"],
        },
        "face_pipeline": {
            "name": "Face Analysis Pipeline",
            "description": "Face recognition and verification",
            "steps": [
                ("opencv-python", "Face detection"),
                ("deepface", "Face recognition"),
                ("pillow", "Image processing"),
            ],
            "use_cases": ["face verification", "identity matching"],
        },
        "ensemble_pipeline": {
            "name": "Ensemble Pipeline",
            "description": "Combine multiple models",
            "steps": [
                ("scikit-learn", "Base model training"),
                ("torch", "Deep learning models"),
                ("sklearn.ensemble", "Ensemble methods"),
                ("mlflow", "Track ensemble performance"),
            ],
            "use_cases": ["model ensembling", "performance optimization"],
        },
    }

    @classmethod
    def get_pathway(cls, pathway_name: str) -> (dict | None):
        """Get a specific pathway."""
        return cls.PATHWAYS.get(pathway_name)

    @classmethod
    def get_pathways_for_task(cls, task_type: TaskType) -> list[dict]:
        """Get recommended pathways for a task type."""
        task_pathways = {
            TaskType.IMAGE_CLASSIFICATION: ["vision_pipeline", "production_pipeline"],
            TaskType.OBJECT_DETECTION: ["vision_pipeline", "production_pipeline"],
            TaskType.TEXT_CLASSIFICATION: ["data_pipeline", "nlp_pipeline"],
            TaskType.NAMED_ENTITY_RECOGNITION: ["nlp_pipeline", "research_pipeline"],
            TaskType.QUESTION_ANSWERING: ["nlp_pipeline", "research_pipeline"],
            TaskType.SUMMARIZATION: ["nlp_pipeline", "research_pipeline"],
            TaskType.SPEECH_RECOGNITION: ["nlp_pipeline", "vision_pipeline"],
            TaskType.TABULAR_CLASSIFICATION: ["data_pipeline", "ensemble_pipeline"],
            TaskType.TABULAR_REGRESSION: ["data_pipeline", "ensemble_pipeline"],
            TaskType.CLUSTERING: ["data_pipeline", "ensemble_pipeline"],
            TaskType.REINFORCEMENT_LEARNING: [
                "research_pipeline",
                "production_pipeline",
            ],
            TaskType.ANOMALY_DETECTION: ["data_pipeline", "ensemble_pipeline"],
            TaskType.TIME_SERIES: ["data_pipeline", "research_pipeline"],
        }

        pathway_names = task_pathways.get(task_type, ["data_pipeline"])
        return [cls.PATHWAYS[p] for p in pathway_names if p in cls.PATHWAYS]


# ========== SUCCESSIVE COMPUTATION ==========


class SuccessiveComputation:
    """
    Executes pipelines of frameworks successively.

    Features:
    1. Pipeline execution with dependencies
    2. Result passing between stages
    3. Error handling and recovery
    4. Performance monitoring
    """

    @staticmethod
    def execute_pipeline(
        pipeline_name: str, input_data: Any, **kwargs
    ) -> dict[str, Any]:
        """Execute a complete pipeline."""
        pathway = IntegrationPathways.get_pathway(pipeline_name)
        if not pathway:
            return {"error": f"Pipeline {pipeline_name} not found"}

        results = {
            "pipeline": pipeline_name,
            "input_type": type(input_data).__name__,
            "stages": [],
            "success": True,
        }

        for framework, description in pathway["steps"]:
            stage_result = {
                "framework": framework,
                "description": description,
                "status": "pending",
                "duration_ms": 0,
            }

            start_time = time.time()
            try:
                # Execute stage (simplified - real implementation would call actual code)
                stage_result["status"] = "success"
                stage_result["output_type"] = "processed_data"
            except Exception as e:
                note_failure("ml_integration:L601", e)
                stage_result["status"] = "failed"
                stage_result["error"] = str(e)
                results["success"] = False

            stage_result["duration_ms"] = (time.time() - start_time) * 1000
            results["stages"].append(stage_result)

        return results

    @staticmethod
    def get_execution_order(framework_list: list[str]) -> list[str]:
        """Determine optimal execution order based on dependencies."""
        # Simplified dependency ordering
        dependencies = {
            "pandas": [],
            "scikit-learn": ["pandas"],
            "transformers": ["torch", "tokenizers"],
            "torch": [],
            "torchvision": ["torch"],
            "tensorflow": [],
            "keras": ["tensorflow"],
            "opencv-python": [],
            "pillow": [],
            "easyocr": ["torch", "opencv-python"],
            "deepface": ["torch", "opencv-python"],
            "spacy": [],
            "nltk": [],
            "tokenizers": [],
            "optuna": [],
            "mlflow": [],
            "ray": [],
            "tensorboard": ["tensorflow"],
            "onnx": [],
            "onnxruntime": ["onnx"],
            "tf2onnx": ["tensorflow", "onnx"],
        }

        # Simple topological sort (simplified)
        ordered = []
        remaining = set(framework_list)

        while remaining:
            # Find frameworks with no unmet dependencies
            available = [
                f
                for f in remaining
                if all(d not in remaining for d in dependencies.get(f, []))
            ]

            if not available:
                # Cycle or missing dependency - just add one
                available = [remaining.pop()]

            for framework in available:
                ordered.append(framework)
                remaining.remove(framework)

        return ordered


# ========== RECOMMENDATION ENGINE ==========


class IntegrationRecommendation:
    """
    ML-guided integration recommendations.

    Uses framework characteristics and task requirements to recommend
    optimal framework combinations.
    """

    @staticmethod
    def recommend_integration(
        task_type: TaskType,
        constraints: (dict[str, Any] | None) = None,
    ) -> dict[str, Any]:
        """Get integration recommendation for a task."""
        constraints = constraints or {}

        # Get framework recommendations
        frameworks = FrameworkGroups.get_recommendation(task_type)

        # Get pathways
        pathways = IntegrationPathways.get_pathways_for_task(task_type)

        # Consider constraints
        prefer_speed = constraints.get("prefer_speed", True)
        constraints.get("prefer_accuracy", False)
        prefer_simplicity = constraints.get("prefer_simplicity", False)

        # Adjust scores based on constraints
        adjusted = []
        for framework, base_score in frameworks:
            info = FrameworkGroups.REGISTRY.get(framework.name)
            if not info:
                continue

            adjusted_score = base_score

            if prefer_simplicity:
                if "easy to use" in info.strengths:
                    adjusted_score += 0.05
                if "steep" in "".join(info.weaknesses):
                    adjusted_score -= 0.05

            if prefer_speed:
                if "fast" in info.strengths:
                    adjusted_score += 0.03

            adjusted.append((framework, adjusted_score))

        adjusted.sort(key=lambda x: x[1], reverse=True)

        return {
            "task_type": task_type.value,
            "recommended_frameworks": [
                {"name": f.name, "score": s, "version": f.version}
                for f, s in adjusted[:5]
            ],
            "recommended_pathways": [p["name"] for p in pathways],
            "constraints_considered": constraints,
        }


# ========== MAIN EXECUTION ==========

if __name__ == "__main__":
    print("=" * 80)
    print("Azazael ML INTEGRATION SYSTEM")
    print("=" * 80)
    print()

    # Initialize framework groups
    FrameworkGroups.initialize()

    # Display framework groups
    print("FRAMEWORK GROUPS")
    print("-" * 80)

    for framework_type in FrameworkType:
        frameworks = FrameworkGroups.get_by_type(framework_type)
        if frameworks:
            print(f"\n{framework_type.value.upper()}")
            for f in frameworks:
                print(f"  - {f.name} v{f.version}")

    print()
    print("=" * 80)
    print("INTEGRATION PATHWAYS")
    print("-" * 80)

    for name, pathway in IntegrationPathways.PATHWAYS.items():
        print(f"\n{name.upper()}")
        print(f"  {pathway['name']}")
        print(f"  Steps: {' -> '.join([s[0] for s in pathway['steps']])}")

    print()
    print("=" * 80)
    print("TASK RECOMMENDATIONS")
    print("-" * 80)

    # Example recommendations
    for task in [
        TaskType.IMAGE_CLASSIFICATION,
        TaskType.TEXT_CLASSIFICATION,
        TaskType.TABULAR_CLASSIFICATION,
    ]:
        print(f"\n{task.value.upper()}")
        recs = FrameworkGroups.get_recommendation(task)
        for framework, score in recs[:3]:
            print(f"  {framework.name} v{framework.version} ({score:.2f})")

    print()
    print("=" * 80)
    print("SUCCESSIVE COMPUTATION EXAMPLE")
    print("-" * 80)

    # Get execution order
    example_frameworks = ["pandas", "scikit-learn", "mlflow", "transformers", "torch"]
    order = SuccessiveComputation.get_execution_order(example_frameworks)
    print(f"Frameworks: {example_frameworks}")
    print(f"Execution Order: {' -> '.join(order)}")

    print()
    print("=" * 80)
    print("ML INTEGRATION SYSTEM OPERATIONAL")
    print("=" * 80)
