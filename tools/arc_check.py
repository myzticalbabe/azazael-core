"""One command, one container verdict, for the whole ARC.

ARC holds accountability wings. Each wing owns a ledger (``marks.json`` or
``pins.json``) and a checker (``tools/<wing>_pins.py``) that grades that ledger.
Before this tool existed, the container's state could only be assembled by
running three checkers by hand and reading three verdicts -- which is how a wing
can be ``GAPS`` for a day while the container is described as "built". A reading
that only exists in someone's memory of three terminal scrollbacks is not a
reading.

So this tool does one thing: run every declared wing's own checker, in a
subprocess, with a wall bound, and report the worst recorded state.

A wing's checker grades whether a pin's text answers a demand. It does not grade
whether the evidence behind that text can still be looked at, and on 2026-11-05
none of the 39 pins in the tree carried bytes that could be re-examined. So one
leg runs beside the wings: ``tools/arc_provenance.py``, in its own bounded child,
re-hashing captures from disk and locating in-tree quotes and instruments. Its
verdict is folded in, the way a deep dive is a stage of a test run rather than a
second test runner. It is not a second grader of pins -- it has no opinion about
whether any pin is enough -- and it can never turn a wing's ``GAPS`` into
``READY``.

What it does NOT do, and must never do:

* It does not grade a wing itself. The wing's checker is the instrument; this
  tool has no opinion about pins, tiers or evidence. A second grader would drift
  from the first one, and the two would disagree in public. The evidence leg is
  the narrow exception and is scoped to keep that true: it grades whether the
  bytes and instruments a pin names still exist, never whether the pin is
  enough, and it is reported under its own row.
* It never turns an unreadable instrument into a pass. Missing checker, missing
  ledger, unparseable output, a crash, a timeout, or a checker whose printed
  verdict disagrees with its own exit code or its own JSON report all grade
  ``BLIND`` for that wing. ``BLIND`` is a state to fix first, not a failure to
  average away.
* It does not hide a wing. A declared wing with no inhabitant (``golgotha``) is
  printed as ``BLOCKED`` every run, and a directory that appears under ``ARC/``
  without being declared is printed as ``UNDECLARED`` and caps the container
  below ``READY``. Otherwise a new, unchecked wing enters the container silently
  and the container's verdict keeps describing a smaller tree than the one that
  exists.

Verdict and exit contract, shared with the wings themselves:

    READY       0   every declared wing READY and nothing undeclared
    GAPS        1   a wing reported a defect in evidence it already claims
    INCOMPLETE  1   no defect, but coverage is missing: a wing is INCOMPLETE, a
                    declared wing is BLOCKED, or a directory is UNDECLARED
    BLIND       2   an instrument could not be read -- never a pass

Headline precedence is BLIND > GAPS > INCOMPLETE > READY. A ``BLOCKED`` or
``UNDECLARED`` wing therefore does not set the headline when a real defect also
exists, which is why both are printed unconditionally in their own section and
enumerated again in the ``--json`` report: the headline names the most urgent
class, the sections name every reason. A container that prints ``GAPS`` and
nothing else about a missing wing would be the silent success this file exists
to prevent.

Usage:

    python tools/arc_check.py
    python tools/arc_check.py --json logs/arc_check.json
    python tools/arc_check.py --timeout 240
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
ARC = ROOT / "ARC"

VERDICT_READY = "READY"
VERDICT_GAPS = "GAPS"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_BLIND = "BLIND"

# A wing that exists but holds no inhabitant. Recorded, never dropped: the term
# was searched for in the tree, the git history and the telemetry store without
# a referent, so the honest state is "nothing to grade yet", not "absent".
VERDICT_BLOCKED = "BLOCKED"
VERDICT_UNDECLARED = "UNDECLARED"

EXIT_READY = 0
EXIT_BAD = 1
EXIT_BLIND = 2

# Per-verdict exit code the wings themselves promise. Used to detect a checker
# that prints one verdict and exits in a way that contradicts it.
WING_EXIT: Dict[str, int] = {
    VERDICT_READY: EXIT_READY,
    VERDICT_GAPS: EXIT_BAD,
    VERDICT_INCOMPLETE: EXIT_BAD,
    VERDICT_BLIND: EXIT_BLIND,
}

# Declaration, not discovery: a wing is a directory under ARC/ with a ledger and
# a checker. Adding a wing is adding a row here plus its checker; if a directory
# under ARC/ is not listed here or in NON_WING_DIRS, the container reports it as
# UNDECLARED rather than ignoring it.
WINGS: Tuple[Dict[str, str], ...] = (
    {
        "wing": "esquire",
        "checker": "tools/esquire_pins.py",
        "ledger": "ARC/esquire/pins.json",
        "inhabitant": "what a court demands before it hears a machine's account",
    },
    {
        "wing": "cain",
        "checker": "tools/cain_obsolescence_pins.py",
        "ledger": "ARC/cain/marks.json",
        "inhabitant": "accountability mark for AI systems and planned obsolescence",
    },
    {
        "wing": "eliza",
        "checker": "tools/eliza_arc_pins.py",
        "ledger": "ARC/eliza/marks.json",
        "inhabitant": "what a conversational agent may claim, and must refuse to claim",
        # The pins cite `tools/<instrument>.py`, and the instruments live in the
        # project the wing grades. Without this, a provenance run reported two
        # tier-A pins as "instrument missing" for a path that resolves one
        # directory down (measured 2026-11-05).
        "project_root": "Project_ELIZA",
    },
)

BLOCKED_WINGS: Dict[str, str] = {
    "golgotha": (
        "the term has no referent in the tree, the git history, or the telemetry "
        "store, so there is nothing to grade"
    ),
}

# Directories under ARC/ that are evidence about the container rather than a
# graded wing. Named explicitly so the undeclared check stays meaningful.
NON_WING_DIRS = frozenset({"environments", "deere"})

# The provenance leg. It is not a wing: it grades no pin's text, it re-examines
# whether the evidence a wing's tier-A pins claim can still be looked at (bytes
# re-hashed from disk, in-tree quotes located, instruments found). It runs as a
# bounded child and its verdict is folded into the container's, the way a deep
# dive is a stage of a test run rather than a second test runner.
PROVENANCE_TOOL = "tools/arc_provenance.py"

# The generated block in ARC/README.md. Bounded by markers so a hand-edited
# verdict cannot survive a write, and --check-readme can fail on drift instead
# of leaving a stale table to be read as current.
STATE_START = "<!-- ARC:STATE START"
STATE_END = "<!-- ARC:STATE END -->"
README = ARC / "README.md"

VERDICT_RE = re.compile(r"^verdict:\s*([A-Za-z]+)\s*$", re.MULTILINE)


def blind(name: str, reason: str) -> Dict[str, Any]:
    """A wing whose instrument could not be read. Never a pass, always named."""
    return {
        "wing": name,
        "verdict": VERDICT_BLIND,
        "exit": EXIT_BLIND,
        "reason": reason,
        "errors": [reason],
        "pins": None,
        "seconds": 0.0,
        "stdout_sha256": None,
    }


def parse_printed_verdict(stdout: str) -> Optional[str]:
    """The verdict line the checker printed, if it printed one."""
    match = VERDICT_RE.search(stdout or "")
    if not match:
        return None
    return match.group(1).upper()


def grade_wing(
    name: str,
    exit_code: int,
    report: Optional[Dict[str, Any]],
    stdout: str,
    seconds: float,
) -> Dict[str, Any]:
    """Grade one wing from what its checker actually said and did.

    Three readings must agree: the printed verdict, the exit code, and the JSON
    report. Any disagreement is BLIND for this wing, because a wing that cannot
    state its own state consistently cannot be quoted in the container's.
    """
    printed = parse_printed_verdict(stdout)
    from_json = report.get("verdict") if isinstance(report, dict) else None
    if isinstance(from_json, str):
        from_json = from_json.upper()
    else:
        from_json = None

    declared = from_json or printed
    if not declared:
        return blind(
            name,
            "no verdict line in output and no report written (exit %d)" % exit_code,
        )

    if declared not in WING_EXIT:
        return blind(name, "unknown verdict %r (exit %d)" % (declared, exit_code))

    disagreements: List[str] = []
    if from_json and printed and from_json != printed:
        disagreements.append("report says %s, printed %s" % (from_json, printed))
    if exit_code != WING_EXIT[declared]:
        disagreements.append(
            "verdict %s promises exit %d, process exited %d"
            % (declared, WING_EXIT[declared], exit_code)
        )
    if disagreements:
        return blind(
            name,
            "instrument disagrees with itself: " + "; ".join(disagreements),
        )

    errors = report.get("errors") if isinstance(report, dict) else None
    if not isinstance(errors, list):
        errors = []
    pins = report.get("pins") if isinstance(report, dict) else None
    return {
        "wing": name,
        "verdict": declared,
        "exit": exit_code,
        "reason": None,
        "errors": [str(e) for e in errors],
        "pins": pins if isinstance(pins, int) else None,
        "seconds": round(seconds, 2),
        "stdout_sha256": hashlib.sha256((stdout or "").encode("utf-8")).hexdigest(),
    }


def run_wing(wing: Dict[str, str], timeout: float) -> Dict[str, Any]:
    """Run one wing's checker under a wall bound and grade what it said."""
    name = wing["wing"]
    checker = ROOT / wing["checker"]
    ledger = ROOT / wing["ledger"]

    if not checker.is_file():
        return blind(name, "checker missing at %s" % wing["checker"])
    if not ledger.is_file():
        return blind(name, "ledger missing at %s" % wing["ledger"])

    with tempfile.TemporaryDirectory(prefix="arc_check_") as tmp:
        out = Path(tmp) / ("%s_report.json" % name)
        cmd = [
            sys.executable,
            str(checker),
            "--check",
            "--json",
            str(out),
            "--ledger",
            str(ledger),
        ]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            reason = "checker exceeded %.0fs wall bound; a hung instrument is not a pass" % timeout
            result = blind(name, reason)
            result["seconds"] = round(elapsed, 2)
            return result
        except OSError as exc:
            reason = "checker could not be started: %s: %s" % (type(exc).__name__, exc)
            return blind(name, reason)
        elapsed = time.monotonic() - started

        report: Optional[Dict[str, Any]] = None
        if out.is_file():
            try:
                loaded = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                reason = "report at %s could not be read: %s" % (out.name, exc)
                result = blind(name, reason)
                result["stdout_sha256"] = hashlib.sha256(
                    (proc.stdout or "").encode("utf-8")
                ).hexdigest()
                return result
            if isinstance(loaded, dict):
                report = loaded

    return grade_wing(name, proc.returncode, report, proc.stdout or "", elapsed)


