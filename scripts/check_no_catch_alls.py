#!/usr/bin/env python3
# coding: utf-8
"""catch-all regression guard for the Azazael codebase.

Flags new **bare excepts** (``except:``) and **over-broad catch-alls**
(``except Exception:`` / ``except BaseException:``) so the cleanup effort can
never regress.

Two scopes:

1. **Dir budget mode** (CI): walks the production dirs, AST-parses every
   ``.py``, and compares counts against a fixed baseline budget. A bare except
   always fails (MAX_BARE, default 0). Broad handlers are budgeted with
   MAX_BROAD (default 1242 = the current live src/+scripts/ count). The guard
   fails whenever the counted broad handlers exceed the budget, i.e. the count
   grows. Lower ``--max-broad`` as refactoring shrinks the surface.

2. **Files mode** (local pre-commit): check an explicit list of files. Bare
   excepts fail; broad handlers are reported (warning) unless ``--strict``.

Handlers marked with an explicit allow comment are exempt from the broad count
so genuinely-needed boundaries can be documented instead of silently allowed:
a comment containing ``# noqa: BLE001``, ``# allow-broad``, ``# catch-all``,
or ``# broad`` on the except line or its first body line.

USAGE:
    python scripts/check_no_catch_alls.py                  # dir mode (src scripts)
    python scripts/check_no_catch_alls.py --dirs src       # one dir
    python scripts/check_no_catch_alls.py --files a.py b.py
    python scripts/check_no_catch_alls.py --json           # machine-readable
    python scripts/check_no_catch_alls.py --max-broad 0    # enforce true zero
"""

import ast
import os
import re
import sys
import warnings

DEFAULT_DIRS = ("src", "scripts")
DEFAULT_MAX_BARE = 0
DEFAULT_MAX_BROAD = 1241  # current live counts; lower as refactoring proceeds

# Scanned files may contain unrelated invalid-escape / deprecation warnings;
# suppress them so the guard's own output stays focused.
warnings.filterwarnings("ignore", category=SyntaxWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Files/dirs that are vendored, generated, or backup copies — never counted.
EXCL_EXT = (".pre_gate_backup", ".py.pre_gate_backup")
SKIP_DIRF = {"__pycache__", ".venv", "BIBL", "bibl"}

# Exemption markers recognized on the except line or its first body line.
ALLOW_RE = re.compile(r"#\s*(noqa[:\s]+BLE001|allow-broad|catch-all|broad)")

# Toplevel handler names we treat as over-broad.
BROAD_NAMES = ("Exception", "BaseException")


def _iter_py_files(dirs):
    """Yield .py file paths under the given dirs (skipping vendored/generated)."""
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, subdirs, files in os.walk(d):
            subdirs[:] = [x for x in subdirs if x not in SKIP_DIRF]
            for f in files:
                if not f.endswith(".py"):
                    continue
                if f.endswith(EXCL_EXT):
                    continue
                yield os.path.join(root, f)


def _source_lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _is_allowed(lines, lineno):
    """True if the handler at lineno carries an explicit allow marker."""
    if not lines:
        return False
    # First body line follows the except-header indentation; use lineno (index+1).
    for idx in (lineno - 1, lineno):  # header line and the first body line
        if 0 <= idx < len(lines) and ALLOW_RE.search(lines[idx]):
            return True
    return False


def scan_file(path, bare, broad):
    """Populate bare/broad finding lists (each an (path, lineno) tuple)."""
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
    except SyntaxError:
        return
    except OSError:
        return
    lines = _source_lines(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        t = node.type
        if t is None:
            bare.append((path, node.lineno))
        elif isinstance(t, ast.Name) and t.id in BROAD_NAMES:
            if not _is_allowed(lines, node.lineno):
                broad.append((path, node.lineno))


def _fmt(findings):
    return [f"{p}:{ln}" for p, ln in findings]


def main(argv):
    dirs = list(DEFAULT_DIRS)
    files = None
    max_bare = DEFAULT_MAX_BARE
    max_broad = DEFAULT_MAX_BROAD
    as_json = False
    strict_files = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dirs":
            i += 1
            dirs = [x for x in argv[i].split()] if i < len(argv) else []
        elif a == "--files":
            i += 1
            files = []
            while i < len(argv) and not argv[i].startswith("--"):
                files.append(argv[i])
                i += 1
            i -= 1
        elif a == "--max-bare":
            i += 1
            max_bare = int(argv[i])
        elif a == "--max-broad":
            i += 1
            max_broad = int(argv[i])
        elif a == "--strict":
            strict_files = True
        elif a == "--json":
            as_json = True
        else:
            print(f"error: unknown option {a}", file=sys.stderr)
            return 2
        i += 1

    if files is None:
        paths = list(_iter_py_files(dirs))
    else:
        paths = [p for p in files if os.path.isfile(p)]

    bare, broad = [], []
    for p in paths:
        scan_file(p, bare, broad)

    bare_cnt = len(bare)
    broad_cnt = len(broad)

    # In files mode, only bare excepts fail unless --strict.
    if files is not None:
        bare_ok = bare_cnt <= max_bare
        broad_ok = (broad_cnt == 0) if strict_files else True
    else:
        bare_ok = bare_cnt <= max_bare
        broad_ok = broad_cnt <= max_broad

    ok = bare_ok and broad_ok

    if as_json:
        import json
        print(json.dumps({
            "scanned_files": len(paths),
            "bare": bare_cnt,
            "broad": broad_cnt,
            "max_bare": max_bare,
            "max_broad": max_broad,
            "bare_findings": _fmt(bare),
            "broad_findings": _fmt(broad),
            "ok": ok,
        }, indent=2))
        return 0 if ok else 1

    print(f"Catch-all scan: {len(paths)} files, {bare_cnt} bare, {broad_cnt} broad")
    if bare:
        print("  FAIL — bare except (never allowed):")
        for loc in _fmt(bare):
            print(f"    {loc}")
    elif files is None and not bare_ok:
        print(f"  FAIL — bare except count {bare_cnt} exceeds max {max_bare}")
    if broad and not broad_ok:
        limit = 0 if files is not None else max_broad
        print(f"  FAIL — broad except count {broad_cnt} exceeds limit {limit}:")
        for loc in _fmt(broad):
            print(f"    {loc}")
    if ok:
        print("  OK - within budget")
    else:
        hint = " (files mode)" if files is not None and strict_files else ""
        print("  Add explicit '# allow-broad' marker if a handler is intentional%s." % hint)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))