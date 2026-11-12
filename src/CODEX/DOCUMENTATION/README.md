---
title: Azazael - ML Platform Development Harness
version: 2.1.0
last_updated: 2026-02-10
status: APPROVED
authors: Azazael System
---

# Azazael - ML Platform Development Harness

> Complete ML Development Environment with LM Studio Integration

**Location:** `C:\Users\clint\Desktop\Azazael`

---

## Overview

Azazael is your complete ML development environment providing:

- **36+ ML frameworks** ready to use
- **Smart selection** - Q-Learning picks the best framework for your task
- **Model conversion** - Turn GGUF models into PyTorch, TensorFlow, ONNX, JAX
- **Health monitoring** - Watch your system's resources in real-time
- **Easy integration** - Works directly with LM Studio
- **Cross-platform** - Windows, WSL, and WSA support

## Quick Start (30 seconds)

```bash
# 1. Check everything works
cd C:\Users\clint\Desktop\Azazael
python ml_platforms\harness.py status

# 2. Get a recommendation
python ml_platforms\harness.py recommend -t cv

# 3. Run analysis
python ml_platforms\harness.py analyze
```

## Documentation

| Section | Description |
|---------|-------------|
| [INSTRUCTIONS.md](INSTRUCTIONS.md) | Complete setup and usage guide |
| [DOCUMENTATION_PROTOCOL.md](DOCUMENTATION_PROTOCOL.md) | Documentation standards |
| [Core Docs](ml_platforms/docs/core/INDEX.md) | Core module API reference |
| [API Reference](ml_platforms/docs/INDEX.md) | All module documentation |
| [Tutorials](docs/TUTORIALS/QUICKSTART.md) | Step-by-step guides |
| [Quick Reference](docs/QUICKREF/COMMANDS_QUICKREF.md) | Command cheat sheet |
| [Contributing](docs/CONTRIBUTING.md) | How to contribute |

## Directory Structure

```
Azazael/
├── ml_platforms/              # Core ML platform modules (grouped by function)
│   ├── harness.py              # Unified CLI interface
│   ├── __init__.py            # Auto-imports
│   ├── config.py              # Configuration management
│   ├── diagnostics.py         # Framework status checks
│   ├── health_monitor.py      # System health monitoring
│   ├── launcher.py            # Interactive session manager
│   ├── universal_converter.py # GGUF model conversion
│   ├── lm_studio_integration.py # Local LLM integration
│   ├── run_qanalysis*.py     # Q-Learning analysis (3 variants)
│   ├── verify_moe.py         # MoE verification
│   ├── improvements/          # Advanced utilities
│   │   ├── qlearning_table.py # Q-Learning algorithm
│   │   ├── deep_learning_qlearning.py # DL-specific Q-Learning (NEW)
│   │   ├── tensor_utils.py   # Tensor utilities
│   │   ├── onnx_integration.py # ONNX export
│   │   ├── ray_integration.py # Ray distributed computing
│   │   └── mlflow_integration.py # MLflow tracking
│   └── docs/                  # API documentation (grouped by category)
│       ├── core/             # CLI, config, diagnostics
│       ├── qlearning/        # Framework selection
│       ├── integration/      # MLflow, ONNX, Ray
│       ├── monitoring/       # Health monitoring
│       ├── lmstudio/         # Local LLM
│       └── ...
├── docs/                      # User documentation
│   ├── TUTORIALS/            # Step-by-step guides
│   └── QUICKREF/             # Command reference
├── scripts/                   # Utility scripts
└── SHORTY/                    # BAT shortcuts
```

## Commands

| Command | Description |
|---------|-------------|
| `harness.py status` | Show system and framework status |
| `harness.py analyze` | Run framework analysis |
| `harness.py convert -m model.gguf -f pytorch` | Convert model |
| `harness.py recommend -t cv` | Get framework recommendation |
| `harness.py monitor` | Run health monitoring |
| `harness.py models` | List available models |
| `harness.py verify` | Full system check |

## Validation

Validate documentation quality:
```bash
python scripts/validate_docs.py .
```

Generate search index:
```bash
python scripts/validate_docs.py --generate-search
```

## Cross-Platform Development

Azazael supports development across Windows, WSL, and WSA:

| Platform | Azazael Path | PERSEPHONE Path |
|----------|--------------|-----------------|
| Windows | `C:\Users\clint\Desktop\Azazael` | `C:\Users\clint\Desktop\PERSEPHONE` |
| WSL | `/mnt/c/Users/clint/Desktop/Azazael` | `/mnt/c/Users/clint/Desktop/PERSEPHONE` |
| WSA | `/mnt/sdcard/` | `/mnt/sdcard/` |

**Cross-Platform Utilities:**
- [PERSEPHONE](../PERSEPHONE/README.md) - Path translation and sync utilities
- [azazael_wsa.py](scripts/azazael_wsa.py) - WSA integration module

## Contributing

See [Contributing Guide](docs/CONTRIBUTING.md) for:
- How to report issues
- How to make changes
- Style guide
- Review process

## Version

- **Current:** 2.1.0
- **Protocol:** DOCUMENTATION_PROTOCOL.md v2.0.0
- **Last Updated:** 2026-02-10