def run_provenance(
    timeout: float, run_instruments: bool = False
) -> Dict[str, Any]:
    """Run the evidence leg and grade it the same way a wing is graded.

    Same discipline as ``run_wing``: a missing tool, an unreadable report, a
    timeout and a self-contradicting instrument all grade ``BLIND``. The leg is
    never allowed to be absent quietly.
    """
    name = "provenance"
    tool = ROOT / PROVENANCE_TOOL
    if not tool.is_file():
        return blind(name, "tool missing at %s" % PROVENANCE_TOOL)

    with tempfile.TemporaryDirectory(prefix="arc_prov_") as tmp:
        out = Path(tmp) / "provenance_report.json"
        cmd = [sys.executable, str(tool), "--check", "--json", str(out)]
        if run_instruments:
            cmd.append("--run-instruments")
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            result = blind(
                name,
                "evidence leg exceeded %.0fs wall bound; a hung instrument is not a pass"
                % timeout,
            )
            result["seconds"] = round(elapsed, 2)
            return result
        except OSError as exc:
            return blind(
                name, "evidence leg could not be started: %s: %s" % (type(exc).__name__, exc)
            )
        elapsed = time.monotonic() - started

        report: Optional[Dict[str, Any]] = None
        if out.is_file():
            try:
                loaded = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                result = blind(name, "report at %s could not be read: %s" % (out.name, exc))
                result["stdout_sha256"] = hashlib.sha256(
                    (proc.stdout or "").encode("utf-8")
                ).hexdigest()
                return result
            if isinstance(loaded, dict):
                report = loaded

    graded = grade_wing(name, proc.returncode, report, proc.stdout or "", elapsed)
    if isinstance(report, dict):
        for key in ("backed", "instruments", "defects", "coverage_gaps"):
            value = report.get(key)
            graded[key] = value if isinstance(value, int) else None
    return graded


