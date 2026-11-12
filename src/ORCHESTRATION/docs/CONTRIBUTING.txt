---
title: Contributing Guide
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# Contributing to Azazael Documentation

Thank you for your interest in improving our documentation! This guide will help you get started.

## Quick Links

- [Main Documentation](../README.md)
- [Documentation Protocol](../DOCUMENTATION_PROTOCOL.md)
- [API Reference](INDEX.md)
- [Quick Reference](QUICKREF/INDEX.md)

## How to Contribute

### 1. Report Issues

Found a typo, broken link, or unclear explanation? Please report it!

**Before reporting:**
- [ ] Check if the issue has already been reported
- [ ] Verify the issue exists in the latest version

**When reporting, include:**
- Document name and section
- Description of the issue
- Suggested fix (if you have one)

### 2. Make Changes

**Steps to update documentation:**

1. Fork the repository
2. Create a feature branch:
   ```bash
   git checkout -b docs/your-feature
   ```
3. Make your changes
4. Validate your changes:
   ```bash
   python scripts/validate_docs.py .
   ```
5. Commit your changes:
   ```bash
   git commit -m "docs: Describe your change"
   ```
6. Push to your fork:
   ```bash
   git push origin docs/your-feature
   ```
7. Create a Pull Request

### 3. Style Guide

All documentation must follow the [Documentation Protocol](../DOCUMENTATION_PROTOCOL.md):

#### Required Sections

```markdown
---
title: Document Title
version: 1.0.0
last_updated: YYYY-MM-DD
status: [DRAFT | REVIEW | APPROVED]
authors: Your Name
---

# Document Title

## Overview

Brief description of what this document covers.

## Usage

How to use the documented feature.

## Examples

Code examples with explanations.

### Example 1: Basic Usage

```python
# Your code here
```

## Related Documentation

- [Related Doc](link.md)
- [Another Doc](link2.md)
```

#### Naming Conventions

| Document Type | Naming Pattern | Example |
|---------------|----------------|---------|
| API Reference | `<module>_API.md` | `harness_API.md` |
| Quick Reference | `<module>_QUICKREF.md` | `commands_QUICKREF.md` |
| Tutorial | `<topic>_TUTORIAL.md` | `quickstart_TUTORIAL.md` |
| Index | `INDEX.md` | `core/INDEX.md` |

#### Link Format

**Internal links:**
```markdown
[Module Name](docs/module/INDEX.md)
[Function](docs/module/file_API.md#function-name)
```

**External links:**
```markdown
[Python Docs](https://docs.python.org/3/)
```

**Code references:**
```markdown
[`function_name()`](#function-name)
[`ClassName`](#classname)
```

### 4. Code Examples

All code examples should:
- Be tested and run without errors
- Include comments explaining key steps
- Have output expectations where applicable
- Be consistent with project style

```python
# Example format
def example_function():
    """Brief description."""
    # Setup
    result = do_something()
    
    # Verify
    assert result is not None
    return result
```

### 5. Review Process

All documentation changes go through:

1. **Automated Checks:**
   - YAML header validation
   - Link verification
   - Code example validation
   - Naming convention check

2. **Peer Review:**
   - Technical accuracy review
   - Clarity and completeness review
   - Style compliance review

3. **Approval:**
   - Status changes from DRAFT → REVIEW → APPROVED

## Areas Needing Help

| Area | Description | Priority |
|------|-------------|----------|
| API Documentation | Generate docs for remaining modules | High |
| Tutorials | Create step-by-step guides | High |
| Troubleshooting | Expand troubleshooting guides | Medium |
| Examples | Add more code examples | Medium |
| Translation | Translate to other languages | Low |

## Documentation Team

| Role | Responsibilities |
|------|-------------------|
| Maintainers | Review and approve changes |
| Contributors | Create and update documentation |
| Reviewers | Provide feedback on changes |

## Questions?

- Open an [Issue](https://github.com/Kilo-Org/Azazael/issues)
- Check [Quick Reference](docs/QUICKREF/COMMANDS_QUICKREF.md)

---

## Contributors

| Name | Contributions | Date |
|------|---------------|------|
| Azazael System | Initial documentation framework | 2026-02-10 |
