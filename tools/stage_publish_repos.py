"""Stage the publishable projects as standalone git repositories.

Why this exists
---------------
The monorepo at C:/Azazael/azazael CANNOT be pushed to GitHub: two blobs in its
history exceed the 100 MB per-file hard limit (archived/2026-05-31/onnxruntime.tar.gz
at 272.339 MB and archived/2026-05-31/onnxruntime-source/onnxruntime.tar.gz at
228.847 MB -- 501.186 MB of a 954.92 MiB pack), and its ``origin`` is a local
directory, not a host. See docs/PUBLISH_READINESS_2026-11-02.md.

The publishable units are not the monorepo. They are the five Project_*
directories, each of which already carries pyproject.toml, LICENSE (Apache-2.0),
NOTICE, README.md, a .gitignore and tests. Each is 1-2 MB. This tool snapshots
each one into its own repository with a single commit on ``main``, so the push
is a fresh history that contains no vendored tarball by construction -- no
``filter-repo`` rewrite of the monorepo, and no monorepo SHA changes.

Nothing here pushes. Staging is local and reversible (the output directory is
plain files); publishing is a separate, owner-authorized step.

Usage
-----
    python tools/stage_publish_repos.py                 # stage all five
    python tools/stage_publish_repos.py --check         # audit what is staged
    python tools/stage_publish_repos.py --only cradle   # stage one
    python tools/stage_publish_repos.py --force         # restage in place

Each project's own .gitignore governs what enters the commit, so build/,
*.egg-info/, __pycache__/ and .pytest_cache/ are excluded by the project's own
rules rather than by a list duplicated here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = Path("C:/Azazael/publish")

# (source directory, github repo name, one-line description, commit subject)
PROJECTS: list[tuple[str, str, str, str]] = [
    (
        "Project_FLAMING_SWORD",
        "flaming-sword",
        "Dispatch and tool-surface layer for the Azazael worm registry.",
        "Publish FLAMING SWORD: dispatch and tool-surface layer",
    ),
    (
        "Project_Nurse_Ratchet",
        "nurse-ratchet",
        "Therapeutic conversation engine with a manifest-driven source of truth.",
        "Publish Nurse Ratchet: therapeutic conversation engine",
    ),
    (
        "Project_CRADLE",
        "cradle",
        "Chain validator: proves an ordered set of operations reproduces a final artifact.",
        "Publish CRADLE: chain validation",
    ),
]

# GitHub refuses a push containing any file above this size; checked, not assumed.
GITHUB_FILE_LIMIT = 100 * 1024 * 1024

#: Per-command bound. A restage clones, rewrites history and pushes to GitHub;
#: 15 minutes is generous for that and still finite.
COMMAND_TIMEOUT_S = 900

# Present in every publishable repo or the repo page is misleading.
REQUIRED_FILES = ("README.md", "LICENSE", "NOTICE", "pyproject.toml")


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, never raising: a missing executable is returncode 127.

    Measured 2026-11-02: `gh` is installed at C:/Azazael/tools_bin/bin/gh.exe but
    not on PATH, and an unguarded subprocess raised FileNotFoundError through the
    caller's clean refusal path. A precondition check that crashes is not a check.

    encoding= is explicit because `text=True` alone decodes with the platform
    default (cp1252 here), which mojibakes a repo description or a gh error
    message and can raise on characters it cannot decode -- inside the very
    helper whose contract is "never raises". The timeout makes a stalled `git
    push`/`gh` a 124 in the caller's evidence rather than a frozen restage.
    """
    try:
        return subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=COMMAND_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", f"not found: {exc.filename}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            cmd, 124, "", f"timed out after {COMMAND_TIMEOUT_S}s"
        )


def force_rmtree(path: Path) -> None:
    """Remove a directory including git's read-only object files.

    On Windows a git object is written read-only, and `shutil.rmtree` stops with
    WinError 5 on the first one (measured restaging nurse-ratchet:
    .git/objects/01/03320be3... -> Access is denied). Clear the read-only bit and
    retry, or every restage of an existing destination fails.
    """
    def retry(func, target, _exc) -> None:  # type: ignore[no-untyped-def]
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=retry)


