#!/usr/bin/env python3
# coding: utf-8
"""Anti-pattern gate — fail on NEW anti-patterns, over a recorded baseline.

The problem this exists to close
--------------------------------
`reports/ERROR_LESSON_AUDIT_2026-10-03.md` measured the anti-pattern backlog
cluster (54 ERROR_LOG entries, #81-#86) and found the mechanism built but not
enforced:

    `src/CORE/compliance/scanner.py` is a full RULE 0X-7 AST scanner covering
    exactly the seven categories, with `tests/test_scanner.py`, a `--fail-on`
    exit code, and a `scripts/worm_pre_commit_hook.sh`.  What is actually true,
    measured: the hook is NOT installed, and the default `pytest` suite does not
    gate on the scanner.  A commit that adds 500 emoji still passes.

Running the scanner over `src/` right now reports 2,224 findings. That is a real
debt, not a regression, and this project's own rule is that a check failing on
facts gets switched off (Lesson #86). So this gate does what
`tools/type_check_gate.py` does for type diagnostics: it records what is known
and fails only on an INCREASE.

Why counts-by-category and not finding identities
-------------------------------------------------
`type_check_gate.py` anchors on source lines because a type diagnostic is a
property of a line. An anti-pattern is a property of a (file, category) pair,
and the same emoji appears thousands of times; an identity scheme over 2,224
findings would be position-dependent (invalidated by any edit above it, Lesson
#97) and noisy. The ratchet only needs to answer "did this class get worse",
which per-category totals answer exactly, cheaply, and without a fragile key.

A category that disappears entirely is recorded as gone and reported, never a
failure -- driving a category to zero is the goal (`t_string`, `os_system` and
`cls_new_factory` were each driven to zero and this keeps them there).

Usage
-----
    python tools/anti_pattern_gate.py --baseline   # record what is known
    python tools/anti_pattern_gate.py --check      # pre-commit/CI: exit 1 on new
    python tools/anti_pattern_gate.py --report     # run, print, no gate
    python tools/anti_pattern_gate.py --check --dir src/CORE
    python tools/anti_pattern_gate.py --check --baseline-file logs/x.json

A narrowed run writes somewhere else (same rule as type_check_gate.py)
----------------------------------------------------------------------
`--dir` selects a SUBSET, so its counts are not the whole-`src/` baseline's
counts. A narrowed `--baseline` with no explicit `--baseline-file` is redirected
to a `.partial` sibling and says so; an explicit path is obeyed as written.

Two scan predicates, two baseline files (2026-11-01)
---------------------------------------------------
`--tracked-only` is not a subset: it is the same directory under a different
FILE predicate, and it is the one the pre-commit hook enforces. So there are two
baselines, each read by exactly the run that shares its scope:

    logs/anti_pattern_baseline.json           --check --tracked-only  (the hook)
    logs/anti_pattern_baseline.worktree.json  --check                  (worktree)

Before this, one file held whichever predicate was recorded last while the check
always read it: a tracked scan diffed untracked scratch (968 vs 953
`import_inside_function`, none of it in any commit), and `--baseline
--tracked-only` wrote a `.partial` file nothing read. A baseline still records
its predicate, so an explicit `--baseline-file` from the other scope is REFUSED
(exit 2) rather than diffed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: Bump only when the meaning of a recorded count changes (e.g. a new scanner
#: category), so an old baseline is refused rather than diffed across.
IDENTITY_SCHEME = "category-counts-v1"

DEFAULT_DIR = "src"
#: The baseline the COMMIT-time gate enforces: `--check --tracked-only`, the same
#: predicate the pre-commit hook runs.
BASELINE = ROOT / "logs" / "anti_pattern_baseline.json"
#: The baseline for the OTHER predicate -- every `.py` under the directory,
#: untracked scratch included. Two predicates mean two sets of counts, so they get
#: two files; keeping one file and refusing the mismatch made a plain `--check`
#: unrunnable by design, which is not a state a diagnostic flag should be in.
WORKTREE_BASELINE = ROOT / "logs" / "anti_pattern_baseline.worktree.json"

#: How many files the failure output names per regressed category. A cap keeps a
#: widely-repeated class (an emoji in 400 files) from burying the one that
#: caused the commit; it is always REPORTED when it truncates, never silent.
ATTRIBUTION_LIMIT = 5


def scan(target: str, tracked_only: bool = False) -> tuple[Counter, int]:
    """Return (per-category counts, files scanned) for a directory.

    Imports the scanner programmatically rather than shelling out, so the gate
    and the hook measure the SAME instrument the audit measured.

    --tracked-only (2026-10-04): scan only `git ls-files` paths. The default
    worktree scan counts untracked scratch (session probes, dated one-shots)
    that are not part of any commit; gating a commit on the desk drawer
    blocks reviewed changesets for files no commit contains. Tracked-only
    keeps the gate on the commit while the full scan stays one flag away.
    """
    counts, files, _per_file = _tally(_scan_results(target, tracked_only), target)
    return counts, files


def _scan_results(target: str, tracked_only: bool = False) -> list:
    """Scan the in-scope files once. The ONE place a scan's scope is decided.

    `scan` and `scan_with_files` both come through here, so they cannot disagree
    about which files a run covered (or about what `--tracked-only` selected).
    The scanner is imported lazily because importing it is not free and this
    module must stay importable from a bare checkout.
    """
    from CORE.compliance.scanner import ASTAntiPatternScanner  # noqa: PLC0415

    if tracked_only:
        proc = subprocess.run(
            ["git", "ls-files", "--full-name", "-z"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            cwd=str(ROOT))
        prefix = target.strip("/") + "/"
        files = [str(ROOT / p) for p in proc.stdout.split("\0")
                 if p.startswith(prefix) and p.endswith(".py")]
        results = ASTAntiPatternScanner().scan_files(files)
    else:
        results = ASTAntiPatternScanner().scan_directory(str(ROOT / target))
    return results


def _git_z(args: list[str]) -> list[str]:
    """Run git and split its NUL-separated output.

    The platform codec is not good enough here, and the failure is SILENT: git
    emits UTF-8 paths, and on Windows the default cp1252 decode raises
    `UnicodeDecodeError` inside subprocess's reader thread, which surfaces as an
    EMPTY stdout rather than as an error. Measured 2026-10-13: that scored a
    file holding two findings as "0 at HEAD" and produced a phantom attribution
    -- a whole-file delta that was an artefact of decoding, not of the code.
    `errors="replace"` keeps a mangled byte mangled instead of a file missing.
    """
    proc = subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120,
                          cwd=str(ROOT))
    if proc.returncode != 0:
        # Loud, deliberately. An empty list here is indistinguishable from
        # "nothing changed", and the caller would then print a plausible, WRONG
        # explanation ("no changed file carries it") for what is really a git
        # failure -- the same class of silent-wrong-answer this file exists to
        # prevent.
        raise RuntimeError("git %s failed (rc=%d): %s"
                           % (" ".join(args), proc.returncode,
                              proc.stderr.strip()[:200]))
    return [p for p in proc.stdout.split("\0") if p.strip()]


def _changed_paths(target: str) -> set[str]:
    """Scan-root-relative paths under `target` whose CONTENT differs from HEAD.

    A repo-wide category count can only grow through a file whose content
    changed, so this is the candidate set for blame when the baseline carries no
    per-file counts. Untracked files count too: they are inside the scan and are
    how a new finding usually arrives.

    Paths are normalised to forward slashes because `per_file` keys come from
    `Path.relative_to`, which keeps whatever separator the platform produced.
    Comparing an unnormalised pair would silently attribute nothing on Windows.
    """
    prefix = target.strip("/") + "/"
    raw = (_git_z(["diff", "--name-only", "-z", "HEAD", "--", target])
           + _git_z(["ls-files", "--others", "--exclude-standard", "-z",
                     "--", target]))
    return {p[len(prefix):].replace("\\", "/") for p in raw
            if p.startswith(prefix) and p.endswith(".py")}


def _tally(results: list, target: str) -> tuple[Counter, int, dict[str, Counter]]:
    """Turn scan results into (per-category counts, files, per-file counts).

    ONE place turns results into counts, so the whole-tree total and the
    per-file attribution cannot drift apart -- the attribution IS the same
    measurement read at a finer grain, and summing it reproduces the total.

    `file_count` stays ``len(results)``: the number of files SCANNED, not the
    number carrying a finding, so `scan`'s existing callers see exactly the
    numbers they saw before.

    ``results`` is a ``list[AntiPatternResult]``; annotated loosely because the
    scanner is imported lazily inside `_scan_results`.
    """
    root = ROOT / target
    counts: Counter = Counter()
    per_file: dict[str, Counter] = {}
    for r in results:
        if not r.findings:
            continue
        try:
            rel = str(Path(r.filepath).relative_to(root))
        except ValueError:
            # Not under the scan root (a caller-supplied path). Keep it visible
            # rather than dropping the finding from the view that explains the
            # total.
            rel = r.filepath
        bucket = per_file.setdefault(rel, Counter())
        for _line, category, _snippet in r.findings:
            counts[category] += 1
            bucket[category] += 1
    return counts, len(results), per_file


def scan_with_files(target: str, tracked_only: bool = False) -> tuple[Counter, int, dict[str, Counter]]:
    """`scan`, plus per-FILE attribution of every category.

    `scan` answers "did this class get worse"; this answers "in which file",
    which is the question a blocked committer actually has. Earned 2026-10-13:
    an unrelated concurrent edit to two `src/` files took the repo-wide count
    over baseline and blocked a commit whose own diff was a single test file,
    and the per-category delta alone could not name the file responsible.

    Shares `_scan_results` with `scan`, so scope is decided in exactly one place
    and the two functions cannot disagree about what was measured.
    """
    return _tally(_scan_results(target, tracked_only), target)


def _print_attribution(categories: list[str], per_file: dict[str, Counter],
                       base_file_counts: dict | None = None,
                       target: str = DEFAULT_DIR) -> None:
    """Name the files that EXPLAIN each regression -- a delta, never a ranking.

    Earned 2026-10-13: the first version of this listed the files with the
    largest CURRENT count of a category. On the live tree it named
    `tests/test_ecosystem.py` (32 findings, untouched by any change) as the owner
    of a regression actually carried by `CORE/skill_library.py` (2 -> 3), which
    the list did not mention at all. A holder is not a cause, and the committer
    still had to bisect the worktree by hand.

    Two modes, and the header ALWAYS says which, so the weaker reference can
    never be mistaken for the exact one:

    * EXACT -- the baseline carries `file_counts`: per file `now - baseline`,
      positive deltas only. This is the real answer.
    * FALLBACK -- every baseline recorded before `file_counts` existed: the
      files whose content changed vs HEAD, the only route by which a repo-wide
      total can grow.

    Either way, a regressed category with no file to blame is reported as
    exactly that. Silence there would read as "the tree is fine", which is the
    opposite of what an unexplained increase means -- and the truncation notice
    exists for the same reason: a capped list that does not admit its cap is a
    quiet claim to be complete.
    """
    want_exact = bool(base_file_counts)
    base = {str(k).replace("\\", "/"): v
            for k, v in (base_file_counts or {}).items()}
    changed = set() if want_exact else _changed_paths(target)
    print("    reference: %s" % (
        "per-file increase vs baseline" if want_exact else
        "changed-files view -- this baseline has no per-file counts, so blame "
        "falls on content that changed; re-run --baseline to record them and "
        "get exact deltas"))
    for cat in categories:
        owners: list[tuple[str, int]] = []
        for raw_rel, counts in per_file.items():
            rel = str(raw_rel).replace("\\", "/")
            if want_exact:
                grew = counts.get(cat, 0) - base.get(rel, {}).get(cat, 0)
                if grew > 0:
                    owners.append((rel, grew))
            elif counts.get(cat) and rel in changed:
                owners.append((rel, counts[cat]))
        if not owners:
            if want_exact:
                print("      %s: no per-file increase in the scan -- the "
                      "category grew without any single file growing (a "
                      "partial baseline?) -- could not be attributed" % cat)
            else:
                print("      %s: no CHANGED file carries it -- the increase is "
                      "NOT explained by the working tree (stale baseline?)"
                      % cat)
            continue
        owners.sort(key=lambda kv: (-kv[1], kv[0]))
        print("    %s:" % cat)
        for rel, n in owners[:ATTRIBUTION_LIMIT]:
            # A delta carries its sign; a changed-file COUNT must not, or the
            # fallback would print a count as if it were an increase.
            print(("      %+5d  %s" if want_exact else "      %5d  %s")
                  % (n, rel))
        omitted = len(owners) - ATTRIBUTION_LIMIT
        if omitted > 0:
            print("      (+%d more file(s) not shown)" % omitted)


def baseline_payload(counts: Counter, files: int, target: str,
                     file_counts: dict[str, Counter] | None = None,
                     tracked_only: bool = False) -> dict:
    """The baseline document. `file_counts` is additive and optional.

    Every existing key keeps its name and meaning, so a baseline recorded
    before per-file counts existed still loads, still gates, and still reports
    the same numbers (such a baseline merely cannot attribute a failure
    exactly -- see `_print_attribution`, which says so).
    """
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "identity_scheme": IDENTITY_SCHEME,
        "scanner": "CORE.compliance.scanner",
        "target": target,
        "files_scanned": files,
        "category_counts": dict(sorted(counts.items())),
        "total_findings": sum(counts.values()),
        #: The SCAN PREDICATE, recorded so `--check` can refuse a comparison it
        #: cannot make. Measured 2026-11-01: `--baseline --tracked-only` was
        #: redirected to a `.partial` file that `--check --tracked-only` never
        #: read, while the check compared TRACKED counts against a WORKTREE
        #: baseline -- so the ratchet went red on untracked scratch from another
        #: lane (968 vs 953 import_inside_function, all of it untracked). A
        #: baseline is only comparable to a scan with the same scope.
        "tracked_only": tracked_only,
        # WHO recorded this, so a rewrite has an attributable author. Two lanes
        # share this worktree; before this, a baseline that changed under a
        # running check left no trace of the process that changed it.
        "writer_pid": os.getpid(),
    }
    if file_counts:
        payload["file_counts"] = {
            str(rel): dict(sorted(bucket.items()))
            for rel, bucket in sorted(file_counts.items()) if bucket
        }
    return payload


def _baseline_fingerprint(path: Path) -> str:
    """sha256 of the baseline bytes; "" when it cannot be read.

    A count comparison is only meaningful against the bytes this process
    actually loaded. `cmd_check` takes this before loading and again after
    scanning; a difference means another writer replaced the file mid-run.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        # "" is a legitimate "no reference" value -- an absent baseline and an
        # unreadable one both leave the comparison nothing to anchor to -- but
        # the difference between those two is exactly what a reader needs, and
        # returning silently made a permissions error look like a first run.
        print("no baseline fingerprint (%s): %s" % (path, exc), file=sys.stderr)
        return ""


