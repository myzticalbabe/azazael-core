---
title: ML Platforms Module File Explanations
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# ML Platforms Module File Explanations

This document explains what each Python file in the `ml_platforms/` directory does.

**Location:** `ml_platforms/FILE_EXPLANATIONS.md`

---

## Overview

This reference guide describes the purpose and usage of each module in the ml_platforms package.

## Usage

Browse the table of contents below to find specific modules:

- [Core Files](#core-files)
- [Integration Files](#integration-files)
- [Improvement Modules](#improvement-modules)

For API documentation, see:
- [Core Documentation](docs/core/INDEX.md)
- [Integration Documentation](docs/integration/INDEX.md)

---

## Core Files

### harness.py

**Purpose:** Unified command-line interface for all operations

**What it does:**
- Single entry point for running analysis, conversions, recommendations, monitoring
- Commands: `analyze`, `convert`, `recommend`, `monitor`, `status`, `models`, `verify`, `install`

**Usage:**
```bash
python harness.py analyze           # Run framework analysis
python harness.py convert -m model.gguf -f pytorch  # Convert model
python harness.py recommend -t cv   # Get CV framework recommendation
python harness.py monitor           # Check system health
```

---

### config.py

**Purpose:** Load and query ML platform configuration

**What it does:**
- Reads `ml_platform_config.json` configuration file
- Provides functions to list platforms, environments, and workflows
- Groups packages by category (deep_learning, cv, nlp, etc.)

**Key functions:**
- `load_config()` - Load configuration data
- `list_platforms()` - List all available platforms
- `get_env_path()` - Get Python environment path
- `list_workflows()` - Get recommended workflows

---

### diagnostics.py

**Purpose:** Check which ML packages are available

**What it does:**
- Tests importing 45+ ML packages
- Returns lists of available and unavailable platforms
- Shows version info for installed packages

**Key function:**
- `check_platform_status()` - Returns (available_packages, unavailable_packages)

---

### launcher.py

**Purpose:** Start an interactive ML session

**What it does:**
- Opens Python with all ml_platforms modules pre-imported
- Creates a convenient interactive environment

**Usage:**
```bash
python launcher.py
```

---

## Model Conversion

### universal_converter.py

**Purpose:** Convert GGUF models to PyTorch, TensorFlow, ONNX, JAX

**What it does:**
- Analyzes GGUF model metadata
- Extracts weights and converts to various formats
- Logs conversion process to `conversion_logs/`

**Usage:**
```bash
python universal_converter.py model.gguf --framework pytorch
python universal_converter.py model.gguf --analyze  # Just analyze
```

**Key class:**
- `UniversalConverter` - Main conversion class with `analyze()` and `convert()` methods

---

## Q-Learning Analysis

### run_qanalysis.py

**Purpose:** Single-pass Q-Learning framework analysis

**What it does:**
- Benchmarks installed frameworks (PyTorch, TensorFlow, JAX, NumPy, SciPy)
- Builds Q-table with scores for each task/expert combination
- Saves results to `analysis_logs/`

**Output:**
- `analysis_logs/dlq_analysis_YYYYMMDD_HHMMSS.json`
- `analysis_logs/q_table_YYYYMMDD_HHMMSS.npy`

---

### run_qanalysis_multi.py

**Purpose:** Multi-iteration Q-Learning (10 iterations)

**What it does:**
- Runs analysis 10 times with incremental learning
- Tracks convergence over iterations
- Shows improvement from initial to final Q-values

---

### run_qanalysis_full.py

**Purpose:** Full convergence Q-Learning (100 iterations)

**What it does:**
- Runs 100 iterations for complete convergence
- Uses decaying learning rate (alpha decay)
- Outputs final converged Q-table and recommendations

**Final Recommendations Example:**
```
computer_vision    -> torch_expert (0.95)
nlp                -> torch_expert (0.90)
tabular            -> sklearn_expert (0.95)
reinforcement      -> torch_expert (0.95)
generative         -> jax_expert (0.92)
```

---

### verify_moe.py

**Purpose:** Full MoE system verification

**What it does:**
- Tests all expert modules (torch, tf, jax, sklearn, onnx, ray)
- Runs deep learning Q-analysis
- Generates compatibility matrix
- Creates verification report

**Usage:**
```bash
python verify_moe.py --full    # Full verification
python verify_moe.py --analysis # Q-analysis only
python verify_moe.py --recommend  # Get recommendations
```

---

### improvements/qlearning_table.py

**Purpose:** Adaptive Q-Learning with experience replay

**What it does:**
- Implements Q-learning with target network
- Supports experience replay buffer
- Benchmarks frameworks dynamically

**Key classes:**
- `AdaptiveExpertLearner` - Main learning class
- `FrameworkState` - Tracks framework state
- `benchmark_framework()` - Benchmark a framework

---

## Health & Monitoring

### health_monitor.py

**Purpose:** Monitor system health (memory, CPU, disk)

**What it does:**
- Checks memory usage
- Monitors CPU load
- Tracks disk space
- Provides health status and warnings

**Key class:**
- `PlatformHealthMonitor` - Monitor with `print_status()`, `check_health()`

---

## Installation

### auto_install.py

**Purpose:** Auto-install missing ML packages

**What it does:**
- Detects unavailable packages
- Installs them via pip
- Adds ml_platforms to Python path

---

### install_manager.py

**Purpose:** Manage package installations

**What it does:**
- Provides installation utilities
- Handles dependency management

---

### install_light.py

**Purpose:** Lightweight installation options

**What it does:**
- Minimal dependency installation
- Quick setup for basic functionality

---

### install_improvements.py

**Purpose:** Install advanced features

**What it does:**
- Installs improvements/ subpackage
- Sets up optional enhancements

---

### installation_debug.py

**Purpose:** Debug installation issues

**What it does:**
- Diagnoses installation problems
- Reports missing dependencies
- Suggests fixes

---

## LM Studio Integration

### lm_studio_integration.py

**Purpose:** LM Studio integration utilities

**What it does:**
- Loads models from LM Studio
- Integrates with local model registry

---

### lm_studio_server.py

**Purpose:** LM Studio API server

**What it does:**
- Provides API for model interactions
- Manages local model serving

---

### lm_studio_autoload.py

**Purpose:** Auto-load LM Studio modules

**What it does:**
- Automatically imports LM Studio integration
- Sets up path for LM Studio

---

## Analysis Modules

### unified_analysis.py

**Purpose:** Unified analysis across all frameworks

**What it does:**
- Combines multiple analysis types
- Provides comprehensive framework comparison

---

### cross_platform_analysis.py

**Purpose:** Cross-platform compatibility analysis

**What it does:**
- Analyzes framework compatibility
- Identifies cross-platform issues

---

### jax_analysis.py

**Purpose:** JAX-specific analysis

**What it does:**
- Benchmarks JAX operations
- Analyzes JAX performance characteristics

---

## Autoloading

### autoload.py

**Purpose:** Automatic path configuration

**What it does:**
- Sets up Python path for ml_platforms
- Enables imports without sys.path modifications

---

### PERMANENT_LOAD.py

**Purpose:** Permanent path configuration

**What it does:**
- Provides persistent path setup
- For system-wide integration

---

## Utilities

### changelog.py

**Purpose:** Track changes

**What it does:**
- Records version history
- Documents changes between versions

---

### openinterpreter_startup.py

**Purpose:** Open Interpreter integration

**What it does:**
- Integrates with Open Interpreter
- Provides ML-specific startup for OI

---

## Improvements Subpackage

### improvements/__init__.py

**Purpose:** Package initialization for improvements

---

### improvements/qlearning_table.py

**Purpose:** Advanced Q-Learning (see above)

---

### improvements/vectorize_utils.py

**Purpose:** Vectorization utilities

**What it does:**
- Provides vectorization helpers
- Optimizes array operations

---

### improvements/onnx_integration.py

**Purpose:** ONNX integration

**What it does:**
- ONNX-specific utilities
- ONNX export/import helpers

---

### improvements/ray_integration.py

**Purpose:** Ray distributed computing

**What it does:**
- Ray cluster integration
- Distributed training utilities

---

### improvements/mlflow_integration.py

**Purpose:** MLflow experiment tracking

**What it does:**
- MLflow logging utilities
- Experiment tracking setup

---

### improvements/tensor_utils.py

**Purpose:** Tensor utilities

**What it does:**
- Tensor manipulation helpers
- Dimension operations

---

## Batch Files

### LAUNCHER.bat

**Purpose:** Windows launcher for harness

**What it does:**
- Launches harness.py with various commands
- Provides shortcuts for common operations

---

### INSTALL_AS_ADMIN.bat

**Purpose:** Install with admin privileges

**What it does:**
- Installs ml_platforms system-wide
- Sets up .pth file for Python path

---

### SET_PYTHONPATH.bat

**Purpose:** Set PYTHONPATH environment variable

**What it does:**
- Configures PYTHONPATH for ml_platforms
- Makes modules importable globally

---

## Configuration Files

### ml_platform_config.json

**Purpose:** Main configuration file

**What it contains:**
- Python environment definitions
- Package lists by category
- Recommended workflows
- Q-Learning parameters

---

### model_config.yaml

**Purpose:** LM Studio model configuration

**What it contains:**
- Model paths and settings
- Framework compatibility
- Conversion targets

---

### ml_platforms.pth

**Purpose:** Python path file

**What it does:**
- Adds ml_platforms to Python path when installed

---

### openinterpreter_config.yaml

**Purpose:** Open Interpreter configuration

**What it contains:**
- OI-specific settings
- Module imports for OI

---

## Output Directories

### analysis_logs/

**Purpose:** Q-Learning analysis output

**Contains:**
- `dlq_analysis_*.json` - Analysis results
- `dlq_full_converge_*.json` - Full convergence results
- `dlq_multi_iter_*.json` - Multi-iteration results
- `compatibility_matrix_*.json` - Framework compatibility
- `verification_report_*.json` - Full verification reports
- `q_table_*.npy` - Q-table NumPy arrays

---

### conversion_logs/

**Purpose:** Model conversion logs

**Contains:**
- `conversion_*.log` - Human-readable logs
- `conversion_*.json` - Machine-readable logs

---

## Quick Reference

| File | Purpose | Key Function |
|------|---------|--------------|
| harness.py | CLI interface | `cmd_*()` functions |
| config.py | Configuration | `MLPlatformConfig` |
| diagnostics.py | Status check | `check_platform_status()` |
| universal_converter.py | Model conversion | `UniversalConverter.convert()` |
| run_qanalysis.py | Q-Learning | `run_q_analysis()` |
| verify_moe.py | Verification | `verify_system()` |
| health_monitor.py | Monitoring | `PlatformHealthMonitor` |
| auto_install.py | Installation | Auto-install packages |

---

## Common Workflows

### Check what's installed
```python
from diagnostics import check_platform_status
available, unavailable = check_platform_status()
```

### Get framework recommendation
```python
from run_qanalysis import run_q_analysis
run_q_analysis()  # Check final recommendations
```

### Convert a model
```python
from universal_converter import UniversalConverter
converter = UniversalConverter()
result = converter.convert("model.gguf", "pytorch")
```

### Monitor system health
```python
from health_monitor import PlatformHealthMonitor
monitor = PlatformHealthMonitor()
monitor.print_status()
```

### Run full verification
```python
from verify_moe import verify_system
report = verify_system()
```
