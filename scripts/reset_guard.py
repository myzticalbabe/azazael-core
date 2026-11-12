#!/usr/bin/env python3
# coding: utf-8
"""Refuse a `git reset` that would throw away work, and say exactly what.

Why this exists
---------------
`git reset` runs no hook. There is no `pre-reset`, so nothing in this repo can
stop one -- which is why the exposure had to be closed with a wrapper rather
than another reminder. Measured 2026-09-29: **28 `reset:` entries in the
reflog**, one of them `reset: moving to HEAD~1` at 04:02, after which 620+
files were dirty and three modules were gone from the working tree. The
recovery was manual and landed as commit `73ee26af`, "Restore three modules
lost by the hard reset". A commit message describing a restore is the system
telling you it has no other option.

What it checks, and why each one
--------------------------------
1. **Uncommitted changes.** `--hard` discards the working tree. Measured here
   routinely at 40+ dirty files, because the live `chain_controller` writes
   generated artifacts into the tree while work is in progress -- so this is
   the normal state, not an exceptional one, and a reset that "should be safe"
   because the operator did not think about it is exactly the case.
2. **Commits that would become unreachable.** Moving to `HEAD~1` does not
   merely unstage; it can orphan the commit you are standing on. The reflog
   keeps it for a while, which is why this class of loss is invisible at the
   time and fatal later, when the reflog has expired.
3. **Unpushed commits**, when a remote exists. Same argument: recoverable for
   a while, then not.

The point is not to forbid `reset`. It is to make the cost visible at the
moment of the command, while the operator still has the choice.

Usage
-----
    python scripts/reset_guard.py --check HEAD~1     # report, change nothing
    python scripts/reset_guard.py HEAD~1              # guarded reset (soft)
    python scripts/reset_guard.py --hard HEAD         # guarded, --hard
    python scripts/reset_guard.py --hard HEAD --force # do it anyway
    python scripts/reset_guard.py --install-alias     # route `git reset` here

Exit codes: 0 safe / 1 blocked / 2 usage or probe failure.

A probe that cannot answer returns 2 and says so. It is never allowed to
report "safe" on the grounds that it could not look.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The repo the probes run against. A module global rather than a constant so a
#: test can point the whole guard at a throwaway repository: the guard's entire
#: value is that it runs against the REAL tree, and a test that exercised it
#: against this one would need a reset to mean anything.
REPO = ROOT

#: The alias name the guard installs. Deliberately NOT "reset": git resolves
#: built-in commands before it looks at aliases, so an alias named `reset` is
#: stored, reads back correctly, and never runs. See `--install-alias`.
ALIAS_NAME = "guarded-reset"

#: Refuse rather than warn. Every finding below is recoverable-but-late, and a
#: warning that scrolls past is the same as no warning.
BLOCK_DIRTY = True
BLOCK_UNREACHABLE = True
BLOCK_UNPUSHED = True


def _git(*args: str) -> tuple[int, str]:
    """Run git in the repo. Returns (rc, combined output); never raises."""
    try:
        p = subprocess.run(["git", *args], cwd=str(REPO),
                           capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"git {' '.join(args)} failed: {exc}"
    # NOT .strip(). `git status --porcelain` encodes the status in the first
    # two columns and separates them from the path with a third space, so the
    # leading whitespace IS data. Stripping the combined output removed it
    # from the first line only, which made `dirty_files()[0]` lose a character
    # of its filename -- the guard named `.txt` where the file was `a.txt`.
    # A guard that misnames the file at risk is worse than one that lists a
    # count, because the count can be checked and the name is what gets read.
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _commits_between(target: str) -> list[str]:
    """Commits reachable from HEAD but not from `target` -- the ones at risk."""
    rc, out = _git("rev-list", f"{target}..HEAD")
    if rc != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def dirty_files() -> list[str]:
    rc, out = _git("status", "--porcelain")
    if rc != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def unpushed() -> list[str]:
    """Commits on this branch that no remote has. Empty when there is no remote.

    A repo with no remote configured is NOT reported here, and that is a
    deliberate exception rather than a "cannot tell" treated as "fine": with
    no remote there is nothing to be out of sync with, so every commit would
    be permanently "unpushed" and the guard would refuse every reset forever.
    The orphan and dirty checks still apply, and those are the ones that
    correspond to the loss actually observed here.
    """
    rc, out = _git("remote")
    if rc != 0 or not out.strip():
        return []
    rc, out = _git("log", "--oneline", "@{u}..HEAD")
    if rc != 0:
        # No upstream configured. Not an error: an unpushed local branch is a
        # normal state, and treating "cannot tell" as "safe" is the failure
        # mode this whole script exists to avoid -- so say so instead.
        return ["(no upstream configured for this branch)"]
    return [ln for ln in out.splitlines() if ln.strip()]


def assess(target: str, hard: bool) -> list[str]:
    """Blockers for resetting to `target`. Empty list means safe."""
    problems: list[str] = []
    rc, _ = _git("rev-parse", "--verify", f"{target}^{{commit}}")
    if rc != 0:
        return [f"cannot resolve '{target}' to a commit"]

    at_risk = _commits_between(target)
    if BLOCK_UNREACHABLE and at_risk:
        head = _git("rev-parse", "--short", "HEAD")[1]
        tgt = _git("rev-parse", "--short", target)[1]
        problems.append(
            f"{len(at_risk)} commit(s) would stop being reachable from HEAD "
            f"({head} -> {tgt}): {', '.join(c[:8] for c in at_risk[:5])}"
            + (" ..." if len(at_risk) > 5 else "")
            + ". The reflog holds them for a while and then does not.")

    if BLOCK_DIRTY:
        dirty = dirty_files()
        # Only TRACKED changes are at risk. `git reset --hard` discards
        # modifications to tracked files; it does not remove untracked ones --
        # that is `git clean`, a different command. An earlier version of this
        # message counted untracked files into the --hard loss, which
        # overstated the damage by however many scratch files were lying
        # around, and in this repo that is routinely 565.
        tracked = [d for d in dirty if not d.startswith("??")]
        untracked = len(dirty) - len(tracked)
        context = f" ({untracked} untracked file(s) are not touched by reset)"
        if hard and tracked:
            problems.append(
                f"--hard would discard {len(tracked)} modified tracked "
                f"file(s) in the working tree{context}: "
                f"{', '.join(d[3:].strip() for d in tracked[:5])}"
                + (" ..." if len(tracked) > 5 else ""))
        elif tracked:
            problems.append(
                f"reset would unstage {len(tracked)} modified tracked "
                f"file(s) from the index{context}: "
                f"{', '.join(d[3:].strip() for d in tracked[:5])}"
                + (" ..." if len(tracked) > 5 else ""))

    if BLOCK_UNPUSHED:
        out = unpushed()
        if out:
            problems.append(
                f"{len(out)} commit(s) are not on any remote: "
                + ("; ".join(out[:3]) + (" ..." if len(out) > 3 else "")))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default="HEAD",
                    help="commit-ish to reset to (default HEAD, a no-op reset)")
    ap.add_argument("--hard", action="store_true", help="pass --hard to git")
    ap.add_argument("--force", action="store_true",
                    help="do it anyway; the blockers are still reported")
    ap.add_argument("--check", action="store_true",
                    help="report and change nothing")
    ap.add_argument("--install-alias", action="store_true",
                    help=f"install `git {ALIAS_NAME}` for this repo; note "
                         "that it cannot intercept `git reset`")
    args = ap.parse_args(argv)

    if args.install_alias:
        # The alias is named `guarded-reset`, NOT `reset`, and that is not a
        # naming preference. MEASURED 2026-09-29 on this git: an alias cannot
        # shadow a built-in command. `git zzztest` fired its alias; `git
        # status` ran real git with `alias.status` set and unread. So
        # `alias.reset` can be written, read back, and still never run -- which
        # is precisely how an enforcement ends up looking installed while
        # enforcing nothing.
        #
        # The honest consequence: `git reset` CANNOT be intercepted, by alias
        # or by hook (git has no pre-reset). The guard is therefore a command
        # that has to be reached for, and this flag installs a short name for
        # it. Claiming otherwise would be a false claim about enforcement,
        # which is the failure this whole script exists to prevent.
        exe = sys.executable.replace(chr(92), "/")
        script = str(ROOT / "scripts" / "reset_guard.py").replace(chr(92), "/")
        want = '!"' + exe + '" "' + script + '"'
        rc, out = _git("config", f"alias.{ALIAS_NAME}", want)
        if rc != 0:
            print(f"could not install the alias: {out}", file=sys.stderr)
            return 2
        stored = _git("config", "--get", f"alias.{ALIAS_NAME}")[1].strip()
        if stored != want:
            print("alias was written but does not read back as written:\n"
                  f"  wanted: {want}\n  stored: {stored}", file=sys.stderr)
            return 2
        print(f"alias.{ALIAS_NAME} installed and verified, so the guard is one "
              f"command away: `git {ALIAS_NAME} HEAD~1`.\n"
              "It does NOT intercept `git reset`, and nothing can: git runs no "
              "pre-reset hook, and an alias cannot shadow a built-in. Use "
              f"`git {ALIAS_NAME}`, or `python scripts/reset_guard.py` directly.")
        return 0

    problems = assess(args.target, args.hard)

    if not problems:
        print(f"reset {args.target}: safe"
              + (" (--hard)" if args.hard else ""))
        if args.check:
            return 0
        rc, out = _git("reset", *(["--hard"] if args.hard else []), args.target)
        if rc != 0:
            print(out, file=sys.stderr)
            return rc
        return 0

    print(f"BLOCKED: reset {'--hard ' if args.hard else ''}{args.target} "
          "would lose work.\n", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    print("", file=sys.stderr)
    if args.check or not args.force:
        print("Nothing was changed. Resolve the above, or re-run with "
              "--force if you have decided the loss is acceptable.",
              file=sys.stderr)
        return 1

    print("Proceeding anyway (--force).", file=sys.stderr)
    rc, out = _git("reset", *(["--hard"] if args.hard else []), args.target)
    if rc != 0:
        print(out, file=sys.stderr)
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
