#!/usr/bin/env python3
# coding: utf-8
"""REVERT SELF-TEST -- prove the rollback path actually restores bytes.

The harness's authority rests entirely on "a mutation that does not earn its
place is reverted". That claim was never tested against real source: the only
evidence was a log line reading `reverted`. A revert that is *partially* applied
is worse than no revert, because the ledger then records a clean rollback while
the tree carries damage.

Two real cases, both against real files, no rehearsal mode:

  1. IN-PLACE: substitution injects a syntax error into a real source file.
     tier1 must fail, the revert must run, and the file's sha256 must be
     byte-identical to the sha256 taken before.
  2. CREATION: a patch creates a file that has never existed. The revert must
     DELETE it -- byte-restore alone leaves the new broken file on disk and the
     rollback is silently half-done. This case found that exact bug once, which
     is why it is now permanent.

Exit code is 0 only when every case passes. Run after any change to the
harness's save/revert logic.

    python tools/revert_selftest.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
BAND = "tests/test_mesh_fabric_followups.py"
SENTINEL = "src/CORE/data_retention.py"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_harness(patch: Path, band: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "self_modify.py"), "--patch", str(patch),
         "--tier2", band, "--model", "revert-selftest"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
        encoding="utf-8", errors="replace")
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def case_in_place() -> dict:
    target = ROOT / SENTINEL
    if not target.is_file():
        return {"case": "in_place", "passed": False,
                "detail": "sentinel file missing: %s" % SENTINEL}
    before = sha256(target)
    original = target.read_text(encoding="utf-8")

    # A real syntax error on a real line of real source. Substitution format, so
    # line endings are preserved byte-for-byte and the patch cannot fail for EOL
    # reasons of its own.
    broken = original.replace("import ", "import (", 1)
    if broken == original:
        return {"case": "in_place", "passed": False, "detail": "could not build a mutation"}
    patch = LOGS / "revert_selftest_inplace.json"
    patch.write_text(json.dumps({"encoding": "utf-8",
                                 "subs": {SENTINEL: [["import ", "import ("]]}}),
                     encoding="utf-8")

    res = run_harness(patch, BAND)
    after = sha256(target) if target.is_file() else "<deleted>"
    restored = (after == before)
    verdict_line = next((l for l in res["out"].splitlines() if l.startswith("verdict")), "")
    revert_line = next((l for l in res["out"].splitlines() if l.startswith("reason")), "")
    return {
        "case": "in_place",
        "passed": bool(restored and "reverted" in verdict_line),
        "sha_before": before[:16],
        "sha_after": str(after)[:16],
        "byte_identical": restored,
        "verdict": verdict_line.strip(),
        "reason": revert_line.strip()[:120],
        "expected": "tier1 catches the syntax error and the file is restored byte-identical",
    }


def case_creation() -> dict:
    rel = "src/CORE/_revert_selftest_created.py"
    target = ROOT / rel
    if target.exists():
        target.unlink()
    diff = LOGS / "revert_selftest_create.diff"
    diff.write_text(
        "diff --git a/%s b/%s\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/%s\n"
        "@@ -0,0 +1,1 @@\n"
        "+def broken(:\n" % (rel, rel, rel),
        encoding="utf-8")
    res = run_harness(diff, BAND)
    survived = target.exists()
    if survived:
        target.unlink()
    verdict_line = next((l for l in res["out"].splitlines() if l.startswith("verdict")), "")
    return {
        "case": "creation",
        "passed": bool((not survived) and "reverted" in verdict_line),
        "file_left_on_disk": survived,
        "verdict": verdict_line.strip(),
        "expected": "tier1 fails and the created file is DELETED, not merely restored",
    }


def main() -> int:
    cases = []
    for fn in (case_in_place, case_creation):
        try:
            cases.append(fn())
        except Exception as exc:  # noqa: BLE001 - a crashed case is a failed case
            cases.append({"case": fn.__name__, "passed": False, "detail": repr(exc)[:200]})

    LOGS.mkdir(exist_ok=True)
    (LOGS / "revert_selftest.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")

    print("=== REVERT SELF-TEST (real source, real failures) ===")
    for c in cases:
        print("[%s] %-10s %s" % ("PASS" if c.get("passed") else "FAIL", c["case"],
                                 c.get("expected", c.get("detail", ""))))
        if c["case"] == "in_place" and "sha_before" in c:
            print("        sha %s -> %s   byte_identical=%s" % (c["sha_before"], c["sha_after"],
                                                               c["byte_identical"]))
        if c["case"] == "creation":
            print("        file_left_on_disk=%s" % c["file_left_on_disk"])
    ok = all(c.get("passed") for c in cases)
    print()
    print("written: logs/revert_selftest.json")
    print("RESULT:", "all revert cases verified" if ok else "REVERT PATH IS NOT TRUSTWORTHY")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
