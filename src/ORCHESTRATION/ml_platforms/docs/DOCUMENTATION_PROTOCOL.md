---
title: ML Platforms Documentation Protocol
version: 2.0.0
last_updated: 2026-02-10
status: DEPRECATED
authors: Azazael System
replaced_by: ../../DOCUMENTATION_PROTOCOL.md
---

> **WARNING:** This protocol is deprecated. Use the consolidated [Azazael Documentation Protocol](../../DOCUMENTATION_PROTOCOL.md) instead.

# ML Platforms Documentation Protocol

**Location:** `ml_platforms/docs/DOCUMENTATION_PROTOCOL.md`

**Parent Protocol:** `DOCUMENTATION_PROTOCOL.md` (root - consolidated)

## Overview

This protocol defines documentation standards for all files in `ml_platforms/` including Python modules, scripts, and configuration files.

## Document Types in ml_platforms

| Type | Location | Suffix | Purpose |
|------|----------|---------|---------|
| Module API | `docs/*/` | `_API.md` | Function/class documentation |
| Module INDEX | `docs/*/` | `INDEX.md` | Module navigation |
| Module Protocol | `docs/*/` | `*_PROTOCOL.md` | Module-specific rules |

## Required Sections

### Python Module Docs

```markdown
1. Title & Metadata (YAML header)
2. What This File Does (Overview)
3. Key Classes/Functions (API)
4. Usage Examples (Code)
5. Configuration Options (if applicable)
6. Relationship to Other Files
7. Troubleshooting
8. Quick Reference
```

### Configuration Files

```markdown
1. Title & Metadata
2. Purpose
3. Configuration Structure
4. Options Reference
5. Examples
6. Related Files
```

## Cross-Reference Format

### Internal Links

```markdown
[Module Index](INDEX.md)
[API Docs](module_API.md)
[Source Code](../module.py)
[Related Module](docs/other/INDEX.md)
```

### Code References

```markdown
[`class_name()`](#classname)
[`function_name()`](#function-name)
[`module.constant`](../module.py)
```

## File Structure

```
ml_platforms/
├── docs/
│   ├── DOCUMENTATION_PROTOCOL.md   # This file
│   ├── INDEX.md                   # Master index
│   ├── CORE/
│   │   ├── INDEX.md
│   │   ├── harness_API.md
│   │   ├── config_API.md
│   │   └── diagnostics_API.md
│   ├── QLEARNING/
│   │   ├── INDEX.md
│   │   ├── run_qanalysis_API.md
│   │   └── verify_moe_API.md
│   ├── INTEGRATION/
│   │   ├── INDEX.md
│   │   ├── onnx_integration_API.md
│   │   ├── ray_integration_API.md
│   │   └── mlflow_integration_API.md
│   └── ...
│
├── harness.py                      # Source
├── config.py                       # Source
└── ...
```

## Version Control

### Document Status

| Status | Meaning |
|--------|---------|
| DRAFT | Initial version |
| REVIEW | Under review |
| APPROVED | Finalized |
| DEPRECATED | Outdated, use newer |

### Update Frequency

| File Type | Update When |
|-----------|------------|
| API Docs | Function signature changes |
| Tutorials | New features added |
| Protocol | Process changes |

## Quick Links

- [Root Documentation Protocol](../../DOCUMENTATION_PROTOCOL.md)
- [Main README](../../README.md)
- [API Index](INDEX.md)
- [Tutorials](../../docs/TUTORIALS/)

---

## See Also

- [Root Protocol](../../DOCUMENTATION_PROTOCOL.md)
- [Core Module](CORE/INDEX.md)
- [Q-Learning Module](QLEARNING/INDEX.md)
- [Integration Module](INTEGRATION/INDEX.md)
