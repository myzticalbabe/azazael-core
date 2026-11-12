#!/usr/bin/env python3
# coding: utf-8
"""Drop the requiem kernel into every codebase on this host, byte-identically.

`src/PSYCHE/requiem.py` is portable by construction: stdlib only, no package
imports, no assumptions about the tree it lands in. This tool is what makes that
portability real rather than claimed — it copies that one file to each codebase
root as `requiem.py`, makes sure `.requiem/` (the grave) is git-ignored there, and
can prove afterwards that every copy is still identical.

  python tools/requiem_propagate.py --discover          # what it would touch
  python tools/requiem_propagate.py --write             # copy + gitignore
  python tools/requiem_propagate.py --check             # drift gate, exit 1
  python tools/requiem_propagate.py --write --target C:/Azazael/azazael-forensics

Why a copy and not a package dependency: a codebase that has to install another
codebase to record its own crash will not record its own crash. The file has to
stand alone in the repository it protects — which is also why the copies must be
byte-identical and checked: sixteen independently edited requiems is sixteen
different accountings of what happened.

A target that already has a `requiem.py` with different contents is a CONFLICT,
never a silent overwrite: someone may have written their own on purpose. `--force`
says the canonical one wins and the reader was told why.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
CANONICAL = REPO / "src" / "PSYCHE" / "requiem.py"
#: The kernel is copied to the codebase root under this name.
FILENAME = "requiem.py"
#: The grave lives beside the code it protects, and is never committed: a crash
#: record can hold paths, arguments and hostnames, none of which belong in git.
GRAVE_ENTRY = ".requiem/"
#: Directories that are never a codebase root of their own.
SKIP_NAMES = {
    "build", "dist", "node_modules", "__pycache__", ".git", ".venv", ".venvs",
    "site-packages", "graphify-out", "publish", "publish_clean", "publish_proof",
    "backups", "scratchpad", "state",
}
#: Files that mark a directory as the root of a codebase.
MARKERS = (".git", "pyproject.toml", "setup.py", "package.json")


#: Directories the walk could not read. A tree nobody could open is a hole in the
#: audit, and silence about it would read as "nothing there".
UNREADABLE: List[str] = []


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as read_error:
        reason = type(read_error).__name__
        return "unreadable:%s" % reason


def discover(roots: Iterable[Path], depth: int = 2) -> List[Path]:
    """Codebase roots under `roots`: a `.git`, a `pyproject.toml`, a `setup.py` or
    a `package.json`, never inside a build, a venv or a dependency tree."""
    found: List[Path] = []
    seen: set = set()

    def walk(directory: Path, level: int) -> None:
        if level > depth:
            return
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError as walk_error:
            UNREADABLE.append("%s (%s)" % (directory, type(walk_error).__name__))
            return
        marker = [m for m in MARKERS if (directory / m).exists()]
        if marker:
            key = str(directory.resolve()).lower()
            if key not in seen:
                seen.add(key)
                found.append(directory)
        for child in children:
            if child.name in SKIP_NAMES or child.name.startswith("."):
                continue
            if not (child / ".git").exists() and not any(
                    (child / m).exists() for m in MARKERS):
                # Descend one level further only when the child looks like code.
                if any(child.glob("*.py")) or any(child.glob("*.js")):
                    walk(child, level + 1)
                continue
            walk(child, level + 1)

    for root in roots:
        walk(root, 0)
    return found


def _gitignore_line(root: Path) -> Tuple[bool, str]:
    """True when the grave is already ignored, and the file that would ignore it."""
    ignore = root / ".gitignore"
    if ignore.is_file():
        text = ignore.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip() in (GRAVE_ENTRY, GRAVE_ENTRY.rstrip("/"), "/" + GRAVE_ENTRY):
                return True, str(ignore)
    return False, str(ignore)


def plan(target: Path) -> dict:
    destination = target / FILENAME
    canon = sha256(CANONICAL)
    if not destination.exists():
        return {"target": str(target), "state": "missing", "canonical": canon}
    current = sha256(destination)
    if current == canon:
        return {"target": str(target), "state": "current", "canonical": canon}
    return {"target": str(target), "state": "conflict", "canonical": canon,
            "found": current}


def write_one(target: Path, force: bool = False) -> dict:
    state = plan(target)
    if state["state"] == "conflict" and not force:
        return dict(state, action="refused: differs from the kernel; --force to replace")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CANONICAL, target / FILENAME)
    ignored, ignore_path = _gitignore_line(target)
    if not ignored:
        ignore = Path(ignore_path)
        prefix = "" if not ignore.exists() or ignore.read_text(
            encoding="utf-8", errors="replace").endswith("\n") else "\n"
        with ignore.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(prefix + GRAVE_ENTRY + "\n")
    return dict(state, action="wrote", gitignore=ignore_path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="replace a differing requiem.py in a target")
    parser.add_argument("--root", action="append", default=None,
                        help="root to discover codebases under (repeatable)")
    parser.add_argument("--target", action="append", default=None,
                        help="explicit codebase root (repeatable)")
    parser.add_argument("--depth", type=int, default=2)
    args = parser.parse_args(argv)

    if not CANONICAL.is_file():
        print("kernel not found: %s" % CANONICAL, file=sys.stderr)
        return 1

    roots = [Path(root) for root in (args.root or [str(REPO.parent)])]
    targets = ([Path(t) for t in args.target] if args.target
               else discover(roots, args.depth))
    if not targets:
        print("no codebases found under %s" % ", ".join(str(r) for r in roots),
              file=sys.stderr)
        return 1

    rows = [plan(target) for target in targets]
    if args.write:
        rows = [write_one(target, args.force) for target in targets]
    for hole in UNREADABLE:
        print("UNREADABLE %s" % hole)
    for row in rows:
        print("%-9s %-9s %s" % (row["state"], row.get("action", "-"), row["target"]))
        if row["state"] == "conflict":
            print("          differs from the kernel: %s" % row.get("found", "?"))
    if args.check:
        bad = [row for row in rows if row["state"] != "current"]
        ignored = [row for row in rows
                   if not _gitignore_line(Path(row["target"]))[0]]
        for row in ignored:
            print("DEFECT: %s does not ignore %s" % (row["target"], GRAVE_ENTRY))
        print("%s -- %d codebase(s), %d drifted, %d unignored"
              % ("clean" if not bad and not ignored else "%d defect(s)"
                 % (len(bad) + len(ignored)), len(rows), len(bad), len(ignored)))
        return 0 if not bad and not ignored else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
