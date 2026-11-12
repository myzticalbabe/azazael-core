---
title: Diagnostics API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# diagnostics.py

**Location:** `ml_platforms/diagnostics.py`

**Purpose:** Check which ML packages are available and report status

---

## What This File Does

`diagnostics.py` tests whether 45+ ML packages can be imported. It's your "health check" for installed packages, telling you what's working and what's missing.

```
┌──────────────────────────────────────────────────────────────────┐
│                      DIAGNOSTICS.PY                              │
│                                                                  │
│   Tests 45+ Packages:                                            │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │ Deep Learning:    torch, tensorflow, keras               │  │
│   │ Data Processing:   numpy, pandas, scipy                   │  │
│   │ Computer Vision:  opencv, torchvision, easyocr            │  │
│   │ NLP:              transformers, huggingface, tokenizers   │  │
│   │ ML Libraries:      sklearn, xgboost, lightgbm             │  │
│   │ And 30+ more...                                           │  │
│   └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│   Returns:                                                       │
│   • available = [(package, display_name), ...]                   │
│   • unavailable = [(package, display_name), ...]                 │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Key Functions

### check_platform_status()

```python
def check_platform_status() -> tuple[list, list]:
    """
    Check which platforms can be imported.
    
    Returns:
        tuple: (available_packages, unavailable_packages)
        
        Each is a list of (package_name, display_name) tuples.
    """
```

---

## Usage Examples

### Quick Check

```python
from diagnostics import check_platform_status

available, unavailable = check_platform_status()

print(f"Available: {len(available)} packages")
print(f"Missing: {len(unavailable)} packages")

if unavailable:
    print("\nMissing packages:")
    for pkg, name in unavailable:
        print(f"  - {name} ({pkg})")
```

### Detailed Status

```python
from diagnostics import PLATFORMS, check_platform_status

available, unavailable = check_platform_status()

print("=" * 60)
print("PLATFORM STATUS")
print("=" * 60)

print(f"\n[OK] Available ({len(available)}):")
for pkg, name in available:
    print(f"  ✓ {name}")

print(f"\n[!!] Missing ({len(unavailable)}):")
for pkg, name in unavailable:
    print(f"  ✗ {name}")
```

### In Health Monitor

`diagnostics.py` is used by [health_monitor.py](../monitoring/health_monitor.md):

```python
# From health_monitor.py
from diagnostics import check_platform_status

def _check_imports(self) -> HealthCheck:
    """Check for import errors in critical packages."""
    critical_packages = [...]
    errors = []
    
    for pkg in critical_packages:
        try:
            importlib.import_module(pkg.replace("-", "_"))
        except Exception as e:
            errors.append(f"{pkg}: {str(e)[:100]}")
```

---

## Complete Package List

### Deep Learning
| Package | Display Name | Import |
|---------|--------------|--------|
| `torch` | PyTorch | `import torch` |
| `torchvision` | TorchVision | `import torchvision` |
| `torchaudio` | TorchAudio | `import torchaudio` |
| `tensorflow` | TensorFlow | `import tensorflow` |
| `keras` | Keras | `import keras` |

### Data Processing
| Package | Display Name | Import |
|---------|--------------|--------|
| `numpy` | NumPy | `import numpy` |
| `pandas` | Pandas | `import pandas` |
| `scipy` | SciPy | `import scipy` |

### Computer Vision
| Package | Display Name | Import |
|---------|--------------|--------|
| `opencv-python` | OpenCV | `import cv2` |
| `pillow` | Pillow | `import PIL` |
| `scikit-image` | Scikit-Image | `import skimage` |
| `easyocr` | EasyOCR | `import easyocr` |
| `deepface` | DeepFace | `import deepface` |
| `mtcnn` | MTCNN | `import mtcnn` |
| `retinaface` | RetinaFace | `import retinaface` |

### NLP
| Package | Display Name | Import |
|---------|--------------|--------|
| `transformers` | Transformers | `import transformers` |
| `huggingface_hub` | HuggingFace Hub | `import huggingface_hub` |
| `tokenizers` | Tokenizers | `import tokenizers` |
| `nltk` | NLTK | `import nltk` |

### Classical ML
| Package | Display Name | Import |
|---------|--------------|--------|
| `scikit-learn` | scikit-learn | `import sklearn` |
| `xgboost` | XGBoost | `import xgboost` |
| `lightgbm` | LightGBM | `import lightgbm` |
| `catboost` | CatBoost | `import catboost` |

### Other
| Package | Display Name | Import |
|---------|--------------|--------|
| `tensorboard` | TensorBoard | `import tensorboard` |
| `mlflow` | MLflow | `import mlflow` |
| `ray` | Ray | `import ray` |
| `onnx` | ONNX | `import onnx` |
| `onnxruntime` | ONNX Runtime | `import onnxruntime` |
| `optuna` | Optuna | `import optuna` |
| `gymnasium` | Gymnasium | `import gymnasium` |
| `stable-baselines3` | Stable-Baselines3 | `import stable_baselines3` |

---

## Relationship to Other Files

```
diagnostics.py
    │
    ├── Uses config.py
    │       └── Gets platform list from config
    │
    └── Used by health_monitor.py
            └── _check_imports() method uses this
```

---

## Integration Points

### With health_monitor.py

```python
# health_monitor.py checks critical imports
from diagnostics import PLATFORMS

critical_packages = [
    "torch", "tensorflow", "numpy", "scipy",
    "pandas", "transformers", "opencv-python", "scikit-learn"
]
```

### With config.py

`diagnostics.py` uses `config.py` indirectly through the main config:

```python
from config import _get_config

config = _get_config()
platforms = config.platforms
```

---

## Common Issues

### False Positives

Some packages import under different names:
```python
# Package name vs import name
"opencv-python"  ->  import cv2
"scikit-learn"   ->  import sklearn
"pillow"         ->  import PIL
```

`diagnostics.py` handles this with `pkg.replace("-", "_")`.

### Version Checks

To get version info:

```python
import importlib

def get_version(pkg_name):
    try:
        mod = importlib.import_module(pkg_name.replace("-", "_"))
        return getattr(mod, "__version__", "unknown")
    except ImportError:
        return None
```

---

## Quick Reference

```python
from diagnostics import check_platform_status, PLATFORMS

# Check what's available
available, unavailable = check_platform_status()

# Get version of specific package
import importlib
mod = importlib.import_module("torch")
print(f"PyTorch version: {torch.__version__}")
```
