#!/usr/bin/env python3
"""head_audit.py -- HED-12: every head, and the gap it is carrying.

A "head" is where work is thought to be. This tree has four kinds, and each one
can be wrong independently:

  * a **local branch** -- may be behind its upstream, or hold commits that exist
    on no other ref (so a `git branch -D` is a deletion, not a shortcut).
  * a **remote-tracking ref** -- may be ahead of the local branch that is
    supposed to track it, so the lane is working on a tree that is missing work
    it already published.
  * a **worktree** -- may be detached at a hash that no branch names (the commit
    survives only in the reflog), may be prunable (the gitdir is gone), or may
    carry uncommitted edits that no ref can recover.
  * a **lane instruction file** -- `state/head.json` records a digest for each
    one it saw; a file whose bytes no longer match that digest has changed since
    the reading, which is how a lane starts obeying an order nobody regenerated.

None of these raises an exception, and each one silently makes a later session's
premise false -- the PRECEDENCE rule in AGENTS.md says a stale number is a defect
to record, and this is the recorder for the numbers that live in git.

Measured 2026-11-05 on this repository, first run: 5 local heads, 2 remote refs,
6 worktrees (3 prunable, 2 detached), 7 lane heads, and the lane branch diverged
from its own published head by +6/-8 commits.

Exit: 0 when clean or advisory (the default -- a gate red on day one is a gate
switched off), 1 when clean is required (--strict) and a gap is open, 2 when the
audit could not be performed (not a git repository). Two is not a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _setting(key: str, fallback):
    """`config/defaults.json` through the shared reader, with a constant fallback."""
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import defaults_profile            # noqa: PLC0415
        return defaults_profile.read_default(key, fallback)
    except Exception:
        return fallback

# One sha width, one place. The ref table, the worktree table and the gap detail
# all name the same commits; when they abbreviated differently (8 here, 12
# there) a reader comparing two tables could not tell a real difference from a
# formatting one. 12 is the shortest width that is unambiguous in this history.
SHA_W = 12
HEAD_STATE = ROOT / "state" / "head.json"


def _git(cwd: Path, *args: str, strip: bool = True) -> tuple[int, str]:
    """(exit code, stdout) for one git read. Never raises; exit 2 says why.

    `strip=False` exists because stripping is not free: `status --porcelain`
    starts its first line with a SPACE when the file is modified but unstaged,
    and `.strip()` removed it, so ` M logs/anti_pattern_baseline.json` came back
    as `M logs/...` and the path was read as `ogs/...` -- a wrong reading that
    looked like a strange file name. Measured 2026-11-05 on the first run.
    """
    try:
        proc = subprocess.run(["git", "-C", str(cwd), *args],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, "%s: %s" % (type(exc).__name__, exc)
    if strip:
        return proc.returncode, (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return proc.returncode, proc.stdout or ""


def _count(cwd: Path, *args: str) -> int | None:
    """A rev-list count, or None when git could not answer -- None is not 0."""
    code, out = _git(cwd, "rev-list", "--count", *args)
    if code != 0:
        return None
    count = None
    try:
        count = int(out.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        # git printed something that is not a count. The reading failed, so it
        # stays None and the caller reports `unmeasured` -- never 0, which would
        # read as "no exclusive commits" and is the opposite of the truth.
        # Printed whole, never sliced: a diagnostic that cuts its own evidence
        # is the defect this tool exists to find. rev-list --count emits one
        # short line, so there is no size to bound.
        print("head_audit: unreadable rev-list count (%s): %r"
              % (type(exc).__name__, out))
    return count


def digest(path: Path) -> str:
    """sha256 of the bytes, first 16 hex. Mirror of head_state.digest()."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def refs(root: Path) -> list:
    """Every head-bearing ref, with its own sha and date.

    Tags are excluded on purpose: a tag is an anchor, not a head, and counting it
    as one would let a lane claim a commit is "on a head" when only a tag names
    it. `refs/stash` is excluded for the same reason -- it survives a branch
    deletion but is not work anyone tracks.
    """
    fmt = "%(refname)|%(objectname)|%(committerdate:iso8601)"
    code, out = _git(root, "for-each-ref", "--format=" + fmt)
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        name, sha, date = parts[0], parts[1], parts[2]
        if name.startswith("refs/tags/") or name == "refs/stash":
            continue
        if name.startswith("refs/remotes/") and name.endswith("/HEAD"):
            continue
        if name.startswith("refs/heads/"):
            kind = "local"
        elif name.startswith("refs/remotes/"):
            kind = "remote"
        else:
            kind = "other"
        rows.append({"ref": name, "kind": kind, "sha": sha, "date": date})
    return rows