def load_baseline(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("no baseline at %s -- run --baseline first" % path, file=sys.stderr)
        raise SystemExit(2)
    except (OSError, ValueError) as exc:
        print("baseline at %s is unreadable: %s" % (path, exc), file=sys.stderr)
        raise SystemExit(2)


def baseline_path(tracked_only: bool) -> Path:
    """The baseline file for a scan predicate. One reader per file, always."""
    return BASELINE if tracked_only else WORKTREE_BASELINE


def _resolve_out(path_arg: str | None, narrowed: bool, kind: str,
                 tracked_only: bool = True) -> Path:
    """Obey an explicit path; redirect a narrowed run to a `.partial` sibling."""
    if path_arg:
        return Path(path_arg).resolve()
    if narrowed:
        return BASELINE.with_name(BASELINE.stem + ".partial" + BASELINE.suffix)
    return baseline_path(tracked_only)


def cmd_baseline(target: str, out: Path, narrowed: bool,
                 tracked_only: bool = False) -> int:
    counts, files, per_file = scan_with_files(target, tracked_only)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        baseline_payload(counts, files, target, per_file, tracked_only), indent=2)
    # Replace, never truncate-and-write: a reader (another lane's --check, or the
    # pre-commit hook) that opens this path mid-write used to see a partial JSON
    # document and die with "baseline is unreadable", which names the symptom and
    # not the race. A same-directory temp + os.replace makes the swap atomic.
    tmp = out.with_name("%s.tmp%d" % (out.name, os.getpid()))
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, out)
    # One line per write, beside the baseline: the journal the ratchet did not
    # have. Append-only, so a rewrite is visible after the fact instead of
    # surviving only as a changed count.
    journal = out.with_suffix(".journal.jsonl")
    entry = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "argv": sys.argv[:8],
        "target": target,
        "tracked_only": tracked_only,
        "files": files,
        "total_findings": sum(counts.values()),
    }
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    print("baseline written: %s" % out)
    print("  scope: %s" % ("git-tracked files only" if tracked_only
                            else "every .py under the directory (worktree)"))
    if narrowed:
        print("  (narrowed to %r -- whole-tree --check will not read this file)"
              % target)
    print("  %d file(s), %d finding(s)" % (files, sum(counts.values())))
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("    %-26s %5d" % (cat, n))
    return 0


