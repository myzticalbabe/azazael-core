#!/usr/bin/env python3
"""git-merge-gate -- answer "would this branch merge?" without touching anything.

Why this exists
---------------
`git merge --no-commit` answers the same question by mutating the index and the
worktree, and when it conflicts you are left in a merge state to abort. Git 2.38
added `git merge-tree --write-tree`, which performs a *real* three-way merge in
the object database and reports the result: exit 0 with a tree OID when the merge
is clean, exit 1 with the conflicted paths when it is not, exit >1 when the merge
could not be attempted at all. Nothing in the working tree, the index or HEAD is
touched, so it is safe to run against every branch in a loop while other work is
happening -- which is what a gate has to be.

The distinction this tool refuses to blur: **exit > 1 is not a conflict.** A bad
ref, a missing object, a shallow clone and a conflicting merge all "fail", and a
gate that maps every non-zero to "conflicts" reports work to resolve that does not
exist. Each branch therefore carries CLEAN / CONFLICTS / ERROR, and only CONFLICTS
is actionable.

Usage
-----
    python tools/git_merge_gate.py                       # every local branch vs HEAD
    python tools/git_merge_gate.py --base origin/main
    python tools/git_merge_gate.py --branch feat/a --branch feat/b --json gate.json
    python tools/git_merge_gate.py --pre-push            # invoked by .githooks/pre-push
    python tools/git_merge_gate.py --queue --branch a --branch b --branch c
    python tools/git_merge_gate.py --queue --queue-file landing.txt   # one per line

Exit status: 0 all clean, 1 at least one conflict, 2 could not run.

Why the PUSH hook rather than the commit hook
---------------------------------------------
A merge conflict is a property of the PAIR (this branch, that remote tip). It
changes when the other side moves, not when you type, so judging it per commit
re-answers the same question on every keystroke and still misses the drift that
happens between two commits. The push is the moment the branch meets the remote,
and it is the last moment a conflict can be surfaced by THIS machine: a hook
that runs there costs one `merge-tree`, entirely in the object database.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

GIT = shutil.which("git") or "git"


def run(args, cwd, timeout=120):
    try:
        p = subprocess.run([GIT] + args, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout


def first_line(text, limit=200):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def local_branches(repo):
    rc, out, _ = run(["for-each-ref", "--format=%(refname:short)", "refs/heads"], repo)
    if rc != 0:
        return []
    return [b.strip() for b in out.splitlines() if b.strip()]


def resolve(repo, ref):
    rc, out, err = run(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], repo)
    return (out.strip(), "") if rc == 0 else ("", first_line(err) or "cannot resolve " + ref)


def ahead_behind(repo, base, branch):
    rc, out, _ = run(["rev-list", "--left-right", "--count", "%s...%s" % (base, branch)], repo)
    if rc != 0:
        return None, None
    parts = out.split()
    if len(parts) != 2:
        return None, None
    return int(parts[1]), int(parts[0])  # ahead, behind


def is_ancestor(repo, maybe_ancestor, descendant):
    rc, _out, _err = run(["merge-base", "--is-ancestor", maybe_ancestor, descendant], repo)
    return rc == 0


def merge_tree_detail(repo, base, branch):
    """`merge_tree`, plus the tree OID -- the chain in `merge_queue` needs it.

    A merge queue is not N independent merges: once branch 1 lands, branch 2 is
    judged against a tree that already CONTAINS branch 1, and two branches that
    are each clean against the base can still conflict with each other. Chaining
    means feeding the produced tree into the next merge, which needs the OID as a
    value and not as a truncated message.
    """
    rc, out, err = run(["merge-tree", "--write-tree", "--name-only", "--no-messages",
                        base, branch], repo)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    tree = lines[0].strip() if lines else ""
    if rc == 0:
        return {"state": "CLEAN", "paths": [], "tree": tree,
                "evidence": "tree %s" % tree[:12]}
    if rc == 1 and len(lines) > 1:
        # first line is the (conflict-marker-carrying) tree OID, then the paths
        paths = [ln.strip() for ln in lines[1:]]
        return {"state": "CONFLICTS", "paths": paths, "tree": tree,
                "evidence": "%d conflicted path(s)" % len(paths)}
    if rc == 1:
        # rc=1 with nothing after the tree line is not a conflict report: a
        # real one names the paths it could not merge. Measured 2026-11-01 on a
        # 40-hex sha that is not in the object database -- `merge-tree` exits 1
        # with a message and no paths, and calling that CONFLICTS reports work
        # to resolve that does not exist.
        return {"state": "ERROR", "paths": [], "tree": "",
                "evidence": (first_line(err) or first_line(out)
                             or "merge-tree rc=1 with no conflicted paths")}
    return {"state": "ERROR", "paths": [], "tree": "",
            "evidence": first_line(err) or first_line(out) or "merge-tree rc=%d" % rc}


def merge_tree(repo, base, branch):
    """(state, conflicted_paths, evidence) using git merge-tree --write-tree."""
    detail = merge_tree_detail(repo, base, branch)
    return detail["state"], detail["paths"], detail["evidence"]


def gate(repo, base_ref, branches):
    base_sha, base_err = resolve(repo, base_ref)
    if not base_sha:
        return {"base": base_ref, "error": base_err, "branches": []}

    rows = []
    for br in branches:
        sha, err = resolve(repo, br)
        if not sha:
            rows.append({"branch": br, "state": "ERROR", "evidence": err,
                         "ahead": None, "behind": None, "paths": []})
            continue
        if sha == base_sha:
            rows.append({"branch": br, "state": "SAME", "evidence": "tip == base",
                         "ahead": 0, "behind": 0, "paths": []})
            continue
        ahead, behind = ahead_behind(repo, base_sha, sha)
        # MERGED must be tested BEFORE the ahead==0 shortcut: a merged branch has
        # zero commits ahead by definition, so an ahead-first ordering makes the
        # MERGED state unreachable and reports every merged branch as merely
        # "contained" (caught by test_gate_merged_branch_is_reported_merged).
        if is_ancestor(repo, sha, base_sha):
            rows.append({"branch": br, "state": "MERGED",
                         "evidence": "branch tip is an ancestor of base",
                         "ahead": ahead, "behind": behind, "paths": []})
            continue
        if ahead == 0:
            # Unreachable in practice -- "nothing ahead" means the tip is reachable
            # from base, which is the MERGED test above -- but reaching here means
            # the two commands disagreed, and that is worth seeing rather than
            # reporting as a clean merge.
            rows.append({"branch": br, "state": "CONTAINED",
                         "evidence": "no commits ahead of base but not an ancestor (inconsistent state)",
                         "ahead": 0, "behind": behind, "paths": []})
            continue
        state, paths, evidence = merge_tree(repo, base_sha, sha)
        rows.append({"branch": br, "state": state, "evidence": evidence,
                     "ahead": ahead, "behind": behind, "paths": paths})
    return {"base": base_ref, "base_sha": base_sha, "branches": rows}


def _preview_commit(repo, tree, parents, branch):
    """Write a dangling commit for an intermediate queue tree. Returns (sha, err).

    `commit-tree` is the only way to make a tree usable as the base of the NEXT
    three-way merge, and it touches nothing that a reader can see: no ref moves,
    no index, no worktree. The objects it writes are unreachable, so `git gc`
    collects them; a queue of N branches leaves N-1 of them.
    """
    args = ["commit-tree", tree]
    for parent in parents:
        args.extend(["-p", parent])
    args.extend(["-m", "merge-queue preview: %s" % branch])
    rc, out, err = run(args, repo)
    sha = (out or "").strip().splitlines()[0] if rc == 0 and out.strip() else ""
    if not sha:
        return "", first_line(err) or first_line(out) or "commit-tree rc=%d" % rc
    return sha, ""


def merge_queue(repo, base_ref, branches):
    """Judge an ORDERED queue: each branch merged onto what the previous produced.

    This is the question a merge queue actually asks and `gate()` does not: not
    "would each of these merge into base" but "would each merge after the ones
    ahead of it". Two branches can each be CLEAN against the base and still
    conflict with each other -- measured on a two-branch fixture where both edit
    one line -- so the independent answers are not the queue's answer.

    States: CLEAN / CONFLICTS / ALREADY-IN (the branch is contained in the tree
    so far, nothing to merge) / ERROR (could not judge this step) / UNJUDGED
    (behind an ERROR: the chain stopped, so these were not tested -- never
    reported as clean).
    """
    base_sha, base_err = resolve(repo, base_ref)
    if not base_sha:
        return {"base": base_ref, "error": base_err, "branches": []}

    rows = []
    current = base_sha
    stopped = ""
    for position, branch in enumerate(branches, 1):
        if stopped:
            rows.append({"order": position, "branch": branch, "state": "UNJUDGED",
                         "into": current[:12], "paths": [],
                         "evidence": "not tested: the chain stopped at %s" % stopped})
            continue
        sha, err = resolve(repo, branch)
        if not sha:
            stopped = branch
            rows.append({"order": position, "branch": branch, "state": "ERROR",
                         "into": current[:12], "paths": [], "evidence": err})
            continue
        if is_ancestor(repo, sha, current):
            rows.append({"order": position, "branch": branch, "state": "ALREADY-IN",
                         "into": current[:12], "paths": [],
                         "evidence": "already contained in the queue so far"})
            continue
        detail = merge_tree_detail(repo, current, sha)
        row = {"order": position, "branch": branch, "state": detail["state"],
               "into": current[:12], "paths": detail["paths"],
               "evidence": detail["evidence"]}
        if detail["state"] == "CLEAN":
            nxt, c_err = _preview_commit(repo, detail["tree"], [current, sha], branch)
            if not nxt:
                row["state"] = "ERROR"
                row["evidence"] = "clean merge, but the preview commit failed: %s" % c_err
                stopped = branch
            else:
                row["preview"] = nxt[:12]
                current = nxt
        elif detail["state"] == "CONFLICTS":
            # The chain cannot continue through a conflict -- pretending the
            # branch landed would judge every later branch against a tree that
            # will never exist.
            stopped = branch
            row["evidence"] = detail["evidence"] + "; chain stopped here"
        else:
            stopped = branch
        rows.append(row)
    return {"base": base_ref, "base_sha": base_sha, "branches": rows}


ZERO_SHA = "0"


def _is_zero(sha):
    """Git's pre-push protocol writes an all-zero sha for "no such ref"."""
    return bool(sha) and not sha.strip(ZERO_SHA)