def oversized(src: Path) -> list[tuple[Path, int]]:
    bad: list[tuple[Path, int]] = []
    for path in src.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        size = path.stat().st_size
        if size > GITHUB_FILE_LIMIT:
            bad.append((path, size))
    return bad


def stage(src: Path, dest: Path, subject: str, force: bool) -> dict[str, object]:
    if not src.is_dir():
        return {"ok": False, "error": f"source missing: {src}"}

    too_big = oversized(src)
    if too_big:
        detail = ", ".join(f"{p.name} {s / 1048576:.1f} MB" for p, s in too_big)
        return {"ok": False, "error": f"file above GitHub limit: {detail}"}

    if dest.exists():
        if not force:
            return {"ok": False, "error": f"destination exists: {dest} (use --force)"}
        force_rmtree(dest)

    # Copy, then let the project's .gitignore decide what is committed. The
    # caches that would otherwise be copied are deleted first so a 2 MB project
    # does not stage 40 MB of __pycache__.
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "build", "dist", "*.egg-info",
            ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git",
        ),
    )

    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", subject],
    ):
        result = run(cmd, cwd=dest)
        if result.returncode != 0:
            return {"ok": False, "error": f"{' '.join(cmd[:3])}: {result.stderr.strip()}"}

    sha = run(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
    tracked = run(["git", "ls-files"], cwd=dest).stdout.splitlines()
    size = sum((dest / f).stat().st_size for f in tracked if (dest / f).is_file())
    return {
        "ok": True,
        "sha": sha,
        "files": len(tracked),
        "bytes": size,
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=dest).stdout.strip(),
    }


def check(dest_root: Path) -> int:
    """Audit staged repos. Exit 1 if any repo is unpublishable."""
    failures = 0
    for _src, name, _desc, _subject in PROJECTS:
        dest = dest_root / name
        print(f"=== {name} ===")
        if not (dest / ".git").is_dir():
            print("  FAIL not staged")
            failures += 1
            continue
        missing = [f for f in REQUIRED_FILES if not (dest / f).is_file()]
        tracked = run(["git", "ls-files"], cwd=dest).stdout.splitlines()
        commits = run(["git", "rev-list", "--count", "HEAD"], cwd=dest).stdout.strip()
        dirty = run(["git", "status", "--porcelain"], cwd=dest).stdout.strip()
        remotes = run(["git", "remote", "-v"], cwd=dest).stdout.strip()
        big = [
            (f, (dest / f).stat().st_size)
            for f in tracked
            if (dest / f).is_file() and (dest / f).stat().st_size > GITHUB_FILE_LIMIT
        ]
        print(f"  branch   {run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=dest).stdout.strip()}")
        print(f"  commits  {commits}")
        print(f"  tracked  {len(tracked)} files")
        print(f"  clean    {'yes' if not dirty else 'NO: ' + dirty.splitlines()[0]}")
        print(f"  remote   {remotes.splitlines()[0] if remotes else '(none yet -- added at push time)'}")
        if missing:
            print(f"  FAIL missing {', '.join(missing)}")
            failures += 1
        if big:
            print(f"  FAIL oversized {big}")
            failures += 1
        if commits != "1":
            print(f"  FAIL expected a single commit, found {commits}")
            failures += 1
    print()
    print("PUBLISHABLE" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--only", action="append", default=None,
                    help="stage only these repo names (repeatable)")
    ap.add_argument("--force", action="store_true", help="replace an existing staging dir")
    ap.add_argument("--check", action="store_true", help="audit staged repos, stage nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args(argv)

    if args.check:
        return check(args.dest)

    args.dest.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {}
    failed = 0
    for src_name, name, _desc, subject in PROJECTS:
        if args.only and name not in args.only:
            continue
        result = stage(REPO_ROOT / src_name, args.dest / name, subject, args.force)
        summary[name] = result
        if result.get("ok"):
            print(f"staged {name:<14} {result['sha'][:12]}  "
                  f"{result['files']} files  {result['bytes'] / 1048576:.2f} MB")
        else:
            failed += 1
            print(f"FAILED {name:<14} {result['error']}")

    if args.json:
        print(json.dumps(summary, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