def cmd_report(target: str, tracked_only: bool = False) -> int:
    counts, files = scan(target, tracked_only)
    print("anti-pattern report -- %s" % (ROOT / target))
    print("  %d file(s), %d finding(s)" % (files, sum(counts.values())))
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("    %-26s %5d" % (cat, n))
    return 0


def cmd_check(target: str, baseline_path: Path,
              tracked_only: bool = False) -> int:
    base = load_baseline(baseline_path)
    if base.get("identity_scheme") != IDENTITY_SCHEME:
        print("baseline %s uses identity_scheme=%r, this gate uses %r -- re-run "
              "--baseline to convert (counts are not comparable across schemes)"
              % (baseline_path, base.get("identity_scheme"), IDENTITY_SCHEME),
              file=sys.stderr)
        return 2

    # The PREDICATE, not just the scheme. A worktree baseline counts untracked
    # scratch a commit never contains, and a tracked baseline does not; diffing
    # one against the other reports regressions that belong to no changeset (and
    # passes ones that do). Old baselines have no key -- they were worktree
    # scans, which is what the key defaults to.
    if bool(base.get("tracked_only", False)) != tracked_only:
        print("baseline %s was recorded %s but this run scans %s -- not comparable. "
              "Re-record it with the same flag:\n    python tools/anti_pattern_gate.py "
              "--baseline%s"
              % (baseline_path,
                 "tracked-files-only" if base.get("tracked_only") else "over the whole worktree",
                 "tracked files only" if tracked_only else "the whole worktree",
                 " --tracked-only" if tracked_only else ""),
              file=sys.stderr)
        return 2

    recorded: dict = base.get("category_counts") or {}
    before = _baseline_fingerprint(baseline_path)
    current, files, per_file = scan_with_files(target, tracked_only)

    # The baseline is a shared file in a worktree with many lanes. If it changed
    # while this scan ran, the counts below are compared against a document this
    # process no longer holds -- which can read as a clean pass OR as a
    # regression that belongs to nobody. Refuse, and say which file moved.
    after = _baseline_fingerprint(baseline_path)
    if before and after != before:
        print("REFUSED: %s changed while this scan was running (%s -> %s).\n"
              "  Another writer replaced the baseline mid-run, so these counts"
              " were not measured\n  against a file this process still holds."
              " Refusing to report a pass or a regression.\n"
              "  Re-run the check (and see %s for who wrote it)."
              % (baseline_path, before[:12] or "gone", after[:12] or "gone",
                 baseline_path.with_suffix(".journal.jsonl")),
              file=sys.stderr)
        return 2

    regressions = []
    for cat, n in current.items():
        was = recorded.get(cat, 0)
        if n > was:
            regressions.append((cat, was, n))

    improved = []
    for cat, was in recorded.items():
        n = current.get(cat, 0)
        if n < was:
            improved.append((cat, was, n))

    new_categories = sorted(set(current) - set(recorded))
    gone_categories = sorted(set(recorded) - set(current))

    total_now = sum(current.values())
    total_was = sum(recorded.values())

    print("anti-pattern gate -- %s" % (ROOT / target))
    print("  scope:    %s" % ("git-tracked files only" if tracked_only
                              else "every .py under the directory (worktree)"))
    print("  now:      %d finding(s) in %d file(s)" % (total_now, files))
    print("  baseline: %d finding(s) (%s)"
          % (total_was, base.get("recorded_at", "?")))

    if new_categories:
        print("\n  NEW category/categories (not in baseline):")
        for cat in new_categories:
            print("    %-26s %5d" % (cat, current[cat]))

    if regressions:
        print("\n  REGRESSIONS (count increased):")
        for cat, was, n in sorted(regressions, key=lambda t: -(t[2] - t[1])):
            print("    %-26s %5d -> %5d  (+%d)" % (cat, was, n, n - was))

    if improved:
        print("\n  improved (count decreased -- re-run --baseline to ratchet down):")
        for cat, was, n in sorted(improved, key=lambda t: t[2] - t[1]):
            print("    %-26s %5d -> %5d  (%d)" % (cat, was, n, n - was))

    if gone_categories:
        print("\n  cleared (recorded, now zero): %s" % ", ".join(gone_categories))

    if regressions or new_categories:
        # Worst delta first, deduped: a NEW category is also a regression
        # (recorded 0 -> n), so without the dedupe it would be attributed twice.
        order = [cat for cat, _was, _n
                 in sorted(regressions, key=lambda t: -(t[2] - t[1]))]
        for cat in new_categories:
            if cat not in order:
                order.append(cat)
        print("\n  WHERE (per-file, worst first, capped at %d per category):"
              % ATTRIBUTION_LIMIT)
        _print_attribution(order, per_file, base.get("file_counts"), target)
        print("\nFAIL: %d regression(s) and %d new category/categories. New "
              "anti-patterns are not allowed; fix them, or -- if a count is a "
              "deliberate, reviewed increase -- re-run --baseline."
              % (len(regressions), len(new_categories)))
        return 1

    print("\nOK: no anti-pattern regressions.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fail on NEW anti-patterns, over a recorded baseline.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--baseline", action="store_true",
                       help="record the current counts as the baseline")
    group.add_argument("--check", action="store_true",
                       help="exit 1 on a count increase or a new category")
    group.add_argument("--report", action="store_true",
                       help="run and print, do not gate")
    ap.add_argument("--dir", default=DEFAULT_DIR,
                    help="directory to scan, repo-relative (default: %s)" % DEFAULT_DIR)
    ap.add_argument("--baseline-file", metavar="JSON")
    ap.add_argument("--tracked-only", action="store_true",
                    help="scan only git-tracked files (ignore untracked scratch)")
    args = ap.parse_args(argv)

    target = args.dir.replace("\\", "/")
    narrowed = target.strip("/") != DEFAULT_DIR
    tracked = bool(args.tracked_only)
    # `--tracked-only` is NOT a narrowing: it selects the same directory under a
    # different file predicate, and it is the predicate `--check --tracked-only`
    # (the hook) reads. Treating it as narrowed wrote a `.partial` file nothing
    # consumed while the check diffed against the worktree baseline -- measured
    # 2026-11-01. Only `--dir` redirects.

    if args.baseline:
        out = _resolve_out(args.baseline_file, narrowed, "baseline", tracked)
        return cmd_baseline(target, out, narrowed, tracked)
    if args.report:
        return cmd_report(target, tracked)
    # The check reads the baseline recorded under the SAME predicate, so the two
    # scopes can each be gated instead of one refusing.
    path = (Path(args.baseline_file).resolve() if args.baseline_file
            else baseline_path(tracked))
    return cmd_check(target, path, tracked)


if __name__ == "__main__":
    sys.exit(main())
