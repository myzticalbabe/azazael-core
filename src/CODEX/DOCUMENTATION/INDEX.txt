---
title: Azazael Documentation Index
version: 3.0.0
last_updated: 2026-02-11
status: APPROVED
---

# Azazael ML Platform

> Complete ML development environment with local model support

## Quick Start

```bash
cd C:\Users\clint\Desktop\Azazael
.venv\Scripts\activate
python -m ml_platforms
```

## Ecosystem Tools

| Tool | File | Purpose |
|------|------|---------|
| **HYDRA** | `HYDRA.py` | Adaptive Sequential ML Cluster |
| **HORUS** | `HORUS.py` | Human-Readable Output System |
| **TARBABY** | `TARBABY.py` | Binary Building Protocol |
| **PERSEPHONE** | `../PERSEPHONE/` | Cross-Platform Utilities |
| **POINDEXTER** | `../POINDEXTER/` | Mathematical Protocol Indexer |
| **WORMZ** | `../WORMZ/` | BIBL Web Crawler |
| **METAWORMS** | `../METAWORMS/` | Meta Worm Management System |

### HYDRA Commands

```bash
python HYDRA.py --discover      # Auto-discover ML nodes
python HYDRA.py --run           # Run sequential processing
python HYDRA.py --popup         # Show popup monitor
python HYDRA.py --status       # Cluster status
```

### HORUS Commands

```bash
python HORUS.py --destination file    # Generate HTML results
python HORUS.py --watch               # Watch for updates
python HORUS.py --destination popup    # Show popup
```

### TARBABY Commands

```bash
python TARBABY.py --test --path C:/ --duration 600    # 10-min test
python TARBABY.py --source script.py --format exe       # Build exe
python TARBABY.py --monitor                               # Popup monitor
```

### POINDEXTER Commands

```bash
python ../POINDEXTER/POINDEXTER.py    # Run mathematical analysis
```

### WORMZ Commands

```bash
python ../WORMZ/WORMZ.py --all        # Crawl all folders (console)
python ../WORMZ/WORMZ_POPUP.py        # Crawl with popup window
python ../WORMZ/WORMZ.py --delta      # Delta crawl (changes only)
python ../WORMZ/WORMZ.py --status     # Show crawler status
```

### METAWORMS Commands

```bash
python ../METAWORMS/METAWORMS_ENRICHED.py --all   # Run all meta worms (enriched)
python ../METAWORMS/METAWORMS_ENRICHED.py --popup  # Run with popup window
python ../METAWORMS/METAWORMS_ENRICHED.py --report # Generate HTML report
python ../METAWORMS/METAWORMS_ENRICHED.py --status # Show status
```

### ERRORZ Commands

```bash
python ../METAWORMS/ERRORZ.py --scan   # Scan for code errors
python ../METAWORMS/ERRORZ.py --popup  # Run with popup window
python ../METAWORMS/ERRORZ.py --status # Show status
```

## Directory Structure

```
Azazael/
├── .venv/                        # Virtual environment (uv-managed)
│   └── Scripts/
│       └── python.exe            # Python 3.11.14
├── ml_platforms/                 # Core ML modules (grouped by function)
│   ├── harness.py                # Unified CLI interface
│   ├── __init__.py              # Auto-imports
│   ├── config.py                # Configuration management
│   ├── diagnostics.py           # Framework status checks
│   ├── health_monitor.py         # System health monitoring
│   ├── launcher.py              # Interactive session manager
│   ├── universal_converter.py   # GGUF model conversion
│   ├── lm_studio_integration.py # Local LLM integration
│   ├── run_qanalysis*.py        # Q-Learning analysis (3 variants)
│   ├── verify_moe.py            # MoE verification
│   ├── improvements/            # Advanced utilities
│   │   ├── qlearning_table.py   # Q-Learning algorithm
│   │   ├── deep_learning_qlearning.py
│   │   ├── tensor_utils.py      # Tensor utilities
│   │   ├── onnx_integration.py  # ONNX export
│   │   ├── ray_integration.py   # Ray distributed computing
│   │   └── mlflow_integration.py # MLflow tracking
│   └── docs/                    # API documentation (by category)
│       ├── core/                # CLI, config, diagnostics
│       ├── qlearning/           # Framework selection
│       ├── integration/         # MLflow, ONNX, Ray
│       ├── monitoring/          # Health monitoring
│       ├── lmstudio/            # Local LLM
│       ├── analysis/            # Cross-framework analysis
│       └── ...
├── scripts/                      # Utility scripts
├── SHORTY/                      # BAT shortcuts
└── docs/                        # User documentation
    ├── TUTORIALS/              # Step-by-step guides
    └── QUICKREF/               # Command reference
```

## Core Commands

| Command | Description |
|---------|-------------|
| `python harness.py status` | System status |
| `python harness.py analyze` | Q-Learning analysis |
| `python harness.py convert` | Model conversion |
| `python launcher.py` | Interactive session |

## ML Platforms

| Category | Frameworks |
|----------|------------|
| Deep Learning | PyTorch, TensorFlow, Keras |
| Computer Vision | OpenCV, TorchVision, EasyOCR |
| NLP | Transformers, HuggingFace Hub, NLTK |
| Classical ML | Scikit-learn, XGBoost, LightGBM |
| MLOps | MLflow, Ray, ONNX |

## Documentation

| Section | File | Description |
|---------|------|-------------|
| Setup | [INSTRUCTIONS.md](INSTRUCTIONS.md) | Installation guide |
| Protocol | [DOCUMENTATION_PROTOCOL.md](DOCUMENTATION_PROTOCOL.md) | Standards |
| Contributing | [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Contribution guide |
| Quick Start | [docs/TUTORIALS/QUICKSTART.md](docs/TUTORIALS/QUICKSTART.md) | 5-minute guide |
| Commands | [docs/QUICKREF/COMMANDS_QUICKREF.md](docs/QUICKREF/COMMANDS_QUICKREF.md) | Command reference |
| API Docs | [ml_platforms/docs/INDEX.md](ml_platforms/docs/INDEX.md) | Complete API documentation |
| File Guide | [ml_platforms/FILE_EXPLANATIONS.md](ml_platforms/FILE_EXPLANATIONS.md) | Module explanations |

## Configuration

| File | Purpose |
|------|---------|
| `.env.local` | Environment variables |
| `model_config.yaml` | Model configuration |
| `ml_platform_config.json` | Platform settings |

## Auto-Install

```bash
python ml_platforms/auto_install.py
```

Installs 40+ ML packages via uv.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Import errors | Run `python ml_platforms/auto_install.py` |
| LM Studio not found | Check `Settings > Python Interpreter` |
| PATH issues | Restart terminal after changes |
