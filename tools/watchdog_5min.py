#!/usr/bin/env python
# coding: utf-8
"""5-minute watchdog (temporary, until caught up): fast signal, not full suite.

Runs collect-only + a fixed fast subset on 3.15t, appends one JSON line per
run to state/watchdog.jsonl. Full runs stay weekly (converge_315).
Disable: schtasks /delete /tn AzazaelWatchdog5min /f
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PY = Path(r"C:\Azazael\.venvs\py316t\Scripts\python.exe")
FAST = [
    "tests/test_config_and_breaker.py",
    "tests/test_boruta_relevance.py",
    "tests/test_nerve_torch_import.py",
    "tests/test_capability_liveness.py",
    "tests/test_registry_importable.py",
]
OUT = HERE / "state" / "watchdog.jsonl"


def main() -> int:
    rec = {"at": datetime.now(timezone.utc).isoformat(), "ok": False}
    try:
        # -n auto: 5 independent files across workers (measured 1.7x on
        # 16-core, Oct 6 2026). Safe here — the FAST subset makes no
        # isolation claims (unlike aggregate_suite_gate, which must stay
        # single-process to observe cross-file shared-state defects).
        r = subprocess.run(
            [str(PY), "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider",
             "-n", "auto", "--continue-on-collection-errors"] + FAST,
            cwd=str(HERE), capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace")
        tail = (r.stdout or "").strip().splitlines()[-2:]
        rec.update({"ok": r.returncode == 0, "rc": r.returncode,
                    "tail": tail})
    except Exception as e:
        rec["error"] = str(e)[:200]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
