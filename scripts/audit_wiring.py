#!/usr/bin/env python3
"""audit_wiring.py — fail when an orchestration entry point has no caller.

WHY THIS EXISTS
---------------
Four separate findings in this repo share one shape:

    benchmark_evolution.py   complete, CI-ready, invoked by nothing
    start_scheduler()        a "daemon thread" entry point with no caller
    MORPHING_WORM            registerable, but nothing registered/ran it
    evolution_audit          built, then never scheduled

Each was individually correct and collectively dead. So the useful question
stopped being "does the subsystem work?" and became "**is the subsystem
called?**". This tool answers that mechanically.

WHAT IT CHECKS
--------------
A function whose NAME declares orchestration intent is expected to be invoked
somewhere. It is reported DEAD when it is never reached:

  * nothing in its own file calls it (the identifier appears only on the `def`
    line), AND
  * for a name that is NOT defined in several files, no other file mentions it
    either.

The name test is split that way because `main` is defined in dozens of files.
A naive "is this name mentioned anywhere else?" rule is then always true for
`main`, so a `main()` with no `__main__` guard -- a script that can never run --
was invisible. Locality decides for common names; cross-file mentions decide
only for names that are unique to one file.

Name verbs are DECLARED rather than guessed, because a guessed rule is a rule
that cries wolf.

SCOPE: AN INCLUDE LIST, NOT AN EXCLUDE LIST
-------------------------------------------
This tree holds ~3,900 .py files that are not ours -- archived/ (2820, three
vendored copies of onnxruntime), build/ (442), nuitka_build_backup/ (379),
archive/ (229), archived_unified/ (210). Findings in vendored code are noise,
and noise is what gets an audit switched off, so the tool names what we own
(src/ scripts/ tests/ tools/ plus root-level scripts) and ignores the rest.

The one exclusion that matters is `build/lib/`: a stale June-17 copy of `src/`
(442 files vs 615) that still carries the pre-fix MemoryBus bug
(`self._stats['subscriptions']` at MemoryBus.py:381) which `src/` no longer
has. Counting it would mark a dead identifier as live and describe a tree that
no longer exists.

Deliberate bias: PRECISION over recall. A name mentioned in a comment counts as
a reference and masks a real finding. That is the correct direction -- an audit
that cries wolf gets switched off, which is worse than one that misses a case.

USAGE
-----
    python scripts/audit_wiring.py                 # report, exit 1 if findings
    python scripts/audit_wiring.py --json          # machine-readable
    python scripts/audit_wiring.py --self-test     # validate the instrument
    python scripts/audit_wiring.py --root DIR      # audit another tree

An accepted-and-intentional entry point goes in scripts/audit_wiring_allow.txt,
one name per line (``#`` comments allowed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = Path(__file__).resolve().parent / "audit_wiring_allow.txt"

# Declared, not guessed. Functions named `<verb>_...` are entry points by
# convention in this codebase; `main` is a script's entry point.
VERBS = (
    "start", "launch", "schedule", "bootstrap", "serve", "orchestrate",
    "cycle", "spawn",
)
ENTRY_RE = re.compile(r"^(?:" + "|".join(VERBS) + r")_[a-z]")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DEF_RE = re.compile(r"^(?P<indent>[ \t]*)(?:async[ \t]+)?def[ \t]+(?P<name>[A-Za-z_]\w*)")

# The project's own code. Naming what we own beats chasing what we do not.
INCLUDE_DIRS = {"src", "scripts", "tests", "tools"}

# Generated / duplicate / vendored trees, filtered even inside an included dir.
SKIP_DIRS = {
    "build", "__pycache__", "node_modules", ".git", ".venv", "venv",
    "venv-314t", "venv-310", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "site-packages", "dist", ".tox", ".eggs", "htmlcov", ".agents",
}
SKIP_FILE_RE = re.compile(r"^(conftest|setup)\.py$")


def _iter_py_files(root: Path):
    """In-scope .py files: our own code, plus scripts sitting in the root.

    A file directly in root counts (run_tests.py, METACLUSTER.py, ...). Any