def render_state_block(
    wings: Sequence[Dict[str, Any]],
    provenance: Optional[Dict[str, Any]],
    blocked: Sequence[str] = (),
    undeclared: Sequence[str] = (),
) -> str:
    """The README state block, computed from the same data the table prints.

    No clock and no HEAD in the text: either one would make ``--check-readme``
    fail the second after a write and train everyone to ignore it. The block
    carries readings, and the command in its first line is how to refresh them.
    """
    rows = list(wings) + ([provenance] if provenance else [])
    verdict, _ = combine(rows, blocked=blocked, undeclared=undeclared)
    lines = [
        STATE_START
        + " (generated by tools/arc_check.py --write-readme; do not edit) -->"
    ]
    lines.append("container: %s" % verdict)
    for row in rows:
        pins = "-" if row.get("pins") is None else str(row.get("pins"))
        lines.append(
            "%-10s %-11s pins=%-6s errors=%d"
            % (row["wing"], row["verdict"], pins, len(row.get("errors") or []))
        )
    lines.append("blocked: %s" % (", ".join(sorted(blocked)) or "none"))
    lines.append("undeclared: %s" % (", ".join(sorted(undeclared)) or "none"))
    lines.append(STATE_END)
    return "\n".join(lines)


def extract_state_block(text: str) -> Optional[str]:
    """The block as written, or None when the markers are absent or unbalanced."""
    start = (text or "").find(STATE_START)
    if start < 0:
        return None
    end = text.find(STATE_END, start)
    if end < 0:
        return None
    return text[start : end + len(STATE_END)]


