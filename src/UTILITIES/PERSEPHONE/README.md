---
title: PERSEPHONE - Cross-Platform Development Environment
version: 1.1.0
last_updated: 2026-02-11
status: ACTIVE
authors: Azazael System
---

# PERSEPHONE - Cross-Platform Development Environment

Cross-platform development utilities for **Azazael ML Platform** running across Windows, WSL, and WSA.

## Directory Structure

```
PERSEPHONE/
├── README.md                    # This file
├── requirements_wsl.txt         # Python dependencies for WSL
├── requirements_wsa.txt         # WSA dependencies (shared with Azazael)
├── persephone.py               # Main cross-platform utilities
├── sync_azazael.bat            # Windows sync script
├── sync_azazael_wsl.sh         # WSL sync script
├── lib/                        # PERSEPHONE libraries
├── bin/                        # PERSEPHONE scripts
├── data/                       # Shared data
├── models/                     # ML models
├── notebooks/                  # Jupyter notebooks
├── docs/                       # PERSEPHONE documentation
└── .env                        # Environment variables
```

## Quick Start

### 1. Install Dependencies

**Windows (PowerShell):**
```powershell
pip install -r requirements_wsl.txt
```

**WSL (Bash):**
```bash
pip install -r requirements_wsl.txt
```

### 2. Configure Environment

**Add to `~/.bashrc` or `~/.zshrc`:**
```bash
# Azazael paths
export Azazael_ROOT="/mnt/c/Users/clint/Desktop/Azazael"
export PERSEPHONE_ROOT="/mnt/c/Users/clint/Desktop/PERSEPHONE"

# Python paths
export PYTHONPATH="$Azazael_ROOT/ml_platforms:$PYTHONPATH"
export PYTHONPATH="$PERSEPHONE_ROOT/lib:$PYTHONPATH"

# Path additions
export PATH="$Azazael_ROOT/scripts:$PATH"
export PATH="$PERSEPHONE_ROOT/bin:$PATH"

# Aliases
alias azazael="python $Azazael_ROOT/scripts/azazael_cli.py"
alias persephone="python $PERSEPHONE_ROOT/bin/persephone.py"
alias sync="bash $PERSEPHONE_ROOT/sync_azazael_wsl.sh"
```

### 3. Run Sync

**Windows:**
```cmd
.\sync_azazael.bat
```

**WSL:**
```bash
./sync_azazael_wsl.sh
```

## Path Mapping

| Concept | Windows Path | WSL Path |
|---------|-------------|----------|
| Azazael | `C:\Users\clint\Desktop\Azazael` | `/mnt/c/Users/clint/Desktop/Azazael` |
| PERSEPHONE | `C:\Users\clint\Desktop\PERSEPHONE` | `/mnt/c/Users/clint/Desktop/PERSEPHONE` |
| WSA Storage | `\\wsl$\Ubuntu\home\` | `/home/` |

## Usage

### Python API

```python
from persephone import Persephone, PathTranslator

# Initialize
p = Persephone()

# Get paths
print(p.azazael.wsl_path)
print(p.azazael.windows_path)

# Translate paths
wsl_path = PathTranslator.windows_to_wsl("C:\\Users\\clint\\Desktop\\Azazael")
windows_path = PathTranslator.wsl_to_windows("/mnt/c/Users/clint/Desktop/PERSEPHONE")

# Get Python path extensions
for path in p.pythonpath_extensions:
    print(path)

# Run commands in WSL
stdout, stderr, code = p.run_in_wsl("python --version")
```

### Sync Scripts

**Windows (robocopy):**
```cmd
sync_azazael.bat
# Options:
#   1 = Azazael -> PERSEPHONE
#   2 = PERSEPHONE -> Azazael
#   3 = Bidirectional
#   4 = Dry run
```

**WSL (rsync):**
```bash
./sync_azazael_wsl.sh
# Options:
#   to-pers    = Azazael -> PERSEPHONE
#   from-pers  = PERSEPHONE -> Azazael
#   both       = Bidirectional
#   dry        = Compare only
```

### Generate Setup Script

```python
from persephone import Persephone

p = Persephone()
bash_script = p.setup_environment("bash")
powershell_script = p.setup_environment("powershell")

print(bash_script)
```

## Features

### Path Translation
- Windows ↔ WSL path conversion
- WSA path detection
- Auto-discovery of Azazael/PERSEPHONE roots

### File Synchronization
- robocopy (Windows)
- rsync (WSL)
- Bidirectional sync
- Dry run mode
- Exclude patterns (`*.pyc`, `__pycache__`, etc.)

### Environment Management
- Multi-shell support (bash, zsh, PowerShell)
- Python path configuration
- Alias generation
- Environment variable loading

### Cross-Platform Development
- Unified Python API
- Platform detection
- Desktop shortcut creation

## Requirements

### Core
- Python 3.9+
- rsync (WSL)
- robocopy (Windows)

### Optional
- WSL2
- Docker
- Git

## Environment Variables

| Variable | Description |
|----------|-------------|
| `Azazael_ROOT` | Azazael root directory |
| `PERSEPHONE_ROOT` | PERSEPHONE root directory |
| `PYTHONPATH` | Python import paths |
| `PATH` | Executable search paths |

## Integration with Azazael

Access Azazael scripts from PERSEPHONE:

```python
from persephone import Persephone

