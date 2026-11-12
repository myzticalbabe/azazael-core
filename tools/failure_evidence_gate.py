#!/usr/bin/env python3
# coding: utf-8
"""Failure-evidence gate — unbounded waits and unnamed encodings, over a baseline.

Why this exists as its own counter
----------------------------------
The REVIVE-RUNNER lane (Engram #653, 2026-10-14) fixed five sites where a
runner DROPPED the evidence of its own failure: an authoritative return code
replaced by `None` or by a verdict word, partial stdout/stderr discarded when
`subprocess.TimeoutExpired` already carried them, a launch-failure reason
logged as a bare `START-FAIL`. Two of the three defects behind those sites ARE
statically visible — an unbounded wait, and a text-mode call that does not name
its encoding — and the ratchet that would catch them (`anti_pattern_gate.py`)
is `src/`-SCOPED. It walks `src/CORE/compliance/scanner.py`, so every
`subprocess` call in `scripts/` and `tools/` is invisible to it. That asymmetry
is the reason this gate is separate rather than folded in.

`tools/encoding_audit.py` already owns the encoding half and is stricter about
it (it also catches a symbolic `text=` value). This gate re-counts encodings
only so that one `--check` answers both halves of "would this call tell me what
happened"; the counts are expected to agree, and a disagreement means one of
the two detectors has a bug.

WHAT IT COUNTS (per file, AST-based — never a line-oriented grep)
-----------------------------------------------------------------
  no_timeout   `subprocess.run` / `call` / `check_output` / `check_call` with
               no `timeout=`. These four always BLOCK, so a missing bound is a
               wait that can outlive the thing that started it.
  no_encoding  a text-mode call (literal `text=True` / `universal_newlines=True`,
               or an `encoding=`/`errors=` kwarg, which is inert outside text
               mode) with no `encoding=`. On Windows the platform codec then
               decodes the output, and non-ASCII raises inside subprocess's
               reader thread where it does NOT reach the caller: the call looks
               successful and yields EMPTY stdout.

WHAT IT DELIBERATELY DOES NOT COUNT
-----------------------------------
  * `Popen` is exempt from `no_timeout`: it does not wait by itself, so a
    missing `timeout=` there is not a missing bound. Whether a LATER
    `.wait()`/`.communicate()` is bounded is not modelled.
  * Failure-path evidence LOSS is not statically visible at all — `rc = None`,
    `"out": ""`, a reason dropped by a bare `except` returning `None`. Those
    were the actual defects, and they are pinned by tests
    (`tests/test_runner_failure_evidence.py`), not by this gate. A clean run
    here does NOT mean the failure paths are sound.
  * A `**kwargs` splat could supply either kwarg, so such a call is reported as
    UNDETERMINED and counted in neither direction. Two of the three sites this
    lane removed were exactly that shape — the honest fix was to delete the
    forwarding seam, which is why they are now statically clear.

An unarmed gate is a RED gate, not a green one
---------------------------------------------
`--check` with no recorded baseline used to print "cannot fail" and exit 0,
and the baseline lives in gitignored `logs/`, so a fresh clone always had none --
the gate was silently inert on every machine that had not armed it. That is the
same "absence of evidence reported as a pass" shape the engram MCP guard
explicitly refuses (no session log is exit 2, never exit 0), and it made the
first version of this file undetectably useless: it CORRECTLY reported the one
site that existed and still exited 0. It now exits 2 when unarmed, and a plain
run exits 1 when it finds a site, so the invariant is checkable with no
baseline at all.

Deliberate exemptions
---------------------
A call whose unbounded wait is intentional carries the marker
`# gate:allow-no-timeout: <reason>` on a line the call itself spans. The reason
is mandatory -- an empty one counts as no marker, so a bare marker cannot be
used to silence a real finding -- and every exemption is COUNTED AND PRINTED,
so granting one stays a disclosed decision rather than a hidden one.

Usage
-----
    python tools/failure_evidence_gate.py                 # exit 1 when a site is found
    python tools/failure_evidence_gate.py --baseline      # record what is known
    python tools/failure_evidence_gate.py --check         # 1 on an INCREASE, 2 if unarmed
    python tools/failure_evidence_gate.py --check --dir src/CORE
    python tools/failure_evidence_gate.py --json
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Recorded in `logs/` beside every other baseline in this project (gitignored,
#: so a fresh clone has none and `--check` says so instead of failing).
BASELINE = ROOT / "logs" / "failure_evidence_baseline.json"

#: Scan roots when `--dir` is not given, repo-relative. The two trees the
#: src/-scoped ratchet cannot see.
DEFAULT_DIRS = ("tools", "scripts")

#: Entry points that BLOCK. A missing `timeout=` here is an unbounded wait.
BLOCKING = ("run", "call", "check_output", "check_call")

#: Every subprocess entry point modelled (Popen is classified, then exempted).
ALL_ENTRY = BLOCKING + ("Popen",)

#: Kwargs that turn text mode on by themselves.
TEXT_KWARGS = ("text", "universal_newlines")

CATEGORIES = ("no_timeout", "no_encoding")

#: A call declares a deliberate unbounded wait with this marker plus a reason.
#: The reason is mandatory (see `_exemption_reason`) and every exemption is
#: printed, because an unlisted exemption is indistinguishable from a blind one.
EXEMPT_MARKER = "gate:allow-no-timeout:"


def _iter_py_files(dirs: "list[str]") -> "list[Path]":
    """Every `.py` file under each `--dir` (a file argument is taken as-is)."""
    out: "list[Path]" = []
    for entry in dirs:
        base = Path(entry)
        base = base if base.is_absolute() else (ROOT / entry)
        if base.is_file():
            if base.suffix == ".py":
                out.append(base)
            continue
        if not base.is_dir():
            continue
        out.extend(sorted(p for p in base.rglob("*.py")
                          if "__pycache__" not in p.parts))
    return out


def _subprocess_names(tree: ast.AST) -> "tuple[set[str], set[str]]":
    """Return (names bound to the module, entry points imported from it).

    Both import forms are collected: `import subprocess as _sp` is in live use,
    and a detector that understands only the literal name misses real sites.
    """
    aliases: "set[str]" = set()
    from_names: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    aliases.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                for alias in node.names:
                    if alias.name in ALL_ENTRY:
                        from_names.add(alias.asname or alias.name)
    return aliases, from_names


def _entry_point(node: ast.Call, aliases: "set[str]",
                 from_names: "set[str]") -> "str | None":
    """The subprocess function this call invokes, or None if it is not one."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in aliases and func.attr in ALL_ENTRY:
            return "subprocess.%s" % func.attr
        return None
    if isinstance(func, ast.Name) and func.id in from_names:
        return "subprocess.%s" % func.id
    return None