def replace_state_block(text: str, block: str) -> Tuple[str, bool]:
    """Replace the block in place. (text, False) when there is nothing to replace."""
    start = (text or "").find(STATE_START)
    if start < 0:
        return text, False
    end = text.find(STATE_END, start)
    if end < 0:
        return text, False
    return text[:start] + block + text[(end + len(STATE_END)) :], True


def combine(
    wings: Sequence[Dict[str, Any]],
    blocked: Sequence[str] = (),
    undeclared: Sequence[str] = (),
) -> Tuple[str, int]:
    """The container verdict from graded wings.

    Precedence BLIND > GAPS > INCOMPLETE > READY: an unreadable instrument is
    fixed before a defect is argued, and a defect outranks missing coverage.
    BLOCKED and UNDECLARED both cap the container below READY, so a wing that
    does not exist can never be counted as a passing one.
    """
    states = {w.get("verdict") for w in wings}
    if VERDICT_BLIND in states:
        return VERDICT_BLIND, EXIT_BLIND
    if VERDICT_GAPS in states:
        return VERDICT_GAPS, EXIT_BAD
    if (
        VERDICT_INCOMPLETE in states
        or blocked
        or undeclared
    ):
        return VERDICT_INCOMPLETE, EXIT_BAD
    return VERDICT_READY, EXIT_READY


