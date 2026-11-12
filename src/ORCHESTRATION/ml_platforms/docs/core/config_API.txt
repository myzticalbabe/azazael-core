---
title: Config API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# config.py

**Location:** `ml_platforms/config.py`

**Purpose:** Load and query ML platform configuration from JSON

---

## What This File Does

`config.py` is your access point for all configuration data. It reads `ml_platform_config.json` and provides convenient functions to query platforms, environments, and workflows.

```
┌─────────────────────────────────────────────────────────┐
│                    CONFIG.PY                             │
│                                                         │
│   Reads: ml_platform_config.json                       │
│                    │                                    │
│                    ▼                                    │
│   ┌───────────────────────────────────────────────┐    │
│   │   Provides Functions:                        │    │
│   │   • list_platforms()  - All platforms        │    │
│   │   • get_env_path()     - Environment paths   │    │
│   │   • list_workflows()   - Recommended flows   │    │
│   │   • load_config()      - Raw config data     │    │
│   └───────────────────────────────────────────────┘    │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Key Classes and Functions

### MLPlatformConfig

```python
class MLPlatformConfig:
    """Main configuration manager."""
    
    DEFAULT_PATH = Path(__file__).parent.parent / "ml_platform_config.json"
    
    def __init__(self, path: Optional[Path] = None)
        """Load config from file."""
        
    @property
    def platforms(self) -> list[dict]
        """Get ALL platforms across all environments."""
        
    @property  
    def total_platforms(self) -> int
        """Count of all platforms."""
        
    def get_environment(self, name: str) -> Optional[dict]
        """Get specific environment by name."""
        
    def list_by_category(self) -> dict[str, list[dict]]
        """Group platforms by category."""
```

### Module-Level Functions

```python
def load_config() -> Optional[dict]
    """Load ML platform configuration."""
    
def list_platforms(env: Optional[str] = None) -> list[dict]
    """List platforms, optionally filtered by environment."""
    
def get_env_path(env_name: str) -> Optional[str]
    """Get Python environment path."""
    
def list_workflows() -> list[dict]
    """Get recommended workflows."""
```

---

## Usage Examples

### Basic Queries

```python
from config import list_platforms, get_env_path

# List ALL platforms
all_platforms = list_platforms()
print(f"Total platforms: {len(all_platforms)}")

# List platforms for specific environment
python311 = list_platforms("python311")
for pkg in python311:
    print(f"  {pkg['name']} ({pkg['version']})")

# Get environment path
path = get_env_path("python314")
print(f"Python 314 at: {path}")
```

### Get Workflow Recommendations

```python
from config import list_workflows

workflows = list_workflows()
for wf in workflows:
    print(f"\n{wf['name']}:")
    print(f"  {wf['description']}")
    print(f"  Frameworks: {', '.join(wf['frameworks'])}")
```

### Access Raw Config

```python
from config import load_config

config = load_config()
environments = config.get("python_environments", {})
```

---

## Configuration File Structure

**File:** `ml_platform_config.json`

```json
{
  "python_environments": {
    "python311": {
      "path": "C:/Program Files/Python311",
      "priority": 1,
      "packages": [
        {
          "name": "tensorflow",
          "version": "2.20.0",
          "category": "deep_learning"
        },
        {
          "name": "pandas", 
          "version": "2.3.3",
          "category": "data_processing"
        }
      ]
    },
    "python314": {
      "path": "C:/WINDOWS/system32/Python-3.14.2/venv-3.14",
      "priority": 2,
      "packages": [...]
    }
  },
  "learning_protocol": {
    "recommended_workflows": [
      {
        "name": "PyTorch CV",
        "description": "Computer vision with PyTorch",
        "frameworks": ["torch", "torchvision", "opencv-python"]
      }
    ]
  }
}
```

---

## Platform Categories

| Category | Description | Examples |
|----------|-------------|----------|
| `deep_learning` | Neural network frameworks | torch, tensorflow, keras |
| `computer_vision` | CV-specific libraries | opencv, torchvision, easyocr |
| `nlp` | Natural language processing | transformers, huggingface_hub |
| `classical_ml` | Traditional ML | scikit-learn, xgboost, lightgbm |
| `data_processing` | Data manipulation | pandas, numpy |
| `visualization` | Plotting and display | matplotlib, tensorboard |
| `distributed_computing` | Parallel processing | ray, dask |

---

## Relationship to Other Files

```
config.py
    │
    └── Reads: ml_platform_config.json
            │
            ├── Used by: diagnostics.py
            │       └── check_platform_status() uses list_platforms()
            │
            ├── Used by: harness.py
            │       └── Gets workflow info
            │
            └── Used by: lm_studio_integration.py
                    └── Imports via lm_studio_integration
```

---

## Integration with diagnostics.py

`diagnostics.py` uses `config.py` to get the list of packages to check:

```python
# From diagnostics.py
from config import _get_config

config = _get_config()
for pkg in config.platforms:
    # Check each package...
```

---

## Common Patterns

### Check What's Available in an Environment

```python
from config import MLPlatformConfig

config = MLPlatformConfig()
for env_name in ["python311", "python314"]:
    env = config.get_environment(env_name)
    if env:
        print(f"\n{env_name}: {env['path']}")
        for pkg in env.get("packages", []):
            print(f"  - {pkg['name']} {pkg['version']}")
```

### Group by Category

```python
from config import MLPlatformConfig

config = MLPlatformConfig()
by_category = config.list_by_category()

for category, packages in by_category.items():
    print(f"\n{category.upper()}:")
    for pkg in packages:
        print(f"  {pkg['name']}: {pkg['version']}")
```

---

## Troubleshooting

### Config File Not Found

```python
# Check the path
from pathlib import Path
from config import MLPlatformConfig

config_path = Path(__file__).parent.parent / "ml_platform_config.json"
print(f"Config file exists: {config_path.exists()}")
```

### Empty Platforms List

Make sure `ml_platform_config.json` is properly formatted JSON:

```bash
python -c "import json; json.load(open('ml_platform_config.json'))"
```

---

## Quick Reference

```python
from config import (
    load_config,      # Get raw config dict
    list_platforms,   # All platforms
    get_env_path,    # Environment path
    list_workflows   # Recommended workflows
)
```
