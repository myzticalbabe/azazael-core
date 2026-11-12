---
title: MLflow Integration API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# mlflow_integration.py

**Location:** `ml_platforms/improvements/mlflow_integration.py`

**Purpose:** Universal MLflow logging wrapper for any ML framework

---

## What This File Does

`mlflow_integration.py` provides a unified interface to MLflow experiment tracking, working with PyTorch, TensorFlow, sklearn, XGBoost, LightGBM, CatBoost, and custom models.

```
┌─────────────────────────────────────────────────────────────────┐
│                MLFLOW_INTEGRATION.PY                            │
│                                                                 │
│   Universal MLflow Wrapper:                                     │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐   │
│   │              SUPPORTED FRAMEWORKS                     │   │
│   │   • PyTorch      → mlflow.pytorch.log_model()       │   │
│   │   • TensorFlow   → mlflow.tensorflow.log_model()    │   │
│   │   • Keras        → mlflow.keras.log_model()          │   │
│   │   • sklearn      → mlflow.sklearn.log_model()       │   │
│   │   • XGBoost      → mlflow.xgboost.log_model()      │   │
│   │   • LightGBM     → mlflow.lightgbm.log_model()     │   │
│   │   • CatBoost     → mlflow.catboost.log_model()     │   │
│   │   • Custom       → mlflow.pyfunc.log_model()       │   │
│   └───────────────────────────────────────────────────────┘   │
│                                                                 │
│   Features:                                                     │
│   • Framework auto-detection                                     │
│   • Simplified logging API                                       │
│   • Experiment management                                        │
│   • Model registry                                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Classes

### MLflowLogger

```python
class MLflowLogger:
    """
    Universal MLflow logger that works with any ML framework.
    
    Why this matters:
    - 27 platforms lack MLflow tracking integration
    - Each framework has different log_model API
    - MLOps requires consistent experiment tracking
    """
    
    def __init__(
        self,
        experiment_name: str,
        tracking_uri: Optional[str] = None,
        artifact_location: Optional[str] = None,
    ):
        """Initialize logger."""
        
    def start_run(self, run_name: Optional[str] = None) -> "MLflowLogger":
        """Start an MLflow run."""
        
    def end_run(self):
        """End the current MLflow run."""
        
    def log_params(self, params: Dict[str, Any]) -> None:
        """Log parameters."""
        
    def log_metrics(
        self,
        metrics: Dict[str, float],
        step: Optional[int] = None,
    ) -> None:
        """Log metrics."""
        
    def log_model(
        self,
        model: Any,
        artifact_path: str,
        framework: Optional[str] = None,
        registered_model_name: Optional[str] = None,
    ) -> None:
        """Log model from any framework."""
        
    def log_artifact(self, local_path: str) -> None:
        """Log a local file as artifact."""
        
    def log_dict(self, dictionary: Dict, artifact_file: str) -> None:
        """Log dict as JSON/YAML artifact."""
        
    def set_tag(self, key: str, value: str) -> None:
        """Set tag on current run."""
```

---

## Usage Examples

### Basic Setup

```python
from improvements.mlflow_integration import MLflowLogger

logger = MLflowLogger(experiment="my_experiment")
logger.start_run(run_name="test_run")

# Log parameters
logger.log_params({
    "learning_rate": 0.001,
    "batch_size": 32,
    "epochs": 100,
})

# Log metrics
logger.log_metrics({
    "accuracy": 0.95,
    "loss": 0.05,
    "val_accuracy": 0.93,
})

logger.end_run()
```

### Log PyTorch Model

```python
import torch
import torch.nn as nn
from improvements.mlflow_integration import MLflowLogger

# Define model
class Net(nn.Module):
    def forward(self, x):
        return self.fc(x)

model = Net()

# Log with MLflow
logger = MLflowLogger(experiment="cv_experiment")
logger.start_run(run_name="cnn_training")

