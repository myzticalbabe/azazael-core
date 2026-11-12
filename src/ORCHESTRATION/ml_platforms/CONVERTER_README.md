---
title: Universal Model Converter
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# Universal Model Converter

## Overview
Converts GGUF models from LM Studio to PyTorch, TensorFlow, ONNX, JAX, and other frameworks.

## Installation

```bash
pip install llama-cpp-python safetensors transformers optimum onnxruntime tensorflow jax jaxlib flax
```

## Usage

### Command Line

```bash
# Analyze a model (no conversion)
python universal_converter.py models/mistral.gguf --analyze

# Convert to PyTorch
python universal_converter.py models/mistral.gguf --framework pytorch

# Convert to TensorFlow
python universal_converter.py models/mistral.gguf --framework tensorflow

# Convert to ONNX
python universal_converter.py models/mistral.gguf --framework onnx

# Convert to JAX
python universal_converter.py models/mistral.gguf --framework jax

# Specify output directory
python universal_converter.py models/mistral.gguf --framework pytorch --output my_conversion
```

### Python API

```python
from universal_converter import UniversalConverter

converter = UniversalConverter()

# Analyze model
metadata = converter.analyze("models/mistral.gguf")
print(metadata)

# Convert to PyTorch
result = converter.convert("models/mistral.gguf", "pytorch")
print(f"Success: {result.success}")
print(f"Output: {result.output_path}")

# Convert to ONNX
result = converter.convert("models/qwen-vl.gguf", "onnx")
```

## Supported Frameworks

| Framework | Format | Notes |
|-----------|--------|-------|
| PyTorch | SafeTensors/.bin | Best support |
| TensorFlow | SavedModel/.pb | Requires weights mapping |
| ONNX | .onnx | Basic export |
| JAX | Config + Weights | NumPy intermediate |

## Model Support

| Model Type | Support | Notes |
|------------|---------|-------|
| LLaMA/Mistral | Full | All quantizations |
| Qwen | Full | VL models need extra step |
| Code Models | Full | NousCoder, etc. |
| Vision Models | Partial | mmproj extraction |

## Logging

Logs are saved to `conversion_logs/` with:
- `conversion_YYYYMMDD_HHMMSS.log` - Human readable
- `conversion_YYYYMMDD_HHMMSS.json` - Machine readable

## Troubleshooting

### GGUF Analysis Fails
Install llama-cpp-python:
```bash
pip install llama-cpp-python
```

### PyTorch Conversion Issues
Check tensor names match expected format:
```python
from universal_converter import debug_conversion
debug_conversion("model.gguf", "pytorch")
```

### ONNX Export
Requires optimum:
```bash
pip install optimum[onnxruntime]
```

## Directory Structure After Conversion

```
model_pytorch/
├── config.json          # Model configuration
├── model.safetensors    # Weights
└── tokenizer.json       # Tokenizer files
```
