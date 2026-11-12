# Compliance Pipeline — Startup Guide

## Overview
The compliance pipeline provides automated AST-based anti-pattern scanning for Python code. It detects 7 anti-pattern categories and generates reports in text, JSON, or HTML format.

## Quick Start

```bash
# Scan a directory and print text report
python src/CORE/compliance/scanner.py --report --dir src/CORE

# JSON output for CI/machine processing
python src/CORE/compliance/scanner.py --report --dir src/CORE --format json

# HTML compliance report
python src/CORE/compliance/scanner.py --report --dir src/CORE --format html --output-dir /tmp

# CI mode — exit code 1 if findings exist
python src/CORE/compliance/scanner.py --report --dir src/CORE --ci
```

## CLI Flags

| Flag | Description |
|------|-------------|
| `--report` | Scan all .py files and print report |
| `--dir DIR` | Directory to scan (default: current) |
| `--output-dir DIR` | Write JSON/HTML output to directory |
| `--format {text,json,html}` | Output format |
| `--ci` | Exit non-zero if findings exist |
| `--exclude-dirs LIST` | Comma-separated dir names to skip |
| `--watch` | Watch mode — re-scan on file changes |
| `--watch-delay SECONDS` | Debounce delay (default: 1.0) |

## Watch Mode

```bash
python src/CORE/compliance/scanner.py --report --dir src/CORE --watch --watch-delay 2.0 --format json
```
Press Ctrl+C to stop.

## CI Integration

```bash
# In CI pipeline, use --ci to fail on findings:
python src/CORE/compliance/scanner.py --report --dir src --ci --format json
```

## Telemetry

Export PreActionGate telemetry:
```bash
python src/SCRIPTS/generate_telemetry.py
```

## Excluding Directories

```bash
# Skip __pycache__ and .git
python scanner.py --report --dir src --exclude-dirs __pycache__,.git
```

## Writing Unit Tests

Import the scanner in tests:
```python
from CORE.compliance.scanner import ASTAntiPatternScanner
scanner = ASTAntiPatternScanner()
result = scanner.scan_source("file.py", source_code)
assert result.passed, f"Findings: {result.findings}"
```

See `tests/test_scanner.py` for examples.