def push_rows(repo, lines):
    """One row per pushed ref, judging only the pushes that NEED a merge.

    The predicate is deliberately narrow. A fast-forward cannot conflict -- the
    local tip already contains the remote one -- so a merge-tree on it answers a
    question nobody asked and makes every ordinary push pay for a three-way
    merge. Only a push the remote would have to MERGE is tested.

    The remote sha here is the LOCALLY-KNOWN one from the remote-tracking ref.
    A hook must not fetch (network in a push hook is how pushes start hanging),
    so the row says which tip it judged rather than implying the true server
    state.
    """
    rows = []
    for raw in lines:
        parts = raw.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if _is_zero(local_sha):
            rows.append({"ref": remote_ref, "state": "DELETE", "paths": [],
                         "evidence": "ref deletion; nothing to merge"})
            continue
        if _is_zero(remote_sha):
            rows.append({"ref": local_ref, "state": "NEW", "paths": [],
                         "evidence": "no locally-known remote tip to merge with"})
            continue
        if not resolve(repo, local_sha)[0]:
            rows.append({"ref": local_ref, "state": "ERROR", "paths": [],
                         "evidence": "local commit %s is unreadable here" % local_sha[:12]})
            continue
        # The REMOTE tip is checked too, and before merge-tree: an unreadable
        # remote sha (a stale remote-tracking ref from a force-push elsewhere is
        # the normal way this happens) must read as ERROR, never as a conflict
        # against a commit this repository does not have.
        if not resolve(repo, remote_sha)[0]:
            rows.append({"ref": local_ref, "state": "ERROR", "paths": [],
                         "evidence": "remote tip %s is unreadable here (stale "
                                     "remote-tracking ref?)" % remote_sha[:12]})
            continue
        if is_ancestor(repo, remote_sha, local_sha):
            rows.append({"ref": local_ref, "state": "FAST-FORWARD", "paths": [],
                         "evidence": "local tip contains the known remote tip; no merge"})
            continue
        state, paths, evidence = merge_tree(repo, remote_sha, local_sha)
        rows.append({"ref": local_ref, "state": state, "paths": paths,
                     "evidence": "vs %s: %s" % (remote_sha[:12], evidence)})
    return rows