def worktrees(root: Path) -> list:
    """Parse `git worktree list --porcelain` into one record per worktree."""
    code, out = _git(root, "worktree", "list", "--porcelain")
    if code != 0:
        return []
    rows, current = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            if current:
                rows.append(current)
            current = {"path": line[len("worktree "):], "head": "", "branch": "",
                       "detached": False, "prunable": ""}
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]
        elif line.strip() == "detached":
            current["detached"] = True
        elif line.startswith("prunable"):
            current["prunable"] = line[len("prunable"):].strip() or "no reason given"
    if current:
        rows.append(current)
    return rows


def dirty_paths(path: Path) -> list | None:
    """Every untracked/modified/staged path in one worktree; None when unreadable.

    A count is not enough here: `28778 uncommitted path(s)` says a worktree is
    dirty and says nothing about which work is at risk. The caller keeps the full
    list in the report and prints it grouped, so no row is hidden.
    """
    if not Path(path).is_dir():
        return None
    # `-z` is not cosmetic: porcelain v1 QUOTES a path containing a space or a
    # non-ASCII byte, so grouping by first component produced keys like
    # "archived (opening quote included) on the first run.
    code, out = _git(Path(path), "status", "--porcelain", "-z", strip=False)
    if code != 0:
        return None
    entries = [entry for entry in out.split("\0") if entry]
    names, skip_next = [], False
    for entry in entries:
        if skip_next:
            # A rename/copy carries its source path in the next NUL field.
            skip_next = False
            continue
        status, name = entry[:2], entry[3:]
        names.append(name)
        if "R" in status or "C" in status:
            skip_next = True
    return names


def audit_refs(root: Path, rows: list, findings: list) -> None:
    """Divergence against upstream, and commits no other ref can recover."""
    by_sha = {}
    for row in rows:
        by_sha.setdefault(row["sha"], []).append(row["ref"])
    for row in rows:
        name = row["ref"]
        shared = len(by_sha[row["sha"]]) > 1
        others = [sha for sha in by_sha if sha != row["sha"]]
        if shared:
            # Two refs at one commit are the SAME work, so neither holds anything
            # exclusive. Measured 2026-11-05: the first version excluded only
            # other SHAs and reported 30 exclusive commits on `main`, `origin/main`
            # and `refs/prefetch/remotes/origin/main` at once -- three copies of
            # one reading, all wrong in the alarming direction.
            exclusive = 0
        elif others:
            exclusive = _count(root, row["sha"], "--not", *others)
        else:
            exclusive = None
        if exclusive is None and others:
            findings.append({"class": "unmeasured", "head": name,
                             "detail": "rev-list could not count exclusive commits"})
        row["exclusive"] = exclusive

        if row["kind"] == "local":
            row["upstream_config"] = True
            # `@{upstream}` takes a SHORT name: `refs/heads/main@{upstream}` exits
            # 128 with "no such branch", which read as "no upstream configured"
            # for all five branches on the first run -- including `main`, whose
            # upstream `origin/main` was right there in for-each-ref.
            short = name[len("refs/heads/"):]
            code, upstream = _git(root, "rev-parse", "--abbrev-ref",
                                  "%s@{upstream}" % short)
            row["upstream"] = upstream if code == 0 else ""
            if not row["upstream"]:
                findings.append({"class": "no_upstream", "head": name,
                                 "detail": "branch.%s.remote is unset: push and pull "
                                           "have no default, so nothing else records "
                                           "where this head is published" % short})
                guess = "origin/%s" % short
                code, _ = _git(root, "rev-parse", "--verify", "--quiet",
                               "refs/remotes/%s" % guess)
                if code == 0:
                    row["upstream"] = guess
                    row["upstream_config"] = False
            if not row["upstream"]:
                continue
            ahead = _count(root, "%s..%s" % (row["upstream"], name))
            behind = _count(root, "%s..%s" % (name, row["upstream"]))
            row["ahead"], row["behind"] = ahead, behind
            if ahead is None or behind is None:
                findings.append({"class": "unmeasured", "head": name,
                                 "detail": "could not compare with %s" % row["upstream"]})
                continue
            if ahead and behind:
                findings.append({"class": "diverged", "head": name,
                                 "detail": "%d commit(s) not on %s, %d on %s not here "
                                           "-- the published head and this one disagree"
                                           % (ahead, row["upstream"], behind,
                                              row["upstream"])})
            elif behind:
                findings.append({"class": "behind_upstream", "head": name,
                                 "detail": "missing %d commit(s) from %s"
                                           % (behind, row["upstream"])})
            elif ahead:
                findings.append({"class": "unpushed", "head": name,
                                 "detail": "%d commit(s) exist only here (upstream at %s)"
                                           % (ahead, row["upstream"])})

        if exclusive:
            findings.append({"class": "exclusive_commits", "head": name,
                             "detail": "%d commit(s) reachable from no other ref -- "
                                       "deleting this head loses them" % exclusive})


