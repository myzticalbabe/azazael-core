#!/usr/bin/env python3
# coding: utf-8
"""VACUOUS-CHECK AUDIT -- find checks that can pass while evaluating nothing.

This session's most expensive defect class was never a crash. It was a check
that reported success while the capability behind it was absent:

  * the harness scored `0 -> 0` on a band that collected zero tests and KEPT a
    mutation, because an empty band compares as a tie
  * `_fabric_variation` "improved" by 3.3x every run against an objective whose
    optimum was "set every gene to 1.0"
  * a freshness gate loaded a partial fleet (111 of 229) as fresh
  * PF16 read 113 -> 117 -> 119 -> 174 -> 166, wrong four times in five

None of those raised. All of them printed success. So this is a scanner for the
shape, not the incident: checks whose PASS path is reachable without the work.

HEURISTIC, NOT PROOF. Each finding is a site to read, not a verdict. It is
deliberately biased toward false positives, because a false positive costs one
read while a false negative costs a silent capability.

    python tools/vacuous_check_audit.py
    python tools/vacuous_check_audit.py --json logs/vacuous_audit.json
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import tokenize
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DETECTORS: tuple[tuple[str, str, str, str], ...] = (
    ("swallow_all",
     r"except\s*(?:Exception|BaseException)?\s*(?:as\s+\w+)?\s*:\s*(?:#[^\n]*)?\n\s*pass\b",
     "HIGH",
     "failure is discarded: the check cannot fail because nothing survives to report"),
    ("default_true",
     r"\.get\(\s*[\"'](?:ok|verified|passed|success|valid|fresh|clean|consistent)[\"']\s*,\s*True\s*\)",
     "HIGH",
     "a missing verification field reads as PASS"),
    ("count_zero_tie",
     r"(?:passed|count|total|n)\s*==\s*0\b|or\s*\[\s*0\s*,\s*0\s*\]",
     "HIGH",
     "zero collected can compare equal to zero collected -- the empty-band tie"),
    ("assert_true",
     r"assert\s+True\b",
     "MED",
     "an assertion that cannot fail"),
    ("return_true_default",
     r"if\s+not\s+\w+[\w\.\[\]\"']*\s*:\s*\n\s*return\s+True\b",
     "MED",
     "absence of evidence is scored as evidence of absence"),
    ("pytest_invocation",
     r"subprocess\.run\(\s*\[[^\]]*pytest",
     "MED",
     "a pytest call: verify the target list cannot be empty or non-test"),
    ("len_guard_only",
     r"if\s+len\([^)]*\)\s*[<>=!]+\s*0\s*:\s*\n\s*pass\b",
     "MED",
     "an empty-input guard that does nothing"),
    ("bare_except",
     r"except\s*:\s*(?:#[^\n]*)?\n",
     "LOW",
     "bare except: also catches KeyboardInterrupt and SystemExit"),
)

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv-3.14t", "archived",
             "archive", "archived_unified", "var", ".nox", "build"}


def _safe(text: str) -> str:
    """Printable on a cp1252 console.

    The first run of this tool died with UnicodeEncodeError on a U+2192 arrow
    inside a source snippet -- meaning the crash happened *while reporting*, so
    no report was written at all. Output has to survive the terminal it prints
    to, or the audit is one more instrument that fails silently.
    """
    return text.encode("ascii", "replace").decode("ascii")


def iter_py(roots: tuple[Path, ...]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            out.append(p)
    return out


#: Files whose prose could not be masked (tokenize/ast refused). An unmasked
#: file is scanned raw, which is the pre-2026-10-13 behaviour, so this is a
#: downgrade in precision rather than a skipped file -- and it is recorded
#: instead of being swallowed.
MASK_FAILURES: list[str] = []


def _mask_prose(text: str) -> str:
    """Blank COMMENTS and DOCSTRINGS, preserving every byte offset.

    MEASURED (engram #569): the detectors ran over the raw file text, so a
    finding could land inside prose that merely QUOTES the pattern. Live, 7
    findings were prose -- `queens.py:790` (a docstring) and six comments in
    `tests/test_colour_queens.py` -- and the meta-evolution control plane
    selected the docstring, proposing to rewrite documentation as if it were a
    check. A comment is never a check, so matching prose is a false positive by
    construction, and this tool's own docstring prices a false positive at one
    read while pricing a false negative at a silently absent capability.

    Only comments and DOCSTRINGS are masked. Inline string literals are left
    intact on purpose: `default_true` matches the literal `.get("ok", True)`, so
    blanking every string token would delete the detector it is meant to sharpen.

    Offsets are preserved character-for-character (newlines kept), so `line` and
    `code` stay exact. `ast` reports columns in UTF-8 BYTES while `tokenize`
    reports CHARACTERS, and this tree contains non-ASCII prose -- mixing the two
    indexed past the end of the buffer, so byte columns are converted per line.
    An unparseable file falls back to its raw text rather than being skipped, and
    the fallback is recorded in `MASK_FAILURES`.
    """
    buf = list(text)
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    starts.append(len(text))  # so the final line is sliceable

    def blank(a: int, b: int) -> None:
        for i in range(max(0, a), min(len(buf), b)):
            if buf[i] != "\n":
                buf[i] = " "

    def offset(row: int, col: int, chars: bool) -> int:
        """Absolute offset of (row, col); `col` is bytes unless `chars`."""
        index = min(max(row, 1), len(starts) - 1)
        base = starts[index - 1]
        if chars:
            return base + col
        line = text[base:starts[index]]
        return base + len(line.encode("utf-8")[:col].decode("utf-8", "ignore"))

    with warnings.catch_warnings():
        # The scanned file's own escapes must not spray SyntaxWarnings into the
        # audit's output: this tool reads 1200+ files, and a warning wall hides
        # the report the run exists to produce.
        warnings.simplefilter("ignore")
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError) as exc:
            MASK_FAILURES.append("ast: %s" % type(exc).__name__)
            tree = None
    if tree is not None:
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if not isinstance(first, ast.Expr):
                continue
            value = first.value
            if not (isinstance(value, ast.Constant)
                    and isinstance(value.value, str)):
                continue
            blank(offset(value.lineno, value.col_offset, chars=False),
                  offset(value.end_lineno, value.end_col_offset, chars=False))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                blank(offset(tok.start[0], tok.start[1], chars=True),
                      offset(tok.end[0], tok.end[1], chars=True))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        MASK_FAILURES.append("tokenize: %s" % type(exc).__name__)
    return "".join(buf)


def scan_file(path: Path) -> list[dict]:
    findings: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings
    lines = text.splitlines()
    masked = _mask_prose(text)
    for name, pattern, sev, why in DETECTORS:
        for m in re.finditer(pattern, masked):
            ln = text.count("\n", 0, m.start()) + 1
            snippet = (lines[ln - 1].strip() if ln - 1 < len(lines) else "")[:100]
            findings.append({
                "detector": name, "severity": sev, "line": ln,
                "code": snippet, "why": why,
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            })
    return findings


def group_repeats(findings: list[dict]) -> list[dict]:
    """Cluster same-idiom findings: same detector + same normalized code +
    same file, occurring 2+ times, is ONE pattern finding with locations.

    Detectors flag instances; one-substitution patches cannot fix an idiom
    repeated N times (measured 2026-09-26: 4 unanimous debate REJECTs, all
    on non-unique old text). Grouping lets the cycle propose one pair
    applied at every site via the validator's repeat mode. Pure; tested.
    """
    groups: dict[tuple, list[dict]] = {}
    for f in findings:
        code = re.sub(r"\s+", " ", str(f.get("code", "")).strip())
        key = (f.get("detector"), f.get("path"), code)
        groups.setdefault(key, []).append(f)
    out = []
    for (detector, path, code), rows in sorted(groups.items()):
        if len(rows) < 2 or not code:
            continue
        out.append({
            "kind": "pattern",
            "detector": detector,
            "severity": rows[0].get("severity", "MED"),
            "code": code,
            "path": path,
            "locations": sorted(r["line"] for r in rows),
            "count": len(rows),
            "why": "%s: same idiom at %d sites — fix all, not one" % (
                rows[0].get("why", ""), len(rows)),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default="", help="also write findings here")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    files = iter_py((ROOT / "tools", ROOT / "src", ROOT / "tests"))
    findings: list[dict] = []
    for f in files:
        findings.extend(scan_file(f))

    by_det: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for f in findings:
        by_det[f["detector"]] = by_det.get(f["detector"], 0) + 1
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        by_file[f["path"]] = by_file.get(f["path"], 0) + 1

    report = {
        "files_scanned": len(files),
        "findings": len(findings),
        "by_detector": by_det,
        "by_severity": by_sev,
        "top_files": sorted(by_file.items(), key=lambda kv: -kv[1])[:args.top],
        "items": findings,
        "patterns": group_repeats(findings),
        "caveat": "heuristic: each row is a site to read, not a proven defect",
    }

    print("=== VACUOUS-CHECK AUDIT ===")
    print("scanned %d python files -> %d candidate site(s)" % (len(files), len(findings)))
    print()
    print("%-22s %-6s %s" % ("detector", "sev", "count"))
    for name, _, sev, why in DETECTORS:
        if by_det.get(name):
            print("%-22s %-6s %-5d %s" % (name, sev, by_det[name], why[:64]))
    print()
    print("worst files:")
    for path, n in sorted(by_file.items(), key=lambda kv: -kv[1])[:args.top]:
        print("  %-58s %d" % (path, n))
    print()
    print("patterns (same idiom, 2+ sites — fix all, not one):")
    for p in report["patterns"][:args.top]:
        print("  %-58s %dx %s" % (
            "%s:%s" % (p["path"], p["detector"]), p["count"],
            ",".join(str(l) for l in p["locations"][:8])))
    if not report["patterns"]:
        print("  none")
    print()
    print("HIGH severity sites (read these first):")
    shown = 0
    for f in findings:
        if f["severity"] == "HIGH" and shown < args.top:
            print(_safe("%s:%d  [%s] %s" % (f["path"], f["line"], f["detector"],
                                             f["code"][:70])))
            shown += 1
    if not shown:
        print("  none")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
        print()
        print("written: %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
