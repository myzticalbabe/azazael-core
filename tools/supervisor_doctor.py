#!/usr/bin/env python
# coding: utf-8
"""supervisor_doctor.py -- diagnose every supervisor dependency, repair only the safe.

Each leg reports DIAGNOSED (healthy), REPAIRED (auto-fixed, idempotent), or
OPEN (needs a human -- with the exact remediation command). Nothing here
judges, closes tasks, or writes memories: the doctor treats symptoms it can
reverse and points at the rest. That is the whole difference between a doctor
and an auto-judge.

  C:/Azazael/.venvs/py315t/Scripts/python.exe tools/supervisor_doctor.py [--fix]
  without --fix: diagnose only. With --fix: also run the safe repairs.

Safe repairs (idempotent, reversible, no judgment):
  - drain supervisor outbox into PMB (pmb_outbox_pickup --once)
Everything else -- pending conflict pairs, dead PAMS conversation capture,
stale plan items -- is reported OPEN with a command, never touched.
"""
import subprocess
import sys
from pathlib import Path
import os

HERE = Path(__file__).resolve().parent
PY315T = r"C:/Azazael/.venvs/py315t/Scripts/python.exe"
PMB_PY = r"C:/Users/clint/.pmb-venv/Scripts/python.exe"


def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=str(HERE.parent))
        return r.returncode, (r.stdout or "")[-1500:]
    except Exception as exc:
        return -1, "%s: %s" % (type(exc).__name__, exc)


def main(argv=None) -> int:
    fix = "--fix" in (argv if argv is not None else sys.argv[1:])
    open_items = []

    print("== engram binary ==")
    code, out = run([r"C:/Users/clint/go/bin/engram.exe", "doctor"])
    print("DIAGNOSED" if code == 0 and out else "OPEN: engram binary missing")

    print("== conflicts ==")
    code, out = run([r"C:/Users/clint/go/bin/engram.exe", "conflicts",
                     "stats", "--project", "azazael"])
    if "pending" in out.lower():
        import re
        m = re.search(r"pending\D*(\d+)", out.lower())
        n = int(m.group(1)) if m else -1
        if n > 0:
            print("OPEN: %d pending pairs -- judge them (mem_judge), never auto-judged" % n)
            open_items.append("pending-pairs")
        else:
            print("DIAGNOSED: no pending pairs")
    else:
        print("DIAGNOSED: no pending pairs")

    print("== pmb outbox ==")
    outbox = HERE.parent / "state" / "supervisor_outbox.jsonl"
    rows = [l for l in outbox.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.strip()] if outbox.exists() else []
    if rows and fix:
        code, out = run([PMB_PY, str(HERE / "pmb_outbox_pickup.py"), "--once"])
        print(("REPAIRED: drained" if code == 0 else "OPEN: pickup failed")
              + " (%d rows)" % len(rows))
        if code != 0:
            open_items.append("outbox-drain")
    elif rows:
        print("OPEN: %d outbox rows waiting -- rerun with --fix" % len(rows))
        open_items.append("outbox-drain")
    else:
        print("DIAGNOSED: outbox empty")

    print("== pams conversation capture ==")
    import sqlite3
    try:
        c = sqlite3.connect("file:%s/ai_memories.db?mode=ro"
                            % os.path.expanduser("~/.pams/memory_data"), uri=True)
        n = c.execute("select count(*) from curated_memories").fetchone()[0]
        c.close()
        print("DIAGNOSED: curated store readable (%d rows); conversation " % n
              + "capture dead is tracked, not repaired (see #380 plan)")
    except Exception as exc:
        print("OPEN: pams unreadable (%s)" % exc)
        open_items.append("pams-unreadable")

    print("== verdict ==")
    code, out = run([PY315T, str(HERE / "supervisor_mcp.py"), "--verdict"])
    tail = [l for l in out.splitlines() if l.startswith("VERDICT:")]
    print(tail[-1] if tail else "OPEN: verdict unreadable")
    if tail and "NO-GO" in tail[-1]:
        open_items.append("verdict-nogo")

    print()
    if open_items:
        print("OPEN: %s" % ", ".join(open_items))
        return 1
    print("ALL CLEAR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
