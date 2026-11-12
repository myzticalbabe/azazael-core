#!/usr/bin/env python3
# coding: utf-8
"""External lint oracle: ruff + pyflakes, written by nobody in this tree.

Why this exists
---------------
Every other gate in this estate is ours: the staged scanner, the anti-pattern
ratchet, the type-check gate, `ast_audit`, the preflight registry. They are one
family of instruments, and a family that agrees with itself has proved only
that it is consistent. On 2026-11-04 `ruff` 0.17.3 and `pyflakes` 4.1.0 were
installed into the 3.15t venv so that at least one reading comes from code this
tree did not write, with rule sets it did not choose.

Scope, stated plainly
---------------------
This is a REPORT, not a gate. It exits 0 when at least one leg ran, and 2 when
neither could (BLIND is its own verdict: a tool that reports success after
failing to run has reported a lie). It is deliberately NOT wired into the commit
hook -- a new blocking gate needs a registry row and a recorded baseline, and
the point of this pass is to look at the debt before deciding to ratchet it.

Usage
-----
    python tools/external_lint.py                       # src/, both legs
    python tools/external_lint.py --paths src/CORE tests
    python tools/external_lint.py --json state/external_lint.json
    python tools/external_lint.py --max-files 200
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"__pycache__", ".git", "node_modules", "site-packages", "archived"}
TIMEOUT_S = 300


def iter_py(paths) -> list[Path]:
    """Every .py file under the given files/dirs, bounded and deterministic."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            if p.suffix == ".py":
                out.append(p)
            continue
        if not p.is_dir():
            continue
        for f in sorted(p.rglob("*.py")):
            if any(part in SKIP_PARTS for part in f.parts):
                continue
            out.append(f)
    return out


def tool_version(module: str) -> str:
    """The tool's own version line, or a stated 'unknown' -- never a guess."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", module, "--version"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
    except Exception as exc:
        return "unknown (%s)" % str(exc)[:80]
    text = (proc.stdout or proc.stderr or "").strip()
    return text.splitlines()[0] if text else "unknown (no output)"


def run_ruff(files: list[Path]) -> dict:
    """Ruff over an explicit file list, as JSON, with its exit code ignored.

    `ruff check` exits 1 when it HAS findings, so the exit code alone cannot
    distinguish "found debt" from "failed to run". The parsed document is the
    reading; a parse failure is reported as `error`, never as zero findings.
    """
    empty = {"leg": "ruff", "state": "error", "by_rule": {}, "findings": []}
    cmd = [sys.executable, "-m", "ruff", "check", "--no-cache",
           "--output-format", "json", *[str(f) for f in files]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=TIMEOUT_S)
    except Exception as exc:
        empty["detail"] = str(exc)[:200]
        return empty
    if "No module named" in (proc.stderr or ""):
        return {"leg": "ruff", "state": "skipped", "detail": "not installed",
                "by_rule": {}, "findings": []}
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError:
        empty["detail"] = (proc.stdout or proc.stderr or "no output")[:200]
        return empty
    by_rule = Counter(str(r.get("code") or "?") for r in rows)
    findings = [
        {"file": r.get("filename"), "row": (r.get("location") or {}).get("row"),
         "code": r.get("code"), "message": r.get("message")}
        for r in rows
    ]
    return {"leg": "ruff", "state": "ok", "by_rule": dict(by_rule),
            "findings": findings, "files": len(files)}


class _Capture:
    """Duck-typed pyflakes reporter; the API only ever calls these three."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.errors: list[str] = []

    def flake(self, message) -> None:
        self.lines.append(str(message))

    def unexpectedError(self, filename, msg) -> None:
        self.errors.append("%s: %s" % (filename, msg))

    def syntaxError(self, filename, msg, lineno, offset, text) -> None:
        self.lines.append("%s:%s: syntax error: %s" % (filename, lineno, msg))


