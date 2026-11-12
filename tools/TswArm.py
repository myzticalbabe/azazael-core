#!/usr/bin/env python3
# coding: utf-8
"""TswArm — the tree-swarming tool: one entry over both worker swarms.

Why this exists
---------------
2026-11-04: two free-threaded worker swarms were built to clean the dirty
azazael worktree and each passed its own staged-scanner gate independently:

- `swarm_clean.py` — CENSUS: expands porcelain's collapsed untracked dirs into
  individual files and triages every dirty path under 32 threads, writing
  per-file verdicts to state/swarm_clean.jsonl. Read-only on git.
- `swarm_fix.py`  — FIXER: re-derives each file's positive unicode_emoji /
  hardcoded_win_path delta vs HEAD, appends the scanner's documented per-line
  `# scanner:ignore` opt-out with an audit reason, validates `ast.parse`
  before writing, auto-reverts any file the transform would break.

TswArm is the single named face for both, so a lane that asks for "the swarm"
gets one command: `census`, `fix`, or `all` (census first, then fix — clean
counters tell you what the fixer is about to touch).

Run under the 3.15t free-threaded interpreter for real parallel threads:
    C:/Azazael/.venvs/py315t/Scripts/python.exe tools/TswArm.py all
    ... tools/TswArm.py census --save OUT.jsonl
    ... tools/TswArm.py fix --file PATH
"""
from __future__ import annotations

import importlib
import sys

TOOLS = __import__("pathlib").Path(__file__).resolve().parent


def _delegate(module_name: str, argv: list[str]) -> int:
    """Run a sibling swarm tool's main() with an injected argv.

    Separate sys.argv swap instead of a subprocess: the interpreter choice
    (3.15t) must carry into the delegate, and a child could pick up a
    different `python` from PATH.
    """
    module = importlib.import_module(module_name)
    old = sys.argv
    sys.argv = [f"TswArm {module_name}"] + argv
    try:
        return int(module.main())
    finally:
        sys.argv = old


def main() -> int:
    sys.path.insert(0, str(TOOLS))
    usage = ("usage: TswArm.py {census|fix|all} [census: --max-workers N "
             "--save P] [fix: --file PATH | --report]")
    if not sys.argv or len(sys.argv) < 2:
        print(usage)
        return 2
    cmd, rest = sys.argv[1], sys.argv[2:]

    gil_free = getattr(sys, "_is_gil_enabled", lambda: True)() is False
    if not gil_free:
        print("TswArm [warn] interpreter has the GIL; run under "
              ".venvs/py315t/Scripts/python.exe for real parallel threads")

    if cmd == "census":
        return _delegate("swarm_clean", rest)
    if cmd == "fix":
        return _delegate("swarm_fix", rest)
    if cmd == "all":
        rc = _delegate("swarm_clean", [])
        if rc != 0:
            return rc
        return _delegate("swarm_fix", [])
    print(usage)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
