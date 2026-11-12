"""receipt_guard.py — subagent guard G2: a completion must carry a receipt.

WHY THIS EXISTS. "Done" is currently an assertion. On 2026-11-02 the guards
audit (docs/SUBAGENT_GUARDS_2026-11-02.md, board row 47198) scored the open
flank as D2 -- a subagent claiming a task finished without an artefact -- and
D8 -- fabricated provenance. The lane board retires a row with a free-text
string, so the string and the work have no required relationship. This guard is
the cheapest mechanism in the tree that puts one there: it refuses a completion
whose evidence cannot name what ran, whether it succeeded, and what it printed.

WHAT A RECEIPT IS. A small JSON object with exactly three load-bearing fields:

    {"command": "python -m pytest tests/test_x.py -q",
     "exit_code": 0,
     "stdout_sha256": "<64 lowercase hex characters>"}

Extra fields (board id, agent, timestamp) are allowed and ignored, so a caller
can annotate a receipt without this guard refusing it for being helpful.

WHAT IT CATCHES
  1. A completion with no receipt at all.
  2. A receipt missing any of the three fields.
  3. A command that is empty or whitespace -- "ran something" is not a command.
  4. An exit_code that is not an int, including a string "0" and a bool. True is
     not an exit code; Coercing it to 1 would silently accept a fabricated one.
  5. A stdout_sha256 that is not 64 lowercase hex characters, with the specific
     defect named: 63 characters, uppercase hex, or a non-hex character.

THE LIMIT, STATED PLAINLY, because overclaiming here would be the very defect.
This validates the SHAPE of a receipt. It cannot prove the command was run, and
a determined writer can hash any bytes and call it stdout. It raises the cost of
the specific failure that occurred -- an unevidenced "done" -- from zero to one
deliberate lie. It does not make fabrication impossible, and RULE 0B-D forbids
saying otherwise. What closes that remainder is the receipt being read back
against the command's own output, which is a wiring job, not this file.

NOT WIRED, ON PURPOSE. The 2026-11-02 audit records "unwired" as the current
state and leaves wiring to the owner. This module refuses nothing by itself
until a pre-push step or the board's `done` verb calls it.

Usage:
    python tools/receipt_guard.py --receipt receipt.json
    python tools/receipt_guard.py --out receipt.json --run -- python tools/foo.py --check
    python tools/receipt_guard.py --selftest

Note on ordering: --out must come BEFORE --run. --run takes the rest of the
line as its command (REMAINDER), so a trailing --out would be handed to the
command instead of being parsed.

Exit: 0 the receipt is accepted, 1 it is refused (or the selftest failed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

FIELDS = ("command", "exit_code", "stdout_sha256")

#: How long --run may wait for the command it is evidencing. 30 minutes covers
#: a full suite run; a longer command needs its own bound, not the guard's.
RUN_TIMEOUT_S = 1800.0

#: GNU timeout's exit code for "killed at the deadline". Reported in the
#: receipt so a timeout cannot be mistaken for a clean or a failing run.
TIMEOUT_EXIT_CODE = 124

_HEX64 = re.compile(r"[0-9a-f]{64}")
_ANY_HEX = re.compile(r"[0-9A-Fa-f]*")
_HEX_CHARS = set("0123456789abcdefABCDEF")


def sha256_bytes(data: bytes) -> str:
    """The digest a receipt carries. Lowercase, always."""
    return hashlib.sha256(data).hexdigest()


def _sha_reason(value: str) -> str:
    """Name the actual defect. 'invalid' tells the reader nothing."""
    if _HEX64.fullmatch(value):
        return ""                                   # not reachable; caller checks
    if len(value) == 64 and _ANY_HEX.fullmatch(value):
        return ("is uppercase hex; this tree stores and compares lowercase "
                "digests, so an uppercase one can never match")
    if _ANY_HEX.fullmatch(value) and len(value) != 64:
        return (f"is {len(value)} hex character(s); exactly 64 are required "
                "for a sha256")
    bad = sorted({c for c in value if c not in _HEX_CHARS})
    shown = ", ".join(repr(c) for c in bad[:4])
    return f"contains non-hex character(s) {shown}"


def validate(receipt: object) -> list[tuple[str, str]]:
    """Return (field, reason) for every problem; an empty list means accepted.

    Field names are returned rather than a boolean so the refusal can say
    exactly which field is missing or malformed.
    """
    if receipt is None:
        return [("<root>", "no receipt supplied; a completion without evidence "
                           "is an assertion, not a completion")]
    if not isinstance(receipt, dict):
        return [("<root>", f"receipt is a JSON {type(receipt).__name__}, not an "
                           "object")]

    problems: list[tuple[str, str]] = []
    for field in FIELDS:
        if field not in receipt:
            problems.append((field, "missing from the receipt"))

    if "command" in receipt:
        command = receipt["command"]
        if not isinstance(command, str):
            problems.append(
                ("command", f"is a {type(command).__name__}, must be a string"))
        elif not command.strip():
            problems.append(
                ("command", "is empty; a receipt must name the command that ran"))

    if "exit_code" in receipt:
        code = receipt["exit_code"]
        # bool is an int subclass, so it must be excluded explicitly.
        if isinstance(code, bool):
            problems.append(
                ("exit_code", f"is a bool ({code}); an exit code is an int, and "
                              "True is not 0"))
        elif not isinstance(code, int):
            problems.append(
                ("exit_code", f"is a {type(code).__name__} ({code!r}), must be an "
                              "int"))

    if "stdout_sha256" in receipt:
        digest = receipt["stdout_sha256"]
        if not isinstance(digest, str):
            problems.append(
                ("stdout_sha256", f"is a {type(digest).__name__}, must be a hex "
                                  "string"))
        elif not _HEX64.fullmatch(digest):
            problems.append(("stdout_sha256", _sha_reason(digest)))

    return problems


def receipt_for(argv: list[str], timeout: float = RUN_TIMEOUT_S) -> dict:
    """Run a command without a shell and return its receipt.

    No shell, so a command string cannot smuggle a second command past the
    reader. stdout only: a receipt that hashed interleaved stderr would change
    between runs for reasons unrelated to the work.

    Bounded, because a guard that can hang is not a guard. A command that never
    returns yields exit_code 124 (what GNU timeout uses) and the partial stdout
    it managed, so "still running" is stated in the receipt instead of the
    caller waiting forever for evidence that cannot arrive.
    """
    try:
        completed = subprocess.run(argv, capture_output=True, check=False,
                                   timeout=timeout)
        exit_code = completed.returncode
        stdout = completed.stdout
    except subprocess.TimeoutExpired as exc:
        exit_code = TIMEOUT_EXIT_CODE
        stdout = exc.stdout or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8", errors="replace")
    return {
        "command": " ".join(argv),
        "exit_code": exit_code,
        "stdout_sha256": sha256_bytes(stdout),
    }


def _report(problems: list[tuple[str, str]]) -> int:
    if not problems:
        return 0
    print(f"REFUSED: receipt rejected ({len(problems)} problem(s))")
    for field, reason in problems:
        print(f"  - {field}: {reason}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="G2: a completion must carry a receipt "
                    "(command, exit_code, stdout_sha256)")
    ap.add_argument("--receipt", metavar="FILE",
                    help="validate a receipt JSON file")
    ap.add_argument("--out", metavar="FILE",
                    help="where --run writes the receipt (default: stdout); "
                         "must precede --run")
    ap.add_argument("--run", nargs=argparse.REMAINDER,
                    help="run CMD, write its receipt to --out, then validate it")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the guard rejects malformed receipts and "
                         "accepts a valid one")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.run is not None:
        cmd = args.run[1:] if args.run[:1] == ["--"] else args.run
        if not cmd:
            print("REFUSED: --run needs a command after --")
            return 1
        receipt = receipt_for(cmd)
        problems = validate(receipt)
        if problems:
            return _report(problems)
        body = json.dumps(receipt, indent=2)
        if args.out:
            Path(args.out).write_text(body + "\n", encoding="utf-8")
            print(f"receipt written: {args.out}")
        else:
            print(body)
        print(f"ACCEPTED: exit_code={receipt['exit_code']} "
              f"stdout_sha256={receipt['stdout_sha256']}")
        return 0

    if not args.receipt:
        print("REFUSED: no receipt supplied (use --receipt, --run or --selftest)")
        return 1
    try:
        raw = Path(args.receipt).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"REFUSED: receipt file cannot be read ({exc.strerror})")
        return 1
    try:
        receipt = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"REFUSED: receipt is not JSON ({exc.msg} at line {exc.lineno})")
        return 1

    problems = validate(receipt)
    if problems:
        return _report(problems)
    print(f"ACCEPTED: {receipt['command']} -> exit_code={receipt['exit_code']} "
          f"stdout_sha256={receipt['stdout_sha256'][:16]}...")
    return 0


# ── selftest: negative controls first, then the positive control ──────────
VALID = {
    "command": "python -m pytest tests/test_receipt_guard.py -q",
    "exit_code": 0,
    "stdout_sha256": "9f2c" + "0" * 60,
}

# (label, receipt, the field the refusal MUST name)
CASES = [
    ("no receipt at all", None, "<root>"),
    ("receipt is a list", [VALID], "<root>"),
    ("missing command", {"exit_code": 0, "stdout_sha256": VALID["stdout_sha256"]},
     "command"),
    ("empty command", {"command": "   ", "exit_code": 0,
                       "stdout_sha256": VALID["stdout_sha256"]}, "command"),
    ("missing exit_code", {"command": VALID["command"],
                           "stdout_sha256": VALID["stdout_sha256"]}, "exit_code"),
    ("exit_code is the string '0'", {"command": VALID["command"],
                                     "exit_code": "0",
                                     "stdout_sha256": VALID["stdout_sha256"]},
     "exit_code"),
    ("exit_code is a bool", {"command": VALID["command"], "exit_code": True,
                             "stdout_sha256": VALID["stdout_sha256"]},
     "exit_code"),
    ("hash is 63 chars", {"command": VALID["command"], "exit_code": 0,
                          "stdout_sha256": "a" * 63}, "stdout_sha256"),
    ("hash is uppercase", {"command": VALID["command"], "exit_code": 0,
                           "stdout_sha256": "A" * 64}, "stdout_sha256"),
    ("hash contains a non-hex char", {"command": VALID["command"], "exit_code": 0,
                                      "stdout_sha256": "z" + "a" * 63},
     "stdout_sha256"),
    ("missing the hash", {"command": VALID["command"], "exit_code": 0},
     "stdout_sha256"),
]

# Receipts that MUST be accepted. The first is the control that proves the
# guard is not simply always refusing.
CLEAN = [
    ("a valid receipt", VALID),
    ("a failing command still carries evidence",
     {"command": "python -m pytest tests/test_x.py -q", "exit_code": 1,
      "stdout_sha256": "b" * 64}),
    ("annotations are ignored, not refused",
     dict(VALID, board=47198, agent="freebuff", at="2026-11-02")),
]


def selftest() -> int:
    fails = 0
    print("=" * 72)
    print("G2 RECEIPT SELF-TEST -- the guard must REFUSE malformed receipts")
    print("=" * 72)
    for label, receipt, field in CASES:
        problems = validate(receipt)
        named = {f for f, _ in problems}
        ok = bool(problems) and field in named
        print(f"  [{'PASS' if ok else 'FAIL'}] refuse: {label}")
        if not ok:
            fails += 1
            print(f"         expected field {field!r}, got {sorted(named)}")

    print("-" * 72)
    print("and MUST ACCEPT a valid one -- otherwise it is a guard that only says no")
    print("-" * 72)
    for label, receipt in CLEAN:
        problems = validate(receipt)
        ok = not problems
        print(f"  [{'PASS' if ok else 'FAIL'}] accept: {label}")
        if not ok:
            fails += 1
            for field, reason in problems:
                print(f"         false refusal: {field}: {reason}")

    # Live control for the bound itself: a command that outlives its timeout
    # must come back as exit 124 in a well-shaped receipt, not hang the guard
    # and not pass as a clean run. 0.2s against a 2s sleep, so the check costs
    # a fifth of a second and actually exercises the except branch.
    timed = receipt_for([sys.executable, "-c", "import time; time.sleep(2)"],
                        timeout=0.2)
    ok = timed["exit_code"] == TIMEOUT_EXIT_CODE and not validate(timed)
    print(f"  [{'PASS' if ok else 'FAIL'}] bound: a command that never returns "
          f"-> exit_code={timed['exit_code']}")
    if not ok:
        fails += 1

    total = len(CASES) + len(CLEAN) + 1
    print(f"\n  {total} cases, {fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
