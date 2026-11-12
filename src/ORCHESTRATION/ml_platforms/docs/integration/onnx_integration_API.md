---
title: ONNX Integration API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# onnx_integration.py

**Location:** `ml_platforms/improvements/onnx_integration.py`

**Purpose:** Unified ONNX export for any ML framework

---

## What This File Does

`onnx_integration.py` provides a unified interface to export models from PyTorch, TensorFlow, sklearn, and other frameworks to ONNX format. It handles framework detection and provides consistent API.

```
┌─────────────────────────────────────────────────────────────────┐
│                 ONNX_INTEGRATION.PY                             │
│                                                                 │
│   Framework Detection & Export:                                  │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐   │
│   │                  SUPPORTED FRAMEWORKS                │   │
│   │   • PyTorch      → torch.onnx.export()             │   │
│   │   • TensorFlow   → tf2onnx.convert.from_keras()    │   │
│   │   • sklearn      → skl2onnx (via pyfunc)          │   │
│   │   • XGBoost      → skl2onnx (via pyfunc)          │   │
│   │   • LightGBM     → skl2onnx (via pyfunc)          │   │
│   └───────────────────────────────────────────────────────┘   │
│                                                                 │
│   Features:                                                     │
│   • Auto-detect model framework                                 │
│   • Dynamic axes support                                        │
│   • ONNX Runtime inference                                      │
│   • Model optimization                                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Classes

### ONNXExporter

```python
class ONNXExporter:
    """
    Unified ONNX export for any ML framework.
    
    Why this matters:
    - 27 platforms lack stable ONNX export
    - ONNX is critical for model interoperability
    - Deployment requires ONNX in production
    """
    
    @staticmethod
    def detect_framework(model) -> Optional[str]:
        """Detect model framework from type."""
        
    @staticmethod
    def export_to_onnx(
        model,
        input_sample,
        output_path,
        input_names=None,
        output_names=None,
        dynamic_axes=None,
        opset_version=13,
    ) -> str:
        """Export model to ONNX format."""
        
    @staticmethod
    def load_onnx_model(
        model_path,
        providers=("CPUExecutionProvider",),
    ) -> ort.InferenceSession:
        """Load ONNX model for inference."""
        
    @staticmethod
    def run_inference(
        session,
        inputs,
        output_names=None,
    ) -> Tuple[np.ndarray, ...]:
        """Run inference on ONNX model."""
        
    @staticmethod
    def optimize_onnx(
        input_path,
        output_path,
        level=1,
    ) -> str:
        """Optimize ONNX model."""
```

---

## Usage Examples

### Detect Framework

```python
from improvements.onnx_integration import ONNXExporter

framework = ONNXExporter.detect_framework(model)
print(f"Detected: {framework}")  # "pytorch", "tensorflow", etc.
```

### Export PyTorch Model

```python
import torch
from improvements.onnx_integration import export_to_onnx

# Define model
class MyModel(torch.nn.Module):
    def forward(self, x):
        return x @ torch.tensor([[1.0, 2.0], [3.0, 4.0]]) + 0.5

model = MyModel()
input_sample = torch.randn(1, 2)

# Export
export_to_onnx(
    model,
    input_sample,
    "my_model.onnx",
    input_names=("input",),
    output_names=("output",),
    dynamic_axes={0: "batch_size", 1: "sequence_length"},
    opset_version=13,
)

print("Model exported!")
```

### Load and Run ONNX Model

```python
from improvements.onnx_integration import load_onnx_model
import numpy as np

# Load model
session = load_onnx_model("my_model.onnx")

# Run inference
inputs = {"input": np.array([[1.0, 2.0]])}
outputs = session.run(None, inputs)

print(f"Output: {outputs}")
```

### Optimize ONNX Model

```python
from improvements.onnx_integration import ONNXExporter

ONNXExporter.optimize_onnx(
    input_path="model.onnx",
    output_path="model_optimized.onnx",
    level=1,  # 1-4 (higher = more optimization)
)
```

---

## Framework Detection

```python
@staticmethod
def detect_framework(model) -> Optional[str]:
    """Detect model framework from type."""
    
    model_type = type(model).__module__
    
    # Check PyTorch
    if hasattr(model, "state_dict"):
        return "pytorch"
    
    # Check TensorFlow/Keras
    if hasattr(model, "variables") or hasattr(model, "get_config"):
        return "tensorflow"
    
    # Check sklearn
    if "sklearn" in model_type:
        return "sklearn"
    
    # Check XGBoost
    if "xgboost" in model_type:
        return "xgboost"
    
    # Check LightGBM
    if "lightgbm" in model_type:
        return "lightgbm"
    
    return None
```

---

## Module-Level Functions

```python
def export_to_onnx(model, input_sample, output_path, **kwargs) -> str:
    """Shorthand for ONNXExporter.export_to_onnx()."""
    
def load_onnx_model(model_path) -> ort.InferenceSession:
    """Shorthand for ONNXExporter.load_onnx_model()."""
```

---

## ONNX Runtime Providers

```python
# CPU only
providers = ("CPUExecutionProvider",)

# With GPU (CUDA)
providers = (
    "CUDAExecutionProvider",
    "CPUExecutionProvider",
)

# With TensorRT
providers = (
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider", 
    "CPUExecutionProvider",
)
```

---

## Relationship to Other Files

```
onnx_integration.py
    │
    ├── Imports: onnx, onnxruntime
    │       └── ONNX core libraries
    │
    ├── Imports: torch (optional)
    │       └── PyTorch export
    │
    ├── Imports: tensorflow (optional)
    │       └── TensorFlow export via tf2onnx
    │
    ├── Used by: lm_studio_integration.py
    │       └── Exports models
    │
    └── Used by: universal_converter.py
            └── Part of conversion pipeline
```

---

## Installation Requirements

```bash
pip install onnx onnxruntime          # Core
pip install tf2onnx                    # TensorFlow
pip install skl2onnx                   # sklearn
```

---

## Quick Reference

```python
from improvements.onnx_integration import (
    export_to_onnx,
    load_onnx_model,
    ONNXExporter
)

# Export
export_to_onnx(model, input_sample, "model.onnx")

# Load
session = load_onnx_model("model.onnx")

# Run
outputs = session.run(None, {"input": numpy_array})

# Optimize
ONNXExporter.optimize_onnx("in.onnx", "out.onnx", level=2)
```
