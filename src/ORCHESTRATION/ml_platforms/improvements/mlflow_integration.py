# coding: utf-8
"""
Mlflow Integration

Module for improvements functionality.

Functions:
    - log_params()
    - log_metrics()
    - start_run()
    - end_run()
    - log_params()
    - log_metrics()
    - log_model()
    - log_artifact()
    - log_dict()
    - set_tag()

Classes:
    - MLflowLogger
    - _GenericModelWrapper

Usage:
    >>> import mlflow_integration
    >>> mlflow_integration.log_params()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
MLflow Integration Wrapper
Solves: No MLflow tracking integration (27/27 platforms affected)
Provides: Universal MLflow logging for any ML platform

Usage:
    from mlflow_utils import MLflowLogger, log_params, log_metrics

    logger = MLflowLogger(experiment="my_experiment")

    # Log parameters from any framework
    logger.log_params({
        "learning_rate": 0.001,
        "batch_size": 32,
    })

    # Log metrics from any framework
    logger.log_metrics({
        "accuracy": 0.95,
        "loss": 0.05,
    })

    # Log models from any framework
    logger.log_model(model, artifact_path="model")
"""

from typing import Any, List
import numpy as np
import json

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    import mlflow

    HAS_MLFLOW = True
except ImportError as exc:
    note_failure("mlflow_integration:L65", exc)
    HAS_MLFLOW = False


class MLflowLogger:
    _active_logger: "MLflowLogger | None" = None
    """
    Universal MLflow logger that works with any ML framework.

    WHY THIS MATTERS:
    - 27 platforms lack MLflow tracking integration
    - Each framework has different mlflow.<name>.log_model API
    - MLOps requires consistent experiment tracking

    IMPLEMENTATION:
    - Framework-agnostic parameter/metric logging
    - Auto-detection of model type for proper logging
    - Simplified API for common operations
    """

    def __init__(
        self,
        experiment_name: str,
        tracking_uri: (str | None) = None,
        artifact_location: (str | None) = None,
    ):
        """
        Initialize MLflow logger.

        Args:
            experiment_name: Name of MLflow experiment
            tracking_uri: Optional MLflow tracking server URI
            artifact_location: Optional S3 or local path for artifacts
        """
        if not HAS_MLFLOW:
            raise ImportError("MLflow not installed. Run: pip install mlflow")

        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)

        if artifact_location:
            mlflow.set_experiment_tag("artifact_location", artifact_location)

        self.experiment = mlflow.get_experiment_by_name(experiment_name)
        if self.experiment is None:
            mlflow.create_experiment(
                experiment_name, artifact_location=artifact_location
            )
            self.experiment = mlflow.get_experiment_by_name(experiment_name)

        mlflow.set_experiment(experiment_name)
        self.run = None
        MLflowLogger._active_logger = self

    def start_run(self, run_name: (str | None) = None) -> "MLflowLogger":
        """Start an MLflow run."""
        self.run = mlflow.start_run(run_name=run_name)
        return self

    def end_run(self):
        """End the current MLflow run."""
        if self.run:
            mlflow.end_run()
            self.run = None

    def log_params(self, params: dict[str, Any]) -> None:
        """Log parameters (works with any framework's params)."""
        if not self.run:
            self.start_run()

        filtered = {
            k: v for k, v in params.items() if isinstance(v, (int, float, str, bool))
        }
        mlflow.log_params(filtered)

    def log_metrics(
        self,
        metrics: dict[str, float],
        step: (int | None) = None,
    ) -> None:
        """Log metrics (works with any framework's metrics)."""
        if not self.run:
            self.start_run()

        for k, v in metrics.items():
            if isinstance(v, (int, float, np.number)):  # type: ignore[reportUnnecessaryIsInstance]
                mlflow.log_metric(k, float(v), step=step)

    def log_model(
        self,
        model: Any,
        artifact_path: str,
        framework: (str | None) = None,
        registered_model_name: (str | None) = None,
    ) -> None:
        """
        Log model from any framework.

        Args:
            model: Model object (PyTorch, sklearn, Keras, etc.)
            artifact_path: Path to save model artifacts
            framework: Model framework (auto-detected if None)
            registered_model_name: Optional name for model registry
        """
        if not self.run:
            self.start_run()

        if framework is None:
            framework = self._detect_framework(model)

        if framework == "sklearn":
            mlflow.sklearn.log_model(
                model, artifact_path, registered_model_name=registered_model_name
            )
        elif framework == "pytorch":
            mlflow.pytorch.log_model(
                model, artifact_path, registered_model_name=registered_model_name
            )
        elif framework == "tensorflow":
            mlflow.tensorflow.log_model(
                model, artifact_path, registered_model_name=registered_model_name
            )
        elif framework == "keras":
            mlflow.keras.log_model(
                model, artifact_path, registered_model_name=registered_model_name
            )
        elif framework == "xgboost":
            mlflow.xgboost.log_model(
                model, artifact_path, registered_model_name=registered_model_name
            )
        elif framework == "lightgbm":
            mlflow.lightgbm.log_model(
                model, artifact_path, registered_model_name=registered_model_name
            )
        elif framework == "catboost":
            mlflow.catboost.log_model(
                model, artifact_path, registered_model_name=registered_model_name
            )
        else:
            mlflow.pyfunc.log_model(
                artifact_path,
                python_model=_GenericModelWrapper(model),
                registered_model_name=registered_model_name,
            )

    def log_artifact(self, local_path: str) -> None:
        """Log a local file or directory as an artifact."""
        if not self.run:
            self.start_run()
        mlflow.log_artifact(local_path)

    def log_dict(self, dictionary: dict, artifact_file: str) -> None:
        """Log a dictionary as a JSON or YAML file."""
        if not self.run:
            self.start_run()

        import tempfile
        import os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(dictionary, f, indent=2)
            mlflow.log_artifact(f.name, artifact_path=artifact_file)
            os.unlink(f.name)

    def set_tag(self, key: str, value: str) -> None:
        """set a tag on the current run."""
        if not self.run:
            self.start_run()
        mlflow.set_tag(key, value)

    def _detect_framework(self, model: Any) -> str:
        """Auto-detect model framework from type."""
        model_type = type(model).__module__.split(".")[0]

        framework_map = {
            "sklearn": ["sklearn", "sklearn"],
            "torch": ["torch", "pytorch"],
            "tensorflow": ["tensorflow", "tf"],
            "keras": ["keras"],
            "xgboost": ["xgboost"],
            "lightgbm": ["lightgbm"],
            "catboost": ["catboost"],
        }

        for framework, prefixes in framework_map.items():
            if any(model_type.startswith(p) for p in prefixes):
                return framework

        return "generic"


