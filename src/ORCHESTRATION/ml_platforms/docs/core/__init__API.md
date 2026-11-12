---
title: Core Init API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# __init__.py

**Location:** `ml_platforms/__init__.py`

**Purpose:** Package initialization with auto-import convenience

---

## What This File Does

`__init__.py` marks `ml_platforms/` as a Python package and can optionally auto-import commonly used modules for convenience.

---

## Standard Content

```python
"""
ML Platform Development Harness
Version: 2.0.0
Location: C:\Users\clint\Desktop\Azazael\ml_platforms
"""

__version__ = "2.0.0"
```

---

## Usage

After importing, you can access submodules:

```python
from ml_platforms import config
from ml_platforms.diagnostics import check_platform_status

# Or if auto-imports are enabled:
from ml_platforms import list_platforms  # From config.py
```

---

## Relationship to Other Files

```
__init__.py
    │
    └── Makes ml_platforms/ a Python package
            │
            ├── Used when: from ml_platforms import config
            │
            └── Configured by: ml_platform_config.json
```

---

## Quick Reference

```python
# Import the package
import ml_platforms

# Access version
print(ml_platforms.__version__)

# Import specific modules
from ml_platforms import config, diagnostics
```
