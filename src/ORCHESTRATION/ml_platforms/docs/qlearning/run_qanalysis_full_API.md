---
title: Run QAnalysis Full API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# run_qanalysis_full.py

**Location:** `ml_platforms/run_qanalysis_full.py`

**Purpose:** Run 100-iteration Q-Learning analysis for complete convergence

---

## What This File Does

`run_qanalysis_full.py` runs the full Q-Learning convergence analysis with 100 iterations. This is the "heavy duty" version that provides the most accurate framework recommendations.

```
┌─────────────────────────────────────────────────────────────────┐
│                 RUN_QANALYSIS_FULL.PY                           │
│                                                                 │
│   Iterations: 100                                              │
│   Alpha Decay: 0.995 per iteration                             │
│   Goal: Complete Q-table convergence                           │
│                                                                 │
│   Output:                                                      │
│   • Final converged Q-table                                    │
│   • Per-task recommendations                                   │
│   • Convergence history                                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Differences from Other Versions

| File | Iterations | Use Case |
|------|-------------|----------|
| `run_qanalysis.py` | 1 | Quick benchmark |
| `run_qanalysis_multi.py` | 10 | Standard analysis |
| `run_qanalysis_full.py` | **100** | Complete convergence |

---

## Usage

```bash
# Run full convergence analysis
python run_qanalysis_full.py

# Results saved to analysis_logs/
```

---

## What It Analyzes

### Frameworks Benchmarked
- **PyTorch** - Matrix multiplication timing
- **TensorFlow** - Matrix multiplication timing
- **JAX** - Matrix multiplication timing
- **NumPy** - Matrix multiplication timing
- **SciPy** - Statistics operations

### Q-Learning Update Rules

```python
# PyTorch updates
q_table[0, 0] += alpha * (torch_score - q_table[0, 0])   # CV
q_table[1, 0] += alpha * (0.90 - q_table[1, 0])        # NLP
q_table[3, 0] += alpha * (0.95 - q_table[3, 0])        # RL

# TensorFlow updates
q_table[0, 1] += alpha * (tf_score - q_table[0, 1])   # CV
q_table[1, 1] += alpha * (0.88 - q_table[1, 1])      # NLP

# JAX updates
q_table[4, 3] += alpha * (0.92 - q_table[4, 3])       # Generative
q_table[3, 3] += alpha * (0.88 - q_table[3, 3])       # RL

# SciPy updates
q_table[2, 2] += alpha * (0.95 - q_table[2, 2])       # Tabular
q_table[2, 4] += alpha * (0.85 - q_table[2, 4])       # Tabular (scipy)
```

---

## Output Example

```
================================================================================
FULL CONVERGENCE Q-LEARNING ANALYSIS
100 ITERATIONS FOR COMPLETE CONVERGENCE
================================================================================

[1/3] Running 100 iterations...

  Iteration  20/100 | Mean: 0.8234 | Std: 0.1421 | Delta: 0.002345
  Iteration  40/100 | Mean: 0.8456 | Std: 0.0987 | Delta: 0.001234
  Iteration  60/100 | Mean: 0.8567 | Std: 0.0765 | Delta: 0.000876
  Iteration  80/100 | Mean: 0.8623 | Std: 0.0654 | Delta: 0.000543
  Iteration 100/100 | Mean: 0.8654 | Std: 0.0587 | Delta: 0.000321

[2/3] Final Q-Table...

  Q-Table State (CONVERGED):
  --------------------------------------------------------------------
  Task                   Torch     TF       Sklearn  JAX      ONNX     Ray
  --------------------------------------------------------------------
  computer_vision        0.9500   0.9200   0.4000   0.8800   0.7000   0.7500
  nlp                    0.9000   0.8800   0.5000   0.8500   0.7000   0.7500
  tabular                0.7000   0.7500   0.9500   0.7500   0.7000   0.8000
  reinforcement          0.9500   0.8500   0.3000   0.8800   0.6500   0.9000
  generative             0.9000   0.8800   0.3500   0.9200   0.7000   0.7500
  --------------------------------------------------------------------

[3/3] Final Recommendations...

  computer_vision       : torch_expert (confidence: 0.9500)
  nlp                   : torch_expert (confidence: 0.9000)
  tabular               : sklearn_expert (confidence: 0.9500)
  reinforcement         : torch_expert (confidence: 0.9500)
  generative            : jax_expert (confidence: 0.9200)

================================================================================
CONVERGENCE COMPLETE
================================================================================

  Iterations: 100
  Final Q-mean: 0.8654
  Final Q-std: 0.0587
  Converged: True
```

---

## Output Files

| File | Contents |
|------|----------|
| `analysis_logs/dlq_full_converge_YYYYMMDD_HHMMSS.json` | Full analysis results |
| `analysis_logs/q_table_converged_YYYYMMDD_HHMMSS.npy` | Q-table as NumPy array |

---

## Relationship to Other Files

```
run_qanalysis_full.py
    │
    ├── Reads: ml_platform_config.json
    │       └── Gets framework configurations
    │
    ├── Imports: numpy
    │       └── Q-table operations
    │
    ├── Imports: torch (optional)
    │       └── Benchmarks if available
    │
    └── Writes: analysis_logs/
            ├── dlq_full_converge_*.json
            └── q_table_converged_*.npy
```

---

## Related Analysis Files

| File | Purpose | See Also |
|------|---------|----------|
| [run_qanalysis.py](run_qanalysis.md) | Single-pass analysis | - |
| [run_qanalysis_multi.py](run_qanalysis_multi.md) | 10-iteration analysis | - |
| [verify_moe.py](verify_moe.md) | Full MoE verification | Uses Q-table |
| [qlearning_table.py](improvements/qlearning_table.md) | Core Q-Learning class | Advanced features |

---

## Technical Details

### Alpha Decay Schedule

```python
alpha = 0.1  # Initial learning rate
alpha_decay = 0.995

# At iteration i:
current_alpha = alpha * (alpha_decay ** i)

# Example:
# Iteration 0:   alpha = 0.1000
# Iteration 25:  alpha = 0.0882
# Iteration 50:  alpha = 0.0778
# Iteration 75:  alpha = 0.0686
# Iteration 99:  alpha = 0.0606
```

### Convergence Criteria

```python
# Converged if:
final_std < 0.1  # Standard deviation below threshold

# Or:
delta < 0.001   # Change between iterations is negligible
```

---

## Quick Reference

```bash
# Run full analysis
python run_qanalysis_full.py

# Check output
ls analysis_logs/

# View results
cat analysis_logs/dlq_full_converge_*.json | python -m json.tool
```

```python
# Load Q-table in Python
import numpy as np

q_table = np.load("analysis_logs/q_table_converged_*.npy")
print("Final Q-table:")
print(q_table)
```
