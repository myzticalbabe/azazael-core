# coding: utf-8
"""
Generate Docs Index

Module for scripts functionality.

Functions:
    - get_module_name()
    - get_description()
    - get_links_table()
    - generate_index()
    - main()
    - generate_root_index()

Usage:
    >>> import generate_docs_index
    >>> generate_docs_index.get_module_name()

Author: Auto-generated
Date: 2026-02-10
"""

#!/usr/bin/env python3
"""
Documentation Index Generator
============================

Auto-generates INDEX.md files for documentation directories.

Usage:
    python scripts/generate_docs_index.py
    python scripts/generate_docs_index.py --force
"""

from pathlib import Path
from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

BASE_DIR = Path(__file__).parent.parent
DOCS_DIR = BASE_DIR / "ml_platforms" / "docs"

TEMPLATE = """---
title: {title}
version: {version}
last_updated: {date}
status: REVIEW
---

# {title}

> {description}

## Quick Navigation

| Category | Documentation |
|----------|---------------|
{links_table}

## Overview

This directory contains documentation for {module_name}.

## Document Types

| Type | Files | Purpose |
|------|-------|---------|
| API Reference | `*_API.md` | Function/class documentation |
| INDEX | `INDEX.md` | Module navigation |
| Protocol | `*_PROTOCOL.md` | Module-specific rules |

## Getting Started

1. Start with [API Reference](GENERATE_API_LINK)
2. See [Examples](GENERATE_EXAMPLES_LINK) for usage
3. Check [Troubleshooting](GENERATE_TROUBLESHOOTING_LINK) for issues

---

## Related Documentation

- [Root Documentation Protocol](../DOCUMENTATION_PROTOCOL.md)
- [Main Documentation Index](../INDEX.md)
- [Tutorials](../../docs/TUTORIALS/)

---

*Generated: {date}*
"""


def get_module_name(dir_path: Path) -> str:
    """Extract module name from directory."""
    return dir_path.name.replace("_", " ").title()


def get_description(dir_path: Path) -> str:
    """Generate description based on directory."""
    descriptions = {
        "CORE": "Core interface files including harness, config, and diagnostics",
        "QLEARNING": "Q-Learning analysis and framework selection tools",
        "INTEGRATION": "Framework-specific integration utilities",
        "MONITORING": "Health monitoring and diagnostics",
        "CONVERSION": "Model conversion tools",
        "LMSTUDIO": "LM Studio integration modules",
        "INSTALLATION": "Installation and setup utilities",
        "ANALYSIS": "Cross-framework analysis tools",
        "AUTOLOAD": "Automatic loading configuration",
    }

    name = dir_path.name.upper()
    return descriptions.get(name, f"Documentation for {get_module_name(dir_path)}")


def get_links_table(dir_path: Path) -> str:
    """Generate links table for INDEX."""
    links = []

    # Find all markdown files
    for md_file in sorted(dir_path.glob("*.md")):
        if md_file.name in ["INDEX.md", "DOCUMENTATION_PROTOCOL.md"]:
            continue

        name = md_file.stem.replace("_", " ").title()
        links.append(f"| [{name}]({md_file.name}) |")

    return "\n".join(links)


def generate_index(dir_path: Path) -> str:
    """Generate INDEX.md content for a directory."""
    module_name = get_module_name(dir_path)
    description = get_description(dir_path)
    date = datetime.now().strftime("%Y-%m-%d")

    links_table = get_links_table(dir_path)
    if not links_table.strip():
        links_table = "| *No documentation files yet* |"

    return TEMPLATE.format(
        title=f"{module_name} Documentation",
        version="1.0.0",
        date=date,
        description=description,
        links_table=links_table,
        module_name=module_name,
    )


def main():
    """Main entry point."""
    print("Documentation Index Generator")
    print("=" * 40)

    # Generate root INDEX.md
    root_index = DOCS_DIR / "INDEX.md"
    root_content = generate_root_index()
    root_index.write_text(root_content, encoding="utf-8")
    print(f"Generated: {root_index}")

    # Generate INDEX.md for each subdirectory
    for subdir in sorted(DOCS_DIR.iterdir()):
        if subdir.is_dir():
            index_path = subdir / "INDEX.md"
            content = generate_index(subdir)
            index_path.write_text(content, encoding="utf-8")
            print(f"Generated: {index_path}")

            print("\nDone!")


def generate_root_index():
    """Generate the master INDEX.md for docs/."""
    date = datetime.now().strftime("%Y-%m-%d")

    modules = []
    for subdir in sorted(DOCS_DIR.iterdir()):
        if subdir.is_dir() and not subdir.name.startswith("."):
            name = subdir.name.replace("_", " ").title()
            modules.append(f"| [{name}]({subdir.name}/INDEX.md) |")

    return f"""---
title: ML Platforms Documentation Index
version: 1.0.0
last_updated: {date}
status: APPROVED
---

# ML Platforms Documentation Index

> Master index for all `ml_platforms/` documentation

## Module Documentation

| Module | Documentation |
|--------|---------------|
{chr(10).join(modules)}

## Document Types

| Type | Location | Purpose |
|------|----------|---------|
| API Reference | `*/` | Function/class documentation |
| INDEX | `*/INDEX.md` | Module navigation |
| Protocol | `*/DOCUMENTATION_PROTOCOL.md` | Module rules |

## Getting Started

1. [Core Module](CORE/INDEX.md) - Basic operations
2. [Q-Learning Module](QLEARNING/INDEX.md) - Analysis tools
3. [Integration Module](INTEGRATION/INDEX.md) - Framework utilities

## Quick Links

- [Root Documentation Protocol](../DOCUMENTATION_PROTOCOL.md)
- [Main README](../../README.md)
- [Tutorials](../../docs/TUTORIALS/)

---

*Generated: {date}*
"""


if __name__ == "__main__":
    main()