def _classify(node: ast.Call, entry: str) -> dict:
    """This call's bound/encoding evidence, and whether a splat hides it."""
    kwargs: "dict[str, ast.expr]" = {}
    splat = False
    for kw in node.keywords:
        if kw.arg is None:
            splat = True
        else:
            kwargs[kw.arg] = kw.value

    def _literal_true(name: str) -> bool:
        value = kwargs.get(name)
        return isinstance(value, ast.Constant) and bool(value.value)

    return {
        "splat": splat,
        "has_timeout": "timeout" in kwargs,
        "has_encoding": "encoding" in kwargs,
        # `encoding=`/`errors=` are inert outside text mode, so passing either
        # IS text mode -- the same rule encoding_audit.py applies.
        "text_confirmed": (any(_literal_true(name) for name in TEXT_KWARGS)
                           or "encoding" in kwargs or "errors" in kwargs),
        "blocks": entry.rsplit(".", 1)[-1] in BLOCKING,
    }


def _exemption_reason(node: ast.Call, lines: "list[str]") -> str:
    """The declared reason on a line this call spans, or "" when it has none.

    The marker must sit on a line the CALL itself covers, so it is attached to
    the call and not to something merely nearby. An empty reason counts as no
    marker: a bare `# gate:allow-no-timeout:` must not silence a real finding.
    """
    start = node.lineno - 1
    end = getattr(node, "end_lineno", None) or node.lineno
    for line in lines[start:end]:
        at = line.find(EXEMPT_MARKER)
        if at >= 0:
            return line[at + len(EXEMPT_MARKER):].strip()
    return ""