class _GenericModelWrapper:
    """Wrapper for models that don't have native MLflow support."""

    def __init__(self, model: Any):
        self.model = model

    def predict(self, data):
        """Prediction method required by MLflow pyfunc."""

        if hasattr(self.model, "predict"):
            return self.model.predict(data)
        elif hasattr(self.model, "forward"):
            return self.model.forward(data)
        return data


def log_params(params: dict[str, Any]) -> None:
    """Module-level function to log parameters."""
    logger = MLflowLogger._active_logger
    if logger:
        logger.log_params(params)


def log_metrics(metrics: dict[str, float]) -> None:
    """Module-level function to log metrics."""
    logger = MLflowLogger._active_logger
    if logger:
        logger.log_metrics(metrics)


if __name__ == "__main__":
    from sklearn.ensemble import RandomForestClassifier
    import numpy as np

    print("=== MLflow Integration Demo ===")

    logger = MLflowLogger(experiment_name="demo_experiment")
    logger.start_run(run_name="test_run")

    logger.log_params(
        {
            "n_estimators": 100,
            "max_depth": 10,
            "learning_rate": 0.01,
        }
    )

    logger.log_metrics(
        {
            "accuracy": 0.95,
            "precision": 0.93,
            "recall": 0.92,
            "f1": 0.925,
        }
    )

    X = np.random.randn(100, 10)
    y = np.random.randint(0, 2, 100)
    model = RandomForestClassifier(n_estimators=100)
    model.fit(X, y)

    logger.log_model(model, artifact_path="rf_model")
    logger.set_tag("dataset", "synthetic")
    logger.set_tag("version", "1.0")

    logger.end_run()

    print("Demo complete! Check MLflow UI with: mlflow ui")
