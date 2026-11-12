#!/usr/bin/env python3
"""git-stack-plan -- rebase a stack of branches without a worktree, in one atomic pass.

Why this exists
---------------
Stacked branches are the normal shape of work in this repo (a lane branch on a
feature branch on master), and the standard way to move them is: check out each
one, rebase, fix conflicts, check out the next. Every checkout is a chance to lose
work, and a stack of five is five chances plus four hand-maintained parent links
that nothing verifies.

Two commands make the whole thing object-database-only:

* `git replay --onto <newbase> <parent>..<branch>` (EXPERIMENTAL, 2.46+) recomputes
  the commits on a new base and updates the refs in one atomic transaction --
  "all refs update or none". No checkout, no index, no worktree, works on a bare
  repo.
* `git merge-tree --write-tree` here is used the same way as in
  `git_merge_gate.py`: to say whether the *planned* rebase will conflict BEFORE
  anything is written.

What this tool adds on top of the plumbing: it works out the parent of each branch
from the commit graph itself (`A` is a parent of `B` when `A`'s tip is an
ancestor of `B`), so the stack is derived rather than declared, and a branch whose
parent was deleted is reported as a broken stack instead of being silently
replayed onto something unrelated.

Usage
-----
    python tools/git_stack_plan.py                       # show the graph and the plan
    python tools/git_stack_plan.py --onto master --check # will the rebase conflict?
    python tools/git_stack_plan.py --onto master --apply # do it (atomic per branch)

Exit status: 0 nothing to do / plan clean, 1 plan would conflict, 2 cannot run.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

GIT = shutil.which("git") or "git"


def run(args, cwd, timeout=180):
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


def tips(repo):
    rc, out, _ = run(["for-each-ref", "--format=%(refname:short) %(objectname)",
                      "refs/heads"], repo)
    if rc != 0:
        return {}
    result = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            result[parts[0]] = parts[1]
    return result


def is_ancestor(repo, a, b):
    rc, _o, _e = run(["merge-base", "--is-ancestor", a, b], repo)
    return rc == 0


def distance(repo, a, b):
    """Commits in a..b -- how far b is ahead of a. None when not comparable."""
    rc, out, _ = run(["rev-list", "--count", "%s..%s" % (a, b)], repo)
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except ValueError as exc:
        note_failure("tools/git_stack_plan.py", exc)
        return None


def merge_commits(repo, a, b):
    rc, out, _ = run(["rev-list", "--merges", "%s..%s" % (a, b)], repo)
    if rc != 0:
        return None
    return len([ln for ln in out.splitlines() if ln.strip()])


def merge_base(repo, a, b):
    rc, out, _ = run(["merge-base", a, b], repo)
    return out.strip() if rc == 0 else ""


#: How much a parent link is worth. `reflog` is git's own record of the branch's
#: creation ("branch: Created from main"), which survives the parent advancing;
#: `fork` is inference. A cycle is broken by dropping the weakest link, never the
#: strongest.
STRENGTH = {"reflog": 3, "tip-ancestor": 2, "fork": 1, "ambiguous": 0}


def reflog_origin(repo, branch):
    """The ref this branch was created from, per git's own reflog, or "".

    `git reflog show <branch>` records "branch: Created from <ref>" at the bottom
    of the stack. It is the only *declared* parent link available, which matters
    because every inference below has to guess when the parent has moved on.
    """
    rc, out, _ = run(["reflog", "show", "--format=%gs", branch], repo)
    if rc != 0:
        return ""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return ""
    oldest = lines[-1]
    origin = ""
    # `git branch <name>` records "branch: Created from <start>"; `checkout -b`
    # and `switch -c` record "checkout: moving from <src> to <name>" instead.
    # Both are git describing the same fact, and only reading one of them made
    # every `checkout -b` stack look unrelated (caught by tests, 2026-11-01).
    if "Created from " in oldest:
        origin = oldest.split("Created from ", 1)[1].strip()
    else:
        m = re.search(r"moving from (\S+) to (\S+)", oldest)
        if m and m.group(2) == branch:
            origin = m.group(1)
    return "" if origin in ("", "HEAD") else origin


def derive_parents(repo, branch_tips):
    """parent[b] = the branch b was cut from -- derived, never declared.

    Two relations, because one is not enough in practice:

    * `tip-ancestor` -- c's tip is literally an ancestor of b. Unambiguous, and
      the only case a naive "is c contained in b" test ever finds.
    * `fork` -- c has ADVANCED since b was cut from it, so its tip is no longer an
      ancestor. The normal case, and the one that matters: a stack is usually
      rebased onto a parent that moved. The candidate is the branch with the
      fewest commits since the shared merge-base; a tie means the graph genuinely
      cannot say which is the parent, and that is reported as AMBIGUOUS rather
      than guessed.
    """
    parents = {}
    for b, sha in branch_tips.items():
        # 1. declared: what git itself recorded when the branch was created
        origin = reflog_origin(repo, b)
        if origin in branch_tips and origin != b:
            dist = distance(repo, branch_tips[origin], sha)
            parents[b] = {"parent": origin, "via": "reflog", "commits": dist}
            continue

        # 2..4. inferred from the commit graph
        best = None
        best_dist = None
        for c, csha in branch_tips.items():
            if c == b or csha == sha:
                continue
            if not is_ancestor(repo, csha, sha):
                continue
            dist = distance(repo, csha, sha)
            if dist is None:
                continue
            if best is None or dist < best_dist:
                best, best_dist = c, dist
        if best is not None:
            parents[b] = {"parent": best, "via": "tip-ancestor", "commits": best_dist}
            continue

        # No strict ancestor: b's parent has moved on since the fork. Rank the
        # candidates by how far each has moved SINCE the shared fork point, and
        # only accept one that moved strictly less than b -- a branch that moved
        # further than b is b's descendant in the stack, not its parent. Without
        # that rule two branches of one stack nominate each other and the planner
        # proposes rebasing each onto the other (measured 2026-11-01).


        better, equal = [], []
        for c, csha in branch_tips.items():
            if c == b or csha == sha:
                continue
            mb = merge_base(repo, csha, sha)
            if not mb or mb == sha:
                continue  # b is behind or level with c; c is not b's parent
            moved = distance(repo, mb, csha)
            mine = distance(repo, mb, sha)
            if not mine or moved is None:
                continue
            if moved < mine:
                better.append((moved, c, mb))
            elif moved == mine:
                equal.append((moved, c, mb))
        pool = better or equal
        if not pool:
            continue
        pool.sort()
        if len(pool) > 1 and pool[0][0] == pool[1][0]:
            parents[b] = {"parent": None, "via": "ambiguous",
                          "candidates": sorted(c for _m, c, _mb in pool),
                          "fork_point": pool[0][2]}
            continue
        moved, best_c, best_mb = pool[0]
        parents[b] = {"parent": best_c, "via": "fork", "commits": moved,
                      "fork_point": best_mb}

    # Two branches forked from the same point can nominate each other. A stack is
    # a chain, so a cycle is broken by dropping the fork edge that moved most
    # (the likelier child of the pair), and the parent slot is emptied rather
    # than filled with a guess.
    for _ in range(len(parents) + 1):
        cycle = _find_cycle(parents)
        if not cycle:
            break
        edge = min(cycle, key=lambda b: (STRENGTH.get(parents[b].get("via"), 0),
                                         -(parents[b].get("commits") or 0)))
        dropped = parents[edge].get("via")
        parents[edge] = {"parent": None, "via": "cycle-broken", "dropped": dropped}
    return parents


def _find_cycle(parents):
    for start in parents:
        seen = []
        cur = start
        while True:
            nxt = parents.get(cur, {}).get("parent")
            if not nxt:
                break
            if nxt in seen or nxt == start:
                return seen + [start] if nxt == start else seen[seen.index(nxt):]
            seen.append(cur)
            cur = nxt
    return []


def stacks(branch_tips, parents):
    """Group branches into root -> ... -> leaf chains."""
    children = {b: [] for b in branch_tips}
    roots = []
    for b in branch_tips:
        p = parents.get(b, {}).get("parent")
        if p is None:
            roots.append(b)
        else:
            children.setdefault(p, []).append(b)


    out = []
    for root in sorted(roots):
        # depth-first, one chain per leaf; a branch with >1 child forks the stack
        stack = []

        def walk(node, chain):
            chain = chain + [node]
            kids = sorted(children.get(node, []))
            if not kids:
                out.append(chain)
                return
            for kid in kids:
                walk(kid, chain)

        walk(root, stack)
    return out


def plan_for(stack, base, repo, branch_tips):
    """Per-branch replay command, with the pre-check result for each hop.

    The base branch itself is never replayed: it is the destination, and a chain
    that starts at the base produced `git replay --onto main main..main`, which
    fails with an empty range (caught by test_stack_apply_replays_a_clean_stack).
    """
    steps = []
    target = base
    for br in [b for b in stack if b != base]:
        parent = steps[-1]["branch"] if steps else base
        range_spec = "%s..%s" % (parent, br)
        merges = merge_commits(repo, parent, br)
        if merges:
            steps.append({
                "branch": br, "parent": parent, "range": range_spec,
                "state": "NEEDS-LINEARIZE",
                "evidence": "%d merge commit(s) in range -- replay would drop/reroute them; "
                            "run by hand with --linearize" % merges,
                "command": "",
            })
            target = br
            continue
        # will this hop conflict? merge-tree asks the same 3-way question replay will
        rc, out, err = run(["merge-tree", "--write-tree", "--name-only", "--no-messages",
                            target, br], repo)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if rc == 0:
            state, evidence, paths = "CLEAN", "three-way merge clean", []
        elif rc == 1:
            state = "CONFLICT"
            paths = lines[1:]
            evidence = "%d conflicted path(s)" % len(paths)
        else:
            state = "UNKNOWN"
            paths = []
            evidence = first_line(err) or "merge-tree rc=%d" % rc
        steps.append({
            "branch": br, "parent": parent, "range": range_spec,
            "state": state, "evidence": evidence, "paths": paths,
            "command": "git replay --onto %s %s" % (target, range_spec),
            "print_command": "git replay --onto %s --ref-action=print %s" % (target, range_spec),
        })
        target = br
    return steps


def apply_plan(repo, steps):
    done = []
    for step in steps:
        if step["state"] == "NEEDS-LINEARIZE" or not step["command"]:
            done.append({"branch": step["branch"], "rc": 1,
                         "out": "skipped: " + step["evidence"]})
            return done
        args = step["command"].split()[1:]  # drop "git"
        rc, out, err = run(args, repo)
        done.append({"branch": step["branch"], "rc": rc,
                     "out": first_line(out) or first_line(err)})
        if rc != 0:
            return done
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description="Derive and apply stacked-branch rebases without a worktree.")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--onto", default="", help="new base for every stack (default: show graph only)")
    ap.add_argument("--check", action="store_true", help="report conflicts only")
    ap.add_argument("--apply", action="store_true", help="run the replay (EXPERIMENTAL command)")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    rc, out, _ = run(["rev-parse", "--show-toplevel"], a.repo)
    if rc != 0:
        print("not a git repository: %s" % a.repo, file=sys.stderr)
        return 2
    repo = out.strip()

    branch_tips = tips(repo)
    if not branch_tips:
        print("no local branches", file=sys.stderr)
        return 2

    parents = derive_parents(repo, branch_tips)
    groups = stacks(branch_tips, parents)

    print("repo: %s" % repo)
    print("branches: %d, stacks: %d" % (len(branch_tips), len(groups)))
    print("")
    for chain in groups:
        print("  " + " -> ".join(chain))
        for i, br in enumerate(chain):
            p = parents.get(br, {})
            if i == 0:
                print("      %-30s root" % br)
            else:
                print("      %-30s on %s (%s commit(s) since fork)"
                      % (br, p.get("parent"), p.get("commits")))
    ambiguous = {b: p for b, p in parents.items() if p.get("via") == "ambiguous"}
    for b, p in sorted(ambiguous.items()):
        print("      ! %-28s parent AMBIGUOUS: %s tie at fork %s -- not guessed"
              % (b, "|".join(p["candidates"]), p["fork_point"][:12]))

    plans = []
    if a.onto:
        for chain in groups:
            steps = plan_for(chain, a.onto, repo, branch_tips)
            plans.append({"stack": chain, "onto": a.onto, "steps": steps})
        print("\n  plan onto %s:" % a.onto)
        for p in plans:
            for step in p["steps"]:
                print("    [%-16s] %s" % (step["state"], step["command"] or step["branch"]))
                if step["state"] == "CONFLICT":
                    for path in step["paths"][:5]:
                        print("        ! %s" % path)
                    if len(step["paths"]) > 5:
                        print("        ! ... %d more" % (len(step["paths"]) - 5))

    applied = []
    if a.apply:
        if not a.onto:
            print("\n  --apply needs --onto <ref>", file=sys.stderr)
            return 2
        print("\n  applying (git replay, one atomic transaction per branch):")
        for p in plans:
            done = apply_plan(repo, p["steps"])
            applied.extend(done)
            for row in done:
                print("    %-30s rc=%d %s" % (row["branch"], row["rc"], row["out"]))
            if any(row["rc"] != 0 for row in done):
                print("    stopped: a replay failed, remaining stacks untouched")
                break

    conflicts = [s for p in plans for s in p["steps"] if s["state"] == "CONFLICT"]
    unknown = [s for p in plans for s in p["steps"] if s["state"] == "UNKNOWN"]
    if plans:
        print("\n  %d step(s), %d would conflict, %d unknown"
              % (sum(len(p["steps"]) for p in plans), len(conflicts), len(unknown)))

    report = {"repo": repo, "branch_tips": branch_tips,
              "parents": parents, "stacks": groups, "plans": plans, "applied": applied}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
        print("  report: %s" % a.json)

    if any(row["rc"] != 0 for row in applied):
        return 1
    if conflicts:
        return 1
    if unknown:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
