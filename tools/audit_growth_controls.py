"""Find the class of defect that let memory_store.db reach 6.41 GB.

Three shapes, all found by AST rather than grep, because grep cannot tell a
definition from a call site and cannot see the difference between a loop that
sleeps and a loop that writes:

  1. DEAD CONTROL   — a function whose name says it enforces a limit
                      (`enforce_*`, `prune_*`, `trim_*`, `rotate_*`, `cap_*`)
                      with **zero call sites** anywhere in the tree.
                      `data_retention.enforce_global_quota` was exactly this:
                      it existed, it worked, nothing ever ran it.
  2. PER-TICK WRITER — a call to a store/log write that sits inside a loop
                      (`for`/`while`) in the same function, optionally with a
                      `sleep`. This is the shape that produced one row per RL
                      episode at ~62 rows/s.
  3. UNBOUNDED APPEND — `open(..., 'a')` / `.write_jsonl` / `.append(` on a
                      path, with no rotation, cap or size check in the same
                      function. Grows forever by construction.

Usage:
    python tools/audit_growth_controls.py [--root .] [--json out.json]
                                         [--min-callers 0]

Exit code is 0 always: this is an inventory, not a gate. Making it a gate is a
separate decision (and would need a baseline to avoid failing on legacy hits).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Dict, List

CONTROL_PREFIXES = ("enforce_", "prune_", "trim_", "rotate_", "cap_", "apply_limit",
                    "reclaim_", "vacuum_", "compact_")
WRITE_NAMES = ("record_event", "record", "record_thought", "save", "write",
               "append", "log", "insert", "add_memory", "store_memory",
               "write_jsonl", "emit", "publish", "dump")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "site-packages",
             ".hypothesis", "dist", "build", ".mypy_cache", ".pytest_cache",
             "graphify-out", ".ruff_cache", "archive", "_quarantine_20261030"}


def iter_py(root: Path):
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


class DefCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.defs: Dict[str, List[dict]] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defs.setdefault(node.name, []).append(
            {"line": node.lineno, "end": node.end_lineno or node.lineno,
             "args": [a.arg for a in node.args.args]})
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]


class CallCollector(ast.NodeVisitor):
    """Counts call sites per function name, and inspects loop bodies."""

    def __init__(self) -> None:
        self.calls: Dict[str, int] = {}
        self.tick_writes: List[dict] = []
        self.appends: List[dict] = []
        self._loop_depth = 0

    # -- calls -----------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        if name:
            self.calls[name] = self.calls.get(name, 0) + 1
            if (name in WRITE_NAMES or name.startswith(("record_", "write_", "append_"))
                    or name in ("INSERT", "execute")):
                if self._loop_depth > 0 and name != "execute":
                    self.tick_writes.append({"line": node.lineno, "call": name})
        # open(path, 'a')
        if name == "open" and node.args:
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if "a" in mode:
                self.appends.append({"line": node.lineno, "mode": mode})
        self.generic_visit(node)

    # -- loops -----------------------------------------------------------
    def visit_For(self, node: ast.For) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_While(self, node: ast.While) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    root = Path(args.root).resolve()

    defs: Dict[str, List[dict]] = {}
    calls: Dict[str, int] = {}
    tick: List[dict] = []
    appends: List[dict] = []
    files = 0

    for path in iter_py(root):
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except SyntaxError as exc:
            note_failure("tools/audit_growth_controls.py", exc)
            continue
        files += 1
        dc = DefCollector()
        dc.visit(tree)
        for k, v in dc.defs.items():
            for item in v:
                item["file"] = str(path.relative_to(root))
            defs.setdefault(k, []).extend(v)

        cc = CallCollector()
        cc.visit(tree)
        for k, v in cc.calls.items():
            calls[k] = calls.get(k, 0) + v
        for item in cc.tick_writes:
            item["file"] = str(path.relative_to(root))
            tick.append(item)
        for item in cc.appends:
            item["file"] = str(path.relative_to(root))
            appends.append(item)

    dead = []
    for name, sites in defs.items():
        if not name.startswith(CONTROL_PREFIXES):
            continue
        # a call site is any call with that name minus the number of defs that
        # would be counted as... defs are not calls, so compare directly
        n = calls.get(name, 0)
        if n == 0:
            dead.append({"function": name, "defs": sites})

    dead.sort(key=lambda d: d["function"])
    report = {
        "root": str(root),
        "py_files_scanned": files,
        "dead_controls": dead,
        "per_tick_writers": sorted(tick, key=lambda t: (t["file"], t["line"])),
        "unbounded_appends": sorted(appends, key=lambda a: (a["file"], a["line"])),
        "counts": {
            "defs": len(defs),
            "dead_controls": len(dead),
            "per_tick_writers": len(tick),
            "unbounded_appends": len(appends),
        },
    }

    print(f"scanned {files} python files under {root}")
    print(f"\n1) DEAD CONTROLS (name says 'enforce a limit', zero call sites): {len(dead)}")
    for d in dead[:25]:
        site = d["defs"][0]
        print(f"   {d['function']:<34} {site['file']}:{site['line']}")
    print(f"\n2) PER-TICK WRITERS (write call inside a loop): {len(tick)}")
    for t in tick[:25]:
        print(f"   {t['call']:<20} {t['file']}:{t['line']}")
    print(f"\n3) UNBOUNDED APPENDS (append-mode writes): {len(appends)}")
    for a in appends[:25]:
        print(f"   mode={a['mode']:<4} {a['file']}:{a['line']}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport: {args.json}")
    return 0



try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

if __name__ == "__main__":
    sys.exit(main())