def _dirty_detail(paths: list) -> str:
    """Name every dirty path: inline when few, grouped by top-level when many."""
    if len(paths) <= 8:
        return "%d uncommitted path(s): %s" % (len(paths), ", ".join(paths))
    groups = {}
    for name in paths:
        top = name.split("/")[0] if "/" in name else "(root files)"
        groups[top] = groups.get(top, 0) + 1
    shown = ", ".join("%s=%d" % kv for kv in sorted(groups.items()))
    return ("%d uncommitted path(s) across %d top-level group(s): %s -- every path "
            "is in the report's dirty_paths" % (len(paths), len(groups), shown))


def audit_worktrees(root: Path, rows: list, findings: list) -> None:
    """Detached, prunable, and dirty worktrees."""
    for row in rows:
        paths = dirty_paths(Path(row["path"]))
        row["dirty"] = None if paths is None else len(paths)
        row["dirty_paths"] = paths or []
        if row["prunable"]:
            findings.append({"class": "prunable_worktree", "head": row["path"],
                             "detail": "gitdir is gone (%s) -- registration only; "
                                       "`git worktree prune` clears it" % row["prunable"]})
        if row["detached"]:
            # Detached is only alarming when NO ref contains the commit. A
            # detached worktree sitting on a published commit is routine; one
            # sitting on a commit no ref names survives only in the reflog, and
            # `git gc` eventually takes it.
            code, containing = _git(root, "for-each-ref", "--contains", row["head"],
                                    "--format=%(refname)") if row["head"] else (2, "")
            refs_at = [line for line in containing.splitlines() if line.strip()]
            row["contained_by"] = refs_at
            if code != 0:
                findings.append({"class": "unmeasured", "head": row["path"],
                                 "detail": "could not tell whether any ref contains %s"
                                           % row["head"][:SHA_W]})
            elif refs_at:
                findings.append({"class": "detached_worktree", "head": row["path"],
                                 "detail": "HEAD %s is on no branch; %d ref(s) still "
                                           "contain it (%s), so nothing is at risk"
                                           % (row["head"][:SHA_W], len(refs_at),
                                              ", ".join(refs_at))})
            else:
                findings.append({"class": "orphaned_worktree_head", "head": row["path"],
                                 "detail": "HEAD %s is on no branch and in NO ref -- it "
                                           "survives only in the reflog, and `git gc` "
                                           "will take it" % row["head"][:SHA_W]})
        if paths:
            findings.append({"class": "dirty_worktree", "head": row["path"],
                             "detail": _dirty_detail(paths)})


