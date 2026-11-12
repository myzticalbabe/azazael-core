# Azazael Ecosystem API Reference

**Version:** 1.0.0  
**Date:** 2026-02-11

---

## Table of Contents

1. [Azazael Integration](#azazael-integration)
2. [HYDRA Cluster](#hydra-cluster)
3. [WORMZ Execution](#wormz-execution)
4. [METAWORMS Orchestration](#metaworms-orchestration)
5. [HORUS Presentation](#horus-presentation)
6. [TERENCE Error Resolution](#terence-error-resolution)
7. [KLINK Command Launcher](#klink-command-launcher)

---

## Azazael Integration

### AzazaelEcosystem

**Description:** Central orchestration layer for the Azazael Ecosystem.

**Location:** `Azazael/Azazael_INTEGRATION.py`

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| version | str | Version string |
| components | dict | Registered components |

**Methods:**

#### discover_all() -> dict

**Description:** Discover all ecosystem components.

**Returns:** (dict) Component inventory

**Example:**
```python
from Azazael.Azazael_INTEGRATION import AzazaelEcosystem
ecosystem = AzazaelEcosystem()
inventory = ecosystem.discover_all()
```

#### get_component_status(name: str) -> dict

**Description:** Get status of a specific component.

**Parameters:**
- `name` (str): Component name

**Returns:** (dict) Component status

---

## HYDRA Cluster

### HYDRACluster

**Description:** Adaptive Sequential ML Cluster System.

**Location:** `HYDRA/HYDRA.py`

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| config | dict | Cluster configuration |
| stages | list | Processing:**

#### run_dis stages |

**Methodscover() -> dict

**Description:** Run discovery stage.

**Returns:** (dict) Discovery results

#### run_analyze() -> dict

**Description:** Run analysis stage.

**Returns:** (dict) Analysis results

#### run_all() -> dict

**Description:** Execute full pipeline.

**Returns:** (dict) Pipeline results

---

## WORMZ Execution

### WORMZ

**Description:** Functional worm execution system.

**Location:** `WORMZ/WORMZ.py`

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| worms | list | Active worms |
| bibl | BIBLEntry | Business Intelligence Basic Lexicon |

**Methods:**

#### crawl(path: str) -> CrawlResult

**Description:** Crawl filesystem starting at path.

**Parameters:**
- `path` (str): Starting directory

**Returns:** (CrawlResult) Crawl results

#### run_all() -> list

**Description:** Execute all worms.

**Returns:** (list) List of worm results

---

## METAWORMS Orchestration

### METAWORMS

**Description:** Tier 3-4 metaworm orchestration system.

**Location:** `METAWORMS/METAWORMS.py`

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| metaworms | list | Available metaworms |
| config | MetaWormConfig | Configuration |

**Methods:**

#### run_metaworm(name: str) -> WormResult

**Description:** Execute a specific metaworm.

**Parameters:**
- `name` (str): Metaworm name

**Returns:** (WormResult) Execution result

#### run_all() -> list

**Description:** Execute all metaworms.

**Returns:** (list) List of results

### HYDRAMETA

**Description:** Genetic algorithm ML optimizer.

**Location:** `METAWORMS/HYDRAMETA.py`

**Methods:**

#### optimize() -> dict

**Description:** Execute genetic algorithm optimization.

**Returns:** (dict) Optimized parameters

---

## HORUS Presentation

### HORUS

**Description:** Human-Readable Output Unified System.

**Location:** `HORUS/HORUS.py`

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| format | OutputFormat | Output format |
| config | OutputConfig | Rendering config |

**Methods:**

#### render(data: dict, format: str) -> str

**Description:** Render data to specified format.

**Parameters:**
- `data` (dict): Data to render
- `format` (str): Output format (html/json/console)

**Returns:** (str) Rendered output

#### show_popup() -> None

**Description:** Display results in popup window.

---

## TERENCE Error Resolution

### TERENCE

**Description:** Error Resolution Protocol with pattern matching.

**Location:** `TERENCE/TERENCE.py`

**Methods:**

#### analyze_file(path: str) -> list

**Description:** Analyze file for fixable patterns.

**Parameters:**
- `path` (str): File path

**Returns:** (list) List of fixes

#### apply_fix(fix: Fix, dry_run: bool) -> bool

**Description:** Apply a fix to a file.

**Parameters:**
- `fix` (Fix): Fix to apply
- `dry_run` (bool): Preview without applying

**Returns:** (bool) Success status

---

## KLINK Command Launcher

### klink

**Description:** Unified command launcher for ecosystem operations.

**Location:** `KLINK/klink.py`

**Methods:**

#### run_command(cmd: str, args: list) -> int

**Description:** Execute a klink command.

**Parameters:**
- `cmd` (str): Command name
- `args` (list): Command arguments

**Returns:** (int) Exit code

---

## Data Structures

### WormResult

```python
@dataclass
class WormResult:
    name: str
    success: bool
    files_processed: int = 0
    items_found: int = 0
    duration_ms: float = 0.0
    output: Dict[str, Any]
    errors: List[str] = []
```

### Fix

```python
@dataclass
class Fix:
    file: str
    line: int
    error_type: str
    original: str
    fixed: str
    description: str
```

---

*Generated: 2026-02-11*
