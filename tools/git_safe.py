#!/usr/bin/env python3
"""git_safe — run a destructive git verb only after both endpoints are named.

WHY THIS EXISTS
---------------
On 2026-11-05 the branch was reset backwards from 2ea4718c to 4b0ace17 and
fifteen commits lost their ref. Nothing raised an exception; the only record was
a reflog line naming the *target* and never the thing left behind. Recovery was
possible only because the worktree bytes had been committed forty minutes
earlier, and only until the (unreachable) reflog entry expired at 30 days.

The verb is not the bug. Running it with one endpoint named is. This wrapper
names both, and stores the dirty bytes in the object database, before the verb
runs:

  refs/preverb/<stamp>/head     the current HEAD, so the tip is recoverable
  refs/preverb/<stamp>/target   the resolved target, so the intent is recorded
  <record>.json                 argv, both shas, dirty paths + blob shas, and
                                the exact commands that undo the operation

Untracked bytes matter here: `git clean -fd` and `reset --hard` delete files
that no ref and no index ever held. They are written as blobs with
`git hash-object -w`, so the record alone is enough to restore them.

USAGE
-----
  python tools/git_safe.py --dry-run reset --hard HEAD~3
  python tools/git_safe.py reset --hard refs/backup/orphaned-2026-11-05
  python tools/git_safe.py restore --staged src/foo.py
  python tools/git_safe.py clean -fd
  python tools/git_safe.py checkout -- src/foo.py
  python tools/git_safe.py --records            # list what has been protected
  python tools/git_safe.py --undo <record.json> # print (or --apply) the undo

EXIT CODES
----------
  0  the verb ran, and its record was written
  2  refused — nothing ran (unresolvable target, hook mid-flight, no git)
  3  the record could not be written, so the verb was NOT run
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DESTRUCTIVE = {"reset", "restore", "clean", "checkout", "rebase", "filter-branch"}
REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / ".audit" / "preverb"


GIT_TIMEOUT = 120


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run git from the repo, never a shell, always utf-8, always bounded.

    A helper whose job is to make destruction reversible cannot itself hang:
    the verbs it wraps are exactly the ones that would leave the tree in the
    state you were trying to escape, and an unbounded call is how that state
    goes unrecorded.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=GIT_TIMEOUT,
    )


def out(*args: str) -> str:
    proc = git(*args)
    return (proc.stdout or "").strip()


def head_sha() -> str:
    return out("rev-parse", "HEAD")


def resolve(rev: str) -> str | None:
    """Resolve a revision to a sha, or None. Never raises on a bad rev."""
    if not rev:
        return None
    proc = git("rev-parse", "--verify", f"{rev}^{{commit}}")
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def in_flight_state() -> str:
    """Name a git operation that is already mid-flight, or ''."""
    state = REPO / ".git"
    for name, label in (
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase/am"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("MERGE_HEAD", "merge"),
        ("BISECT_LOG", "bisect"),
    ):
        if (state / name).exists():
            return label
    return ""


def dirty_snapshot() -> list[dict]:
    """Every changed or untracked path, with its bytes stored as a blob.

    The blob write is the point: `reset --hard` and `clean -fd` remove bytes
    that no ref holds, so a sha we can `cat-file` is the only durable copy.
    """
    rows: list[dict] = []
    proc = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if proc.returncode != 0:
        return rows
    entries = [e for e in (proc.stdout or "").split("\0") if e]
    i = 0
    while i < len(entries):
        entry = entries[i]
        status, path = entry[:2], entry[3:]
        if status[:1] in ("R", "C") and i + 1 < len(entries):
            path = entries[i + 1]
            i += 1
        i += 1
        full = REPO / path
        row = {"status": status, "path": path, "blob": None, "bytes": None}
        if full.is_file():
            written = git("hash-object", "-w", "--", path)
            if written.returncode == 0:
                row["blob"] = (written.stdout or "").strip()
            try:
                row["bytes"] = full.stat().st_size
            except OSError:
                row["bytes"] = None
        rows.append(row)
    return rows


def named_ref(stamp: str, kind: str, sha: str) -> str:
    ref = f"refs/preverb/{stamp}/{kind}"
    proc = git("update-ref", ref, sha)
    if proc.returncode != 0:
        return ""
    return ref


def undo_steps(record: dict) -> list[tuple[list[str], str | None]]:
    """The commands that put the tree back, as (argv, outfile).

    `outfile` carries what used to be a shell `>` redirect. Executing these
    steps needed `shell=True`, which is the one way an unreviewed command
    string becomes arbitrary execution inside a tool whose whole job is to
    make destruction reversible.
    """
    steps: list[tuple[list[str], str | None]] = []
    head_ref = record.get("head_ref")
    verb = record.get("verb")
    paths = shlex.split(record.get("pathspec_text") or "")
    if head_ref:
        if verb in ("reset", "rebase", "filter-branch"):
            steps.append((["git", "reset", "--hard", head_ref], None))
        elif verb == "checkout":
            steps.append((["git", "checkout", head_ref, "--", *paths], None))
        elif verb == "restore":
            steps.append((["git", "restore", "--source", head_ref, "--worktree", "--", *paths], None))
        elif verb == "clean":
            steps.append((["git", "reset", "--hard", head_ref], None))
    for row in record.get("dirty", []):
        if row.get("blob"):
            steps.append((["git", "cat-file", "blob", row["blob"]], row["path"]))
    return steps


def render_step(step: tuple[list[str], str | None]) -> str:
    argv, outfile = step
    text = " ".join(shlex.quote(arg) for arg in argv)
    return f"{text} > {shlex.quote(outfile)}" if outfile else text


def undo_commands(record: dict) -> list[str]:
    """The printable undo plan: the note first, then each step."""
    cmds: list[str] = []
    if record.get("target_ref"):
        cmds.append(f"# the target you asked for is still named: {record['target_ref']}")
    cmds.extend(render_step(step) for step in undo_steps(record))
    if not cmds:
        cmds.append("# nothing was recorded — treat this operation as unrecoverable")
    return cmds


def record_path(stamp: str) -> Path:
    return RECORD_DIR / f"{stamp}.json"


def write_record(record: dict) -> Path:
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    path = record_path(record["stamp"])
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path


def pick_target(argv: list[str]) -> str:
    """The first non-flag argument is the revision for reset/checkout."""
    for token in argv:
        if not token.startswith("-"):
            return token
    return ""


def run(argv: list[str], dry_run: bool, force_clean: bool) -> int:
    if not argv:
        print("git_safe: no git arguments given", file=sys.stderr)
        return 2
    verb = argv[0]
    if verb not in DESTRUCTIVE:
        print(f"git_safe: '{verb}' is not in the destructive set {sorted(DESTRUCTIVE)}; "
              f"run it directly", file=sys.stderr)
        return 2

    busy = in_flight_state()
    if busy:
        print(f"git_safe: REFUSED — a {busy} is already in flight; finish or abort it "
              f"first, because a destructive verb on top of it compounds the loss",
              file=sys.stderr)
        return 2

    head = head_sha()
    if not head:
        print("git_safe: REFUSED — HEAD does not resolve, so there is no endpoint to "
              "name and no way to undo", file=sys.stderr)
        return 2

    target_text = pick_target(argv[1:])
    target = resolve(target_text) if target_text else None
    if target_text and not target:
        print(f"git_safe: REFUSED — '{target_text}' does not resolve to a commit. "
              f"A destructive verb against an unresolved revision is the shape that "
              f"lost 15 commits on 2026-11-05", file=sys.stderr)
        return 2

    if verb == "clean" and not force_clean:
        print("git_safe: REFUSED — 'clean' deletes bytes no ref holds. Re-run with "
              "--force-clean after reading the dirty snapshot below; the blobs are "
              "written first, so the deletion is recoverable either way", file=sys.stderr)

    dirty = dirty_snapshot()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record: dict = {
        "stamp": stamp,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "verb": verb,
        "argv": ["git", *argv],
        "head": head,
        "head_branch": out("rev-parse", "--abbrev-ref", "HEAD"),
        "target_text": target_text,
        "target": target,
        "pathspec_text": " ".join(t for t in argv[1:] if not t.startswith("-")),
        "dirty_count": len(dirty),
        "dirty": dirty,
        "dry_run": dry_run,
    }

    if dry_run:
        record["head_ref"] = f"(would be refs/preverb/{stamp}/head)"
        record["target_ref"] = f"(would be refs/preverb/{stamp}/target)" if target else ""
        print(f"DRY RUN — would name both endpoints, then run: git {' '.join(argv)}")
        print(f"  head     {head[:12]} ({record['head_branch']})")
        print(f"  target   {(target or '(none — the verb takes no revision)')[:12]}")
        print(f"  dirty    {len(dirty)} path(s) snapshotted as blobs")
        for row in dirty[:10]:
            print(f"    {row['status']} {row['path']}  blob={str(row['blob'])[:12]}")
        for cmd in undo_commands(record):
            print(f"  undo: {cmd}")
        return 0

    if verb == "clean" and not force_clean:
        for row in dirty[:10]:
            print(f"  would delete: {row['path']}", file=sys.stderr)
        return 2

    record["head_ref"] = named_ref(stamp, "head", head)
    if not record["head_ref"]:
        print("git_safe: REFUSED — could not create the pre-verb ref, so the tip would "
              "be unnamed while the verb ran", file=sys.stderr)
        return 2
    if target:
        record["target_ref"] = named_ref(stamp, "target", target)

    try:
        path = write_record(record)
    except OSError as exc:
        print(f"git_safe: REFUSED — record not written ({type(exc).__name__}: {exc}); "
              f"the ref {record['head_ref']} exists, but without the record the dirty "
              f"bytes are unlisted, so the verb is not run", file=sys.stderr)
        return 3

    print(f"git_safe: both endpoints named — head={record['head_ref']} "
          f"target={record['target_ref'] or '(none)'} record={path}")

    proc = subprocess.run(["git", *argv], cwd=str(REPO), text=True,
                          encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT)
    record["exit_code"] = proc.returncode
    record["head_after"] = head_sha()
    try:
        write_record(record)
    except OSError as exc:
        print(f"git_safe: WARNING — the verb ran (exit {proc.returncode}) but the "
              f"post-state could not be recorded: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    if proc.returncode != 0:
        print(f"git_safe: the verb exited {proc.returncode}; the endpoints are still "
              f"named at {record['head_ref']}", file=sys.stderr)
    else:
        print(f"git_safe: done — head is now {str(record['head_after'])[:12]}; undo with "
              f"'python tools/git_safe.py --undo {path}'")
    return proc.returncode


def list_records() -> int:
    if not RECORD_DIR.exists():
        print("no pre-verb records yet")
        return 0
    rows = sorted(RECORD_DIR.glob("*.json"))
    print(f"{len(rows)} record(s) in {RECORD_DIR}")
    for path in rows[-20:]:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  {path.name}  UNREADABLE ({type(exc).__name__})")
            continue
        print(f"  {rec.get('stamp')}  git {' '.join(rec.get('argv', [])[1:])[:60]}  "
              f"head={str(rec.get('head'))[:12]} dirty={rec.get('dirty_count')}")
    return 0


def undo(record_file: str, apply: bool) -> int:
    path = Path(record_file)
    if not path.exists():
        print(f"git_safe: no such record: {path}", file=sys.stderr)
        return 2
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"git_safe: record unreadable ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2
    cmds = undo_commands(rec)
    print(f"undo plan for {path.name} ({rec.get('verb')} at {rec.get('measured_at')}):")
    for cmd in cmds:
        print(f"  {cmd}")
    if not apply:
        print("\n(printed only — pass --apply to run these)")
        return 0
    for step in undo_steps(rec):
        argv, outfile = step
        print(f"running: {render_step(step)}")
        try:
            if outfile:
                proc = subprocess.run(argv, cwd=str(REPO), capture_output=True,
                                      timeout=GIT_TIMEOUT)
                if proc.returncode == 0:
                    Path(outfile).write_bytes(proc.stdout)
            else:
                proc = subprocess.run(argv, cwd=str(REPO), text=True, encoding="utf-8",
                                      errors="replace", timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"git_safe: undo step could not run ({type(exc).__name__}: {exc}): "
                  f"{render_step(step)}", file=sys.stderr)
            return 1
        if proc.returncode != 0:
            print(f"git_safe: undo step failed ({proc.returncode}): {render_step(step)}",
                  file=sys.stderr)
            return 1
    print("undo complete")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="git_safe", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="name nothing on disk; print the plan and the undo")
    ap.add_argument("--force-clean", action="store_true",
                    help="allow 'clean' after the dirty snapshot is written")
    ap.add_argument("--records", action="store_true", help="list pre-verb records")
    ap.add_argument("--undo", metavar="RECORD", help="print the undo plan for a record")
    ap.add_argument("--apply", action="store_true", help="with --undo, run the plan")
    ap.add_argument("git_args", nargs=argparse.REMAINDER,
                    help="the git verb and its arguments, e.g. reset --hard HEAD~3")
    args = ap.parse_args()

    if args.records:
        return list_records()
    if args.undo:
        return undo(args.undo, args.apply)
    argv = [a for a in args.git_args if a != "--"]
    return run(argv, args.dry_run, args.force_clean)


if __name__ == "__main__":
    sys.exit(main())
