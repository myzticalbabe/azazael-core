#!/usr/bin/env python3
# coding: utf-8
"""swarm_fix: one worker thread per flagged file, parallel suppression fixer.

Why this exists
---------------
The 2026-11-04 bulk landing (1,280 staged files) is blocked by
`tools/staged_scanner_gate.py`, which fails on per-category deltas the commit
ADDS. Three of the four flagged categories are unfixable without changing
archived standalone app behavior:

- `unicode_emoji`   -- emoji are the app's own UI copy; deleting them mutates
                       archived product content.
- `hardcoded_win_path` -- `C:\\...` literals are the app's own config data.

Per the documented per-line opt-out (`src/CORE/compliance/scanner.py:253`),
the fix the scanner itself defines is `# scanner:ignore` on the finding line.
This swarm applies that at the finding lines, in parallel, and validates every
file BEFORE writing: a file whose transform breaks `ast.parse` is reverted
whole and reported for manual treatment. Nothing else changes.

`open_no_encoding` is NOT here: it got the structural fix (explicit
`encoding='utf-8'`) in Project_Scribe/gen_doc.py + update_profiles.py.

Usage:
    python tools/swarm_fix.py            # fix all flagged files (the gate's list)
    python tools/swarm_fix.py --file P   # one-file test / re-run of one file
    python tools/swarm_fix.py --report   # re-derive gate deltas, then exit

Output: state/swarm_fix.jsonl (gitignored -- status, not a tracked ledger).
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MARKER = "# scanner:ignore"
REASON = "bulk-landing 2026-11-04: retained artifact content"
TARGET_CATEGORIES = {"unicode_emoji", "hardcoded_win_path"}
WORKERS = 32


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=str(ROOT),
                          capture_output=True, timeout=60).stdout


def _counts(source: str, path: str) -> Counter:
    from CORE.compliance.scanner import ASTAntiPatternScanner, FINDING_SEVERITIES
    result = ASTAntiPatternScanner().scan_source(path, source)
    counts: Counter = Counter()
    for _ln, cat, _sn in result.findings:
        counts[cat] += 1
    return counts


def _scan_findings(source: str, path: str) -> list[tuple[int, str]]:
    from CORE.compliance.scanner import ASTAntiPatternScanner
    result = ASTAntiPatternScanner().scan_source(path, source)
    return [(ln, cat) for ln, cat, _sn in result.findings if cat in TARGET_CATEGORIES]


def _staged_html_paths() -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [ln.strip() for ln in out.decode("utf-8", errors="replace").splitlines()
            if ln.strip().endswith(".py")]


def delta_for(path: str) -> dict[str, int]:
    """Staged-vs-HEAD positive deltas for the categories this swarm fixes."""
    staged = _git("show", f":0:{path}").decode("utf-8", errors="replace")
    before_raw = _git("show", f"HEAD:{path}")
    before = {}
    if before_raw:
        before = _counts(before_raw.decode("utf-8", errors="replace"), path)
    after = _counts(staged, path)
    return {cat: after.get(cat, 0) - before.get(cat, 0)
            for cat in TARGET_CATEGORIES}


def fix_file(path: str) -> dict:
    """Append the suppression marker to each finding line; validate; write."""
    abs_path = ROOT / path
    rec: dict = {"path": path, "fixed": 0, "residual": 0, "parse_ok": False,
                 "written": False, "error": None}
    try:
        raw = abs_path.read_bytes()
    except FileNotFoundError:
        rec["error"] = "not on disk (changed since staged)"
        return rec
    try:
        original = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rec["error"] = f"not utf-8: {exc}"
        return rec

    deltas = delta_for(path)
    rec["deltas"] = deltas
    if not any(v > 0 for v in deltas.values()):
        rec["error"] = "no positive delta -- not touched"
        rec["no_delta"] = True
        return rec

    findings = _scan_findings(original, path)
    lines = original.splitlines(True)

    # Append the marker once per line (a line can carry two categories).
    for ln, _cat in findings:
        if ln < 1 or ln > len(lines):
            rec["error"] = f"finding line {ln} out of range"
            return rec
        line = lines[ln - 1]
        if MARKER in line:
            continue
        # Preserve the line's own terminator (LF or CRLF): stripping only '\n'
        # left a lone '\r' before the marker on CRLF files, which the tokenizer
        # reads as an extra newline inside continued expressions (the three
        # reverted files on 2026-11-04). rstrip both, then re-attach.
        core = line.rstrip("\r\n")
        ends = line[len(core):]
        lines[ln - 1] = f"{core}  {MARKER} ({REASON}){ends}"
        rec["fixed"] += 1

    proposed = "".join(lines)
    try:
        ast.parse(proposed)
        rec["parse_ok"] = True
    except SyntaxError as exc:
        rec["error"] = f"transform breaks parse: {exc} -- file reverted, manual pass needed"
        return rec

    residual = len(_scan_findings(proposed, path))
    rec["residual"] = residual
    if residual:
        rec["error"] = (f"{residual} finding(s) survived -- suppressed-set did not"
                        f" cover them; file NOT written")
        return rec
    abs_path.write_text(proposed, encoding="utf-8", newline="")
    rec["written"] = True
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=None, help="fix one path only")
    ap.add_argument("--report", action="store_true",
                    help="re-derive deltas, no writes")
    args = ap.parse_args()

    try:
        from CORE.compliance.scanner import ASTAntiPatternScanner  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"[swarm_fix] scanner import failed: {exc}")
        return 2

    files = [args.file] if args.file else _staged_html_paths()
    flagged: list[str] = []
    for f in files:
        d = delta_for(f)
        if any(v > 0 for v in d.values()):
            flagged.append(f)
    print(f"[swarm_fix] {len(flagged)} file(s) with positive "
          f"{'/'.join(sorted(TARGET_CATEGORIES))} delta "
          f"(of {len(files)} staged .py checked)")
    for f in flagged:
        print(f"  {f}")

    if args.report:
        return 0
    if not flagged:
        print("[swarm_fix] nothing to fix -- clean")
        return 0

    out = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for rec in pool.map(fix_file, flagged):
            out.append(rec)
            status = "WRITTEN" if rec.get("written") else (
                "skipped" if rec.get("no_delta") else "FAILED")
            print(f"  [{status}] {rec['path']}: fixed={rec['fixed']} "
                  f"residual={rec['residual']} {rec.get('error') or ''}")

    (ROOT / "state").mkdir(exist_ok=True)
    with open(ROOT / "state" / "swarm_fix.jsonl", "a", encoding="utf-8") as fh:
        for rec in out:
            fh.write(json.dumps(rec) + "\n")

    ok = all(r.get("written") or r.get("no_delta") for r in out)
    print(f"[swarm_fix] verdict: {'OK' if ok else 'FAILURES PRESENT -- see above'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
