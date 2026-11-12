# TD-006: TERENCE & KLINK Technical Specification

**Document ID:** TD-006  
**Components:** TERENCE (`TERENCE.py`), KLINK (`klink.py`)  
**Classification:** Technical Specification  
**Version:** 1.0.0  
**Date:** 2026-02-11  

---

## Abstract

This document presents comprehensive technical specifications for two supporting infrastructure components of the Azazael Ecosystem:

1. **TERENCE**: Error Resolution Protocol implementing automated code repair with pattern matching and 26 fix patterns
2. **KLINK**: Unified Command Launcher providing cross-platform CLI access to all ecosystem operations

**Keywords:** error resolution, pattern matching, automated repair, CLI, cross-platform, command orchestration

---

## Part A: TERENCE Error Resolution Protocol

### A.1 Introduction

TERENCE implements an automated error resolution system based on **pattern-directed repair** [1] and **program repair** [2] paradigms. The system identifies common Python anti-patterns and applies predefined fixes through regex-based pattern matching.

### A.2 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     TERENCE Error Resolution                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Pattern Registry                          │  │
│  │  ├── Syntax Patterns (6)                                  │  │
│  │  ├── Style Patterns (8)                                   │  │
│  │  ├── Security Patterns (4)                                 │  │
│  │  └── Performance Patterns (8)                               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Fix Engine                                │  │
│  │  ├── Pattern Matcher (Regex)                               │  │
│  │  ├── Code Transformer (AST-based)                          │  │
│  │  └── Preview Generator                                     │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Statistics Tracker                       │  │
│  │  ├── Fix counters                                         │  │
│  │  └── Error tracking                                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### A.3 Core Data Structures

```python
@dataclass
class Fix:
    """Single fix for an error."""
    file: str              # Target file
    line: int             # Line number
    error_type: str       # Error category
    original: str         # Original code
    fixed: str           # Fixed code
    description: str     # Fix description


@dataclass
class FixStats:
    """Fix statistics."""
    files_scanned: int = 0
    errors_found: int = 0
    fixes_applied: int = 0
    fixes_previewed: int = 0
    errors_remaining: int = 0
    duration_ms: float = 0.0
```

### A.4 Fix Patterns

| Pattern ID | Category | Description | Fix |
|------------|----------|-------------|-----|
| TER-001 | Syntax | Bare `except:` | `except Exception as e:` |
| TER-002 | Style | `print()` statements | Comment out debug prints |
| TER-003 | Style | Tab indentation | Convert to spaces |
| TER-004 | Security | Hardcoded secrets | Flag for review |
| TER-005 | Performance | Inefficient string concat | Use f-strings |
| TER-006 | Syntax | Missing docstrings | Auto-generate |
| TER-007 | Style | Long lines (>79 chars) | Wrap content |
| TER-008 | Syntax | Unused imports | Remove unused |
| ... | ... | ... | ... |

### A.5 Implementation

```python
class TERENCE:
    """Error Resolution Protocol."""
    
    VERSION = "1.0.0"
    
    FIXES = [
        # Bare except -> Specific exception
        {
            "pattern": r"except\s*:",
            "match": "except:",
            "fix": "except Exception as e:",
            "desc": "Replace bare except with Exception handling",
        },
        # print(x) -> logging
        {
            "pattern": r"print\s*\(\s*(?![\"'])",
            "match": "print(",
            "fix": "# print(  # Debug",
            "desc": "Comment out print statement",
        },
        # Tab indentation -> Spaces
        {
            "pattern": r"^\t+",
            "match": "\t",
            "fix": "    ",  # 4 spaces
            "desc": "Replace tabs with 4 spaces",
        },
    ]
    
    def analyze_file(self, file_path: Path) -> List[Fix]:
        """Analyze file for fixable patterns."""
        fixes = []
        content = file_path.read_text()
        
        for fix_rule in self.FIXES:
            matches = re.finditer(fix_rule["pattern"], content, re.MULTILINE)
            for match in matches:
                fixes.append(Fix(
                    file=str(file_path),
                    line=content[:match.start()].count('\n') + 1,
                    error_type=fix_rule["desc"],
                    original=match.group(),
                    fixed=fix_rule["fix"],
                    description=fix_rule["desc"]
                ))
        
        return fixes
    
    def apply_fix(self, fix: Fix, dry_run: bool = True) -> bool:
        """Apply a single fix to a file."""
        content = Path(fix.file).read_text()
        
        if fix.original in content:
            new_content = content.replace(fix.original, fix.fixed)
            
            if not dry_run:
                Path(fix.file).write_text(new_content)
            
            return True
        return False
    
    def preview_fixes(self, fixes: List[Fix]) -> str:
        """Generate preview of all fixes."""
        preview = []
        for fix in fixes:
            preview.append(f"Line {fix.line}: {fix.description}")
            preview.append(f"  Original: {fix.original}")
            preview.append(f"  Fixed:    {fix.fixed}")
            preview.append("")
        
        return "\n".join(preview)
```

---

## Part B: KLINK Unified Command Launcher

### B.1 Introduction

KLINK provides a **unified command-line interface** [3] to all ecosystem operations, supporting multiple platforms (Windows, Linux, macOS, WSL) through a consistent command structure.

