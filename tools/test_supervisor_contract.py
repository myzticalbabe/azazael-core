#!/usr/bin/env python
# coding: utf-8
"""Contract test: supervisor --verdict output vs the opencode gate parser.

The plugin (engram.ts) fails OPEN on unparsed output, so a format change in
verdict() silently disarms enforcement. This pins the exact contract both
sides rely on:
  1. `supervisor_mcp.py --verdict` exits 0 and prints a trailing
     `VERDICT: <GO|NO-GO|CLARIFY>...` line.
  2. The plugin's regex /^VERDICT:\\s*(GO|NO-GO|CLARIFY)(.*)$/m matches it.
  3. Every probe line is ASCII-safe (cp1252 consoles killed the old gate).

Run: C:/Azazael/.venvs/py315t/Scripts/python.exe tools/test_supervisor_contract.py
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = r"C:/Azazael/.venvs/py315t/Scripts/python.exe"

# Transcribed from engram.ts (the gate's actual parser). If the plugin
# changes, update BOTH sides in the same commit.
PLUGIN_RE = re.compile(r"^VERDICT:\s*(GO|NO-GO|CLARIFY)(.*)$", re.M)


def main() -> int:
    failures = []
    r = subprocess.run(
        [PY, str(HERE / "supervisor_mcp.py"), "--verdict"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, cwd=str(HERE.parent))
    if r.returncode != 0:
        failures.append("--verdict exited %d" % r.returncode)
    out = r.stdout or ""
    m = PLUGIN_RE.search(out)
    if not m:
        failures.append("plugin regex does not match --verdict output; tail=%r"
                        % out[-200:])
    else:
        print("verdict parsed as: %s" % m.group(1))
    try:
        out.encode("cp1252")
    except UnicodeEncodeError as exc:
        failures.append("non-cp1252 output: %s" % exc)
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("contract ok (%d output chars)" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