def audit_lane_heads(state: dict, findings: list) -> list:
    """Does each instruction file still hash to what the reading recorded?"""
    rows = []
    for path_text, recorded in sorted((state.get("lane_prompts") or {}).items()):
        path = Path(path_text)
        if not path.is_file():
            rows.append({"path": path_text, "recorded": recorded, "actual": "",
                         "state": "missing"})
            findings.append({"class": "lane_head_missing", "head": path_text,
                             "detail": "recorded by state/head.json but absent now"})
            continue
        actual = digest(path)
        ok = actual == recorded
        rows.append({"path": path_text, "recorded": recorded, "actual": actual,
                     "state": "match" if ok else "drift"})
        if not ok:
            findings.append({"class": "lane_head_drift", "head": path_text,
                             "detail": "sha256:%s recorded, sha256:%s on disk -- the "
                                       "lane is obeying bytes nobody re-read"
                                       % (recorded, actual)})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT), help="repository to audit")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any gap is open")
    ap.add_argument("--json", default="", help="also write the full audit here")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    code, top = _git(root, "rev-parse", "--show-toplevel")
    if code != 0:
        print("head_audit: %s is not a git work tree (%s). Nothing audited -- that "
              "is not a pass." % (root, top))
        return 2

    findings, refs_rows = [], refs(root)
    audit_refs(root, refs_rows, findings)
    wt_rows = worktrees(root)
    audit_worktrees(root, wt_rows, findings)

    state, state_gap = {}, ""
    if HEAD_STATE.is_file():
        try:
            state = json.loads(HEAD_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            state_gap = "%s: %s" % (type(exc).__name__, exc)
    else:
        state_gap = "absent"
    if state_gap:
        findings.append({"class": "stale_head_state", "head": "state/head.json",
                         "detail": "%s -- the head numbers must be re-measured" % state_gap})
    elif state.get("head"):
        code, live = _git(root, "rev-parse", "HEAD")
        if code == 0 and live != state["head"]:
            findings.append({"class": "stale_head_state", "head": "state/head.json",
                             "detail": "records %s, HEAD is %s"
                                       % (state["head"][:SHA_W], live[:SHA_W])})
    # A state file can agree with HEAD and still be a stale reading: the numbers
    # in it (test count, skill count, gate verdict) were measured on some day, and
    # a month-old file answers for a tree it no longer describes. The floor is a
    # knob because the right age differs per lane -- 0 keeps it off, which is the
    # behaviour every existing caller was written against.
    limit_days = float(_setting("head_audit.max_state_age_days", 0))
    if limit_days > 0 and not state_gap:
        try:
            age_days = (time.time() - HEAD_STATE.stat().st_mtime) / 86400.0
        except OSError as exc:
            findings.append({"class": "stale_head_state", "head": "state/head.json",
                             "detail": "mtime unreadable (%s) -- the reading cannot "
                                       "be dated" % exc})
        else:
            if age_days > limit_days:
                findings.append({
                    "class": "stale_head_state", "head": "state/head.json",
                    "detail": "%.1f day(s) old, floor %.1f -- re-run "
                              "tools/head_state.py --write" % (age_days, limit_days)})
    lane_rows = audit_lane_heads(state, findings)

    by_class = {}
    for row in findings:
        by_class[row["class"]] = by_class.get(row["class"], 0) + 1

    print("head_audit: %d ref(s) [%s], %d worktree(s), %d lane head(s), "
          "%d gap(s) [%s]"
          % (len(refs_rows),
             ", ".join("%s=%d" % (k, sum(1 for r in refs_rows if r["kind"] == k))
                       for k in ("local", "remote", "other")) or "none",
             len(wt_rows), len(lane_rows), len(findings),
             ", ".join("%s=%d" % kv for kv in sorted(by_class.items())) or "none"))
    for row in refs_rows:
        track = ""
        if row.get("upstream"):
            track = " vs %s +%s/-%s" % (row["upstream"], row.get("ahead"),
                                        row.get("behind"))
        print("  %-6s %-38s %s  excl=%s%s"
              % (row["kind"], row["ref"].replace("refs/heads/", "")
                 .replace("refs/remotes/", ""), row["sha"][:SHA_W],
                 row.get("exclusive"), track))
    for row in wt_rows:
        print("  wt     %-38s %s %s dirty=%s%s"
              % (row["path"], row["head"][:SHA_W],
                 (row["branch"].replace("refs/heads/", "") if row["branch"]
                  else "DETACHED"), row.get("dirty"),
                 " PRUNABLE" if row["prunable"] else ""))
    for row in lane_rows:
        print("  lane   %-38s %s" % (Path(row["path"]).name, row["state"]))
    for row in findings:
        print("  - %-20s %-40s %s" % (row["class"], row["head"], row["detail"]))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "schema": "head-audit/1", "root": str(root), "refs": refs_rows,
            "worktrees": wt_rows, "lane_heads": lane_rows,
            "findings": findings, "by_class": by_class,
        }, indent=2) + "\n", encoding="utf-8")
        print("report: %s" % out)

    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
