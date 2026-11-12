---
title: Launcher API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# launcher.py

**Location:** `ml_platforms/launcher.py`

**Purpose:** Start an interactive Python session with all modules pre-loaded

---

## What This File Does

`launcher.py` opens an interactive Python session where all `ml_platforms` modules are already imported. Think of it as a "pre-warmed" Python environment for ML work.

```
┌─────────────────────────────────────────────────────────────────┐
│                      LAUNCHER.PY                                 │
│                                                                 │
│   When you run:                                                  │
│                                                                 │
│       python launcher.py                                         │
│                                                                 │
│   You get:                                                       │
│                                                                 │
│       Python 3.x.x                                              │
│       >>> _                                                      │
│                                                                 │
│   But with these already imported:                               │
│       • config                                                  │
│       • diagnostics                                             │
│       • health_monitor                                           │
│       • And more...                                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Usage

```bash
# Run from Azazael directory
cd C:\Users\clint\Desktop\Azazael
python ml_platforms\launcher.py

# Now in interactive mode:
>>> list_platforms()
>>> check_platform_status()
>>> monitor = PlatformHealthMonitor()
```

---

## What's Pre-Imported

```python
# From launcher.py (conceptual)
from config import load_config, list_platforms, get_env_path
from diagnostics import check_platform_status, PLATFORMS
from health_monitor import PlatformHealthMonitor
```

---

## Technical Details

### How It Works

1. Creates a Python interactive session
2. Pre-imports commonly used modules
3. Provides a ready-to-use environment

### Key Difference from harness.py

| harness.py | launcher.py |
|------------|-------------|
| Command-line interface | Interactive REPL |
| One-shot commands | Continuous session |
| `python harness.py status` | `python launcher.py` then type `status()` |

---

## When to Use

### Use harness.py when:
- You want to run a single command
- You're scripting automation
- You need JSON output for parsing

### Use launcher.py when:
- You're exploring interactively
- You want to try multiple commands
- You're debugging and want a live environment

---

## Relationship to Other Files

```
launcher.py
    │
    ├── Imports from config.py
    │       └── Pre-loads config functions
    │
    ├── Imports from diagnostics.py
    │       └── Pre-loads diagnostics functions
    │
    └── Imports from health_monitor.py
            └── Pre-loads monitoring class
```

---

## Quick Reference

```bash
# Start interactive session
python ml_platforms\launcher.py

# In session:
>>> available, unavailable = check_platform_status()
>>> print(f"Available: {len(available)}")
>>> monitor = PlatformHealthMonitor()
>>> monitor.print_status()
```

---

## Customization

To add more pre-imports, edit `launcher.py`:

```python
# Add to the imports section
from universal_converter import UniversalConverter
from improvements.qlearning_table import AdaptiveExpertLearner
```