deeper file must sit under a dir named in INCLUDE_DIRS. A tree that has no
src/ or scripts/ (the self-test fixture) is walked whole, minus SKIP_DIRS.
    """
    scoped = (root / "src").is_dir() or (root / "scripts").is_dir()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.endswith(".egg-info")
        ]
        rel_parts = Path(dirpath).relative_to(root).parts
        if scoped and rel_parts and rel_parts[0] not in INCLUDE_DIRS:
            dirnames[:] = []          # prune: nothing below is ours, stop walking
            continue
        for fn in filenames:
            if fn.endswith(".py") and not SKIP_FILE_RE.match(fn):
                yield Path(dirpath) / fn


def _scan(root: Path):
    """One pass over the tree.

    Returns (texts, candidates, def_files):
      texts      rel_path -> file text
      candidates [(name, rel_path)]  entry points, tests excluded
      def_files  name -> {rel_paths that define it at module level}
    """
    texts: dict[str, str] = {}
    candidates: list[tuple[str, str]] = []
    def_files: dict[str, set[str]] = {}

    for path in _iter_py_files(root):
        rel = str(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        texts[rel] = text
        in_tests = "tests" in path.parts
        for line in text.splitlines():
            m = DEF_RE.match(line)
            if not m or m.group("indent"):
                continue                      # nested defs are helpers, not entry points
            name = m.group("name")
            def_files.setdefault(name, set()).add(rel)
            if in_tests or name.startswith("_"):
                continue
            if name == "main" or ENTRY_RE.match(name):
                candidates.append((name, rel))
    return texts, candidates, def_files


def audit(root: Path) -> dict:
    """Return the wiring report for a tree."""
    root = root.resolve()
    texts, candidates, def_files = _scan(root)
    allowed = _load_allowlist(ALLOWLIST)

    findings, allowed_hits = [], []
    for name, rel in candidates:
        text = texts[rel]
        local_occ = len(re.findall(rf"\b{re.escape(name)}\b", text))
        # 1 occurrence == the `def` line and nothing else in this file.
        if local_occ > 1:
            continue
        defined_in = def_files.get(name, set())
        if len(defined_in) == 1:
            # Name unique to this file: any other mention is a real reference.
            if any(name in t for other, t in texts.items() if other != rel):
                continue
        rec = {
            "name": name,
            "file": _shown(path_str=rel, root=root),
            "occurrences": local_occ,
            "defined_in_files": len(defined_in),
        }
        (allowed_hits if name in allowed else findings).append(rec)

    return {
        "root": str(root),
        "scanned_files": len(texts),
        "candidates": len(candidates),
        "findings": sorted(findings, key=lambda r: (r["file"], r["name"])),
        "allowed": sorted(allowed_hits, key=lambda r: (r["file"], r["name"])),
    }


def _shown(path_str: str, root: Path) -> str:
    try:
        return str(Path(path_str).relative_to(root))
    except ValueError:
        return path_str


def _load_allowlist(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


# --------------------------------------------------------------------------
# Self-test. An instrument that has never fired on a known-bad input is not
# evidence of anything -- a previous audit of this repo reported a confident
# "0 findings" from a detector that could not detect the bug it was built for.
# --------------------------------------------------------------------------
_FIXTURE_DEAD = '''\
"""fixture: entry points nothing calls."""
import json


def start_forgotten_service(interval_hours: float = 24.0) -> None:
    """Complete, importable, invoked by nothing."""
    json.dumps(interval_hours)


def main() -> None:
    """No __main__ guard in this file, so this never runs."""
    json.dumps(2)
'''

_FIXTURE_CALLED = '''\
"""fixture: entry points that ARE wired."""
import json


def start_used_service() -> None:
    json.dumps(1)


def main() -> None:
    start_used_service()


if __name__ == "__main__":
    main()
'''

_FIXTURE_CALLER = '''\
"""fixture: another module reaching an entry point."""
from cross import start_cross_called


def go() -> None:
    start_cross_called()
'''

_FIXTURE_CROSS = '''\
"""fixture: reached only from another module."""


def start_cross_called() -> None:
    return None
'''


def self_test() -> int:
    """Validate the instrument against known-bad and known-good fixtures."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "dead.py").write_text(_FIXTURE_DEAD, encoding="utf-8")
        (root / "called.py").write_text(_FIXTURE_CALLED, encoding="utf-8")
        (root / "caller.py").write_text(_FIXTURE_CALLER, encoding="utf-8")
        (root / "cross.py").write_text(_FIXTURE_CROSS, encoding="utf-8")
        # A stale duplicate tree must contribute NOTHING.
        (root / "build" / "lib").mkdir(parents=True)
        (root / "build" / "lib" / "stale.py").write_text(
            "def start_from_stale_build():\n    pass\n", encoding="utf-8")

        result = audit(root)
        hits = {(r["name"], Path(r["file"]).name) for r in result["findings"]}

        checks = [
            ("fires on a forgotten entry point",
             ("start_forgotten_service", "dead.py") in hits),
            ("fires on a main with no __main__ guard",
             ("main", "dead.py") in hits),
            ("SILENT on a called entry point",
             ("start_used_service", "called.py") not in hits),
            ("SILENT on a main that IS guarded",
             ("main", "called.py") not in hits),
            ("SILENT on a cross-module call",
             ("start_cross_called", "cross.py") not in hits),
            ("IGNORES the stale build/ tree",
             not any(n == "start_from_stale_build" for n, _ in hits)),
        ]

    print("=== SELF-TEST: instrument validation ===")
    ok = True
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= passed
    print()
    print("  instrument validated: fires on known-bad, silent on known-good"
          if ok else "  INSTRUMENT INVALID -- its findings cannot be trusted")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--root", default=None, help="tree to audit (default: project root)")
    ap.add_argument("--self-test", action="store_true",
                    help="validate the instrument against fixtures and exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    result = audit(Path(args.root) if args.root else PROJECT_ROOT)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=== WIRING AUDIT: orchestration entry points with no caller ===")
        print(f"    tree      : {result['root']}")
        print(f"    files     : {result['scanned_files']}")
        print(f"    candidates: {result['candidates']} entry-point names examined")
        print()
        for r in result["findings"]:
            print(f"  DEAD  {r['name']:<36} {r['file']}")
        if not result["findings"]:
            print("  none — every entry point has a caller")
        if result["allowed"]:
            print()
            print(f"  allowlisted ({len(result['allowed'])}):")
            for r in result["allowed"]:
                print(f"    {r['name']:<34} {r['file']}")

    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