def undeclared_dirs() -> List[str]:
    """Directories under ARC/ that no declaration accounts for."""
    if not ARC.is_dir():
        return []
    declared = {w["wing"] for w in WINGS} | set(BLOCKED_WINGS) | set(NON_WING_DIRS)
    found: List[str] = []
    for entry in sorted(ARC.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in declared:
            continue
        found.append(entry.name)
    return found


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run every ARC wing checker and report one container verdict."
    )
    parser.add_argument(
        "--check", action="store_true", help="run the wings and exit (default)"
    )
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON")
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        metavar="SECONDS",
        help="per-wing wall bound (default 180)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print the table and verdict only"
    )
    parser.add_argument(
        "--no-provenance",
        action="store_true",
        help="skip the evidence leg (for a wing-only reading; never in a gate)",
    )
    parser.add_argument(
        "--run-instruments",
        action="store_true",
        help="forward to the evidence leg: execute each declared tier-A command",
    )
    parser.add_argument(
        "--readme",
        metavar="PATH",
        default=str(README),
        help="README carrying the generated state block (default ARC/README.md)",
    )
    parser.add_argument(
        "--write-readme",
        action="store_true",
        help="rewrite the state block in --readme from this run",
    )
    parser.add_argument(
        "--check-readme",
        action="store_true",
        help="fail when the state block in --readme is stale",
    )
    args = parser.parse_args(argv)

    if not ARC.is_dir():
        print("verdict: %s" % VERDICT_BLIND)
        print("reason: no ARC directory at %s" % ARC)
        return EXIT_BLIND

    graded = [run_wing(wing, args.timeout) for wing in WINGS]
    provenance = (
        None
        if args.no_provenance
        else run_provenance(args.timeout, run_instruments=args.run_instruments)
    )
    legs = graded + ([provenance] if provenance else [])
    undeclared = undeclared_dirs()
    name, code = combine(legs, blocked=list(BLOCKED_WINGS), undeclared=undeclared)

    if args.json:
        out = Path(args.json)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        "verdict": name,
                        "exit": code,
                        "wings": graded,
                        "provenance": provenance,
                        "blocked": [
                            {"wing": w, "reason": r} for w, r in BLOCKED_WINGS.items()
                        ],
                        "undeclared": undeclared,
                        "non_wing_dirs": sorted(NON_WING_DIRS),
                        "timeout_s": args.timeout,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            # A report that could not be written is not a passing run.
            print("verdict: %s" % VERDICT_BLIND)
            print("reason: report could not be written to %s: %s" % (out, exc))
            return EXIT_BLIND
        print("wrote %s" % out)

    print("%-10s %-12s %-5s %-6s %-8s %s" % ("wing", "verdict", "exit", "pins", "secs", "errors"))
    for wing in legs:
        print(
            "%-10s %-12s %-5d %-6s %-8.2f %d"
            % (
                wing["wing"],
                wing["verdict"],
                wing["exit"],
                "-" if wing["pins"] is None else wing["pins"],
                wing.get("seconds", 0.0),
                len(wing["errors"]),
            )
        )

    # Every leg that did not grade is named with its reason, whatever the
    # headline says. This is the section that stops a missing wing being silent.
    for wing in legs:
        if wing["verdict"] == VERDICT_BLIND:
            print("  [BLIND] %s: %s" % (wing["wing"], wing["reason"]))

    if blocked := BLOCKED_WINGS:
        for wing, reason in blocked.items():
            print("  [BLOCKED] %s: %s" % (wing, reason))
    for wing in undeclared:
        print(
            "  [UNDECLARED] ARC/%s has no checker declaration; add it to WINGS "
            "or NON_WING_DIRS in tools/arc_check.py" % wing
        )

    if not args.quiet:
        for wing in legs:
            for err in wing["errors"][:3]:
                print("  [%s] %s" % (wing["verdict"], err))
            hidden = len(wing["errors"]) - 3
            if hidden > 0:
                print("  [%s] ... and %d more (run %s for the full list)"
                      % (wing["verdict"], hidden, wing["wing"]))

    print("verdict: %s" % name)
    print(
        "container: %d graded wings (%s), %d blocked, %d undeclared"
        % (
            len(graded),
            ", ".join("%s=%s" % (w["wing"], w["verdict"]) for w in graded) or "none",
            len(BLOCKED_WINGS),
            len(undeclared),
        )
    )
    if provenance is not None:
        print(
            "evidence: %s (%s)"
            % (
                provenance["verdict"],
                "pins=None"
                if provenance.get("pins") is None
                else "backed=%s instruments=%s defects=%s coverage=%s"
                % (
                    provenance.get("backed"),
                    provenance.get("instruments"),
                    provenance.get("defects"),
                    provenance.get("coverage_gaps"),
                ),
            )
        )

    if args.write_readme or args.check_readme:
        block = render_state_block(
            graded, provenance, list(BLOCKED_WINGS), undeclared
        )
        readme = Path(args.readme)
        try:
            text = readme.read_text(encoding="utf-8")
        except OSError as exc:
            print("verdict: %s" % VERDICT_BLIND)
            print("reason: README could not be read at %s: %s" % (readme, exc))
            return EXIT_BLIND
        current = extract_state_block(text)
        if current is None:
            # Appending a block the check cannot find would be a state that
            # reads as current and never updates again.
            print("verdict: %s" % VERDICT_BLIND)
            print(
                "reason: no %s ... %s markers in %s; the block cannot be placed"
                % (STATE_START, STATE_END, readme)
            )
            return EXIT_BLIND
        if args.write_readme:
            updated, replaced = replace_state_block(text, block)
            if not replaced:
                print("verdict: %s" % VERDICT_BLIND)
                print("reason: block markers in %s could not be replaced" % readme)
                return EXIT_BLIND
            if updated != text:
                try:
                    readme.write_text(updated, encoding="utf-8")
                except OSError as exc:
                    print("verdict: %s" % VERDICT_BLIND)
                    print("reason: README could not be written: %s" % exc)
                    return EXIT_BLIND
                print("wrote state block to %s" % readme)
            else:
                print("state block in %s already current" % readme)
        if args.check_readme and current.strip() != block.strip():
            print("  [STALE] %s state block disagrees with this run:" % readme)
            for line in block.splitlines():
                print("    %s" % line)
            code = EXIT_BAD
        elif args.check_readme:
            print("state block in %s is current" % readme)
    return code


if __name__ == "__main__":
    sys.exit(main())
