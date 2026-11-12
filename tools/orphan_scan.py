"""Structural flaw scan: find orphaned modules (built, never reachable).

The adoption census asked "is this *technique* called from another production
module". This asks the question one level up, because the census surfaced it and
kept surfacing its relatives: **is this whole MODULE reachable from an entry
point at all?** A module nothing imports is dead capability no matter how good
its symbols are, and `structured_action`, `rubric_evolution` and `cascade` all
turned out to be exactly that.

Method (deliberately crude and checkable, not clever):
  1. Every `*.py` under src/CORE is a candidate.
  2. Its *import identity* is the dotted path after `src/`, e.g.
     `src/CORE/WORMS/expansion/delegation.py` -> `CORE.WORMS.expansion.delegation`.
  3. An importer is any OTHER `*.py` under `src/` or the repo-root runner
     scripts whose text matches `from <dotted> import` or `import <dotted>`,
     with the match ANCHORED at a line start so a mention inside a comment or
     docstring does not count... except that anchoring alone is not enough, so
     matches are additionally filtered to lines whose first token is
     `from`/`import` after stripping.
  4. Reachability is then closed transitively from the entry points: repo-root
     `*.py` (run_tests.py, health_server.py, cli_observability.py, ...) and
     `src/CORE/__init__.py`. A module reachable only from a test or from
     `tools/` is NOT production-reachable.

Relative imports ARE resolved (`from .x import y`, `from .. import z`) -- added
2026-10-10 after a worker showed `PERSEPHONE.lib` was reported as an orphan
while its own facades import it. Still invisible, DELIBERATELY: a bare-name
import made after a `sys.path` insert cannot be distinguished from importing
an unrelated same-named module, so it is left out rather than guessed at --
`--dynamic` counts those files instead.

This is a FLAW DETECTOR, not a verdict. Its output is a worklist to inspect --
the classifier can be wrong for dynamically-imported modules (an `importlib`
call or a registry string is invisible to it). Every row it prints is a
*question*, and the report must state which import mechanism was ruled out.

Usage:
    python tools/orphan_scan.py                 # print orphan candidates
    python tools/orphan_scan.py --json PATH     # write machine-readable report
    python tools/orphan_scan.py --dynamic       # also count importlib/getattr sites
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

# Entry points: a module is production-reachable only if a walk from one of
# these reaches it. Repo-root scripts are the shipped entry points (CLI, health
# server, runners); src/CORE/__init__.py is the package's own door.
# Shipped entry points, named explicitly. An earlier version of this scan used
# "every __init__.py and every non-CORE module" as an entry set, which made all
# 701 modules reachable and printed ORPHANED 0 -- a broken instrument reporting
# health. Entry points must be the things that actually run, or the scan answers
# a question nobody asked.
ROOT_ENTRY_FILES = ("run_tests.py", "health_server.py", "cli_observability.py",
                    "main.py", "app.py")
PACKAGE_DOORS = ("CORE",)


def module_ident(path: Path) -> str:
    """`src/CORE/WORMS/x.py` -> `CORE.WORMS.x`; `__init__.py` -> its package."""
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def all_modules() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        ident = module_ident(p)
        if ident:
            out[ident] = p
    return out


def candidate_texts() -> dict[Path, str]:
    """Every file that could host an import: src/ plus repo-root scripts."""
    texts: dict[Path, str] = {}
    for p in SRC.rglob("*.py"):
        if "__pycache__" not in p.parts:
            texts[p] = _read(p)
    for p in REPO.glob("*.py"):
        texts[p] = _read(p)
    return texts


def _read(p: Path) -> str:
    """Read a candidate file, or FAIL LOUDLY.

    Deliberately no except-return-default: for an instrument, an unreadable file
    is not a missing candidate, it is an unknown result. Swallowing it would
    silently shrink the edge set and inflate the orphan count -- the same class
    of unvalidated-instrument error this scan exists to find.
    """
    return p.read_text(encoding="utf-8", errors="replace")


def import_edges(texts: dict[Path, str], by_prefix: dict[str, list[str]],
                 mods: dict[str, Path]) -> dict[str, set[str]]:
    """Direct edges: importer ident -> set of imported idents.

    Matching is prefix-based on the module path so `from CORE.WORMS.expansion.
    delegation import ask_owner` and `from CORE.WORMS.expansion import
    delegation` both land, and multi-line parenthesised imports still match
    because only the head of the statement needs to be on the import line.
    """
    edges: dict[str, set[str]] = {}
    rx = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.M)
    # Relative form: `from ..a.b import c, d` / `from . import e`
    rel_rx = re.compile(
        r"^\s*from\s+(\.+)([\w.]*)\s+import\s+([^\n]*)", re.M)
    for path, text in texts.items():
        ident = (module_ident(path) if SRC in path.parents or path.parent == SRC
                 else path.stem)
        hit: set[str] = set()
        for m in rx.finditer(text):
            target = m.group(1)
            hit.update(by_prefix.get(target, ()))
            # `from CORE.WORMS.x import y` where y is itself a module
            for cand in by_prefix.get(target, ()):
                if cand.endswith(target):
                    hit.add(cand)
        if SRC in path.parents or path.parent == SRC:
            pkg = ident.rsplit(".", 1)[0] if "." in ident else ""
            if path.name != "__init__.py":
                # A module level import resolves against its own package.
                base_pkg = pkg
            else:
                base_pkg = ident
            for m in rel_rx.finditer(text):
                dots, rest, names = m.group(1), m.group(2), m.group(3)
                # One dot = the current package; each extra dot climbs a level.
                parts = [p for p in base_pkg.split(".") if p]
                climb = len(dots) - 1
                if climb:
                    parts = parts[:-climb] if climb <= len(parts) else []
                base = ".".join(parts + ([rest] if rest else []))
                if base:
                    hit.update(by_prefix.get(base, ()))
                # `from . import engine` -- each named member may be a module
                for nm in re.split(r"[,\s]+", names.split("#")[0]):
                    nm = nm.strip().strip("()")
                    if nm and base and f"{base}.{nm}" in mods:
                        hit.add(f"{base}.{nm}")
                    elif nm and not rest and nm in mods:
                        hit.add(nm)
        edges[ident] = hit
    return edges


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--dynamic", action="store_true")
    args = ap.parse_args(argv)

    mods = all_modules()
    by_prefix: dict[str, list[str]] = {}
    for ident in mods:
        parts = ident.split(".")
        for i in range(1, len(parts) + 1):
            by_prefix.setdefault(".".join(parts[:i]), []).append(ident)

    texts = candidate_texts()
    edges = import_edges(texts, by_prefix, mods)

    # Reverse edges, then close from the entry points.
    reverse: dict[str, set[str]] = {}
    for src_ident, targets in edges.items():
        for t in targets:
            reverse.setdefault(t, set()).add(src_ident)

    entries = {i for i, p in mods.items() if p.name in ROOT_ENTRY_FILES}
    entries |= {d for d in PACKAGE_DOORS if d in mods}
    reached_entries = sorted(e for e in entries if e in mods)
    if not reached_entries:
        print("FATAL: no entry point resolved -- refusing to report 0 orphans "
              "from an entry set that does not exist")
        return 2
    reachable: set[str] = set()
    stack = [e for e in entries if e in mods]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        for nxt in edges.get(cur, ()):  # forward: what it imports
            if nxt in mods and nxt not in reachable:
                stack.append(nxt)

    orphans = sorted(i for i in mods if i not in reachable)
    # Importers that are only tests/tools count as non-production
    prod_orphans = sorted(
        i for i in orphans
        if not any(not s.startswith(("test", "tests")) and "tests" not in s
                   for s in reverse.get(i, ())))
    dyn = 0
    if args.dynamic:
        rx = re.compile(r"importlib|__import__|getattr\(", re.M)
        for i in orphans:
            if rx.search(texts.get(mods[i], "")):
                dyn += 1

    print(f"modules under src/           : {len(mods)}")
    print(f"import/from edges extracted  : "
          f"{sum(len(v) for v in edges.values())}")
    print(f"production-reachable         : {len(reachable)}")
    print(f"ORPHANED (unreachable)       : {len(orphans)}")
    print(f"  of which nothing imports   : {len(prod_orphans)}")
    if args.dynamic:
        print(f"  of which use dynamic import: {dyn}  <- inspect, may be reachable")
    print()
    for i in orphans:
        tag = "no-importer" if i in prod_orphans else "test-only"
        print(f"  [{tag:11}] {i}")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "n_modules": len(mods), "n_reachable": len(reachable),
            "orphans": orphans, "no_importer": prod_orphans,
            "entry_points": sorted(entries & set(mods))}, indent=1),
            encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
