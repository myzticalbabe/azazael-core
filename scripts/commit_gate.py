#!/usr/bin/env python3
"""commit_gate.py -- bulk-diff gate for commits (2026-09-28).

Why this exists: three straight forensic-fix commits swept pre-existing
worktree dirt into their messages (260-line local_memory.py for a 6-line
hunk, dead .bats, 470-line retention files). Nothing broke, but a commit
message that describes 5 lines while carrying 400 is a lie in the ledger,
and the ledger is load-bearing here.

Two parts:
  1. `commit_gate.py --hook` (pre-commit): fail when the STAGED changeset
     exceeds bulk thresholds unless ALLOW_BULK_COMMIT=1. Small focused
     commits always pass; deliberate bulk ops (frame untracking, snapshots)
     pass by declaring intent in the env.
  1b. `commit_gate.py --hook --base REV` (push): judge `REV..HEAD` instead of
     the index. Those are different questions and neither answers the other:
     at push time the index holds whatever a concurrent lane has staged, so an
     index-scoped run refuses a push over files the commit never touched
     (measured 2026-11-03: a 2-file commit blocked by 7 forensic files staged
     by another lane). `REV..HEAD` is the commit being pushed.
  2. `commit_gate.py review`: interactive worktree review -- groups changed
     files (source vs generated), shows per-file +/- and lets the operator
     stage exactly what the commit message will describe.

Thresholds via env: COMMIT_GATE_MAX_LINES (default 150 changed lines in one
file), COMMIT_GATE_MAX_FILES (default 8 staged files).

Exit codes: 0 pass / 1 block (--hook) or user abort (review).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX_LINES = 150
DEFAULT_MAX_FILES = 8

GENERATED_SUFFIXES = (".ppm", ".png", ".log", ".jsonl", ".html")
GENERATED_DIRS = ("output/", "graphify-out/", "backup/")
#: Dated machine snapshots: state/<name>-YYYY-MM-DD.json. These are
#: regenerated whole by engines (proffer, manifests, recipe snapshots), so a
#: 300-line regeneration is the file doing its job, not hidden dirt --
#: harness improvement #4 (2026-11-06; the corporate commit's proffer
#: rewrite tripped the per-file cap). Narrow on purpose: bare state/*.json
#: and undated names stay judged like source.
STATE_SNAPSHOT_RE = re.compile(
    r"^state/[A-Za-z0-9_][A-Za-z0-9_.-]*-\d{4}-\d{2}-\d{2}\.json$")


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       timeout=60, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr[:200]))
    return r.stdout


def staged_numstat() -> list[tuple[int, int, str]]:
    """[(added, deleted, path)] for the index. Binary files report -/-."""
    rows = []
    for line in _git("diff", "--cached", "--numstat").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        try:
            added, deleted = int(parts[0]), int(parts[1])
        except ValueError:
            added, deleted = -1, -1  # binary
        rows.append((added, deleted, parts[2]))
    return rows


def staged_statuses() -> dict[str, str]:
    """path -> XY status for staged entries (A = newly created)."""
    out: dict[str, str] = {}
    for line in _git("status", "--short").splitlines():
        if len(line) > 3 and line[0] != " " and line[0] != "?":
            out[line[3:].strip()] = line[:2]
    return out


def range_numstat(base: str) -> list[tuple[int, int, str]]:
    """[(added, deleted, path)] for `base..HEAD` -- a commit, not the index."""
    rows = []
    for line in _git("diff", "--numstat", base, "HEAD").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        try:
            added, deleted = int(parts[0]), int(parts[1])
        except ValueError:
            added, deleted = -1, -1  # binary
        rows.append((added, deleted, parts[2]))
    return rows


def declares_bulk(message: str) -> bool:
    """True when a commit message carries the bulk declaration.

    AGENTS.md names the commit MESSAGE as the place to declare a bulk operation
    ("never rebaseline a scanner finding ... or declare ALLOW_BULK_COMMIT=1 in
    the message"). Pre-commit honoured only the env var, and that was enough
    while the push never re-judged the commit. Commit mode re-judges it, so a
    commit that was deliberately declared bulk would be refused on the way out
    by a gate that cannot see the declaration -- measured 2026-11-03 on
    12e47073, whose message declares it and whose 5,750-line generated ledger
    the range check then flagged.
    """
    return "ALLOW_BULK_COMMIT=1" in (message or "")


def single_commit_bulk_declaration(base: str) -> str | None:
    """The pushed commit's sha if it is exactly one commit that declares bulk.

    NARROW ON PURPOSE. The first version accepted a declaration from ANY commit
    in the range, and over a normal history every range contains one (28 of the
    last 90 commits declared something), so the range check could no longer fail
    at all -- a gate that answers PASS to everything is the defect this file
    exists to catch, not a feature. A commit message describes the commit it is
    attached to, so the declaration counts only when the range IS that commit
    (`base..HEAD` = 1). A multi-commit push has no single message describing it
    and is judged by the caps like anything else.
    """
    raw = _git("log", "--format=%H%x1f%B%x1e", "%s..HEAD" % base)
    commits = []
    for chunk in raw.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, message = chunk.partition("\x1f")
        commits.append((sha.strip(), message))
    if len(commits) != 1:
        return None
    sha, message = commits[0]
    return sha if declares_bulk(message) else None


def range_statuses(base: str) -> dict[str, str]:
    """path -> status letter for the same range (A = created by it)."""
    out: dict[str, str] = {}
    for line in _git("diff", "--name-status", base, "HEAD").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out[parts[-1].strip()] = parts[0].strip()
    return out


def worktree_status() -> list[tuple[str, str]]:
    """[(xy, path)] for worktree + index (porcelain)."""
    rows = []
    for line in _git("status", "--short").splitlines():
        if len(line) > 3:
            rows.append((line[:2].strip(), line[3:].strip()))
    return rows


def is_generated(path: str) -> bool:
    p = path.replace("\\", "/")
    if any(p.startswith(d) for d in GENERATED_DIRS):
        return True
    if STATE_SNAPSHOT_RE.match(p):
        return True
    return p.lower().endswith(GENERATED_SUFFIXES)


def evaluate(rows: list[tuple[int, int, str]], max_lines: int,
             max_files: int, created: set[str] | None = None,
             changeset: str = "staged files",
             remedy: str = "Stage a focused set, or set "
                          "ALLOW_BULK_COMMIT=1 to declare a bulk op.",
             file_remedy: str = "Split it, or set ALLOW_BULK_COMMIT=1."
             ) -> list[str]:
    """Bulk violations, human-readable. Empty means pass.

    `changeset` names what the rows are ("staged", or a `base..HEAD` range). It
    is only wording, but a refusal that says "staged" over a commit range names
    the wrong desk -- the operator goes looking for a staging mistake that does
    not exist. The count is the same either way; only the noun changes. The
    same goes for `remedy`/`file_remedy`: "stage a focused set" is not
    actionable advice about a commit that is already written, and an operator
    who follows it will find nothing to unstage.

    Newly created files are exempt from the per-file line cap: a create
    carries no hidden dirt (everything in it is new by definition), while
    a 400-line modification to a tracked file is exactly the sweep this
    gate exists to catch. Creates still count toward the file tally.

    Generated artifacts (is_generated) are exempt from BOTH tallies: the
    advisory `review` command already reports them separately, so the
    blocking path must not demand a bulk declaration for bytes no human
    wrote. Generated files cannot rescue an otherwise-blocked changeset --
    the source rows are judged exactly as if the generated rows were
    absent.
    """
    created = created or set()
    problems = []
    tracked = [r for r in rows if r[2] and not r[2].startswith("(")]
    tracked = [r for r in tracked if not is_generated(r[2])]
    if len(tracked) > max_files:
        problems.append(
            "%d %s exceed the bulk limit (%d). %s"
            % (len(tracked), changeset, max_files, remedy))
    for added, deleted, path in tracked:
        if added < 0:
            continue  # binary: counted in the file tally, not line tally
        if path in created:
            continue  # new file: nothing hidden inside
        if added + deleted > max_lines:
            problems.append(
                "%s changes %d lines (limit %d/file). %s"
                % (path, added + deleted, max_lines, file_remedy))
    return problems


def cmd_hook(base: str | None = None) -> int:
    """Judge a changeset. `base` names a commit (`base..HEAD`); None = the index.

    The two scopes are different questions. Pre-commit asks "is what I am about
    to commit too big"; a push asks "is the commit I am leaving with too big",
    and the index cannot answer the second: it also holds whatever another lane
    staged while this one worked. So a named base replaces the index rather than
    adding to it -- a run that judged both would still be refused by the
    neighbour's desk drawer, which is the defect --base exists to remove.
    """
    max_lines = int(os.environ.get("COMMIT_GATE_MAX_LINES",
                                   str(DEFAULT_MAX_LINES)))
    max_files = int(os.environ.get("COMMIT_GATE_MAX_FILES",
                                   str(DEFAULT_MAX_FILES)))
    if os.environ.get("ALLOW_BULK_COMMIT") == "1":
        print("commit-gate: bulk declared via ALLOW_BULK_COMMIT=1, passing")
        return 0
    scope = "%s..HEAD" % base if base else "the staged index"
    if base:
        try:
            declared = single_commit_bulk_declaration(base)
        except Exception as exc:  # noqa: BLE001 - a named range must be readable
            print("commit-gate: cannot read the messages of %s (%s) -- refusing "
                  "to pass on a range that was not read" % (scope, exc))
            return 1
        if declared:
            print("commit-gate: bulk declared in the message of %s, passing "
                  "(%s)" % (declared[:8], scope))
            return 0
    try:
        if base:
            rows = range_numstat(base)
            statuses = range_statuses(base)
        else:
            rows = staged_numstat()
            statuses = staged_statuses()
    except Exception as exc:
        if base:
            # A NAMED base that cannot be read is a caller error, not a broken
            # gate: passing loud here would go green on a range never read (a
            # typo, or a ref this clone does not have).
            print("commit-gate: cannot read %s (%s) -- refusing to pass on a "
                  "range that was not read" % (scope, exc))
            return 1
        # A broken gate must never block all commits; be loud instead.
        print("commit-gate: cannot read staged diff (%s) -- passing loud" % exc)
        return 0
    if not rows:
        if base:
            # An empty range is a wrong base far more often than a clean one.
            print("commit-gate: %s is empty -- nothing to judge" % scope)
        return 0
    created = {p for p, s in statuses.items() if "A" in s}
    gen_count = len([r for r in rows if r[2] and is_generated(r[2])])
    if gen_count:
        print("commit-gate: %d generated file(s) in %s -- exempt from bulk "
              "caps (review: python scripts/commit_gate.py review)"
              % (gen_count, scope))
    if base:
        problems = evaluate(
            rows, max_lines, max_files, created,
            changeset="files in %s..HEAD" % base,
            remedy="Declare the bulk op in the message of the single commit "
                   "being pushed (a commit message is the surface this gate "
                   "reads), or push the commits separately.",
            file_remedy="Split the commit, or declare the bulk op in its "
                        "message.")
    else:
        problems = evaluate(rows, max_lines, max_files, created)
    if not problems:
        return 0
    print("commit-gate: BULK CHANGESET BLOCKED")
    for p in problems:
        print("  - " + p)
    print("  Review with: %s" % ("git show --stat %s..HEAD" % base if base
                                 else "python scripts/commit_gate.py review"))
    return 1


def cmd_review() -> int:
    rows = worktree_status()
    if not rows:
        print("worktree clean")
        return 0
    src = [(s, p) for s, p in rows if not is_generated(p)]
    gen = [(s, p) for s, p in rows if is_generated(p)]
    print("=== source (%d) ===" % len(src))
    for s, p in src:
        print("  [%s] %s" % (s or "?", p))
    if gen:
        print("=== generated (%d, never stage these) ===" % len(gen))
        for s, p in gen[:15]:
            print("  [%s] %s" % (s or "?", p))
        if len(gen) > 15:
            print("  ... and %d more" % (len(gen) - 15))
    print()
    print("Stage exactly what your message will describe, e.g.:")
    print("  git add <file> ... && git commit -m \"...\"")
    print("Bulk ops: ALLOW_BULK_COMMIT=1 git commit ...")
    return 0


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    base = None
    if "--base" in args:
        at = args.index("--base")
        if at + 1 >= len(args):
            print("commit_gate.py: --base needs a revision")
            return 2
        base = args[at + 1]
        del args[at:at + 2]
    if "--hook" in args:
        return cmd_hook(base)
    if "review" in args:
        return cmd_review()
    print(__doc__.strip().splitlines()[0])
    print("usage: commit_gate.py [--hook [--base REV] | review]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