logger.log_params({"lr": 0.001, "batch_size": 64})
logger.log_metrics({"train_acc": 0.89, "val_acc": 0.87})

# Auto-detects PyTorch
logger.log_model(model, artifact_path="model")

logger.end_run()
```

### Log sklearn Model

```python
from sklearn.ensemble import RandomForestClassifier
from improvements.mlflow_integration import MLflowLogger

model = RandomForestClassifier(n_estimators=100, max_depth=10)

logger = MLflowLogger(experiment="tabular")
logger.start_run(run_name="rf_experiment")

logger.log_params({
    "n_estimators": 100,
    "max_depth": 10,
})

logger.log_metrics({
    "accuracy": 0.95,
    "precision": 0.93,
})

# Auto-detects sklearn
logger.log_model(model, artifact_path="rf_model")

logger.end_run()
```

### Log XGBoost Model

```python
import xgboost as xgb
from improvements.mlflow_integration import MLflowLogger

train = xgb.DMatrix(X_train, label=y_train)
params = {"objective": "binary:logistic", "eta": 0.1}
model = xgb.train(params, train, num_boost_round=100)

logger = MLflowLogger(experiment="xgboost")
logger.start_run(run_name="xgb_experiment")

logger.log_params(params)
logger.log_metrics({"error": 0.05})

logger.log_model(model, artifact_path="xgb_model")

logger.end_run()
```

### TensorFlow/Keras

```python
import tensorflow as tf
from improvements.mlflow_integration import MLflowLogger

model = tf.keras.Sequential([...])
model.compile(optimizer='adam', loss='mse')

model.fit(X_train, y_train, epochs=10)

logger = MLflowLogger(experiment="tf_experiment")
logger.start_run(run_name="keras_training")

logger.log_params({"epochs": 10, "lr": 0.001})
logger.log_metrics({"loss": 0.05})

logger.log_model(model, artifact_path="tf_model")

logger.end_run()
```

### Log Custom Model

```python
from improvements.mlflow_integration import MLflowLogger

class MyCustomModel:
    def predict(self, x):
        return x * 2

model = MyCustomModel()

logger = MLflowLogger(experiment="custom")
logger.start_run(run_name="custom_test")

logger.log_model(model, artifact_path="custom_model")

logger.end_run()
```

### Log Artifacts

```python
# Log a file
logger.log_artifact("confusion_matrix.png")

# Log a dictionary as JSON
logger.log_dict(
    {"accuracy": 0.95, "f1": 0.93},
    "metrics.json"
)

# Set tags
logger.set_tag("dataset", "mnist")
logger.set_tag("version", "1.0")
```

---

## Framework Auto-Detection

```python
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
```

---

## Relationship to Other Files

```
mlflow_integration.py
    │
    ├── Imports: mlflow
    │       └── Core MLflow library
    │
    ├── Used by: lm_studio_integration.py
    │       └── Exports to MLflowLogger
    │
    └── Used by: harness.py (via lm_studio_integration)
            └── 'track_experiment' function
```

---

## Installation Requirements

```bash
pip install mlflow                    # Core
pip install torch                     # PyTorch support
pip install tensorflow                # TensorFlow support
pip install scikit-learn              # sklearn support
pip install xgboost                   # XGBoost support
pip install lightgbm                  # LightGBM support
pip install catboost                  # CatBoost support
```

---

## Quick Reference

```python
from improvements.mlflow_integration import MLflowLogger

# Initialize
logger = MLflowLogger(experiment="name")

# Start run
logger.start_run(run_name="optional")

# Log data
logger.log_params({"lr": 0.001, "batch_size": 32})
logger.log_metrics({"acc": 0.95, "loss": 0.05})

# Log model (auto-detects framework)
logger.log_model(model, artifact_path="model")

# Log artifacts
logger.log_artifact("file.png")
logger.log_dict(dict_data, "data.json")

# End run
logger.end_run()

# View UI
# mlflow ui
```