p = Persephone()

# Run Azazael CLI
p.run_in_wsl("python $Azazael_ROOT/scripts/azazael_cli.py status")

# Run tests
p.run_in_wsl("python $Azazael_ROOT/scripts/run_tests.py")
```

## Troubleshooting

### Paths Not Detected
Set environment variables:
```bash
export Azazael_ROOT="/mnt/c/Users/clint/Desktop/Azazael"
export PERSEPHONE_ROOT="/mnt/c/Users/clint/Desktop/PERSEPHONE"
```

### rsync Not Found (WSL)
Install rsync in WSL:
```bash
sudo apt install rsync
```

### robocopy Not Found (Windows)
robocopy is built into Windows. Run from Command Prompt or PowerShell.

### Permission Denied (WSL)
Make scripts executable:
```bash
chmod +x sync_azazael_wsl.sh
```

## Files Generated

| File | Description |
|------|-------------|
| `~/.persephone/config.json` | Path configuration |
| `~/.persephone/.env` | Environment variables |
| `~/.persephone/sync_log.txt` | Sync operation logs |

## Relationship to Azazael

PERSEPHONE is the cross-platform development companion to Azazael:

| Aspect | Azazael | PERSEPHONE |
|--------|---------|------------|
| Purpose | ML Platform Harness | Cross-Platform Utilities |
| Location (Windows) | `C:\Users\clint\Desktop\Azazael` | `C:\Users\clint\Desktop\PERSEPHONE` |
| Location (WSL) | `/mnt/c/Users/clint/Desktop/Azazael` | `/mnt/c/Users/clint/Desktop/PERSEPHONE` |
| Version | 2.1.0 | 1.0.0 |

**Integration:**
- PERSEPHONE can execute Azazael scripts
- Bidirectional file synchronization
- Shared Python path configuration
- WSA integration support

## Ecosystem Integration

PERSEPHONE integrates with the complete Azazael ecosystem:

| System | Location | Purpose |
|--------|----------|---------|
| **HYDRA** | `C:\Users\clint\Desktop\HYDRA.py` | Adaptive Sequential ML Cluster |
| **HORUS** | `C:\Users\clint\Desktop\HORUS.py` | Human-Readable Output System |
| **TARBABY** | `C:\Users\clint\Desktop\TARBABY.py` | Binary Builder Protocol |
| **POINDEXTER** | `C:\Users\clint\Desktop\POINDEXTER\` | Mathematical Protocol Indexer |
| **WORMZ** | `C:\Users\clint\Desktop\WORMZ\` | BIBL Web Crawler |
| **BIBL** | `C:\Users\clint\Desktop\Azazael\BIBL\` | Hyperlinked Lexicon |
| **Metrics** | `ml_platforms/improvements/metrics_tracker.py` | Quantitative Metrics |

### HYDRA Integration

Run HYDRA sequential processing from PERSEPHONE:

```python
from persephone import Persephone

p = Persephone()

# Run HYDRA processing
stdout, stderr, code = p.run_in_wsl(f"python {p.azazael.wsl_path}/HYDRA.py --run")
```

### HORUS Output

Access HORUS results from PERSEPHONE:

```python
from persephone import Persephone

p = Persephone()

# HORUS results are stored in HORUS directory
horus_path = p.azazael.windows_path.replace("Azazael", "HORUS")
```

### POINDEXTER Analysis

Run mathematical analysis from PERSEPHONE:

```python
from persephone import Persephone

p = Persephone()

# Run POINDEXTER analysis
stdout, stderr, code = p.run_in_wsl(f"python {p.poindexter.wsl_path}/POINDEXTER.py")
```

### WORMZ Crawler

Run BIBL crawler from PERSEPHONE:

```python
from persephone import Persephone

p = Persephone()

# Run WORMZ to update BIBL
stdout, stderr, code = p.run_in_wsl(f"python {p.wormz.wsl_path}/WORMZ.py --all")
```

### METAWORMS Management

Run meta worm management from PERSEPHONE:

```python
from persephone import Persephone

p = Persephone()

# Run all meta worms
stdout, stderr, code = p.run_in_wsl(f"python {p.metaworms.wsl_path}/METAWORMS_ENRICHED.py --all")

# Run with popup
stdout, stderr, code = p.run_in_wsl(f"python {p.metaworms.wsl_path}/METAWORMS_POPUP.py")
```

### ERRORZ Code Error Crawler

Run code error crawler from PERSEPHONE:

```python
from persephone import Persephone

p = Persephone()

# Scan for code errors
stdout, stderr, code = p.run_in_wsl(f"python {p.metaworms.wsl_path}/ERRORZ.py --scan")

# Run with popup
stdout, stderr, code = p.run_in_wsl(f"python {p.metaworms.wsl_path}/ERRORZ.py --popup")
```

## See Also

- [Azazael README](../Azazael/README.md)
- [Azazael Enhancement Plan](../Azazael/Azazael_ENHANCEMENT_PLAN.md)
- [WSA Integration](../Azazael/scripts/azazael_wsa.py)
- [Test Results](../Azazael/TEST_RESULTS.md)

---

*PERSEPHONE v1.0.0 - Cross-Platform Development Environment*
