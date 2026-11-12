"""check_tree_parity.py — fail-closed visibility on two-Windows-tree drift.

FLIP AUTHORITY (2026-10-03): the editable install executes
C:\\Azazael\\azazael, so A is the de-facto hub and B
(C:\\Users\\clint\\azazael) is where interactive work lands; sync roots are
derived (hub = whichever tree runs the script), so there is no literal left
to repoint. What was missing is drift VISIBILITY: A once reported 0/0
up-to-date while 6 commits behind, because nothing compared the trees.

RETIRED 2026-10-21 (single-tree era): the second tree was archived, so
there is nothing to compare against. main() exits 0 with a notice;
the original comparator is preserved below for history.

This check compared HEAD hashes (via merge-base when history is shared),
commit dates, and worktree dirt counts. Exit 1 on ANY divergence, with the
exact state printed — run it at session start before trusting either tree.

Usage:
    python scripts/check_tree_parity.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

A = Path("C:/Azazael/azazael")
B = Path("G:/azazael.archived-20261021")  # RETIRED 2026-10-21: frozen archive, not a live tree


def git(tree: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(tree), *args],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError("%s: git %s failed: %s" % (
            tree, " ".join(args), (proc.stderr or "").strip()[:200]))
    return (proc.stdout or "").strip()


def main() -> int:
    print("check_tree_parity RETIRED 2026-10-21: single tree; "
          "old tree archived to G:/azazael.archived-20261021")
    return 0


def _retired_compare() -> int:
    problems: list[str] = []
    try:
        ha = git(A, "rev-parse", "HEAD")
        hb = git(B, "rev-parse", "HEAD")
        da = git(A, "log", "-1", "--format=%ci %s")
        db = git(B, "log", "-1", "--format=%ci %s")
    except RuntimeError as exc:
        print("parity: UNREADABLE (%s)" % exc)
        return 2
    print("A HEAD: %s  %s" % (ha[:12], da[:80]))
    print("B HEAD: %s  %s" % (hb[:12], db[:80]))
    if ha != hb:
        try:
            base = git(A, "merge-base", ha, hb)
            aa = git(A, "rev-list", "--count", "%s..%s" % (base, ha))
            bb = git(B, "rev-list", "--count", "%s..%s" % (base, hb))
            # merge-base needs both commits reachable from ONE repo; when
            # the clones do not share objects, fall back to dates below.
            print("shared base: %s (A+%s B+%s)" % (base[:12], aa, bb))
            if int(aa) or int(bb):
                problems.append(
                    "trees diverged (A+%s B+%s since %s)" % (aa, bb, base[:12]))
        except RuntimeError:
            problems.append("no shared history; comparing dates: A [%s] B [%s]"
                            % (da[:30], db[:30]))
    for name, tree in (("A", A), ("B", B)):
        try:
            dirt = git(tree, "status", "--porcelain")
            n = len([l for l in dirt.splitlines() if l.strip()])
        except RuntimeError:
            n = -1
        print("%s dirt: %d" % (name, n))
    if problems:
        print("parity: DIVERGED")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("parity: OK (same HEAD)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