### B.2 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      KLINK Command Launcher                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Command Parser                          │  │
│  │  ├── ArgumentParser wrapper                               │  │
│  │  ├── Command discovery                                    │  │
│  │  └── Help generation                                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Command Registry                        │  │
│  │  ├── Core Commands (status, version, list)               │  │
│  │  ├── WORMZ Commands (systemz, processz, diskz, etc.)     │  │
│  │  ├── METAWORMS Commands (errorz, trimz, healthz, etc.)   │  │
│  │  └── Ecosystem Commands (terence, horus, tarbaby, etc.)  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Platform Adapter                        │  │
│  │  ├── Windows (CMD/PowerShell)                            │  │
│  │  ├── Linux (Bash)                                       │  │
│  │  ├── macOS (Zsh)                                        │  │
│  │  └── WSL                                                 │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### B.3 Command Registry

```python
# Command mappings
COMMANDS = {
    # Core
    "status": {"func": "cmd_status", "help": "Show ecosystem status"},
    "version": {"func": "cmd_version", "help": "Show version info"},
    "list": {"func": "cmd_list", "help": "List all commands"},
    
    # WORMZ
    "worms": {"func": "cmd_worms", "help": "Run all WORMZ worms"},
    "systemz": {"func": "cmd_systemz", "help": "System information worm"},
    "processz": {"func": "cmd_processz", "help": "Process management worm"},
    "diskz": {"func": "cmd_diskz", "help": "Disk analysis worm"},
    "networkz": {"func": "cmd_networkz", "help": "Network analysis worm"},
    "clockz": {"func": "cmd_clockz", "help": "Time synchronization worm"},
    
    # METAWORMS
    "metaworms": {"func": "cmd_metaworms", "help": "Run all METAWORMS"},
    "errorz": {"func": "cmd_errorz", "help": "Error tracking metaworm"},
    "trimz": {"func": "cmd_trimz", "help": "Optimization metaworm"},
    "healthz": {"func": "cmd_healthz", "help": "Health monitoring metaworm"},
    "hydrometa": {"func": "cmd_hydrometa", "help": "HYDRA metaworm"},
    
    # HYDRA
    "hydra": {"func": "cmd_hydra", "help": "Run HYDRA cluster", "has_args": True},
    
    # Ecosystem
    "terence": {"func": "cmd_terence", "help": "TERENCE error resolver"},
    "horus": {"func": "cmd_horus", "help": "HORUS output formatter"},
    "tarbaby": {"func": "cmd_tarbaby", "help": "Binary builder"},
    "poindexter": {"func": "cmd_poindexter", "help": "Mathematical indexer"},
    "persephone": {"func": "cmd_persephone", "help": "Cross-platform utilities"},
    
    # Optimization
    "optimize": {"func": "cmd_optimize", "help": "Run ecosystem optimizer"},
    "monitor": {"func": "cmd_monitor", "help": "Launch live monitoring popup"},
    
    # Full runs
    "all": {"func": "cmd_all", "help": "Run full ecosystem"},
    "full": {"func": "cmd_all", "help": "Run full ecosystem"},
}
```

### B.4 Platform Detection

```python
# Detect platform and set root
if sys.platform == "win32":
    Azazael_ROOT = os.environ.get("Azazael_ROOT", DEFAULT_Azazael_ROOT)
else:
    Azazael_ROOT = os.environ.get("Azazael_ROOT", LINUX_Azazael_ROOT)


def run_python(script: str, args: List[str] = None):
    """Run Python script with arguments."""
    cmd = [sys.executable, script] + (args or [])
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result
```

### B.5 Command Implementations

```python
def cmd_status():
    """Show ecosystem status."""
    run_python(f"{Azazael_ROOT}/Azazael/Azazael_INTEGRATION.py", ["--status"])


def cmd_worms():
    """Run all WORMZ worms."""
    run_python(f"{Azazael_ROOT}/WORMZ/WORMZ.py", ["--all"])


def cmd_hydra(args):
    """Run HYDRA cluster."""
    if "--all" in args:
        run_python(f"{Azazael_ROOT}/Azazael/HYDRA.py", ["--all"])
    else:
        run_python(f"{Azazael_ROOT}/Azazael/HYDRA.py", args)


def cmd_terence():
    """Run TERENCE error resolver."""
    run_python(f"{Azazael_ROOT}/TERENCE/TERENCE.py", ["--scan", "--fix"])


def cmd_horus():
    """Launch HORUS output formatter."""
    run_python(f"{Azazael_ROOT}/Azazael/HORUS.py", ["--popup"])


def cmd_all():
    """Run full ecosystem."""
    subprocess.run(f"{Azazael_ROOT}/Azazael/ECOSYSTEM_TEST.bat", shell=True)
```

---

## References

[1] Perkins, J. H., et al. (2009). Automatically patching errors in deployed software. ACM SOSP.

[2] Le Goues, C., et al. (2012). The GenProg approach to automated software repair. IEEE TSE.

[3] Raymond, E. S. (2003). The Art of Unix Programming. Addison-Wesley.

---

*Document ID: TD-006*
*Components: TERENCE, KLINK*
*Classification: Technical Specification*
*Version: 1.0.0*
