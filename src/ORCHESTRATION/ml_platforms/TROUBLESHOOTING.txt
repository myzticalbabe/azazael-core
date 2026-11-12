---
title: ML Platform Troubleshooting
version: 1.0.0
last_updated: 2026-02-10
status: APPROVED
authors: Azazael System
---

# ML Platform Troubleshooting Guide

**Location:** `ml_platforms/TROUBLESHOOTING.md`

---

## Overview

Common issues and solutions for the Azazael ML Platform.

## Usage

1. Find your issue type below
2. Follow the solution steps
3. If unresolved, see [Diagnostics API](docs/core/diagnostics_API.md)

---

## Installation Issues

### pip install fails
```bash
Error: Could not find version that satisfies the requirement
```
**Solution:**
1. Check Python: `python --version`
2. Use virtual env: `uv venv ml_env && source ml_env/bin/activate`
3. Install: `uv pip install -r requirements.txt`

### Module not found
```bash
Error: No module named 'numpy'
```
**Solution:**
1. Activate venv: `source venv/bin/activate` (Linux) or `venv\Scripts\activate` (Windows)
2. Reinstall: `uv pip install numpy`
3. Check path: `python -c "import numpy; print(numpy.__file__)"`

---

## Runtime Errors

### CUDA Out of Memory
```python
torch.cuda.OutOfMemoryError: CUDA out of memory
```
**Solutions:**
- Reduce batch size
- Use gradient accumulation: `accumulate_grad_batches=4`
- Use mixed precision: `torch.cuda.amp`
- Offload to CPU: `device='cpu'`

### NumPy Version Mismatch
```bash
numpy.ndarray size changed
```
**Solutions:**
- Install correct version: `uv pip install numpy==1.26.4`
- Use isolated venv: `uv venv --python 3.11 ml_env`
- Reinstall packages after NumPy upgrade

### Gymnasium Import Error
```bash
ModuleNotFoundError: No module named 'gymnasium'
```
**Solutions:**
1. Install: `uv pip install gymnasium`
2. Use correct import: `import gymnasium as gym`

---

## Performance Issues

### Slow Training
1. Check GPU: `nvidia-smi`
2. Enable workers: `DataLoader(num_workers=4)`
3. Use mixed precision

### Memory Leaks
1. Check GPU memory: `torch.cuda.memory_summary()`
2. Clear cache: `torch.cuda.empty_cache()`
3. Delete tensors: `del tensor; gc.collect()`

---

## Quick Commands

```bash
# Check Python path
python -c "import sys; print('\n'.join(sys.path))"

# Check packages
pip list | grep -i torch

# Check CUDA
python -c "import torch; print(torch.cuda.is_available())"

# Platform status
python -m ml_platforms.launcher
```

---

## Getting Help

1. Search knowledge base (if available)
2. Check session logs
3. Review platform documentation
