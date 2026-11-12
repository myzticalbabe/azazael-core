#!/usr/bin/env python3
# coding: utf-8
"""Warn when a path-scoped commit could sweep in unrelated dirty files.

The problem, measured rather than assumed
------------------------------------------
The working tree carries 633 dirty entries. A commit made while that is true
has to be path-scoped, and path-scoped commits are exactly the kind that
quietly include the wrong thing:

    git commit -m "..." src/CORE/cd2_verifier.py

stages that one path, but a `git add -A` three commands earlier, a partially
completed `git reset`, or a hook that runs `git add` will have staged others.
The commit then contains code the message never mentions, and the message is
the only description anyone gets.

This tool reads the **index**, not the working tree, because the index is
what a commit will actually contain. `git status` conflates the two: 633
entries there is a fact about the tree, while what matters is how many of
those are staged.

A correction worth recording
----------------------------
The priority item this was built for said the live `chain_controller` was
"continuously rewriting the tree" and that commits had to be path-scoped for
that reason. Measured over 40 seconds: **zero** non-`state/` files changed
content. Five `state/*.json` bookkeeping files churn; no source does. The
controller is not currently mutating source at all.

The 633 dirty entries are accumulated machine state, not a live hazard. So
the guard is aimed at the real failure -- a staged set that disagrees with
the commit message -- and not at a process that has stopped.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: Never part of a human's commit message, and worth naming if staged.
NOISE_PREFIXES = ("state/", "logs/", "output/", ".audit/", "graphify-out/")

#: Generated or machine-owned even inside src/.
NOISE_NAMES = {".mypy_cache", "__pycache__"}


def _git(*args: str, cwd: Path = ROOT) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=str(cwd), timeout=120,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def staged(repo: Path = ROOT) -> list[str]:
    """Paths currently in the index, i.e. what a commit would contain."""
    rc, out = _git("diff", "--cached", "--name-only", cwd=repo)
    if rc != 0:
        return []
    return [p for p in out.splitlines() if p.strip()]


def dirty(repo: Path = ROOT) -> list[str]:
    rc, out = _git("status", "--porcelain", cwd=repo)
    if rc != 0:
        return []
    return [ln[3:].strip().strip('"') for ln in out.splitlines() if ln.strip()]


def is_noise(p: str) -> bool:
    if p.startswith(NOISE_PREFIXES):
        return True
    return any(part in NOISE_NAMES for part in Path(p).parts)


def classify(staged_paths: list[str], dirty_paths: list[str]) -> dict[str, Any]:
    staged_set = set(staged_paths)
    noise = sorted(p for p in staged_paths if is_noise(p))
    source = sorted(p for p in staged_paths if not is_noise(p))
    unstaged = sorted(set(dirty_paths) - staged_set)
    unstaged_source = sorted(p for p in unstaged if not is_noise(p))
    return {
        "staged": len(staged_paths),
        "staged_source": len(source),
        "staged_noise": len(noise),
        "unstaged": len(unstaged),
        "unstaged_source": len(unstaged_source),
        "noise_paths": noise,
        "source_paths": source,
        "unstaged_source_sample": unstaged_source[:20],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report what a commit would actually contain.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-noise", action="store_true",
                    help="do not fail on staged state/logs/output files")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    # `--git-dir` alone is not a test: git walks UP the tree, and this
    # project's root is the user's home directory, so a temp dir anywhere
    # under it resolves to C:/Users/clint/.git and looks like a repository.
    # Compare the toplevel against the path asked about.
    rc, top = _git("rev-parse", "--show-toplevel", cwd=repo)
    if rc != 0:
        print("INCONCLUSIVE: %s is not a git repository" % repo, file=sys.stderr)
        return 2
    try:
        same = Path(top.strip()).resolve() == repo.resolve()
    except OSError:
        same = False
    if not same:
        print("INCONCLUSIVE: %s is inside the repository at %s, not its root; "
              "the index read would report that repo's staged set, not this "
              "directory's" % (repo, top.strip()), file=sys.stderr)
        return 2

    rep = classify(staged(repo), dirty(repo))

    if args.json:
        print(__import__("json").dumps(rep, indent=2))
        return 0

    print("STAGED: %d (%d source, %d machine-state)"
          % (rep["staged"], rep["staged_source"], rep["staged_noise"]))
    if rep["source_paths"]:
        print("   source in this commit:")
        for p in rep["source_paths"][:25]:
            print("      %s" % p)
        if len(rep["source_paths"]) > 25:
            print("      ... %d more" % (len(rep["source_paths"]) - 25))
    if rep["noise_paths"]:
        print("   machine-state staged (check this is intended):")
        for p in rep["noise_paths"][:12]:
            print("      %s" % p)
        if len(rep["noise_paths"]) > 12:
            print("      ... %d more" % (len(rep["noise_paths"]) - 12))

    print("\nUNSTAGED: %d (%d source) -- left out of this commit"
          % (rep["unstaged"], rep["unstaged_source"]))
    if rep["unstaged_source_sample"]:
        print("   e.g. %s"
              % ", ".join(rep["unstaged_source_sample"][:6]))

    if rep["staged_noise"] and not args.allow_noise:
        print("\nFAIL: %d machine-state file(s) are staged. A commit message "
              "describing\nsource work should not carry them; pass "
              "--allow-noise if intended." % rep["staged_noise"])
        return 1
    if rep["staged"] == 0:
        print("\nOK: nothing staged.")
        return 0
    print("\nReminder: the commit message is the only description this commit "
          "gets.\nRead the source list above and confirm it matches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