def read_queue_file(path):
    """(branches, error) from a queue file: one name per line, # comments ignored.

    A batch landing is an ORDER someone wrote down, and retyping it as repeated
    `--branch` flags is where the order gets lost. The parse is deliberately
    strict about nothing except emptiness: a line is a ref name, trimmed, and
    anything starting with `#` is a note. An empty file is an error, not an
    empty queue -- "nothing to judge" must never read as "nothing conflicts".
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], "%s: %s" % (path, exc)
    branches = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        branches.append(line)
    if not branches:
        return [], "%s lists no branches" % path
    return branches, ""


def print_queue(repo, base_ref, branches, show_paths=5):
    """Print one queued branch per line, in order, and return the exit status."""
    result = merge_queue(repo, base_ref, branches)
    if result.get("error"):
        print("cannot resolve base %r: %s" % (base_ref, result["error"]), file=sys.stderr)
        return 2
    print("queue: %s (%s) -> %s" % (base_ref, result["base_sha"][:12],
                                    " -> ".join(branches)))
    print("")
    width = max([len(r["branch"]) for r in result["branches"]] + [6])
    counts = {}
    for r in result["branches"]:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
        print("  %d. %-*s  %-11s  into %s  %s"
              % (r["order"], width, r["branch"], r["state"], r["into"], r["evidence"]))
        shown = r["paths"] if not show_paths else r["paths"][:show_paths]
        for path in shown:
            print("       ! %s" % path)
        if show_paths and len(r["paths"]) > show_paths:
            print("       ! ... %d more" % (len(r["paths"]) - show_paths))
    print("\n  %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())))
    # CONFLICTS first: it is the only actionable state, and a queue that both
    # conflicts and cannot be judged must not hide the conflict behind exit 2.
    if counts.get("CONFLICTS"):
        return 1
    if counts.get("ERROR") or counts.get("UNJUDGED"):
        return 2
    return 0


def pre_push(repo, stream=None, show_paths=5):
    """Exit status for a pre-push hook: 0 clear, 1 CONFLICTS, 2 could not judge."""
    rows = push_rows(repo, (stream or sys.stdin).read().splitlines())
    if not rows:
        print("  pre-push: no refs on stdin, so nothing was judged", file=sys.stderr)
        return 2
    print("base: the locally-known remote tip of each pushed ref (a hook does not fetch)")
    print("")
    width = max([len(r["ref"]) for r in rows] + [6])
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
        print("  %-*s  %-12s  %s" % (width, r["ref"], r["state"], r["evidence"]))
        shown = r["paths"] if not show_paths else r["paths"][:show_paths]
        for path in shown:
            print("      ! %s" % path)
        if show_paths and len(r["paths"]) > show_paths:
            print("      ! ... %d more" % (len(r["paths"]) - show_paths))
    print("\n  %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())))
    if counts.get("CONFLICTS"):
        return 1
    if counts.get("ERROR"):
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Non-destructive merge readiness for every local branch.")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", default="HEAD", help="base ref to merge into (default HEAD)")
    ap.add_argument("--branch", action="append", default=[],
                    help="branch to test (repeatable); default all local heads")
    ap.add_argument("--json", default="")
    ap.add_argument("--queue", action="store_true",
                    help="chain the --branch list: each branch merged onto the "
                         "tree the branches ahead of it produced")
    ap.add_argument("--queue-file", metavar="PATH",
                    help="chain the branches listed in PATH, one per line "
                         "(blank lines and # comments skipped); appended after "
                         "any --branch, and implies --queue")
    ap.add_argument("--pre-push", action="store_true",
                    help="read git's pre-push stdin protocol and judge only "
                         "the pushes that need a merge")
    ap.add_argument("--show-paths", type=int, default=5,
                    help="conflicted paths to print per branch (0 = all)")
    a = ap.parse_args(argv)

    rc, out, _ = run(["rev-parse", "--show-toplevel"], a.repo)
    if rc != 0:
        print("not a git repository: %s" % a.repo, file=sys.stderr)
        return 2
    repo = out.strip()

    if a.pre_push:
        return pre_push(repo, show_paths=a.show_paths)

    if a.queue or a.queue_file:
        ordered = list(a.branch)
        if a.queue_file:
            listed, err = read_queue_file(Path(a.queue_file))
            if err:
                print("cannot read the queue file: %s" % err, file=sys.stderr)
                return 2
            ordered.extend(listed)
        if not ordered:
            print("--queue needs an ORDER: pass --branch once per queue position, "
                  "or --queue-file with one branch name per line", file=sys.stderr)
            return 2
        return print_queue(repo, a.base, ordered, a.show_paths)

    branches = a.branch or local_branches(repo)
    result = gate(repo, a.base, branches)
    if result.get("error"):
        print("cannot resolve base %r: %s" % (a.base, result["error"]), file=sys.stderr)
        return 2

    base_sha = result["base_sha"]
    print("base: %s (%s)" % (result["base"], base_sha[:12]))
    print("")
    width = max([len(r["branch"]) for r in result["branches"]] + [6])
    counts = {}
    for r in result["branches"]:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
        ab = ""
        if r["ahead"] is not None:
            ab = "+%d/-%d" % (r["ahead"], r["behind"] or 0)
        print("  %-*s  %-10s  %-10s %s" % (width, r["branch"], r["state"], ab, r["evidence"]))
        shown = r["paths"] if a.show_paths == 0 else r["paths"][:a.show_paths]
        for path in shown:
            print("      ! %s" % path)
        if a.show_paths and len(r["paths"]) > a.show_paths:
            print("      ! ... %d more" % (len(r["paths"]) - a.show_paths))

    print("\n  %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())))
    if a.json:
        result["counts"] = counts
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
        print("  report: %s" % a.json)

    if counts.get("CONFLICTS"):
        return 1
    if counts.get("ERROR"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
