"""attr_push_block.py -- a blocked push routes itself to its owner.

Twice in one night a push died on someone else's row and cost a full triage
pass to attribute (servemesh wire row -> opencode 5514c7da; worktree_snapshot
finding -> origin's own ce64a668). This tool answers the only question that
matters about a push-blocking finding: whose is it, and where do I send it?

    python tools/attr_push_block.py tools/worktree_snapshot.py
    python tools/attr_push_block.py NEURAL_MESH_WIRE_OR_CUT.json servemesh

Prints: newest commits touching each path (hash, date, subject), the turf
owner per the TURF table, and the delivery suggestion. Read-only.

TURF is a routing table, not a claim of ownership: when the newest toucher
and the turf owner differ, both are named and the owning lane adjudicates.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Path prefix -> lane that owns the turf. Gaps welcome: unknown prints
# UNKNOWN and the newest toucher decides.
TURF = (
    ("src/eve_unleashed/", "freebuff"),
    ("src/CORE/WORMS/expansion/servemesh", "opencode"),
    ("NEURAL_MESH_WIRE_OR_CUT", "opencode"),
    ("tools/interp_cycle.py", "freebuff"),
    ("tools/repoint_316t.py", "freebuff"),
    ("tools/lane_forensics.py", "freebuff"),
    ("tools/lane_watchdog.py", "freebuff"),
    ("tools/perm_block.py", "freebuff"),
    ("tools/attr_push_block.py", "freebuff"),
    ("tools/worktree_snapshot.py", "UNKNOWN"),
    ("docs/bible/", "UNKNOWN"),
)


def _env() -> dict:
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    return env


def newest_touchers(path: str, n: int = 3) -> list[str]:
    p = subprocess.run(
        ["git", "log", f"--format=%h %ad %s", "--date=short", "-n", str(n),
         "--", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, cwd=REPO, env=_env())
    out = (p.stdout or "").strip().splitlines()
    return out or ["no commits touch this path (untracked or foreign)"]


def turf_owner(path: str) -> str:
    norm = path.replace("\\", "/")
    for prefix, lane in TURF:
        if norm.startswith(prefix) or prefix in norm:
            return lane
    return "UNKNOWN"


def main() -> int:
    ap = argparse.ArgumentParser(description="Attribute a push-blocking finding.")
    ap.add_argument("paths", nargs="+", help="failing file(s)")
    ap.add_argument("--row", default=None, help="failing row/symbol, if any")
    args = ap.parse_args()
    for path in args.paths:
        print(f"== {path}" + (f"  row: {args.row}" if args.row else ""))
        print(f"   turf owner: {turf_owner(path)}")
        for line in newest_touchers(path):
            print(f"   touched: {line}")
        owner = turf_owner(path)
        if owner != "UNKNOWN":
            print(f"   route: post delivery to lane.{owner} quoting this block")
        else:
            print("   route: newest toucher adjudicates; no turf table entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
