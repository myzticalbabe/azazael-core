---
title: Harness API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# harness.py

**Location:** `ml_platforms/harness.py`

**Purpose:** Unified command-line interface for all ML platform operations

---

## What This File Does

Think of `harness.py` as the "front door" to your ML platform tools. Instead of remembering 10 different commands for 10 different scripts, you just use this one file with consistent commands.

```
┌─────────────────────────────────────────────────────────────┐
│                    HARNESS.PY                               │
│                                                             │
│   Commands:                                                 │
│   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│   │analyze │  │convert │  │recommend│  │monitor  │       │
│   └─────────┘  └─────────┘  └─────────┘  └─────────┘       │
│   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│   │ status  │  │ models  │  │ verify  │  │ install │       │
│   └─────────┘  └─────────┘  └─────────┘  └─────────┘       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Commands Reference

| Command | What It Does | Related Files |
|---------|--------------|---------------|
| `analyze` | Run framework analysis and Q-learning | [run_qanalysis_full_API.md](../qlearning/run_qanalysis_full_API.md) |
| `convert` | Convert models between formats | [universal_converter.py](../../universal_converter.py) |
| `recommend` | Get framework recommendations | [verify_moe_API.md](../qlearning/verify_moe_API.md) |
| `monitor` | Run health monitoring | [health_monitor_API.md](../monitoring/health_monitor_API.md) |
| `status` | Show system status | [diagnostics_API.md](diagnostics_API.md) |
| `models` | List available models | [model_config.yaml](../../model_config.yaml) |
| `verify` | Run full verification | [verify_moe_API.md](../qlearning/verify_moe_API.md) |
| `install` | Install dependencies | [INDEX.md](../../installation/INDEX.md) |

---

## Usage Examples

### Basic Commands

```bash
# Run framework analysis
python harness.py analyze

# Convert a model
python harness.py convert -m model.gguf -f pytorch

# Get a recommendation for computer vision
python harness.py recommend -t cv

# Check system health
python harness.py monitor

# Show installed platforms
python harness.py status --json

# List available models
python harness.py models
```

### With Options

```bash
# Verbose output
python harness.py analyze --verbose

# JSON output for parsing
python harness.py status --json

# Specify output directory
python harness.py convert -m model.gguf -f pytorch -o my_output
```

---

## Technical Details

### How It Works

1. **Argument Parsing:** Uses `argparse` to handle commands and options
2. **Path Auto-Configuration:** Automatically adds `ml_platforms` to `sys.path`
3. **Command Routing:** Calls appropriate functions based on command

### Key Functions

```python
def cmd_analyze(args)
    """Runs framework analysis. Delegates to run_qanalysis.py"""
    from run_qanalysis import run_q_analysis
    run_q_analysis()

def cmd_convert(args)
    """Converts models. Uses UniversalConverter class"""
    from universal_converter import UniversalConverter
    converter = UniversalConverter()
    result = converter.convert(model_path, framework)

def cmd_recommend(args)
    """Returns framework recommendations based on task type"""

def cmd_monitor(args)
    """Runs health checks. Uses PlatformHealthMonitor class"""
```

---

## Relationship to Other Files

```
harness.py
    │
    ├── Imports from config.py
    │       └── Reads ml_platform_config.json
    │
    ├── Imports from run_qanalysis.py (via cmd_analyze)
    │       └── Uses improvements/qlearning_table.py
    │
    ├── Imports from universal_converter.py (via cmd_convert)
    │       └── Logs to conversion_logs/
    │
    ├── Imports from verify_moe.py (via cmd_verify)
    │       └── Saves to analysis_logs/
    │
    └── Imports from health_monitor.py (via cmd_monitor)
            └── Uses psutil for system metrics
```

---

## Output Locations

| Command | Output Directory | File Pattern |
|---------|------------------|---------------|
| `analyze` | `analysis_logs/` | `dlq_analysis_*.json` |
| `convert` | `conversion_logs/` | `conversion_*.log`, `conversion_*.json` |
| `verify` | `analysis_logs/` | `verification_report_*.json` |

---

## Configuration File

**File:** `ml_platform_config.json`

This file tells harness about available platforms and workflows:

```json
{
  "python_environments": {
    "python311": {
      "packages": [...]
    },
    "python314": {
      "packages": [...]
    }
  }
}
```

See [config.py](config.md) for full details.

---

## Troubleshooting

### "Command not recognized"

```bash
# Check available commands
python harness.py --help
```

### Module not found

Make sure you're running from the Azazael directory:

```bash
cd C:\Users\clint\Desktop\Azazael
python ml_platforms\harness.py status
```

### Slow startup

First run imports all modules. Subsequent runs are faster due to Python's module caching.

---

## Integration Points

### With LM Studio

Harness doesn't directly integrate with LM Studio, but related modules do:

- [lm_studio_integration.py](../../lm_studio_integration.py)

### With Q-Learning

The `analyze` command runs Q-Learning to determine best frameworks:

- [qlearning_table_API.md](../qlearning/improvements/qlearning_table_API.md)
- [run_qanalysis_full_API.md](../qlearning/run_qanalysis_full_API.md)

---

## Quick Reference

```bash
# From Azazael directory
cd C:\Users\clint\Desktop\Azazael

# Show help
python ml_platforms\harness.py --help

# Quick status check
python ml_platforms\harness.py status

# Full analysis
python ml_platforms\harness.py analyze

# Get recommendation
python ml_platforms\harness.py recommend -t cv
```
