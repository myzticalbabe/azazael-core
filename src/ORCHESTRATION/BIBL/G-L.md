---
title: Azazael Lexicon G-L
version: 1.0.0
last_updated: 2026-02-11
status: DRAFT
---

# G-L Lexicon

> [A-F](A-F.md) | [Back to Index](INDEX.md) | [M-R](M-R.md) | [S-Z](S-Z.md)

## G

### GGUF

**Category:** Conversion

**Definition:** GPT-Generated Unified Format - a file format for storing trained models, optimized for fast loading and inference.

**Usage:** Primary format for local LLM models (Mistral, Qwen, etc.)

**See Also:**
- [Universal Converter](S-Z.md#universal-converter)
- [LM Studio](G-L.md#lm-studio)

---

### GitHub Actions

**Category:** CI/CD

**Definition:** Automated workflow system for continuous integration and deployment of Azazael.

**File:** `.github/workflows/ci-cd.yml`

**Capabilities:**
- Automated testing
- Documentation validation
- Linting and type checking

**See Also:**
- [Validation](S-Z.md#validation)
- [Documentation Protocol](A-F.md#documentation-protocol)

---

## H

### Harness

**Category:** Core

**Definition:** Unified CLI interface for all Azazael operations including analysis, conversion, recommendations, and monitoring.

**File:** `ml_platforms/harness.py`

**Commands:**
| Command | Description |
|---------|-------------|
| `status` | Show system and framework status |
| `analyze` | Run framework analysis |
| `convert` | Convert model formats |
| `recommend` | Get framework recommendation |
| `monitor` | Run health monitoring |
| `verify` | Full system check |

**See Also:**
- [CLI](A-F.md#cli)
- [Launcher](G-L.md#launcher)

---

### Health Monitor

**Category:** Monitoring

**Definition:** System resource tracking module monitoring CPU, memory, and disk usage.

**File:** `ml_platforms/health_monitor.py`

**Metrics:**
- Memory usage
- CPU load
- Disk space
- GPU availability (if available)

**See Also:**
- [Diagnostics](A-F.md#diagnostics)
- [Platform Health](G-L.md#platform-health)

---

## I

### Improvements

**Category:** Q-Learning

**Definition:** Advanced ML utilities subpackage containing enhanced algorithms and utilities.

**Directory:** `ml_platforms/improvements/`

**Modules:**
- `qlearning_table.py` - Q-Learning algorithm
- `enhanced_qlearning.py` - Enhanced implementation
- `tensor_utils.py` - Tensor utilities
- `onnx_integration.py` - ONNX export
- `ray_integration.py` - Ray distributed computing
- `mlflow_integration.py` - MLflow tracking
- `strategic_intelligence.py` - ML-powered improvements

**See Also:**
- [Core](G-L.md#core-modules)
- [Q-Learning](M-R.md#q-learning)

---

### Installation Manager

**Category:** Core

**Definition:** Module for managing ML package installations and dependencies.

**Files:**
- `ml_platforms/install_manager.py`
- `ml_platforms/install_light.py`
- `ml_platforms/install_improvements.py`
- `ml_platforms/auto_install.py`

**See Also:**
- [Diagnostics](A-F.md#diagnostics)
- [Installation Debug](G-L.md#installation-debug)

---

## J

### JAX

**Category:** ML Framework

**Definition:** High-performance ML framework from Google featuring JIT compilation, automatic differentiation, and parallel computation.

**Analysis:** `ml_platforms/jax_analysis.py`

**Use Cases:**
- High-performance numerical computing
- Neural network research
- Scientific machine learning

**See Also:**
- [PyTorch](M-R.md#pytorch)
- [TensorFlow](S-Z.md#tensorflow)

---

### JIT Compilation

**Category:** Optimization

**Definition:** Just-In-Time compilation technique used to accelerate Q-Learning updates and tensor operations.

**Implementations:**
- `ml_platforms/improvements/enhanced_qlearning.py` - JIT Q-Learning
- Numba JIT decorators

**Benefits:**
- Faster execution
- Near-C performance
- Dynamic optimization

**See Also:**
- [Enhanced Q-Learning](A-F.md#enhanced-q-learning)
- [Tensor Utilities](S-Z.md#tensor-utilities)

---

## K

### Keras

**Category:** ML Framework

**Definition:** High-level neural network API running on top of TensorFlow, providing simplified model building.

**Integration:** Via TensorFlow

**Use Cases:**
- Rapid prototyping
- Convolutional networks
- Recurrent networks
- Transfer learning

**See Also:**
- [TensorFlow](S-Z.md#tensorflow)
- [Deep Learning](G-L.md#deep-learning)

---

## L

### Launcher

**Category:** Core

**Definition:** Interactive session manager that opens Python with all ml_platforms modules pre-imported.

**File:** `ml_platforms/launcher.py`

**Usage:**
```bash
python launcher.py
```

**Benefits:**
- Pre-imported modules
- Interactive REPL
- Ready-to-use environment

**See Also:**
- [Harness](G-L.md#harness)
- [CLI](A-F.md#cli)

---

### Learning Rate

**Category:** Q-Learning

**Definition:** Step size (alpha) controlling how much new information overrides old information in Q-Learning updates.

**Formula:**
```
Q(s,a) = Q(s,a) + α * [r + γ * max(Q(s',a')) - Q(s,a)]
```

**Related:**
- [Alpha Decay](G-L.md#alpha-decay)
- [Discount Factor](G-L.md#discount-factor)

---

### LM Studio

**Category:** Local LLM

**Definition:** Desktop application for running local language models (Llama, Mistral, etc.) with OpenAI-compatible API.

**Integration Files:**
- `ml_platforms/lm_studio_integration.py`
- `ml_platforms/lm_studio_client.py`
- `ml_platforms/lm_studio_server.py`
- `ml_platforms/lm_studio_autoload.py`

**Configuration:**
- `model_config.yaml`

**See Also:**
- [GGUF](G-L.md#gguf)
- [Local LLM](G-L.md#local-llm)

---

*See also: [A-F](A-F.md) | [INDEX](INDEX.md) | [M-R](M-R.md) | [S-Z](S-Z.md)*
