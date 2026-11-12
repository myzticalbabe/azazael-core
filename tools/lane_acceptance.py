#!/usr/bin/env python3
"""lane_acceptance.py -- mechanically verify a worker lane's completion claim.

A worker saying "done" is a lead, never proof (Oct-14 doctrine). This tool
re-measures the claim: the commit exists, the report exists and has no
PLACEHOLDER cells, the named tests pass on both interpreters, the changed
paths stay inside the lane's scope, and --neg-check proves the tests are
adequate (they fail WITHOUT the fix: green suites alone prove nothing,
UTBoost found 345 erroneous patches passing as correct).

Usage:
    python tools/lane_acceptance.py --sha <commit> --report <path>
        [--tests test_a.py,test_b.py] [--scope src/CORE,tools]
        [--py315t ../.venvs/py315t/Scripts/python.exe]
        [--neg-check] [--cost-model NAME --cost-tokens N --cost-seconds S]

Exit 0 only when every check passes. Prints a PASS/FAIL table.
Stdlib-only. All subprocess reads use encoding="utf-8", errors="replace".
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"PLACEHOLDER", re.IGNORECASE)


def _run(cmd, timeout=600):
    """Text-mode subprocess.

    The `**kw` forwarding seam was removed 2026-10-14: no caller ever passed
    one, and it is what made the encoding audit report this call UNDETERMINED
    -- a forwarded kwarg could in principle override `encoding` (or `text`),
    so the audit could not statically prove the call names its encoding.
    """
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(ROOT),
        timeout=timeout)


def _run_bytes(cmd, timeout=600):
    """Binary-mode subprocess: no `text=`, so nothing is decoded and no
    encoding is required. Named explicitly rather than forwarded so the audit
    can SEE that -- it reported this call UNDETERMINED while the kwargs that
    might have added `text=True` were opaque.
    """
    return subprocess.run(
        cmd, capture_output=True, cwd=str(ROOT), timeout=timeout)


def _tree_dirty():
    r = _run(["git", "status", "--short"])
    return [l for l in r.stdout.splitlines()
            if l.strip() and not l.startswith("??")]


def _tree_files_at(sha, files):
    """Worktree bytes of `files` as of `sha` (None where sha lacks the file)."""
    out = {}
    for f in files:
        r = _run_bytes(["git", "show", "%s:%s" % (sha, f)])
        out[f] = r.stdout if r.returncode == 0 else None
    return out


def _worktree_bytes(blob):
    """Convert blob bytes to what checkout would write on this machine.

    With core.autocrlf=true an LF-only blob checks out as CRLF; writing raw
    blob bytes leaves a permanent phantom `M` (hashes match, status dirty).
    Mirror checkout: convert only when the blob itself has no CR.
    """
    if b"\r" not in blob:
        conv = _run(["git", "config", "core.autocrlf"])
        if conv.stdout.strip().lower() == "true":
            return blob.replace(b"\n", b"\r\n")
    return blob


def check_sha(sha):
    r = _run(["git", "log", "--oneline", "-1", sha])
    ok = r.returncode == 0 and bool(r.stdout.strip())
    return ok, (r.stdout.strip()[:100] if ok else "SHA not in log")


def check_files(sha, scope):
    r = _run(["git", "show", "--name-only", "--format=", sha])
    if r.returncode != 0:
        return False, "cannot list commit files"
    files = [l for l in r.stdout.splitlines() if l.strip()]
    if not files:
        return False, "commit touches no files"
    if scope:
        allowed = tuple(scope)
        bad = [f for f in files if not f.startswith(allowed)]
        if bad:
            return False, "out-of-scope paths: %s" % ", ".join(bad[:5])
    return True, "%d file(s) in scope" % len(files)


def check_report(path):
    if not path:
        return True, "no report required"
    p = ROOT / path
    if not p.exists():
        return False, "report missing: %s" % path
    text = p.read_text(encoding="utf-8", errors="replace")
    holes = PLACEHOLDER.findall(text)
    if holes:
        return False, "%d PLACEHOLDER cell(s) in %s" % (len(holes), path)
    if len(text.strip()) < 200:
        return False, "report suspiciously short (%d chars)" % len(text)
    return True, "report present, no placeholders"


def check_tests(tests, py315t):
    if not tests:
        return True, "no tests named"
    failures = []
    interpreters = [sys.executable]
    if py315t and Path(py315t).exists():
        interpreters.append(py315t)
    for interp in interpreters:
        r = _run([interp, "-m", "pytest", "-q"] + tests)
        tail = (r.stdout + r.stderr).strip().splitlines()
        summary = tail[-1] if tail else "no output"
        if r.returncode != 0 or " failed" in summary:
            failures.append("%s: %s" % (Path(interp).name, summary[:120]))
    if failures:
        return False, "; ".join(failures)
    return True, "green on %d interpreter(s)" % len(interpreters)


def check_neg_control(sha, tests, py315t):
    """Prove test adequacy: with the fix reverted, the tests must FAIL.

    Passing-without-the-fix means the tests cannot discriminate (the UTBoost
    case: hundreds of erroneous patches scored as correct). Reverts via
    `git show SHA~1:path` round-trip (never stash: stashing a clean tree is a
    no-op that makes the later pop fail). Refuses on a dirty tree; restores
    byte-exact and hash-verifies, aborting loudly on any mismatch.
    """
    if not tests:
        return True, "no tests named, control skipped"
    dirty = _tree_dirty()
    if dirty:
        return False, ("tree dirty (%d tracked change(s)) -- revert would "
                       "endanger uncommitted work; commit first" % len(dirty))
    files = [l for l in _run(["git", "show", "--name-only", "--format=",
                              sha]).stdout.splitlines() if l.strip()]
    if not files:
        return False, "cannot list commit files"
    live = _tree_files_at(sha, files)
    pre = _tree_files_at(sha + "~1", files)
    missing = [f for f in files if live[f] is None]
    if missing:
        return False, "cannot read committed bytes for: %s" % ", ".join(
            missing[:3])
    try:
        for f in files:
            p = ROOT / f
            if pre[f] is None:
                if p.exists():
                    p.unlink()  # file added by the fix: absence is pre-image
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(_worktree_bytes(pre[f]))
        r = _run([sys.executable, "-m", "pytest", "-q"] + tests)
        tail = (r.stdout + r.stderr).strip().splitlines()
        summary = tail[-1] if tail else "no output"
        if r.returncode == 0 and " failed" not in summary:
            return False, ("tests PASS without the fix -- inadequate tests "
                           "(UTBoost case); strengthen tests, not the fix")
        return True, "tests fail without the fix (%s)" % summary[:100]
    finally:
        for f in files:
            p = ROOT / f
            if live[f] is None:
                if p.exists():
                    p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(_worktree_bytes(live[f]))
        after = _tree_files_at("HEAD", files)
        bad = [f for f in files
               if (after[f] or b"") != (live[f] or b"")]
        _run(["git", "update-index", "--refresh"])
        if bad:
            print("FATAL: restore mismatch on %s -- worktree differs from "
                  "the verified bytes; inspect by hand, refusing to "
                  "continue." % ", ".join(bad[:5]))
            raise SystemExit(2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--report", default="")
    ap.add_argument("--tests", default="")
    ap.add_argument("--scope", default="")
    ap.add_argument("--neg-check", action="store_true",
                    help="prove test adequacy: stash the fix, expect red, "
                         "restore, then run the normal green check")
    ap.add_argument("--cost-model", default="")
    ap.add_argument("--cost-tokens", default="")
    ap.add_argument("--cost-seconds", default="")
    ap.add_argument("--py315t",
                    default=str(ROOT / ".." / ".venvs" / "py315t" /
                                "Scripts" / "python.exe"))
    args = ap.parse_args(argv)
    tests = [t for t in args.tests.split(",") if t.strip()]
    scope = tuple(s for s in args.scope.split(",") if s.strip())
    rows = [
        ("sha-in-log", *check_sha(args.sha)),
        ("files-in-scope", *check_files(args.sha, scope)),
        ("report", *check_report(args.report)),
    ]
    if args.neg_check:
        rows.append(("neg-control", *check_neg_control(
            args.sha, tests, args.py315t)))
    rows.append(("tests", *check_tests(tests, args.py315t)))
    if args.cost_model or args.cost_tokens or args.cost_seconds:
        rows.append(("cost", True, "model=%s tokens=%s seconds=%s" % (
            args.cost_model or "?", args.cost_tokens or "?",
            args.cost_seconds or "?")))
    width = max(len(name) for name, _, _ in rows)
    failed = 0
    for name, ok, detail in rows:
        failed += not ok
        print("%-*s  %s  %s" % (width, name, "PASS" if ok else "FAIL",
                                detail))
    print("ACCEPT" if failed == 0 else "REJECT (%d failing)" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