def scan_file(path: Path) -> dict:
    """One file's findings.

    A file that will not parse is REPORTED as skipped, never silently scored
    zero -- an unreadable file and a clean file must not look alike.
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"scanned": False, "findings": [], "undetermined": [],
                "exempt": [], "skipped": "line %s: %s" % (exc.lineno, exc.msg)}

    aliases, from_names = _subprocess_names(tree)
    lines = source.splitlines()
    findings: "list[dict]" = []
    undetermined: "list[dict]" = []
    exempt: "list[dict]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        entry = _entry_point(node, aliases, from_names)
        if entry is None:
            continue
        info = _classify(node, entry)
        where = {"line": node.lineno, "call": entry,
                 "source": (lines[node.lineno - 1].strip()[:120]
                            if node.lineno <= len(lines) else "")}
        notes: "list[str]" = []
        if info["blocks"] and not info["has_timeout"]:
            if info["splat"]:
                notes.append("**kwargs may supply timeout")
            else:
                reason = _exemption_reason(node, lines)
                if reason:
                    exempt.append({**where, "reason": reason})
                else:
                    findings.append({**where, "category": "no_timeout"})
        if info["text_confirmed"] and not info["has_encoding"]:
            if info["splat"]:
                notes.append("**kwargs may supply encoding")
            else:
                findings.append({**where, "category": "no_encoding"})
        if notes:
            undetermined.append({**where, "note": "; ".join(notes)})
    return {"scanned": True, "findings": findings, "undetermined": undetermined,
            "exempt": exempt, "skipped": None}


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def collect(dirs: "list[str]") -> dict:
    """Scan `dirs` and return {by_file, undetermined, exempt, skipped, total}."""
    by_file: "dict[str, dict[str, int]]" = {}
    undetermined: "dict[str, list[dict]]" = {}
    exempt: "dict[str, list[dict]]" = {}
    skipped: "list[str]" = []
    scanned = 0
    for path in _iter_py_files(dirs):
        result = scan_file(path)
        rel = _relative(path)
        if result["skipped"]:
            skipped.append("%s: %s" % (rel, result["skipped"]))
            continue
        scanned += 1
        for row in result["findings"]:
            slot = by_file.setdefault(rel, {name: 0 for name in CATEGORIES})
            slot[row["category"]] = slot.get(row["category"], 0) + 1
        if result["undetermined"]:
            undetermined[rel] = result["undetermined"]
        for row in result["exempt"]:
            exempt.setdefault(rel, []).append(row)
    total = sum(sum(slot.values()) for slot in by_file.values())
    return {"scanned": scanned, "by_file": by_file, "undetermined": undetermined,
            "exempt": exempt, "skipped": skipped, "total": total}


def _load_baseline(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        # A corrupt baseline is reported, not treated as "nothing recorded":
        # silently passing on an unreadable baseline is how a gate stops gating.
        print("WARNING: unreadable baseline %s (%s)" % (path, exc))
        return {}


def _regressions(now: dict, base: dict) -> "list[str]":
    """Per (file, category) increases, plus files absent from the baseline."""
    out: "list[str]" = []
    for rel, slot in sorted(now.items()):
        was = (base.get(rel) or {})
        if rel not in base:
            out.append("%s: NEW file with %d finding(s)" % (rel, sum(slot.values())))
            continue
        for name, count in sorted(slot.items()):
            before = int(was.get(name, 0) or 0)
            if count > before:
                out.append("%s: %s %d -> %d (+%d)"
                           % (rel, name, before, count, count - before))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", action="append", default=None, metavar="PATH",
                    help="file or directory to scan, repeatable "
                         "(default: %s)" % " ".join(DEFAULT_DIRS))
    ap.add_argument("--baseline", action="store_true",
                    help="record the current counts as the baseline")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when a finding INCREASED against the baseline")
    ap.add_argument("--baseline-file", metavar="JSON",
                    help="baseline path (default: %s)"
                         % _relative(BASELINE))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.dir:
        # A path that does not exist scans nothing and therefore reports zero
        # findings, which is indistinguishable from a clean tree. Refuse it.
        missing = [d for d in args.dir
                   if not (Path(d) if Path(d).is_absolute()
                           else ROOT / d).exists()]
        if missing:
            print("ERROR: --dir path(s) do not exist: %s" % ", ".join(missing))
            print("An empty scan reports zero findings, which looks exactly like a")
            print("clean tree -- refusing to print a false zero.")
            return 2
    dirs = args.dir or list(DEFAULT_DIRS)
    result = collect(dirs)
    base_path = Path(args.baseline_file).resolve() if args.baseline_file else BASELINE
    base = _load_baseline(base_path)

    if args.json:
        print(json.dumps({"dirs": dirs, "scanned": result["scanned"],
                          "total": result["total"], "by_file": result["by_file"],
                          "undetermined": result["undetermined"],
                          "exempt": result["exempt"],
                          "skipped": result["skipped"]}, indent=2))
        return 0

    print("failure-evidence gate -- %s" % ", ".join(dirs))
    print("  scanned:  %d file(s)" % result["scanned"])
    print("  findings: %d site(s) in %d file(s)"
          % (result["total"], len(result["by_file"])))
    for rel, slot in sorted(result["by_file"].items(),
                            key=lambda kv: (-sum(kv[1].values()), kv[0])):
        print("    %5d  %s  %s" % (sum(slot.values()), rel,
                                   " ".join("%s=%d" % (k, v)
                                            for k, v in sorted(slot.items()) if v)))

    if result["undetermined"]:
        print()
        print("  UNDETERMINED -- not counted either way; a **kwargs splat could")
        print("  supply the kwarg, so a constant cannot settle it:")
        for rel, rows in sorted(result["undetermined"].items()):
            for row in rows:
                print("    %s:%s  %s  (%s)"
                      % (rel, row["line"], row["call"], row["note"]))

    if result["skipped"]:
        print()
        print("  SKIPPED -- could not be parsed, so nothing was checked in them:")
        for line in result["skipped"]:
            print("    %s" % line)

    if result["exempt"]:
        print()
        print("  EXEMPT -- unbounded wait declared deliberate by a marker; listed")
        print("  so that granting an exemption stays a visible decision:")
        for rel, rows in sorted(result["exempt"].items()):
            for row in rows:
                print("    %s:%s  %s" % (rel, row["line"], row["reason"]))

    if args.baseline:
        payload = {"recorded": datetime.now(timezone.utc).isoformat(),
                   "dirs": dirs, "total": result["total"],
                   "by_file": result["by_file"]}
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print()
        print("baseline recorded: %s (%d finding(s))"
              % (_relative(base_path), result["total"]))
        return 0

    if args.check:
        print()
        if not base:
            print("NO BASELINE at %s -- an UNARMED gate is a red gate, not a"
                  % _relative(base_path))
            print("green one: with nothing to compare against, this run cannot")
            print("fail, which is indistinguishable from passing. Arm it with")
            print("`--baseline`, or run without `--check` for the absolute check.")
            return 2
        regressions = _regressions(result["by_file"], base.get("by_file") or {})
        if regressions:
            print("FAIL: %d regression(s) against the %s baseline:"
                  % (len(regressions), str(base.get("recorded"))[:19]))
            for line in regressions:
                print("    %s" % line)
            return 1
        print("OK: no failure-evidence regressions (baseline %d, now %d)."
              % (int(base.get("total", 0) or 0), result["total"]))
        return 0

    # A plain run is a gate too. A site it just printed violates the
    # zero-invariant this lane established, so it must not exit 0 -- that was
    # the defect this file shipped with.
    return 1 if result["total"] else 0


if __name__ == "__main__":
    sys.exit(main())
