#!/usr/bin/env python3
# coding: utf-8
"""SILENT-FAILURE CENSUS -- where does a failure leave no signal at all?

Why this exists. The 2026-09-21 incident was not a broken gate: the spine gate
refused to write, correctly, and every caller reported success anyway. The class
of defect is broader than that one logging switch -- a failure that produces no
signal is invisible until something downstream starves. `except ...: pass` is
that class in its most common form, and this tree has a lot of them (much of it
deliberate best-effort). A check that fails on facts gets switched off, so this
tool is a CENSUS, not a gate:

    python tools/silent_failure_audit.py                # ranked report
    python tools/silent_failure_audit.py --json         # machine-readable
    python tools/silent_failure_audit.py --check        # strictest signal only

`--check` fails on exactly one thing: a BARE `except:` (no exception type) whose
body is a lone `pass`. A bare except swallows KeyboardInterrupt/SystemExit too
and is never the right default; the tree currently has none, so this is a
tripwire that stays green and catches a NEW one, rather than a nightly red.

It also flags handlers that swallow a WRITE-like call (open(...).write, save,
record, dump, ...) -- the shape where lost signal means lost data, as when the
berks monitor's ledger writes failed silently. Those are reported, never failed
on: legitimate best-effort cache writes are in the same bucket by inspection
alone, and only a human can tell them apart.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ROOTS = ("src", "scripts", "tools")

SKIP_DIRS = {
    "__pycache__", ".mypy_cache", ".git", ".venv-3.14t", ".venv",
    "node_modules", ".pytest_cache",
}

#: Call names whose failure usually means lost data or lost training signal.
WRITEY = {
    "write", "writelines", "write_text", "write_bytes", "save", "save_spine_state",
    "record", "record_outcome", "persist", "append", "dump", "flush",
}


def _iter_py(roots):
    for root in roots:
        base = ROOT / root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in sorted(filenames):
                if name.endswith(".py"):
                    yield Path(dirpath) / name


def _call_names(nodes) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute):
                    names.add(func.attr)
                elif isinstance(func, ast.Name):
                    names.add(func.id)
    return names


def _silent_kind(handler: ast.ExceptHandler) -> str | None:
    """'bare' | 'Exception' | 'specific' when the handler just swallows; else None.

    A handler counts as silent when its body reduces to a lone `pass` (a
    docstring is not a signal).
    """
    body = [n for n in handler.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str))]
    if len(body) != 1 or not isinstance(body[0], ast.Pass):
        return None
    exc = handler.type
    if exc is None:
        return "bare"
    if isinstance(exc, ast.Name) and exc.id == "Exception":
        return "Exception"
    return "specific"


def scan(roots=DEFAULT_ROOTS) -> dict:
    findings: list[dict] = []
    for path in _iter_py(roots):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"),
                             filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            writey = sorted(_call_names(node.body) & WRITEY)
            for handler in node.handlers:
                kind = _silent_kind(handler)
                if kind is None:
                    continue
                findings.append({
                    "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "line": handler.lineno,
                    "kind": kind,
                    "swallows_write": writey,
                })
    by_kind: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for f in findings:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
        by_file[f["path"]] = by_file.get(f["path"], 0) + 1
    return {
        "total": len(findings),
        "by_kind": by_kind,
        "write_swallowing": sum(1 for f in findings if f["swallows_write"]),
        "top_files": sorted(by_file.items(), key=lambda kv: -kv[1])[:15],
        "findings": findings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if a BARE `except:` (no type) swallows to pass")
    args = ap.parse_args(argv)

    res = scan()
    if args.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"pass-only handlers: {res['total']}")
        for kind in ("bare", "Exception", "specific"):
            if res["by_kind"].get(kind):
                print(f"  {kind:9s} {res['by_kind'][kind]}")
        print(f"swallowing a write-like call: {res['write_swallowing']}")
        print("top files:")
        for path, n in res["top_files"]:
            print(f"  {n:5d}  {path}")

    bare = [f for f in res["findings"] if f["kind"] == "bare"]
    if args.check and bare:
        print(f"FAIL: {len(bare)} bare `except:` handler(s) swallow silently")
        for f in bare[:10]:
            print(f"  {f['path']}:{f['line']}")
        return 1
    if args.check:
        print("OK: no bare `except:` handler swallows silently")
    return 0


if __name__ == "__main__":
    sys.exit(main())
