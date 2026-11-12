"""Publish the staged project repos to GitHub.

Companion to tools/stage_publish_repos.py. That tool prepares five standalone
repositories under C:/Azazael/publish; this one creates the GitHub remote and
pushes, which is the only step that cannot be done without an interactive
`gh auth login`.

Dry run is the DEFAULT. Nothing leaves this machine unless `--execute` is passed,
and the visibility has no default either: `--public` and `--private` are mutually
exclusive required choices, because "which repos become world-readable" is an
owner decision and not a flag a script should guess. FLAME ships no tests (see
docs/PUBLISH_STATUS_2026-11-02.md), so `--skip` exists to leave a repo behind
without editing this file.

Usage
-----
    python tools/publish_repos.py                    # dry run, prints every command
    python tools/publish_repos.py --public --execute # create + push, public
    python tools/publish_repos.py --private --skip flame-engine --execute

Preconditions, checked before any network call:
  * `gh` is on PATH and `gh auth status` succeeds;
  * each staged repo is a single-commit, clean `main` branch;
  * each staged repo carries README.md, LICENSE, NOTICE and pyproject.toml;
  * no tracked file exceeds GitHub's 100 MB per-file limit.
A precondition failure aborts the whole run: a half-published set is worse than
none, because the next session cannot tell which repos are old.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stage_publish_repos import (  # noqa: E402  (same-directory helper)
    DEFAULT_DEST,
    GITHUB_FILE_LIMIT,
    PROJECTS,
    REQUIRED_FILES,
    run,
)

OWNER_FALLBACK = "clint"


def gh() -> str | None:
    probe = run(["gh", "--version"])
    return "gh" if probe.returncode == 0 else None


def auth_state() -> tuple[bool, str]:
    result = run(["gh", "auth", "status"])
    if result.returncode == 0:
        return True, result.stdout.strip().splitlines()[0] if result.stdout.strip() else "authenticated"
    return False, (result.stderr or result.stdout).strip()


def owner() -> str:
    result = run(["gh", "api", "user", "--jq", ".login"])
    login = result.stdout.strip()
    return login if result.returncode == 0 and login else OWNER_FALLBACK


def preflight(dest_root: Path, skip: set[str]) -> list[str]:
    problems: list[str] = []
    for _src, name, _desc, _subject in PROJECTS:
        if name in skip:
            continue
        dest = dest_root / name
        if not (dest / ".git").is_dir():
            problems.append(f"{name}: not staged (run tools/stage_publish_repos.py)")
            continue
        missing = [f for f in REQUIRED_FILES if not (dest / f).is_file()]
        if missing:
            problems.append(f"{name}: missing {', '.join(missing)}")
        branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=dest).stdout.strip()
        if branch != "main":
            problems.append(f"{name}: branch is {branch!r}, not 'main'")
        commits = run(["git", "rev-list", "--count", "HEAD"], cwd=dest).stdout.strip()
        if commits != "1":
            problems.append(f"{name}: {commits} commits, expected a fresh single-commit history")
        dirty = run(["git", "status", "--porcelain"], cwd=dest).stdout.strip()
        if dirty:
            problems.append(f"{name}: working tree dirty ({dirty.splitlines()[0]})")
        tracked = run(["git", "ls-files"], cwd=dest).stdout.splitlines()
        big = [f for f in tracked
               if (dest / f).is_file() and (dest / f).stat().st_size > GITHUB_FILE_LIMIT]
        if big:
            problems.append(f"{name}: tracked file over GitHub's limit: {big[0]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    vis = ap.add_mutually_exclusive_group()
    vis.add_argument("--public", action="store_true", help="create public repos")
    vis.add_argument("--private", action="store_true", help="create private repos")
    ap.add_argument("--skip", action="append", default=[], help="repo name to leave unpublished")
    ap.add_argument("--execute", action="store_true",
                    help="actually create and push (default: dry run)")
    args = ap.parse_args(argv)

    dest_root: Path = args.dest
    skip = set(args.skip)

    problems = preflight(dest_root, skip)
    if problems:
        print("PREFLIGHT FAILED -- nothing pushed:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1

    # A dry run needs no network and no credential: it prints the commands. The
    # gh checks gate execution only, so the plan stays reviewable while gh is
    # logged out -- which is the state this machine is in today.
    if args.execute:
        if gh() is None:
            print("PREFLIGHT FAILED -- `gh` not on PATH. Nothing pushed.", file=sys.stderr)
            print("  Portable build lives at C:/Azazael/tools_bin/bin/gh.exe", file=sys.stderr)
            return 1
        ok, detail = auth_state()
        if not ok:
            print("PREFLIGHT FAILED -- gh is not authenticated. Nothing pushed.", file=sys.stderr)
            print(f"  gh auth status: {detail}", file=sys.stderr)
            print("  Remedy (interactive, owner only): gh auth login --hostname github.com "
                  "--git-protocol https --web", file=sys.stderr)
            return 1

    if not (args.public or args.private):
        print("REFUSED -- choose --public or --private. Visibility is an owner decision;\n"
              "          it is not defaulted here so no repo becomes world-readable by omission.",
              file=sys.stderr)
        return 1

    visibility = "public" if args.public else "private"
    account = owner() if args.execute else OWNER_FALLBACK
    print(f"owner: {account}   visibility: {visibility}   mode: "
          f"{'EXECUTE' if args.execute else 'DRY RUN (pass --execute to push)'}\n")

    planned = [(name, dest_root / name) for _s, name, _d, _x in PROJECTS if name not in skip]
    for name, path in planned:
        full = f"{account}/{name}"
        cmd = [
            "gh", "repo", "create", full,
            f"--{visibility}",
            "--source", str(path),
            "--remote", "origin",
            "--push",
            "--description", next(d for _s, n, d, _x in PROJECTS if n == name),
        ]
        print("$ " + " ".join(cmd))
        if not args.execute:
            continue
        result = run(cmd)
        print((result.stdout or "").strip() or (result.stderr or "").strip())
        if result.returncode != 0:
            print(f"STOPPED at {name}; earlier repos are published and named above.",
                  file=sys.stderr)
            return 1
        sha = run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()
        print(f"  pushed {sha[:12]}")

    if not args.execute:
        print(f"\nDRY RUN complete: {len(planned)} repo(s) would be created and pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
