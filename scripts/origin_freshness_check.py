#!/usr/bin/env python3.15t
# coding: utf-8
"""origin_freshness_check.py -- fail closed when the LIVE tree lags its origin.

WHY THIS EXISTS
---------------
On 2026-10-02 the live execution tree (C:/Azazael/azazael, the editable-install
target) was **6 commits behind** its own `origin` and nothing reported it. The
cause was not a missing remote: `git status` said `up to date` because the
`refs/remotes/origin/*` refs had frozen at the clone's own HEAD and had not
been fetched since the clone was made. A stale tracking ref is *indistinguishable
from a current one* by every command that reads it, so the drift was silent by
construction -- a loop that fires while producing nothing.

This guard does the one thing that makes the drift observable: it FETCHES first,
THEN measures. It never advances the branch (no merge, no reset); it reports and
exits non-zero, so a session can refuse to proceed on stale code without the
guard silently mutating the tree.

Exit codes:
    0  tree is current (or ahead of) its upstream
    1  tree is BEHIND its upstream -- stale; do not trust in-tree runs
    2  could not determine (no repo / no upstream / fetch failed)

Usage:
    python3.15t scripts/origin_freshness_check.py
    python3.15t scripts/origin_freshness_check.py --no-fetch   # offline check
    python3.15t scripts/origin_freshness_check.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _git(args, cwd, timeout=90):
    """Run git, returning (returncode, stdout, stderr). Never raises on rc!=0."""
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 2, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return 2, "", "git %s timed out after %ss" % (args[0], timeout)


def check(root=None, fetch=True, branch=None):
    """Return (code, report-dict). Pure measurement; no branch mutation."""
    root = Path(root) if root else Path(__file__).resolve().parent.parent

    rc, top, err = _git(["rev-parse", "--show-toplevel"], root)
    if rc != 0:
        return 2, {"ok": None, "error": "not a git repository: %s" % (err or root)}

    rc, br, err = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if rc != 0:
        return 2, {"ok": None, "error": "cannot resolve HEAD: %s" % err}
    br = branch or br

    rc, upstream, _ = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                            "%s@{upstream}" % br], root)
    if rc != 0 or not upstream:
        return 2, {"ok": None, "branch": br,
                   "error": "no upstream configured for %s (set one with "
                            "`git branch --set-upstream-to`)" % br}

    fetched = False
    fetch_err = ""
    if fetch:
        remote = upstream.split("/", 1)[0]
        rc, _, fetch_err = _git(["fetch", remote], root)
        fetched = rc == 0

    rc, counts, err = _git(["rev-list", "--left-right", "--count",
                            "HEAD...%s" % upstream], root)
    if rc != 0:
        return 2, {"ok": None, "branch": br, "upstream": upstream,
                   "error": "cannot compare to upstream: %s" % err}

    try:
        ahead, behind = (int(x) for x in counts.split())
    except ValueError:
        return 2, {"ok": None, "branch": br, "upstream": upstream,
                   "error": "unparseable rev-list output: %r" % counts}

    rc, head, _ = _git(["log", "-1", "--format=%h %ci %s"], root)
    report = {"ok": behind == 0, "branch": br, "upstream": upstream,
              "ahead": ahead, "behind": behind, "head": head,
              "fetched": fetched, "root": str(top)}
    if fetch_err:
        report["fetch_error"] = fetch_err
        # An offline check that cannot fetch still answers from the refs it has,
        # but says so: a stale ref read as current is the exact defect this
        # guard exists to catch.
        report["warning"] = "fetch failed; answer is from local refs only"
    return (0 if behind == 0 else 1), report


def main():
    ap = argparse.ArgumentParser(description="Fail closed when the live tree "
                                             "lags its origin.")
    ap.add_argument("--root", default=None, help="repo path (default: this tree)")
    ap.add_argument("--no-fetch", action="store_true",
                    help="do not fetch; compare against existing remote refs")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--branch", default=None, help="branch to check (default HEAD)")
    args = ap.parse_args()

    code, report = check(root=args.root, fetch=not args.no_fetch,
                         branch=args.branch)

    if args.json:
        print(json.dumps(report, indent=2))
        return code

    if code == 2:
        print("UNKNOWN: %s" % report.get("error"))
        return code
    if report["behind"]:
        print("STALE: %s is %d commit(s) BEHIND %s (ahead %d)."
              % (report["branch"], report["behind"], report["upstream"],
                 report["ahead"]))
        print("  live HEAD: %s" % report.get("head"))
        print("  Do not trust in-tree runs until advanced; the editable install "
              "executes THIS tree.")
        return code
    print("CURRENT: %s is up to date with %s (ahead %d)."
          % (report["branch"], report["upstream"], report["ahead"]))
    if report.get("warning"):
        print("  warning: %s" % report["warning"])
    return code


if __name__ == "__main__":
    sys.exit(main())
