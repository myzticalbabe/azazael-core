#!/usr/bin/env python3
"""orphan_watch — fail when a backup ref holds work the branch no longer reaches.

WHY THIS EXISTS
---------------
A `reset --hard` backwards is invisible to every check in this tree: no exception,
no failing test, no dirty worktree, and `git status` reports clean. On 2026-11-05
that state stood for forty minutes and would have stood until `gc` pruned it. The
only durable evidence was a reflog line, and reflog entries for unreachable
commits expire at 30 days (`gc.reflogExpireUnreachable`).

So the condition to watch is not "did something fail" — nothing did. It is a
set-membership question a pairwise check cannot answer:

    does every ref under refs/backup/ still appear in the history of the branch?

While the answer is yes, the backup is redundant (a safety net nothing is using).
When it becomes no, a lane protected work and the branch is behind it. That is a
fact worth printing in the same turn it happens, not thirty days later.

USAGE
-----
  python tools/orphan_watch.py            # exit 1 if any backup ref is unreached
  python tools/orphan_watch.py --json     # machine-readable
  python tools/orphan_watch.py --all-refs # also audit refs/other than backup

EXIT CODES
----------
  0  every backup ref is reachable from HEAD (nothing stranded)
  1  at least one backup ref is NOT in HEAD's history — work is stranded
  2  could not measure (no git, no HEAD)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


GIT_TIMEOUT = 120


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=GIT_TIMEOUT)


def out(*args: str) -> str:
    return (git(*args).stdout or "").strip()


def refs_under(prefix: str) -> list[tuple[str, str]]:
    raw = out("for-each-ref", "--format=%(refname) %(objectname)", prefix)
    rows = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2:
            rows.append((parts[0], parts[1]))
    return rows


def _count(raw: str, what: str) -> int:
    """A count git would not give us is not zero.

    Zero means "this ref adds nothing", which is the one reading that hides a
    stranded ref. An unparseable answer raises instead.
    """
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{what}: git printed {raw!r}, which is not a count") from exc


def ahead_count(sha: str) -> int:
    """Commits reachable from the ref but not from HEAD."""
    return _count(out("rev-list", "--count", f"HEAD..{sha}"), "ahead")


def behind_count(sha: str) -> int:
    return _count(out("rev-list", "--count", f"{sha}..HEAD"), "behind")


def audit(prefixes: list[str]) -> dict:
    head = out("rev-parse", "HEAD")
    branch = out("rev-parse", "--abbrev-ref", "HEAD")
    report = {"head": head, "branch": branch, "checked_at_prefixes": prefixes,
              "stranded": [], "redundant": [], "ok": True}
    if not head:
        report["ok"] = False
        report["error"] = "HEAD does not resolve"
        return report

    for prefix in prefixes:
        for ref, sha in refs_under(prefix):
            try:
                ahead = ahead_count(sha)
                behind = behind_count(sha)
            except RuntimeError as exc:
                report["ok"] = False
                report["error"] = str(exc)
                return report
            row = {"ref": ref, "sha": sha, "commits_not_in_head": ahead,
                   "head_behind_by": behind}
            if ahead > 0:
                report["stranded"].append(row)
            else:
                report["redundant"].append(row)
    report["ok"] = not report["stranded"]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(prog="orphan_watch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all-refs", action="store_true",
                    help="audit every ref outside refs/heads and refs/remotes")
    args = ap.parse_args()

    prefixes = ["refs/backup"]
    if args.all_refs:
        prefixes = ["refs/backup", "refs/preverb", "refs/recovered", "refs/keep"]

    report = audit(prefixes)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        # An unmeasurable tree is 2, not 1: 1 means "measured, and work is
        # stranded", and a caller that cannot tell them apart reads a broken
        # git as a clean tree.
        if report.get("error"):
            return 2
        return 0 if report.get("ok") else 1

    if report.get("error"):
        print(f"orphan_watch: cannot measure — {report['error']}", file=sys.stderr)
        return 2

    print(f"orphan_watch  head={str(report['head'])[:12]} on {report['branch']}")
    if not report["stranded"] and not report["redundant"]:
        print("  no backup refs exist — nothing is stranded, and nothing is protected")
        return 0

    for row in report["redundant"]:
        print(f"  ok        {row['ref']} -> {row['sha'][:12]} is already in HEAD")
    for row in report["stranded"]:
        print(f"  STRANDED  {row['ref']} -> {row['sha'][:12]} holds "
              f"{row['commits_not_in_head']} commit(s) HEAD does not reach "
              f"(HEAD is {row['head_behind_by']} behind it)")
        print(f"            adopt:  python tools/git_safe.py reset --hard {row['ref']}")
        print(f"            or drop deliberately: git update-ref -d {row['ref']}")

    if report["stranded"]:
        print("VERDICT: STRANDED — the branch no longer reaches work a ref is holding. "
              "Decide to adopt or drop; do not leave it unadopted, because the ref is "
              "the only thing standing between those commits and a gc.")
        return 1
    print("VERDICT: NAMED-BUT-REDUNDANT — every backup ref is already in HEAD's history. "
          "The protection is doing nothing; drop it when you are sure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
