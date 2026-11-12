#!/usr/bin/env python3
# coding: utf-8
"""Detect tracked .py files that shadow a same-named module elsewhere.

The problem, and the correction
------------------------------
An audit on 2026-09-29 reported "8 tracked .py files at the repo root shadow
a same-named file elsewhere with different content, last touched three weeks
before the src/ copies they shadow" and proposed deleting them.

**That was backwards, and this tool exists because of the correction.**
Measuring line-level difference instead of trusting commit dates:

    health_server.py   120 lines unique to the root copy, 1 unique to src/

and the root copy defines `_get_azazael_health()`, which
`src/CORE/health_server.py` does not. The root copy was *ahead*. Its last
commit was 2026-09-06 against the src/ copy's 2026-06-21 -- but that is
because the src/ copy has not been touched since June, not because the root
copy is stale. "Older commit date" and "older content" are different claims,
and the audit read the first as the second.

Deleting on that evidence would have destroyed working code.

What this checks
----------------
A tracked `.py` whose path has no directory separator, where a tracked file
elsewhere ends in the same name. That is the shape that makes every
whole-repo tool ambiguous: `dict_key_contract` reported line numbers against
`NATURE_CLUSTERS.py` when the reader had
`src/CORE/WORMS/NATURE_CLUSTERS.py` open, so the numbers pointed at a
different file with the same name.

For each pair it reports the line-level difference in both directions, and
which side holds symbols the other lacks. It does not decide which to keep.
Deciding needs intent, and intent is not derivable from line counts -- the
whole point of the correction above.

An import check is included because that is the difference between
"ambiguous" and "actively dangerous": a shadow that nothing imports is a
naming problem, one something imports is a resolution hazard.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: Trees that legitimately hold historical copies. A root file shadowing one
#: of these is a naming problem, not a lost-code problem, and the two are
#: reported separately so they are not fixed with the same action.
ARCHIVE_PREFIXES = ("archive/", "archived", "packages/")

SKIP_PARTS = {"__pycache__", ".mypy_cache", ".pytest_cache", "graphify-out",
              ".git", "node_modules", "output", "build", ".venv", "venv"}


def _is_archived(rel: str) -> bool:
    return rel.startswith(ARCHIVE_PREFIXES)


def tracked_py(repo: Path) -> list[str]:
    """Tracked .py paths, repo-relative and forward-slashed.

    Falls back to walking the directory when `git ls-files` fails, so the
    tool works on any tree -- including a temp fixture with no .git. Without
    the fallback it silently reported "no shadows" on exactly the input a
    test would give it, which is the worst possible failure for a checker.
    """
    try:
        r = subprocess.run(["git", "ls-files", "*.py"], cwd=str(repo), timeout=120,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            out = [p for p in r.stdout.splitlines() if p.strip()]
            if out:
                return out
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(
            "  git ls-files unavailable (%s: %s); walking the tree instead\n"
            % (type(exc).__name__, exc))
    out = []
    for p in repo.rglob("*.py"):
        if SKIP_PARTS & set(p.parts):
            continue
        try:
            out.append(p.relative_to(repo).as_posix())
        except ValueError:
            continue
    return sorted(out)


def top_level_shadowers(repo: Path) -> dict[str, list[str]]:
    """basename -> [paths], for tracked .py with no directory separator."""
    out: dict[str, list[str]] = {}
    for p in tracked_py(repo):
        if "/" not in p:
            out.setdefault(Path(p).name, []).append(p)
    return {k: v for k, v in out.items() if len(v) == 1}


def twins(repo: Path, shadowers: dict[str, list[str]]) -> dict[str, list[str]]:
    """shadow basename -> every other tracked file with that name."""
    allp = tracked_py(repo)
    res: dict[str, list[str]] = {}
    for base in shadowers:
        res[base] = [p for p in allp if p != shadowers[base][0]
                     and Path(p).name == base]
    return res


def _lines(repo: Path, rel: str) -> list[str]:
    try:
        return (repo / rel).read_text(encoding="utf-8", errors="replace") \
            .splitlines()
    except OSError:
        return []


def unique_line_counts(repo: Path, a: str, b: str) -> dict[str, int]:
    """Lines unique to `a` vs unique to `b`. Direction matters."""
    la, lb = _lines(repo, a), _lines(repo, b)
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    only_a = only_b = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            only_b += (i2 - i1)
        if tag in ("insert", "replace"):
            only_a += (j2 - j1)
    return {"only_root": only_a, "only_twin": only_b}


def _symbols(repo: Path, rel: str) -> set[str]:
    out: set[str] = set()
    try:
        tree = ast.parse((repo / rel).read_text(encoding="utf-8",
                                                 errors="replace"))
    except (SyntaxError, OSError):
        return out
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
    return out


def import_check(repo: Path, mods: set[str]) -> dict[str, list[str]]:
    """Which tracked .py actually import any of these module names.

    This is the difference between "ambiguous" and "actively dangerous".
    A shadow nothing imports is a naming problem. A shadow something imports
    -- from the repo root, before src/ is on the path -- is a resolution
    hazard that picks the wrong file at runtime.
    """
    hits: dict[str, list[str]] = {}
    if not mods:
        return hits
    for p in tracked_py(repo):
        parts = set(Path(p).parts)
        if SKIP_PARTS & parts or _is_archived(p):
            continue
        if len(parts) == 1:
            continue          # a shadow does not import itself
        try:
            tree = ast.parse((repo / p).read_text(encoding="utf-8",
                                                  errors="replace"))
        except (SyntaxError, OSError):
            continue
        for n in ast.walk(tree):
            names: list[str] = []
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                names = [n.module]
            for nm in names:
                if nm.split(".")[0] in mods:
                    hits.setdefault(nm.split(".")[0], []).append(
                        f"{p}:{n.lineno}")
    return hits


def analyse(repo: Path) -> dict[str, Any]:
    shadowers = top_level_shadowers(repo)
    tw = twins(repo, shadowers)
    rows: list[dict[str, Any]] = []
    for base, tlist in sorted(tw.items()):
        if not tlist:
            continue
        root = shadowers[base][0]
        for t in tlist:
            archived = _is_archived(t)
            counts = unique_line_counts(repo, root, t)
            s_root, s_twin = _symbols(repo, root), _symbols(repo, t)
            rows.append({
                "root": root, "twin": t, "twin_archived": archived,
                "only_root": counts["only_root"],
                "only_twin": counts["only_twin"],
                "identical": (repo / root).read_bytes()
                             == (repo / t).read_bytes()
                             if (repo / t).exists() else False,
                "root_has": sorted(s_root - s_twin)[:12],
                "twin_has": sorted(s_twin - s_root)[:12],
            })
    mods = {Path(b).stem for b in shadowers if not b.startswith("__")}
    imported = import_check(repo, mods)
    return {"shadowers": len(shadowers), "pairs": rows, "imports": imported}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Detect tracked .py files shadowing a same-named module.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    rep = analyse(repo)

    if not rep["shadowers"]:
        if args.json:
            print(json.dumps(rep, indent=2))
        else:
            print("OK: no tracked .py file at the repo root shadows a "
                  "same-named module elsewhere.")
        return 0

    if args.json:
        print(json.dumps(rep, indent=2))
        return 1

    live = [r for r in rep["pairs"] if not r["twin_archived"]]
    arch = [r for r in rep["pairs"] if r["twin_archived"]]
    print("root-level .py shadowing a same-named file: %d "
          "(%d against a LIVE module, %d against an archived copy)"
          % (len(rep["pairs"]), len(live), len(arch)))
    print("modules importable by any of these names: %d\n"
          % len(rep["imports"]))

    for title, rows in (("SHADOWS A LIVE MODULE", live),
                        ("SHADOWS AN ARCHIVED COPY", arch)):
        if not rows:
            continue
        print("%s (%d):" % (title, len(rows)))
        for r in rows:
            verdict = "IDENTICAL" if r["identical"] else "DIFFERS"
            print("   %s  <->  %s" % (r["root"], r["twin"]))
            print("      %s | %d lines only in root, %d only in twin"
                  % (verdict, r["only_root"], r["only_twin"]))
            if r["root_has"]:
                print("      root defines, twin does not: %s"
                      % ", ".join(r["root_has"][:6]))
            if r["twin_has"]:
                print("      twin defines, root does not: %s"
                      % ", ".join(r["twin_has"][:6]))
        print()

    if rep["imports"]:
        print("IMPORTS THAT RESOLVE AMBIGUOUSLY (module -> sites):")
        for m, sites in sorted(rep["imports"].items()):
            print("   %-28s %d" % (m, len(sites)))
            for s in sites[:4]:
                print("      ", s)
    else:
        print("No tracked .py imports any of these module names by their "
              "bare name,\nso no import resolves ambiguously. The hazard is "
              "naming, not resolution.")

    print("\nThis tool does not say which copy to keep. The 2026-09-29 audit\n"
          "deleted-by-recency reasoning was wrong: health_server.py has 120\n"
          "lines the src/ copy lacks, including a function it does not\n"
          "define. Commit date is not content age.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
