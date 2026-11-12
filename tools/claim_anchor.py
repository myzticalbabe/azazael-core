#!/usr/bin/env python3
"""claim_anchor — a claim about the tree carries the object it is about.

WHY THIS EXISTS (Z-9)
---------------------
On 2026-11-05 a lane closed a turn with `git merge-base --is-ancestor 2ea4718c HEAD`
-> exit 0, "ON_BRANCH". Forty minutes later a reset moved the branch back fifteen
commits and that claim became false while the work itself was untouched. The claim
was not wrong; it was anchored to a moving pointer and had no expiry printed on it.

A positional claim ("it is on the branch", "the file is at docs/X.md") expires
silently. A content-anchored claim ("tree 4f2a… and blob f77a… were measured at
14:31Z") can be re-tested after any move, because the object it names is the object
it is about.

WHAT IT WRITES
--------------
  .audit/anchors/<stamp>.json   {lane, branch, head, tree, paths:{path:blob}, measured_at}

WHAT IT PROVES LATER
--------------------
  verify <anchor.json>   re-hashes each path and re-resolves the branch:
     SURVIVED  every path still holds the recorded bytes (exit 0)
     MOVED     bytes intact, but the branch no longer points where it did (exit 0)
     PARTIAL   some paths hold different bytes — the claim must be re-derived (exit 1)
     LOST      a path is gone and its blob is not in the object store (exit 2)

The distinction between MOVED and PARTIAL is the whole point: relocation is normal
and must not read as loss, and loss must not hide behind relocation.

USAGE
-----
  python tools/claim_anchor.py measure --lane freebuff --paths docs/A.md src/b.py
  python tools/claim_anchor.py measure --lane freebuff            # branch tip only
  python tools/claim_anchor.py verify .audit/anchors/20261105T1431Z.json
  python tools/claim_anchor.py board-line <anchor.json>           # paste into `done`
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ANCHOR_DIR = REPO / ".audit" / "anchors"


GIT_TIMEOUT = 120


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=GIT_TIMEOUT)


def out(*args: str) -> str:
    return (git(*args).stdout or "").strip()


def blob_of(path: str, rev: str | None = None) -> str | None:
    """The blob sha for a path, from the worktree's index entry or a revision."""
    spec = f"{rev}:{path}" if rev else f":{path}"
    proc = git("rev-parse", "--verify", spec)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def worktree_blob(path: str) -> str | None:
    """Hash the bytes actually on disk — the honest reading of 'what I delivered'."""
    full = REPO / path
    if not full.is_file():
        return None
    proc = git("hash-object", "--", path)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def measure(lane: str, paths: list[str], rev: str | None) -> int:
    head = out("rev-parse", "HEAD")
    if not head:
        print("claim_anchor: REFUSED — HEAD does not resolve, so there is nothing to "
              "anchor to; fix the repository before claiming anything about it",
              file=sys.stderr)
        return 2
    branch = out("rev-parse", "--abbrev-ref", "HEAD")
    tree = out("rev-parse", f"{rev or 'HEAD'}^{{tree}}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    entries: dict[str, dict] = {}
    for path in paths:
        entries[path] = {
            "worktree_blob": worktree_blob(path),
            "committed_blob": blob_of(path, rev or "HEAD"),
        }

    record = {
        "stamp": stamp,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "lane": lane,
        "branch": branch,
        "head": head,
        "tree": tree,
        "rev": rev or "HEAD",
        "paths": entries,
    }
    ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
    path = ANCHOR_DIR / f"{stamp}.json"
    try:
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        print(f"claim_anchor: could not write the anchor ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return 2

    print(f"ANCHOR lane={lane} branch={branch} head={head[:12]} tree={tree[:12]} "
          f"measured_at={record['measured_at']}")
    for p, info in entries.items():
        wt, cm = info["worktree_blob"], info["committed_blob"]
        flag = "" if wt == cm else "  (worktree differs from committed — uncommitted bytes)"
        print(f"  {p}  worktree={str(wt)[:12]} committed={str(cm)[:12]}{flag}")
    print(f"record: {path}")
    return 0


def exists_in_ods(sha: str) -> bool:
    return git("cat-file", "-e", f"{sha}^{{blob}}").returncode == 0


def verify(anchor_file: str) -> int:
    path = Path(anchor_file)
    if not path.exists():
        print(f"claim_anchor: no such anchor: {path}", file=sys.stderr)
        return 2
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"claim_anchor: anchor unreadable ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2

    now_head = out("rev-parse", "HEAD")
    now_branch = out("rev-parse", "--abbrev-ref", "HEAD")
    same_branch = now_branch == rec.get("branch")
    same_head = now_head == rec.get("head")

    intact, changed, lost = [], [], []
    for p, info in (rec.get("paths") or {}).items():
        want = info.get("worktree_blob") or info.get("committed_blob")
        have = worktree_blob(p)
        if have and want and have == want:
            intact.append(p)
        elif have:
            changed.append((p, want, have))
        else:
            if want and exists_in_ods(want):
                lost.append((p, want, "path gone; blob still in the object store"))
            else:
                lost.append((p, want, "path gone and blob not in the object store"))

    print(f"anchor {rec.get('stamp')} lane={rec.get('lane')} measured={rec.get('measured_at')}")
    print(f"  then: {str(rec.get('head'))[:12]} on {rec.get('branch')}")
    print(f"  now : {str(now_head)[:12]} on {now_branch}"
          f"{'' if same_head else '   (HEAD HAS MOVED)'}"
          f"{'' if same_branch else '   (BRANCH CHANGED)'}")
    print(f"  intact {len(intact)}  changed {len(changed)}  lost {len(lost)}")

    if lost:
        for p, want, why in lost:
            print(f"    LOST {p} want={str(want)[:12]} — {why}")
            if want and exists_in_ods(want):
                print(f"         recover: git cat-file blob {want} > '{p}'")
        print("VERDICT: LOST — the bytes are gone from the worktree; the claim is not "
              "re-testable until they are restored")
        return 2
    if changed:
        for p, want, have in changed:
            print(f"    PARTIAL {p} want={str(want)[:12]} have={str(have)[:12]}")
        print("VERDICT: PARTIAL — the claim must be re-derived against the new bytes; "
              "relocation alone is not the cause")
        return 1
    if not same_head or not same_branch:
        print("VERDICT: MOVED — every recorded byte is intact; only the pointer moved. "
              "The claim survives, and now needs re-stating against the new head.")
        return 0
    print("VERDICT: SURVIVED — bytes and pointer are both where the anchor recorded them")
    return 0


def board_line(anchor_file: str) -> int:
    path = Path(anchor_file)
    if not path.exists():
        print(f"claim_anchor: no such anchor: {path}", file=sys.stderr)
        return 2
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"claim_anchor: anchor unreadable ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2
    paths = rec.get("paths") or {}
    shas = ",".join(sorted({str(v.get("worktree_blob"))[:12]
                            for v in paths.values() if v.get("worktree_blob")}))
    print(f"anchor={rec.get('stamp')} head={str(rec.get('head'))[:12]} "
          f"tree={str(rec.get('tree'))[:12]} blobs=[{shas or 'branch-only'}] "
          f"measured_at={rec.get('measured_at')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="claim_anchor", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("measure", help="record what the tree holds, with hashes")
    m.add_argument("--lane", required=True)
    m.add_argument("--paths", nargs="*", default=[])
    m.add_argument("--rev", default=None, help="anchor to a revision (default HEAD)")

    v = sub.add_parser("verify", help="re-test an anchor after the tree may have moved")
    v.add_argument("anchor")

    b = sub.add_parser("board-line", help="one line to paste into board done/block")
    b.add_argument("anchor")

    args = ap.parse_args()
    if args.cmd == "measure":
        return measure(args.lane, args.paths, args.rev)
    if args.cmd == "verify":
        return verify(args.anchor)
    return board_line(args.anchor)


if __name__ == "__main__":
    sys.exit(main())
