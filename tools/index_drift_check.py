#!/usr/bin/env python3
# coding: utf-8
"""Stale-index guard: fail when a staged file also has UNSTAGED edits.

Why this exists
---------------
`git commit` commits the INDEX, not the worktree. So a file can be staged, then
edited again, and the commit will carry the version from *before* the second
edit -- silently, with no warning from git. Measured 2026-10-11: a commit was
REJECTED by the anti-pattern scanner, the file was then CORRECTED in the
worktree, and the re-commit was run without re-running `git add`. The commit
landed the pre-rejection text; HEAD recorded `tools/repoint_315t.py` as "landed"
while the worktree said the opposite, and the correction sat unstaged. The
record was not merely incomplete, it was inverted.

The tell was a diff SHAPE: the staged diff was `68 insertions(+)` and
**0 deletions** for an edit that REMOVED words. Insertions-vs-deletions is a
shape to read, not just a count -- but a human reading it after the fact is a
check that runs too late. This makes it a check that runs at commit time.

Why it runs BEFORE the staged scanner
-------------------------------------
`tools/staged_scanner_gate.py` scans the index (`git show :0:<path>`). If the
index is stale, that scanner certifies a version of the file nobody has on disk
-- the gate's verdict is about a blob that is not what the author edited. A
stale index does not just corrupt the record; it invalidates a downstream gate's
verdict, so the guard has to come first.

What it does
------------
Intersects `git diff --cached --name-only` (index vs HEAD) with
`git diff --name-only` (worktree vs index). A path in both is staged AND edited
after staging: committing it would store the stale blob.

Exit codes
----------
    0  clean -- no staged path has unstaged edits (or nothing is staged)
    1  stale index -- at least one staged path also has unstaged edits

Override
--------
`ALLOW_STALE_INDEX=1` downgrades to a loud warning and exits 0. It exists
because a genuine two-step intent is possible (stage a subset of a file's edits
on purpose). The default must still be FAIL: the failure mode this catches --
a commit whose content silently differs from what the author believes -- has no
symptom at commit time and is only noticed by whoever trusts the record later.
Overriding is declaring that you know which version you are committing. It
mirrors the existing `ALLOW_BULK_COMMIT=1` convention in `scripts/commit_gate.py`.

Usage:
    python tools/index_drift_check.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT = 60
EXIT_STALE = 1


def _git(*args: str) -> tuple[int, str]:
    """Run git; return (returncode, stdout). Never raises.

    Bytes, not text: `text=True` decodes with the OS locale (cp1252 here) and
    a git failure leaves `.stdout` as None. Decode UTF-8 with errors="replace"
    so a non-cp1252 byte in a path cannot crash a gate that runs on every
    commit. The return code is returned, never assumed -- `check=False` plus an
    explicit inspection is the difference between knowing git succeeded and
    believing it did.
    """
    try:
        proc = subprocess.run(["git", *args], cwd=str(ROOT),
                              capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        # Loud, and NEVER a silent pass: an unrunnable guard must say so.
        print("[IndexDrift] git %s could not run (%s: %s)"
              % (" ".join(args), type(exc).__name__, exc))
        return 128, ""
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


def _names(args: list[str]) -> list[str] | None:
    """Path list from a `git diff ... --name-only`, or None if git failed."""
    code, out = _git(*args)
    if code != 0:
        print("[IndexDrift] git %s failed (exit %s) -- passing loud"
              % (" ".join(args), code))
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    _ = argv  # no options: this guard has exactly one question to answer

    staged = _names(["diff", "--cached", "--name-only"])
    if staged is None:
        return 0
    if not staged:
        print("[IndexDrift] Nothing staged, skipping.")
        return 0

    unstaged = _names(["diff", "--name-only"])
    if unstaged is None:
        return 0

    # Iterate `staged` (not a set) so the report follows git's own ordering.
    edited_after_staging = set(unstaged)
    drift = [p for p in staged if p in edited_after_staging]

    if not drift:
        print("[IndexDrift] %d staged path(s); none edited after staging."
              % len(staged))
        return 0

    print("")
    print("[IndexDrift] STALE INDEX -- %d staged path(s) also have unstaged "
          "edits:" % len(drift))
    for path in drift:
        print("  %s" % path)
    print("")
    print("[IndexDrift] Committing now stores the version from BEFORE your last")
    print("[IndexDrift] edit to each path above -- the blob in the commit is not")
    print("[IndexDrift] the file on disk. Re-stage to fix: git add <path>")
    print("[IndexDrift] (or declare intent: ALLOW_STALE_INDEX=1)")

    if os.environ.get("ALLOW_STALE_INDEX") == "1":
        print("[IndexDrift] ALLOW_STALE_INDEX=1 -- stale index declared, "
              "passing with the warning above.")
        return 0
    return EXIT_STALE


if __name__ == "__main__":
    raise SystemExit(main())