def _pyflakes_bucket(msg: str) -> str:
    """A stable label per message family, so counts aggregate across files."""
    # Order matters. The star-import note ("unable to detect undefined names")
    # CONTAINS the needle a real finding uses ("undefined name"), so the loose
    # label first swallowed both families. Measured over src/ on 2026-11-04: the
    # label `undefined_name` reported 9 findings and every one was a star-import
    # note, while the 3 genuine undefined names were hiding in `other`. A count
    # that names the wrong family is the defect HED-3 warns about, so the two
    # are now separate labels and the narrower needle wins.
    for needle, label in (("imported but unused", "unused_import"),
                          ("assigned to but never used", "unused_variable"),
                          ("unable to detect undefined names", "star_import"),
                          ("undefined name", "undefined_name"),
                          ("redefinition of unused", "redefinition"),
                          ("f-string is missing placeholders",
                           "fstring_no_placeholder"),
                          ("syntax error", "syntax_error")):
        if needle in msg:
            return label
    return "other"


def run_pyflakes(files: list[Path]) -> dict:
    """Pyflakes in-process: no argv limit, and no shell to quote against."""
    try:
        from pyflakes import api as pf_api
    except Exception as exc:
        return {"leg": "pyflakes", "state": "skipped",
                "detail": "not installed: %s" % str(exc)[:120],
                "by_rule": {}, "findings": []}
    cap = _Capture()
    for f in files:
        try:
            pf_api.checkPath(str(f), cap)
        except Exception as exc:
            cap.errors.append("%s: %s" % (f, exc))
    by_rule = Counter(_pyflakes_bucket(line.split(": ", 3)[-1])
                      for line in cap.lines)
    return {"leg": "pyflakes", "state": "ok", "by_rule": dict(by_rule),
            "findings": [{"raw": line} for line in cap.lines],
            "errors": cap.errors[:20], "files": len(files)}


def render(legs: list[dict], files: int) -> str:
    lines = ["EXTERNAL LINT ORACLE  (%d file(s))" % files]
    for leg in legs:
        state = leg["state"]
        if state == "ok":
            total = sum(leg["by_rule"].values())
            lines.append("[%s    ] %-9s %d finding(s)" % (
                "RAN" if total else "CLEAN", leg["leg"], total))
        else:
            lines.append("[BLIND   ] %-9s %s (%s)" % (
                leg["leg"], state, leg.get("detail", "")))
    for leg in legs:
        if leg["state"] != "ok" or not leg["by_rule"]:
            continue
        lines.append("--- %s by rule ---" % leg["leg"])
        for rule, count in sorted(leg["by_rule"].items(),
                                  key=lambda kv: (-kv[1], kv[0]))[:12]:
            lines.append("  %-28s %d" % (rule, count))
    ran = [leg for leg in legs if leg["state"] == "ok"]
    verdict = "RAN (report only; not a gate)" if ran else "BLIND (no leg could run)"
    lines.append("verdict: " + verdict)
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paths", nargs="+", default=[str(REPO / "src")],
                    help="files or directories to read (default: src/)")
    ap.add_argument("--max-files", type=int, default=4000,
                    help="cap the file list so a run stays bounded")
    ap.add_argument("--json", default=None, help="write the full payload here")
    args = ap.parse_args(argv)

    files = iter_py(args.paths)[: args.max_files]
    if not files:
        print("[BLIND   ] no .py files under: %s" % " ".join(args.paths))
        return 2

    legs = [run_ruff(files), run_pyflakes(files)]
    for leg in legs:
        leg["version"] = tool_version(leg["leg"])
        print("  %-9s %s" % (leg["leg"], leg["version"]))
    print(render(legs, len(files)))

    if args.json:
        payload = {"schema": "external-lint/1", "files": len(files),
                   "paths": args.paths, "legs": legs}
        Path(args.json).write_text(json.dumps(payload, indent=1),
                                   encoding="utf-8")
        print("wrote %s" % args.json)

    return 0 if any(leg["state"] == "ok" for leg in legs) else 2


if __name__ == "__main__":
    raise SystemExit(main())
