---
title: Azazael Lexicon A-F
version: 1.0.0
last_updated: 2026-02-11
status: DRAFT
---

# A-F Lexicon

> [Back to Index](INDEX.md) | [G-L](G-L.md) | [M-R](M-R.md) | [S-Z](S-Z.md)

## A

### Adaptive Platform Selection

**Category:** Q-Learning

**Definition:** Intelligent framework selection mechanism that uses Q-Learning to choose the optimal ML framework based on task characteristics (CV, NLP, Tabular, etc.).

**See Also:**
- [Mixture of Experts](M-R.md#mixture-of-experts)
- [Q-Learning](M-R.md#q-learning)
- [Verify MoE](S-Z.md#verify-moe)

**Source:** `ml_platforms/improvements/qlearning_table.py`

---

### Admin Activator

**Category:** Cross-Platform

**Definition:** Scripts that configure system-wide environment variables, aliases, and PowerShell profiles for maximum command capabilities.

**Files:**
- `PERSEPHONE/admin_activator.bat` (Windows)
- `PERSEPHONE/admin_activator_wsl.sh` (WSL)

**Commands Enabled:**
- `azazael` - Run CLI
- `az-test` - Run tests
- `sync-to-pers` - Sync to PERSEPHONE
- `kill-python` - Kill Python processes

**See Also:**
- [PERSEPHONE](M-R.md#persephone)
- [WSL](S-Z.md#wsl)

---

### Analysis

**Category:** Core

**Definition:** Framework for examining ML pipeline performance, including Q-Learning analysis and cross-framework comparisons.

**Modules:**
- `run_qanalysis.py` - Single-pass analysis
- `run_qanalysis_multi.py` - Multi-iteration analysis
- `run_qanalysis_full.py` - Full convergence analysis
- `unified_analysis.py` - Unified multi-framework analysis

**See Also:**
- [JAX Analysis](G-L.md#jax-analysis)
- [Cross-Platform Analysis](M-R.md#cross-platform-analysis)

**Source:** `ml_platforms/*_analysis.py`

---

## B

### BAT File

**Category:** Scripts

**Definition:** Windows batch script files used for automation, shortcuts, and launching Azazael commands.

**Locations:**
- `SHORTY/` - BAT shortcuts
- `LAUNCHER.bat` - Main launcher
- `INSTALL_AS_ADMIN.bat` - Admin installation
- `SET_PYTHONPATH.bat` - Path configuration

**See Also:**
- [CLI](A-F.md#cli)
- [Harness](G-L.md#harness)

---

### Benchmark

**Category:** Q-Learning

**Definition:** Performance measurement of ML frameworks used to build Q-tables and determine optimal framework selection.

**Metrics:**
- Training speed
- Inference time
- Memory usage
- Accuracy

**See Also:**
- [Q-Learning](M-R.md#q-learning)
- [Framework Activator](A-F.md#framework-activator)

---

### BIBL

**Category:** Documentation

**Definition:** Hyperlinked concordance of Azazael terminology providing an A-Z lexicon of all ML platform terms.

**Structure:**
- `INDEX.md` - Main index
- `A-F.md` - Terms A through F
- `G-L.md` - Terms G through L
- `M-R.md` - Terms M through R
- `S-Z.md` - Terms S through Z

**See Also:**
- [Documentation Protocol](A-F.md#documentation-protocol)

---

## C

### CLI

**Category:** Core

**Definition:** Command-Line Interface for Azazael operations providing interactive and single-command modes.

**File:** `scripts/azazael_cli.py`

**Commands:**
| Command | Description |
|---------|-------------|
| `help` | Show help |
| `cd`, `ls`, `cat` | File operations |
| `run`, `exec` | Execute scripts |
| `analyze` | Run analysis |
| `test` | Run tests |
| `validate` | Validate docs |

**Aliases:** `q`, `h`, `s`, `v`, `r`

**See Also:**
- [Harness](G-L.md#harness)
- [Launcher](G-L.md#launcher)

---

### Config

**Category:** Core

**Definition:** Configuration management module that loads and provides access to ML platform settings.

**File:** `ml_platforms/config.py`

**Configuration Files:**
- `model_config.yaml` - LM Studio configuration
- `ml_platform_config.json` - Platform settings

**See Also:**
- [Python Path](M-R.md#python-path)

---

### Convergence

**Category:** Q-Learning

**Definition:** Point where Q-values stabilize during Q-Learning iterations, indicating the algorithm has found optimal or near-optimal values.

**Process:**
1. Initial Q-values set to zero
2. Iterative updates via Bellman equation
3. Decaying learning rate (alpha decay)
4. Q-values converge as iterations increase

**See Also:**
- [Run QAnalysis Full](M-R.md#run-qanalysis-full)
- [Q-Table](M-R.md#q-table)

---

### Converter

**Category:** Conversion

**Definition:** Module for converting between model formats.

**File:** `ml_platforms/universal_converter.py`

**Supported Conversions:**
- GGUF â†’ PyTorch
- GGUF â†’ TensorFlow
- GGUF â†’ ONNX
- GGUF â†’ JAX

**See Also:**
- [GGUF](G-L.md#gguf)
- [ONNX](M-R.md#onnx)

---

## D

### Diagnostics

**Category:** Core

**Definition:** System health and framework availability checks that determine which ML packages are installed and functional.

**File:** `ml_platforms/diagnostics.py`

**Checks:**
- Package availability
- Version information
- Import functionality

**See Also:**
- [Health Monitor](G-L.md#health-monitor)
- [Installation Manager](G-L.md#installation-manager)

---

### Distributed Computing

**Category:** Integration

**Definition:** Multi-node/multi-GPU computation using Ray for parallelizing ML workloads.

**File:** `ml_platforms/improvements/ray_integration.py`

**Capabilities:**
- Ray cluster integration
- Distributed training
- Parallel inference

**See Also:**
- [Ray](M-R.md#ray)
- [MLflow](M-R.md#mlflow)

---

## E

### Enhanced Q-Learning

**Category:** Q-Learning

**Definition:** Advanced Q-Learning implementation with multi-agent support, JIT-compiled updates, and hyperparameter auto-tuning.

**File:** `ml_platforms/improvements/enhanced_qlearning.py`

**Features:**
- Multi-Agent Q-Learning
- Sum-Tree Prioritized Replay
- JIT-Compiled Updates
- Hyperparameter Auto-Tuning

**See Also:**
- [Q-Learning](M-R.md#q-learning)
- [JIT Compilation](G-L.md#jit-compilation)

---

### Experience Replay

**Category:** Q-Learning

**Definition:** Memory buffer that stores state-action-reward-next_state transitions for later replay during Q-Learning updates.

**Benefits:**
- Breaks correlation in sequence of experiences
- Reuses experiences multiple times
- Stabilizes learning

**See Also:**
- [Q-Learning](M-R.md#q-learning)
- [SumTree](S-Z.md#sumtree)

---

## F

### Framework Activator

**Category:** Core

**Definition:** Module for enabling/disabling ML frameworks dynamically based on task requirements.

**File:** `ml_platforms/framework_activator.py`

**Related:**
- [Config](A-F.md#config)
- [Diagnostics](A-F.md#diagnostics)

---

## H

### HORUS

**Category:** Output System

**Definition:** Human-Readable Output Unified System - Visual popup display for ML groupings results.

**File:** `HORUS.py`

**Features:**
- HTML output with gradient styling
- Popup notifications
- Browser integration
- Configurable formats (HTML, JSON, Console)

**Usage:**
```python
import horus
horus.display_results()
horus.config_output(format="html", destination="popup")
```

**Output Path:** `C:/Users/clint/Desktop/HORUS/results/`

**See Also:**
- [HYDRA](G-L.md#hydra)
- [BIBL](A-F.md#bibl)

---

### HYDRA

**Category:** Cluster System

**Definition:** Adaptive Sequential ML Cluster System for self-developing ML groupings processed in successive protocol stages.

**File:** `HYDRA.py`

**Sequential Protocol Stages:**
1. Discovery - Auto-detect ML modules
2. Analysis - Process Analysis group
3. Optimization - Optimize Q-Learning & Integration
4. Validation - Validate Core & Monitoring
5. Deployment - Deploy LM Studio & Installation
6. Feedback - Generate feedback report

**Features:**
- Dependency-aware processing
- Adaptive learning
- Priority scheduling
- Retry logic
- Feedback generation

**Usage:**
```python
import hydra
cluster = hydra.create_hydra_cluster()
cluster.run_sequential()
cluster.adaptive_learn()
```

**Output Path:** `C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/HYDRA/`

**See Also:**
- [HORUS](A-F.md#horus)
- [Sequential Protocol](A-F.md#sequential-protocol)

---

### HYDRAMetrics

**Category:** Quantitative

**Definition:** Quantitative metrics tracker for measuring processing times, success rates, and performance benchmarks.

**File:** `ml_platforms/improvements/metrics_tracker.py`

**Classes:**
- `MetricsTracker` - Track run metrics
- `SuccessRateTracker` - Module success rates
- `PerformanceBenchmark` - System benchmarking

**Metrics Tracked:**
- Processing duration
- Memory usage
- Success/failure rates
- Stage-level timing

**Usage:**
```python
from metrics_tracker import MetricsTracker
tracker = MetricsTracker()
tracker.record_run(success=True, duration=5.2)
rate = tracker.get_success_rate()
```

**See Also:**
- [Benchmark](A-F.md#benchmark)
- [Success Rate](A-F.md#success-rate)

---

## M

### MetricsTracker

**Category:** Quantitative

**Definition:** Class for tracking and analyzing quantitative metrics across processing runs.

**File:** `ml_platforms/improvements/metrics_tracker.py`

**Methods:**
| Method | Description |
|--------|-------------|
| `record_run()` | Record a processing run |
| `get_success_rate()` | Calculate success rate |
| `get_average_duration()` | Average run duration |
| `get_performance_summary()` | Complete summary |

**See Also:**
- [SuccessRateTracker](A-F.md#successratetracker)
- [PerformanceBenchmark](A-F.md#performancebenchmark)

---

### Module Stats

**Category:** Quantitative

**Definition:** Per-module statistics tracking successful/failed runs and durations.

**Tracked Per Module:**
- Total runs
- Successes
- Failures
- Average duration
- Success rate

**See Also:**
- [SuccessRateTracker](A-F.md#successratetracker)
- [MetricsTracker](A-F.md#metricstracker)

---

## P

### PerformanceBenchmark

**Category:** Quantitative

**Definition:** System for benchmarking ML operations including discovery, Q-Learning, and integration tests.

**File:** `ml_platforms/improvements/metrics_tracker.py`

**Benchmarks:**
| Benchmark | Measures |
|-----------|----------|
| `benchmark_discovery()` | Module discovery time |
| `benchmark_qlearning()` | Q-Learning operations |
| `benchmark_integration()` | Integration tests |

**Usage:**
```python
benchmark = PerformanceBenchmark()
result = benchmark.run_full_benchmark()
```

**See Also:**
- [MetricsTracker](A-F.md#metricstracker)
- [Benchmark](A-F.md#benchmark)

---

### PerformanceMetrics

**Category:** Quantitative

**Definition:** Data class storing metrics for a single processing run.

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `run_id` | str | Unique run identifier |
| `timestamp` | str | ISO timestamp |
| `duration` | float | Run duration (seconds) |
| `memory_peak` | float | Peak memory (MB) |
| `success` | bool | Whether run succeeded |
| `stage_times` | Dict | Time per stage |

**See Also:**
- [MetricsTracker](A-F.md#metricstracker)
- [Stage Times](A-F.md#stage-times)

---

## Q

### Qualitative Improvements

**Category:** Quality

**Definition:** Advanced ML enhancements including neural predictions, ensemble methods, and context awareness.

**File:** `ml_platforms/improvements/qualitative_improvements.py`

**Features:**
1. Neural Predictions - Neural network scoring
2. Ensemble Methods - Combined recommendation strategies
3. Real-Time Alerts - Anomaly detection
4. Context Awareness - Hardware-aware scheduling
5. Memory Management - Automatic data cleanup

**Classes:**
- `NeuralPredictor` - Neural framework scoring
- `EnsembleRecommender` - Multi-strategy recommendations
- `AnomalyDetector` - Real-time alerts
- `ContextAwareScheduler` - Hardware-aware scheduling

**See Also:**
- [Quantitative](A-F.md#quantitative)
- [SuccessRateTracker](A-F.md#successratetracker)

---

### Quantitative Metrics

**Category:** Quantitative

**Definition:** Measurable numerical metrics including success rates, processing times, and performance benchmarks.

**Tracked Metrics:**
| Metric | Description |
|--------|-------------|
| Success Rate | % of successful runs |
| Avg Duration | Mean processing time |
| Memory Peak | Maximum memory used |
| Stage Times | Time per protocol stage |

**See Also:**
- [Qualitative Improvements](A-F.md#qualitative-improvements)
- [MetricsTracker](A-F.md#metricstracker)

---

## S

### Sequential Protocol

**Category:** Cluster System

**Definition:** Successive processing stages used by HYDRA for ordered ML module development.

**Stages:**
| Stage | Purpose | Dependencies |
|-------|---------|--------------|
| Discovery | Auto-detect modules | None |
| Analysis | Process Analysis group | Discovery |
| Optimization | Optimize Q-Learning | Analysis |
| Validation | Validate Core/Monitoring | Optimization |
| Deployment | Deploy LM Studio | Validation |
| Feedback | Generate report | All complete |

**See Also:**
- [HYDRA](G-L.md#hydra)
- [Processing Stages](A-F.md#processing-stages)

---

### Stage Times

**Category:** Quantitative

**Definition:** Per-stage duration tracking within the HYDRA sequential protocol.

**Example:**
```json
{
  "discovery": 0.5,
  "analysis": 2.1,
  "optimization": 5.3,
  "validation": 1.2,
  "deployment": 0.8,
  "feedback": 0.3
}
```

**See Also:**
- [Sequential Protocol](A-F.md#sequential-protocol)
- [PerformanceMetrics](A-F.md#performancemetrics)

---

### Success Rate

**Category:** Quantitative

**Definition:** Percentage of successful processing runs across the system or per module.

**Calculation:**
```
Success Rate = Successful Runs / Total Runs Ã— 100%
```

**Targets:**
- System: > 95%
- Per Module: > 90%

**See Also:**
- [SuccessRateTracker](A-F.md#successratetracker)
- [MetricsTracker](A-F.md#metricstracker)

---

### SuccessRateTracker

**Category:** Quantitative

**Definition:** Class for tracking success rates per individual module.

**Methods:**
| Method | Description |
|--------|-------------|
| `record_module_result()` | Record module outcome |
| `get_module_success_rate()` | Module-specific rate |
| `get_overall_success_rate()` | Aggregate rate |
| `get_report()` | Comprehensive report |

**See Also:**
- [Success Rate](A-F.md#success-rate)
- [Module Stats](A-F.md#module-stats)

---

*See also: [INDEX](INDEX.md) | [G-L](G-L.md) | [M-R](M-R.md) | [S-Z](S-Z.md)*


