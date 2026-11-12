#!/usr/bin/env python3
# coding: utf-8
"""Type-check gate — fail on NEW diagnostics, over a recorded baseline.

Why basedpyright and not mypy
-----------------------------
mypy was the check the CI file asked for and it does not work on this tree. Two
measured reasons, both fatal rather than cosmetic:

* **It cannot read the repository.** `python -m mypy src/CORE/` aborts with
  `ValueError: path is on mount '\\\\.\\nul'` because two literal files named
  `nul` sat in the tree (junk from a `dir /b src\\... > nul` run inside Git Bash,
  where `> nul` is a real file and not the Windows device). Removed
  2026-09-22; the crash was never a mypy bug, but it is why "type safety is
  unmeasured" was true for months.
* **It answers a narrower question.** On the same tree mypy reports 655 errors
  in 134 files over `src/CORE/`; basedpyright reports 1,191 errors + 252 warnings
  over 690 files in 64 seconds, and the classes it adds are the ones this
  codebase actually suffers from — `reportUndefinedVariable` (94),
  `reportPossiblyUnboundVariable` (17), `reportOptionalMemberAccess` (120)
  against try/except-ImportError and Optional returns. mypy's run took ~6
  minutes; this took ~1.

So the instrument is `basedpyright`, which the venv already had installed
(1.40.1) and which reads the repo's existing `pyrightconfig.json`. mypy is kept
installed and is still useful as a *second* opinion on a single file; it is no
longer the gate.

Why a baseline rather than a clean run
--------------------------------------
1,191 errors are a real debt, not a regression, and this project's own rule is
that a check failing on facts gets switched off. So the gate compares against
`logs/basedpyright_baseline.json`:

* a diagnostic identity not in the baseline -> exit 1,
* a baseline identity no longer reported     -> reported, exit 0, with a hint,
* a checker that could not run               -> exit 1 (no output is not a pass).

Identity is `relpath:rule:anchor`, where `anchor` is the source line the
diagnostic points at, whitespace-normalised, plus an ordinal when a file repeats
the same `(rule, anchor)` pair. The message text is deliberately NOT part of it,
so rewording a message is not a "fix" and a changed message is not a regression.

Why not `file:line:col:rule` (the first scheme, and what Error #203 measured)
----------------------------------------------------------------------------
Positional ids are invalidated by any edit above a finding. A session that
introduced no type defect at all reported **15 new diagnostics and 6 gone**; the
six "new" ones were the six "gone" ones, moved down six files by unrelated
edits, and only reading both lists told you so. Worse, the id carried an
ABSOLUTE path, so the same tree on another machine or user account shares not one
id with the baseline. A key that an unrelated edit can change is a position, not
an identity (Lesson #97).

Anchoring on the source line fixes both: lines inserted above a finding change no
id, and the path is repo-relative. The ordinal keeps the collapsing honest -- two
diagnostics of one rule pointing at identical text are still two findings, and
one of them disappearing still shows up as a missing id rather than passing.

A scheme change cannot be compared across, so the baseline records which scheme
produced it and `--check` refuses to diff across schemes instead of reporting
thousands of false NEW ids. That refusal is the upgrade path.

Usage
-----
    python tools/type_check_gate.py --check      # CI + pre-commit: exit 1 on new
    python tools/type_check_gate.py --baseline   # record what is known
    python tools/type_check_gate.py --report     # run, write report, no gate
    python tools/type_check_gate.py --check --only src/CORE/neural_loop.py
    python tools/type_check_gate.py --diff-from logs/type_report.json

A narrowed run writes somewhere else (ADR-012)
----------------------------------------------
`--only` selects a SUBSET, so its diagnostics are not the whole-tree baseline's
diagnostics. Without this rule `--baseline --only src/CORE/` would replace
`logs/basedpyright_baseline.json` with one subtree's findings under a filename
that claims the whole tree, and the next `--check` would call every diagnostic
outside that subtree a regression. So a narrowed run with no explicit
`--baseline-file` / `--report-file` is redirected to a `.partial` sibling and
says so; an explicit path is obeyed as written. Same rule, one implementation:
`tools/evidence_paths.py`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: Shared ADR-012 rule: a derived artifact's path follows the scope it measured.
import evidence_paths as evidence

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "logs" / "basedpyright_baseline.json"
REPORT = REPO / "logs" / "basedpyright_report.json"

#: Console script next to the running interpreter, then the module, then the
#: Node build. The venv is preferred because it pins the version; `npx` is the
#: fallback so the gate still works from a bare checkout in CI.
_TOOL_CANDIDATES = ("basedpyright", "pyright")


def resolve_checker() -> Optional[List[str]]:
    """Command prefix that runs the checker, or None if none is available."""
    bindir = Path(sys.executable).resolve().parent
    for name in _TOOL_CANDIDATES:
        exe = bindir / (name + (".exe" if sys.platform == "win32" else ""))
        if exe.exists():
            return [str(exe)]
    for name in _TOOL_CANDIDATES:
        found = shutil.which(name)
        if found:
            return [found]
    if shutil.which("npx"):
        return ["npx", "--yes", "pyright"]
    return None


def configured_targets() -> List[str]:
    """The default scope: what `pyrightconfig.json` includes.

    Read rather than copied, so `--only` is measured against the scope the
    checker actually uses by default.
    """
    try:
        config = json.loads((REPO / "pyrightconfig.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    include = config.get("include")
    return [str(x) for x in include] if isinstance(include, list) else []


def run_checker(targets: Optional[Sequence[str]] = None,
                timeout: int = 1200) -> Tuple[int, str]:
    """Run the checker. Returns (returncode, stdout). 125 = could not run."""
    cmd = resolve_checker()
    if cmd is None:
        return 125, ""
    cmd = cmd + ["--outputjson"]
    if targets:
        cmd += list(targets)
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True,
                              text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("checker failed to run: %s" % exc, file=sys.stderr)
        return 125, ""
    return proc.returncode, proc.stdout or ""


#: Bumped whenever `diagnostic_ids` changes shape. A baseline recorded under a
#: different scheme cannot be diffed against, and `--check` says so instead of
#: reporting every id as new and every baseline id as gone.
IDENTITY_SCHEME = "content-anchored-v1"

#: `path -> {line: normalised source}`. Reading each diagnosed file once keeps the
#: identity cheap: only files that already have a diagnostic are opened.
_LINE_CACHE: Dict[str, Dict[int, str]] = {}


def _repo_relative(path: str) -> str:
    """Repo-relative, forward-slashed. Absolute paths made ids machine-specific."""
    raw = str(path or "").replace("\\", "/")
    try:
        return str(Path(raw).resolve().relative_to(REPO)).replace("\\", "/")
    except (ValueError, OSError):
        return raw


def _source_lines(relpath: str) -> Dict[int, str]:
    """Line number -> whitespace-normalised text, for one file, cached."""
    cached = _LINE_CACHE.get(relpath)
    if cached is not None:
        return cached
    out: Dict[int, str] = {}
    try:
        text = (REPO / relpath).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for index, line in enumerate(text.splitlines(), start=1):
        out[index] = " ".join(line.split())
    _LINE_CACHE[relpath] = out
    return out


def _anchor(relpath: str, line: int, character: int) -> str:
    """The source line as identity text; falls back to the position when unread.

    The fallback is a position and carries a marker saying so, because a silent
    fallback would reintroduce exactly the instability this scheme removed for
    every unreadable file.
    """
    text = _source_lines(relpath).get(line)
    if text:
        return text
    return "@pos:%d:%d" % (line, character)


def diagnostic_ids(payload: Dict[str, object]) -> Set[str]:
    """Stable identity per diagnostic: relpath:rule:anchor[#ordinal].

    A message is prose; prose gets reworded, and a gate that treats rewording as
    a change teaches people to ignore it. A LINE NUMBER is not prose but it is
    just as unstable -- see the module docstring and Error #203.

    Ordinals: diagnostics are numbered per `(relpath, rule, anchor)`, so two
    same-rule findings on identical text stay two ids, and one of them being
    fixed shows up as a missing id. Nothing collapses silently.
    """
    out: Set[str] = set()
    seen: Dict[str, int] = {}
    for diag in payload.get("generalDiagnostics") or []:  # type: ignore[union-attr]
        rng = diag.get("range") or {}
        start = rng.get("start") or {}
        line = int(start.get("line", -1))
        col = int(start.get("character", -1))
        relpath = _repo_relative(str(diag.get("file") or ""))
        rule = str(diag.get("rule") or "no-rule")
        head = "%s:%s:%s" % (relpath, rule, _anchor(relpath, line + 1, col + 1))
        ordinal = seen.get(head, 0)
        seen[head] = ordinal + 1
        out.add(head if ordinal == 0 else "%s#%d" % (head, ordinal + 1))
    return out


def severity_counts(payload: Dict[str, object]) -> Dict[str, int]:
    """Errors and warnings, counted, so a growing warning pile is visible."""
    counts = {"error": 0, "warning": 0, "information": 0}
    for diag in payload.get("generalDiagnostics") or []:  # type: ignore[union-attr]
        key = str(diag.get("severity") or "error")
        counts[key] = counts.get(key, 0) + 1
    return counts


def rule_histogram(payload: Dict[str, object], top: int = 10) -> Dict[str, int]:
    tally: Dict[str, int] = {}
    for diag in payload.get("generalDiagnostics") or []:  # type: ignore[union-attr]
        rule = str(diag.get("rule") or "no-rule")
        tally[rule] = tally.get(rule, 0) + 1
    return dict(sorted(tally.items(), key=lambda kv: -kv[1])[:top])


def contract_summary(payload: Dict[str, object]) -> Dict[str, object]:
    """The summary fields that describe the TREE, not the stopwatch.

    `timeInSec` is excluded on purpose. The report is rewritten by every run and
    the baseline only by `--baseline`, so the two files legitimately come from
    different runs; if wall-clock time were part of the contract, the two would
    ALWAYS disagree and the agreement test would have to be deleted. Keeping the
    field in the report (where it is useful) and out of the contract (where it is
    noise) is what lets the baseline and the report be compared at all.
    """
    summary = dict(payload.get("summary") or {})
    summary.pop("timeInSec", None)
    return summary


def baseline_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "identity_scheme": IDENTITY_SCHEME,
        "checker": "basedpyright (pyrightconfig.json, typeCheckingMode basic)",
        "summary": contract_summary(payload),
        "severity_counts": severity_counts(payload),
        "top_rules": rule_histogram(payload),
        "diagnostic_ids": sorted(diagnostic_ids(payload)),
    }


def load_baseline(path: Path) -> Dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_ids(path: Path) -> Set[str]:
    return set(load_baseline(path).get("diagnostic_ids") or ())


def diff_against_baseline(current: Iterable[str],
                          known: Iterable[str]) -> Dict[str, List[str]]:
    cur, base = set(current), set(known)
    return {"new": sorted(cur - base), "fixed": sorted(base - cur),
            "unchanged": sorted(cur & base)}


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    global BASELINE, REPORT
    parser = argparse.ArgumentParser(
        description="Gate the type checker on NEW diagnostics")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="exit 1 when a diagnostic is not in the baseline")
    group.add_argument("--baseline", action="store_true",
                       help="record the current diagnostics as known")
    group.add_argument("--report", action="store_true",
                       help="run and write the report without gating")
    group.add_argument("--diff-from", metavar="JSON",
                       help="skip the run and diff a saved report")
    parser.add_argument("--only", nargs="+", metavar="PATH",
                        help="paths to check (default: pyrightconfig include)")
    parser.add_argument("--baseline-file", metavar="JSON")
    parser.add_argument("--report-file", metavar="JSON")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args(argv)

    # ADR-012: a path follows the scope it measured. `--only` narrows the check
    # to a subtree, so its baseline and report are a subtree's evidence and are
    # redirected unless the caller named them explicitly -- otherwise
    # `--baseline --only src/CORE/` would replace the whole-tree baseline with
    # the diagnostics of one subtree under a filename that claims the whole tree.
    expected = configured_targets()
    # An unreadable config must not let a narrowed run take the whole-tree path:
    # with no declared scope to compare against, an explicit selection counts as
    # narrowed. (Fail-closed, because the alternative is the defect it guards.)
    narrowed = (evidence.is_narrowed(expected, args.only) if expected
                else bool(args.only))
    if args.baseline_file:
        BASELINE = Path(args.baseline_file).resolve()
    if args.report_file:
        REPORT = Path(args.report_file).resolve()
    if narrowed:
        if not args.baseline_file:
            BASELINE = evidence.partial(BASELINE)
        if not args.report_file:
            REPORT = evidence.partial(REPORT)
        print(evidence.narrowed_note("the configured type-check scope", REPORT,
                                     BASELINE))

    if args.diff_from:
        payload = {"generalDiagnostics": [
            {"file": i.rsplit(":", 3)[0], "range": {"start": {}},
             "rule": i.rsplit(":", 1)[-1]} for i in sorted(
                load_ids(Path(args.diff_from)))]}
        rc = 0
    else:
        rc, out = run_checker(args.only, args.timeout)
        if rc == 125:
            print("NO CHECKER: neither basedpyright nor pyright could be run; "
                  "treating this as a failure because an unchecked tree must "
                  "not read as a clean one", file=sys.stderr)
            return 1
        try:
            payload = json.loads(out)
        except ValueError:
            print("checker produced no JSON (exit %s); not a pass" % rc,
                  file=sys.stderr)
            return 1

    ids = diagnostic_ids(payload)
    counts = severity_counts(payload)
    summary = payload.get("summary") or {}
    files = summary.get("filesAnalyzed")
    print("type check: %s errors / %s warnings over %s files"
          % (counts.get("error"), counts.get("warning"), files))
    for rule, n in rule_histogram(payload).items():
        print("  %5d %s" % (n, rule))

    if not args.diff_from:
        _write_json(REPORT, {
            "recorded_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "checker": "basedpyright",
            "summary": contract_summary(payload), "severity_counts": counts,
            "top_rules": rule_histogram(payload),
            "diagnostic_ids": sorted(ids)})

    if args.baseline:
        _write_json(BASELINE, baseline_payload(payload))
        print("baseline recorded: %d known diagnostics -> %s" % (len(ids),
                                                                 BASELINE))
        return 0

    if not args.check:
        return 0

    baseline_doc = load_baseline(BASELINE)
    known = set(baseline_doc.get("diagnostic_ids") or ())
    if not known and not BASELINE.exists():
        print("no baseline at %s -- run --baseline first" % BASELINE,
              file=sys.stderr)
        return 1

    # A baseline recorded under a different identity scheme shares no ids with
    # this run, so diffing it would report every diagnostic as new AND every
    # baseline id as fixed -- two lists of a thousand lines that describe the
    # scheme, not the code. Refusing is the honest upgrade path.
    recorded_scheme = str(baseline_doc.get("identity_scheme") or "positional-v0")
    if recorded_scheme != IDENTITY_SCHEME:
        print(
            "baseline %s was recorded under identity scheme %r, this gate now "
            "uses %r -- re-run --baseline to convert (the ids are not "
            "comparable, so nothing here is reported as new)"
            % (BASELINE, recorded_scheme, IDENTITY_SCHEME), file=sys.stderr)
        return 1

    delta = diff_against_baseline(ids, known)
    for ident in delta["new"]:
        print("NEW DIAGNOSTIC: %s" % ident)
    for ident in delta["fixed"]:
        print("no longer reported: %s" % ident)
    if delta["fixed"]:
        print("(%d baseline diagnostic(s) are gone -- re-run --baseline to "
              "tighten the gate)" % len(delta["fixed"]))
    if delta["new"]:
        print("FAIL: %d new diagnostic(s) vs %s" % (len(delta["new"]), BASELINE))
        return 1
    print("OK: no new diagnostics (%d known, %d unchanged)"
          % (len(known), len(delta["unchanged"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
