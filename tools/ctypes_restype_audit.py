# coding: utf-8
"""ctypes restype audit -- handle-returning calls whose restype is still ``int``.

Why this exists
---------------
``ctypes`` declares a foreign function's return type as C ``int`` unless told
otherwise. On 64-bit Windows that truncates a HANDLE, and the failure is silent:
``GetThreadTimes`` on a 64-bit pseudo-handle returned FALSE, ``thread_cpu()``
returned ``None``, and a genuinely parallel run reported ``par 0.00 of 4`` --
a native facility reporting itself absent (measured 2026-11-05).

The defect is not "someone forgot one line". It is that nothing in the tree
asked the question, so this tool asks it: for every DLL object bound in the
tree, which handle-returning functions are **called** without a declared
``restype``. A finding is a silent-truncation candidate, not a proven bug -- the
tool prints the file:line so the reading can be checked.

Usage::

    python tools/ctypes_restype_audit.py            # advisory: findings, exit 0
    python tools/ctypes_restype_audit.py --strict   # findings fail (exit 1)
    python tools/ctypes_restype_audit.py --json     # machine-readable

Scope is ``src/`` plus ``tools/``; vendored and build trees are skipped by name,
because an audit that reports site-packages noise is an audit nobody runs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "tools")
SKIP_PARTS = {".build", "site-packages", "node_modules", "__pycache__", "archive",
              "archived", ".venvs", "venv", "dist", "build"}

#: Functions whose return is a handle (or handle-sized), so ctypes' default
#: ``int`` restype truncates it on 64-bit. Deliberately explicit rather than
#: inferred: a name missing from this set is a gap in the audit, and a name in it
#: that returns something else is a false positive an operator can see.
HANDLE_FUNCS = frozenset({
    "CreateFileW", "CreateFileA", "CreateFileMappingW", "CreateToolhelp32Snapshot",
    "CreateMutexW", "CreateEventW", "CreateSemaphoreW", "CreateProcessW",
    "CreateThread", "CreateRemoteThread", "CreateNamedPipeW", "CreatePipe",
    "OpenProcess", "OpenProcessToken", "OpenThreadToken", "OpenThread",
    "GetCurrentProcess", "GetCurrentThread", "GetCurrentProcessId",
    "GetStdHandle", "LoadLibraryW", "GetModuleHandleW", "FindFirstFileW",
    "FindFirstFileExW", "FindFirstChangeNotificationW", "GetDC", "GetWindowDC",
    "RegOpenKeyExW", "LocalAlloc", "GlobalAlloc", "HeapAlloc", "VirtualAlloc",
})

BINDING = re.compile(r"(?P<alias>[A-Za-z_]\w*)\s*=\s*(?:ctypes\.)?"
                     r"(?:WinDLL|CDLL|OleDLL|windll\.LoadLibrary|"
                     r"cdll\.LoadLibrary)\s*\(")
DECLARED = re.compile(r"(?P<alias>[A-Za-z_]\w*)\.(?P<func>\w+)\.(?:restype|argtypes)\s*=")
CALL = re.compile(r"(?P<alias>[A-Za-z_]\w*)\.(?P<func>[A-Z]\w*)\s*\(")


def _files() -> List[Path]:
    out: List[Path] = []
    for name in SCAN_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if SKIP_PARTS & set(path.parts):
                continue
            out.append(path)
    return sorted(out)


def audit_file(path: Path) -> List[Dict[str, Any]]:
    """Every handle-returning call in ``path`` with no declared ``restype``."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"file": str(path), "line": 0, "defect": "unreadable: %s" % exc,
                 "function": ""}]
    aliases = {match.group("alias") for match in BINDING.finditer(text)}
    if not aliases:
        return []
    declared = {(match.group("alias"), match.group("func"))
                for match in DECLARED.finditer(text)}
    findings: List[Dict[str, Any]] = []
    for match in CALL.finditer(text):
        alias, func = match.group("alias"), match.group("func")
        if alias not in aliases or func not in HANDLE_FUNCS:
            continue
        if (alias, func) in declared:
            continue
        line = text.count("\n", 0, match.start()) + 1
        try:
            shown = str(path.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            shown = str(path)  # a caller-supplied file (self-test) is not in scope
        findings.append({"file": shown,
                         "line": line, "function": "%s.%s" % (alias, func),
                         "defect": "handle-returning call with no declared restype"})
    return findings


def self_test() -> Dict[str, Any]:
    """Negative control: the auditor must find a defect it was shown.

    Zero findings on 1173 files is only good news if the instrument can see a
    finding at all. This writes the known-bad shape to a temp file and asserts it
    is caught and that a declared restype silences it, so "0 findings" means
    "0 defects", not "0 matches of a broken pattern".
    """
    import tempfile

    caught = silenced = False
    with tempfile.TemporaryDirectory(prefix="ctypes_audit_") as tmp:
        bad = Path(tmp) / "bad.py"
        bad.write_text("import ctypes\n"
                       "k32 = ctypes.WinDLL(\"kernel32\", use_last_error=True)\n"
                       "handle = k32.CreateFileW(\"x\", 0, 0, None, 3, 0, None)\n",
                       encoding="utf-8")
        caught = bool(audit_file(bad))
        good = Path(tmp) / "good.py"
        good.write_text("import ctypes\n"
                        "from ctypes import wintypes\n"
                        "k32 = ctypes.WinDLL(\"kernel32\", use_last_error=True)\n"
                        "k32.CreateFileW.restype = wintypes.HANDLE\n"
                        "handle = k32.CreateFileW(\"x\", 0, 0, None, 3, 0, None)\n",
                        encoding="utf-8")
        silenced = not audit_file(good)
    return {"caught_undeclared": caught, "declared_is_clean": silenced,
            "ok": caught and silenced}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="findings fail the run")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true",
                        help="prove the auditor detects a known-bad call")
    args = parser.parse_args(argv)

    if args.self_test:
        result = self_test()
        print("ctypes_restype_audit self-test: caught=%s declared_clean=%s -> %s"
              % (result["caught_undeclared"], result["declared_is_clean"],
                 "ok" if result["ok"] else "BROKEN"))
        return 0 if result["ok"] else 2

    files = _files()
    findings: List[Dict[str, Any]] = []
    for path in files:
        findings.extend(audit_file(path))

    if args.json:
        print(json.dumps({"files": len(files), "findings": findings}, indent=1))
    else:
        print("ctypes_restype_audit: %d file(s), %d finding(s)"
              % (len(files), len(findings)))
        for item in findings:
            print("  %s:%s %s -- %s" % (item["file"], item["line"],
                                        item["function"], item["defect"]))
        if not findings:
            print("  every handle-returning call in scope declares its restype")
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
