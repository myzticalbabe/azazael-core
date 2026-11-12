---
title: Azazael Lexicon M-R
version: 1.0.0
last_updated: 2026-02-11
status: DRAFT
---

# M-R Lexicon

> [A-F](A-F.md) | [G-L](G-L.md) | [Back to Index](INDEX.md) | [S-Z](S-Z.md)

## M

### ML Platform

**Category:** Core

**Definition:** Collection of ML framework integrations and utilities for streamlined development.

**Location:** `ml_platforms/`

**Structure:**
- Core modules (harness, launcher, config)
- Q-Learning analysis (run_qanalysis, verify_moe)
- Improvements (qlearning_table, tensor_utils)
- Integration (mlflow, onnx, ray)

**See Also:**
- [Framework](M-R.md#framework)
- [ML Framework](M-R.md#ml-framework)

---

### MLflow

**Category:** MLOps

**Definition:** Open-source platform for managing the ML lifecycle including experimentation, reproducibility, and deployment.

**File:** `ml_platforms/improvements/mlflow_integration.py`

**Capabilities:**
- Experiment tracking
- Model registry
- Project deployment
- Metric logging

**See Also:**
- [Ray](M-R.md#ray)
- [Monitoring](M-R.md#monitoring)

---

### Mixture of Experts

**Category:** Q-Learning

**Definition:** Ensemble architecture where specialized "expert" modules (torch_expert, sklearn_expert, etc.) contribute to framework selection decisions.

**File:** `ml_platforms/verify_moe.py`

**Experts:**
| Expert | Domain |
|--------|--------|
| torch_expert | Deep Learning |
| sklearn_expert | Classical ML |
| jax_expert | High-Performance |
| onnx_expert | Deployment |
| ray_expert | Distributed |

**See Also:**
- [Adaptive Platform Selection](A-F.md#adaptive-platform-selection)
- [Verify MoE](S-Z.md#verify-moe)

---

### Module Network

**Category:** Analysis

**Definition:** Graph representation of framework dependencies and relationships within the ML platform.

**Visualization:** `module_network.png`

**See Also:**
- [Cross-Platform Analysis](M-R.md#cross-platform-analysis)
- [Unified Analysis](M-R.md#unified-analysis)

---

## N

### NumPy

**Category:** ML Framework

**Definition:** Fundamental package for numerical computing in Python, providing support for arrays, matrices, and mathematical functions.

**Usage:** Base for most ML frameworks

**See Also:**
- [Scikit-learn](S-Z.md#scikit-learn)
- [Tensor Utilities](S-Z.md#tensor-utilities)

---

## O

### ONNX

**Category:** Conversion

**Definition:** Open Neural Network Exchange - open format for representing ML models enabling interoperability between frameworks.

**File:** `ml_platforms/improvements/onnx_integration.py`

**Use Cases:**
- Framework interoperability
- Model deployment
- Inference optimization

**See Also:**
- [Universal Converter](S-Z.md#universal-converter)
- [ONNX Runtime](M-R.md#onnx-runtime)

---

### ONNX Runtime

**Category:** ML Framework

**Definition:** Cross-platform, high-performance inference engine for ONNX models.

**Related:**
- [ONNX](M-R.md#onnx)
- [Deployment](M-R.md#deployment)

---

### Open Interpreter

**Category:** Integration

**Definition:** Local AI coding assistant that can run code and interact with local LLMs.

**Files:**
- `ml_platforms/openinterpreter_startup.py`
- `ml_platforms/openinterpreter_config.yaml`

**See Also:**
- [LM Studio](G-L.md#lm-studio)
- [Local LLM](G-L.md#local-llm)

---

## P

### PERSEPHONE

**Category:** Cross-Platform

**Definition:** Windows/WSL/WSA development utilities providing path translation, synchronization, and environment management.

**Location:** `C:\Users\clint\Desktop\PERSEPHONE`

**Structure:**
| Directory | Contents |
|-----------|-----------|
| `bin/` | CLI utilities (persephone_cli.py, setup_wizard.py) |
| `lib/` | Library modules (path_manager.py, sync_manager.py, environment.py) |
| `data/` | Shared data storage |
| `models/` | ML models |
| `notebooks/` | Jupyter notebooks |

**Key Features:**
| Feature | Description |
|---------|-------------|
| Path Translation | Windows ↔ WSL ↔ WSA |
| File Synchronization | robocopy (Windows), rsync (WSL) |
| Environment Management | Multi-shell support (bash, zsh, PowerShell) |
| Desktop Shortcuts | Quick access to Azazael/PERSEPHONE |

**Python API:**
```python
from persephone import Persephone

p = Persephone()
p.azazael.wsl_path           # /mnt/c/Users/clint/Desktop/Azazael
p.azazael.windows_path       # C:\Users\clint\Desktop\Azazael
p.sync_to_wsl(dry_run=True)   # Sync files
```

**Scripts:**
| Script | Platform | Purpose |
|--------|----------|---------|
| `sync_azazael.bat` | Windows | Robocopy sync |
| `sync_azazael_wsl.sh` | WSL | rsync sync |
| `admin_activator.bat` | Windows | Admin config |
| `admin_activator_wsl.sh` | WSL | Admin config |

**Ecosystem Integration:**
| System | Integration |
|--------|-------------|
| [HYDRA](A-F.md#hydra) | Can run HYDRA via WSL |
| [HORUS](A-F.md#horus) | Access HORUS results via cross-platform paths |
| [BIBL](A-F.md#bibl) | Access lexicon from cross-platform |

**See Also:**
- [WSL](S-Z.md#wsl)
- [WSA](S-Z.md#wsa)
- [Cross-Platform](S-Z.md#cross-platform)

---

### Platform Health

**Category:** Monitoring

**Definition:** Overall status of ML platform including installed packages, environment configuration, and resource availability.

**See Also:**
- [Health Monitor](G-L.md#health-monitor)
- [Diagnostics](A-F.md#diagnostics)

---

### PyTorch

**Category:** ML Framework

**Definition:** Open-source ML framework from Meta featuring dynamic computation graphs and strong GPU acceleration.

**Analysis:** `ml_platforms/*_analysis.py`

**Use Cases:**
- Deep learning research
- Computer vision
- Natural language processing

**Default Expert:** torch_expert

**See Also:**
- [TensorFlow](S-Z.md#tensorflow)
- [JAX](G-L.md#jax)

---

### Python Path

**Category:** Core

**Definition:** PYTHONPATH configuration that allows Python to find and import ml_platforms modules.

**Configuration:**
- `ml_platforms.pth` - Path file
- `SET_PYTHONPATH.bat` - Path setter
- `autoload.py` - Auto configuration

**See Also:**
- [Config](A-F.md#config)
- [PERMANENT LOAD](M-R.md#permanent-load)

---

## Q

### Q-Table

**Category:** Q-Learning

**Definition:** Lookup table storing Q-values for each state-action pair, used to determine optimal actions.

**Storage:**
- NumPy arrays (`.npy` files)
- JSON for analysis results

**Format:**
```
Q[state][action] = expected_reward
```

**See Also:**
- [Q-Value](M-R.md#q-value)
- [Q-Learning](M-R.md#q-learning)

---

### Q-Learning

**Category:** Algorithm

**Definition:** Reinforcement learning algorithm that learns action-values (Q-values) for maximizing cumulative reward through trial and error.

**Files:**
- `ml_platforms/improvements/qlearning_table.py`
- `ml_platforms/run_qanalysis*.py`

**Core Equation:**
```
Q(s,a) = Q(s,a) + α[r + γmax Q(s',a') - Q(s,a)]
```

**Parameters:**
- α (alpha) - Learning rate
- γ (gamma) - Discount factor

**See Also:**
- [Experience Replay](A-F.md#experience-replay)
- [Target Network](S-Z.md#target-network)

---

### Q-Value

**Category:** Q-Learning

**Definition:** Expected cumulative reward for taking action a in state s and following the optimal policy thereafter.

**Also Known As:** Action Value

**See Also:**
- [Q-Table](M-R.md#q-table)
- [Bellman Equation](M-R.md#bellman-equation)

---

## R

### Ray

**Category:** Distributed

**Definition:** Distributed computing framework for scaling ML workloads across multiple nodes and GPUs.

**File:** `ml_platforms/improvements/ray_integration.py`

**Capabilities:**
- Distributed training
- Parallel inference
- Hyperparameter tuning

**See Also:**
- [Distributed Computing](A-F.md#distributed-computing)
- [MLflow](M-R.md#mlflow)

---

### Reinforcement Learning

**Category:** Algorithm

**Definition:** Learning paradigm where an agent learns optimal behavior through interactions with an environment receiving rewards/penalties.

**See Also:**
- [Q-Learning](M-R.md#q-learning)
- [Experience Replay](A-F.md#experience-replay)

---

### Run QAnalysis

**Category:** Q-Learning

**Definition:** Framework analysis runner that benchmarks installed frameworks and builds Q-tables.

**Variants:**
- `run_qanalysis.py` - Single-pass (10 iterations)
- `run_qanalysis_multi.py` - Multi-iteration (10 runs)
- `run_qanalysis_full.py` - Full convergence (100 iterations)

**Output:**
- `analysis_logs/dlq_analysis_*.json`
- `analysis_logs/q_table_*.npy`

**See Also:**
- [Q-Learning](M-R.md#q-learning)
- [Convergence](A-F.md#convergence)

---

*See also: [A-F](A-F.md) | [G-L](G-L.md) | [INDEX](INDEX.md) | [S-Z](S-Z.md)*
