"""Which declared surfaces in ARC carry no pin -- and whether the wing would notice.

A pin is only an accountability device if its absence is detectable. Each wing
declares its own surface inventory and its own binding rule:

    esquire   GATE_METADATA ids           pins[].covers        "has no pin"
    cain      live (vendor, architecture) pins[].vendor+architecture
                                                               "is published and unpinned"
    eliza     README rows in a table whose last header cell declares a measurement
                                          pins[].source "README.md:<line>"
                                                               "publishes a measurement and
                                                                no pin answers for it"

This tool does not invent a fourth inventory: it asks each wing's own module for
the declared set, reads that wing's ledger, and reports the difference. Two
numbers per wing, because they answer different questions:

    declared   surfaces the wing says exist
    pinned     of those, how many a pin answers for
    missing    declared - pinned, named one per line
    orphans    pins that answer for nothing the wing declares

`--prove` is the control. It copies a wing's ledger to a temporary file, deletes
one pin, runs that wing's own checker against the copy, and reports whether the
checker turned red. A rule that exists in source but does not fire on a deleted
pin is reported SILENT: the binding is then a comment, not a gate. A control that
cannot be run is BLIND, never a pass.

Verdicts and exit codes:

    CLEAN    exit 0  every declared surface is pinned and every pin answers for one
    MISSING  exit 1  at least one declared surface has no pin (or a pin answers for nothing)
    BLIND    exit 2  a wing's declaration, ledger or checker could not be read

Exit 2 is never a pass. Usage:

    python tools/arc_pin_audit.py              # inventory across the three wings
    python tools/arc_pin_audit.py --wing cain  # one wing
    python tools/arc_pin_audit.py --prove      # + delete-a-pin control per wing
    python tools/arc_pin_audit.py --json out.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"

ESQUIRE_LEDGER = REPO_ROOT / "ARC" / "esquire" / "pins.json"
CAIN_LEDGER = REPO_ROOT / "ARC" / "cain" / "marks.json"
ELIZA_LEDGER = REPO_ROOT / "ARC" / "eliza" / "marks.json"
ELIZA_PROJECT = REPO_ROOT / "Project_ELIZA"

VERDICT_CLEAN = "CLEAN"
VERDICT_MISSING = "MISSING"
VERDICT_BLIND = "BLIND"

EXIT_CLEAN = 0
EXIT_MISSING = 1
EXIT_BLIND = 2


class Blinded(Exception):
    """The reading could not be performed. Never a pass."""


def load_module(name: str) -> Any:
    """Import a tool by path so this audit reads the same code the gate runs."""
    path = TOOLS / ("%s.py" % name)
    if not path.is_file():
        raise Blinded("module not found: %s" % path)
    spec = importlib.util.spec_from_file_location("_arc_pin_audit_%s" % name, path)
    if spec is None or spec.loader is None:
        raise Blinded("module could not be loaded: %s" % path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a module that will not load is BLIND
        raise Blinded("%s failed to import: %s: %s" % (name, type(exc).__name__, exc))
    return module


def read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise Blinded("ledger not found: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Blinded("ledger %s could not be read: %s" % (path, exc))
    if not isinstance(data, dict):
        raise Blinded("ledger %s is %s, not an object" % (path, type(data).__name__))
    return data


def string_items(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int))]


def esquire_inventory() -> Dict[str, Any]:
    instrument = load_module("esquire_pins")
    ledger = read_json(ESQUIRE_LEDGER)
    try:
        gates = instrument.load_instrument_gates()
    except Exception as exc:  # noqa: BLE001 - an unreadable declaration is BLIND
        raise Blinded("esquire gate metadata could not be read: %s: %s" % (type(exc).__name__, exc))
    declared = [str(g.get("id")) for g in gates if isinstance(g, dict) and g.get("id")]
    titles = {str(g.get("id")): str(g.get("title") or "") for g in gates if isinstance(g, dict)}
    pins = [p for p in ledger.get("pins") or [] if isinstance(p, dict)]
    gaps = [g for g in ledger.get("declared_gaps") or [] if isinstance(g, dict)]
    for gap in gaps:
        gap_id = str(gap.get("gate") or "")
        if gap_id:
            declared.append(gap_id)
            titles.setdefault(gap_id, str(gap.get("title") or "declared gap"))

    covered: Dict[str, List[str]] = {}
    orphans: List[str] = []
    for pin in pins:
        pid = str(pin.get("id") or "?")
        targets = string_items(pin.get("covers"))
        pending = pin.get("pending_gate")
        if isinstance(pending, str) and pending:
            targets = targets + [pending]
        for gate_id in targets:
            covered.setdefault(gate_id, []).append(pid)
        if not targets and not pin.get("context"):
            orphans.append(pid)
    for gap in gaps:
        gap_id = str(gap.get("gate") or "")
        if gap_id and string_items(gap.get("pins")):
            covered.setdefault(gap_id, []).extend(string_items(gap.get("pins")))

    missing = [
        "%s %s" % (gate_id, titles.get(gate_id, ""))
        for gate_id in declared
        if gate_id not in covered
    ]
    return {
        "wing": "esquire",
        "declared": len(declared),
        "pinned": len([g for g in declared if g in covered]),
        "missing": missing,
        "orphans": orphans,
        "rule": "tools/esquire_pins.py: '%s has no pin'",
        "_supporters": [(gate_id, covered.get(gate_id, [])) for gate_id in declared],
    }


def cain_inventory() -> Dict[str, Any]:
    instrument = load_module("cain_obsolescence_pins")
    ledger = read_json(CAIN_LEDGER)
    try:
        marks = instrument.load_marks(instrument.load_mark_module())
        described = [instrument.describe(mark) for mark in marks]
    except Exception as exc:  # noqa: BLE001 - the live mark is the declaration; unreadable is BLIND
        raise Blinded("cain mark could not be described: %s: %s" % (type(exc).__name__, exc))

    declared: List[Tuple[str, str]] = []
    for row in described:
        for arch in row.get("architectures") or []:
            declared.append((str(row.get("vendor") or ""), str(arch.get("name") or "")))
    pins = [p for p in ledger.get("pins") or [] if isinstance(p, dict)]

    pinned: Dict[Tuple[str, str], List[str]] = {}
    orphans: List[str] = []
    for pin in pins:
        pid = str(pin.get("id") or "?")
        key = (str(pin.get("vendor") or ""), str(pin.get("architecture") or ""))
        if not key[0] or not key[1] or pin.get("context"):
            continue
        if key in declared:
            pinned.setdefault(key, []).append(pid)
        else:
            orphans.append("%s %s/%s" % (pid, key[0], key[1]))

    missing = [
        "%s / %s" % key
        for key in declared
        if key not in pinned
    ]
    return {
        "wing": "cain",
        "declared": len(declared),
        "pinned": len([key for key in declared if key in pinned]),
        "missing": missing,
        "orphans": orphans,
        "rule": "tools/cain_obsolescence_pins.py: 'is published and unpinned'",
        "_supporters": [("%s / %s" % key, pinned.get(key, [])) for key in declared],
    }


def eliza_inventory() -> Dict[str, Any]:
    instrument = load_module("eliza_arc_pins")
    ledger = read_json(ELIZA_LEDGER)
    try:
        rows = instrument.readme_claim_rows(ELIZA_PROJECT)
    except Exception as exc:  # noqa: BLE001 - no README means no claim surface; BLIND
        if isinstance(exc, instrument.Blinded):
            raise Blinded(str(exc))
        raise Blinded("eliza claim rows could not be read: %s: %s" % (type(exc).__name__, exc))

    pins = [p for p in ledger.get("pins") or [] if isinstance(p, dict)]
    measured_lines = {int(row["line"]) for row in rows}
    cited: Dict[int, List[str]] = {}
    for pin in pins:
        pid = str(pin.get("id") or "?")
        for match in instrument.README_CITATION_RE.finditer(str(pin.get("source") or "")):
            start = int(match.group(1))
            end = int(match.group(2) or start)
            for line in range(start, end + 1):
                cited.setdefault(line, []).append(pid)

    missing = [
        "README.md:%d %s" % (int(row["line"]), row.get("surface"))
        for row in rows
        if int(row["line"]) not in cited
    ]
    orphans = [
        "README.md:%d (%s)" % (line, ", ".join(pin_ids))
        for line, pin_ids in sorted(cited.items())
        if line not in measured_lines
    ]
    return {
        "wing": "eliza",
        "declared": len(rows),
        "pinned": len([row for row in rows if int(row["line"]) in cited]),
        "missing": missing,
        "orphans": orphans,
        "rule": "tools/eliza_arc_pins.py: 'publishes a measurement and no pin answers for it'",
        "_supporters": [
            ("README.md:%d" % int(row["line"]), cited.get(int(row["line"]), [])) for row in rows
        ],
    }


INVENTORIES = (
    ("esquire", esquire_inventory, ESQUIRE_LEDGER, "esquire_pins"),
    ("cain", cain_inventory, CAIN_LEDGER, "cain_obsolescence_pins"),
    ("eliza", eliza_inventory, ELIZA_LEDGER, "eliza_arc_pins"),
)


def audit(only: Optional[str] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, function, _ledger, _checker in INVENTORIES:
        if only and name != only:
            continue
        out.append(function())
    return out


def prove(row: Dict[str, Any], ledger_path: Path, checker: str) -> Dict[str, Any]:
    """Delete every pin that answers for one declared surface; the checker must turn red.

    Deleting a single pin proves nothing when another pin shares its surface: the
    gate is still covered and the checker is right to stay green. The condition
    the binding claims to catch is a surface losing its pins, so the control
    removes all of them for one surface.
    """
    wing = str(row["wing"])
    supporters = [pair for pair in row.get("_supporters") or [] if pair[1]]
    if not supporters:
        raise Blinded("%s declares no pinned surface, so there is no control to run" % wing)
    surface, victim_ids = supporters[0]
    data = read_json(ledger_path)
    pins = data.get("pins")
    if not isinstance(pins, list) or not pins:
        raise Blinded("%s ledger has no pins to delete" % wing)
    targets = {str(pid) for pid in victim_ids}
    remaining = [p for p in pins if not (isinstance(p, dict) and str(p.get("id")) in targets)]
    if len(remaining) == len(pins):
        raise Blinded("%s control could not find %s in the ledger" % (wing, ", ".join(sorted(targets))))
    data["pins"] = remaining

    temp = Path(tempfile.mkdtemp(prefix="arc_pin_audit_"))
    try:
        copy = temp / ("%s.json" % wing)
        copy.write_text(json.dumps(data, indent=2), encoding="utf-8")
        command = [sys.executable, str(TOOLS / ("%s.py" % checker)), "--ledger", str(copy)]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise Blinded("%s control could not run: %s" % (wing, exc))
        output = (proc.stdout or "") + (proc.stderr or "")
        return {
            "wing": wing,
            "surface": surface,
            "deleted": ", ".join(sorted(targets)),
            "exit": proc.returncode,
            "control": "BITES" if proc.returncode != 0 else "SILENT",
            "command": " ".join(command[1:]),
            "detail": output.strip().splitlines()[:4],
        }
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def verdict(rows: List[Dict[str, Any]]) -> str:
    if any(row.get("missing") or row.get("orphans") for row in rows):
        return VERDICT_MISSING
    return VERDICT_CLEAN


def exit_code_for(name: str) -> int:
    if name == VERDICT_CLEAN:
        return EXIT_CLEAN
    if name == VERDICT_BLIND:
        return EXIT_BLIND
    return EXIT_MISSING


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the ARC wings for declared surfaces with no pin.")
    parser.add_argument("--wing", choices=[name for name, _f, _l, _c in INVENTORIES], help="one wing only")
    parser.add_argument("--prove", action="store_true", help="delete one pin per wing and check the checker fires")
    parser.add_argument("--json", metavar="PATH", help="write the audit as JSON")
    args = parser.parse_args(argv)

    try:
        rows = audit(args.wing)
        controls: List[Dict[str, Any]] = []
        if args.prove:
            for row in rows:
                name = str(row["wing"])
                entry = next(
                    (e for e in INVENTORIES if e[0] == name), None
                )
                if entry is None:
                    raise Blinded("no ledger registered for wing %s" % name)
                controls.append(prove(row, entry[2], entry[3]))
    except Blinded as exc:
        print("verdict: %s" % VERDICT_BLIND)
        print("reason: %s" % exc)
        return EXIT_BLIND

    name = verdict(rows)
    print("ARC PIN AUDIT  (declared surfaces vs pins)")
    print("  %-8s %8s %7s %8s  %s" % ("wing", "declared", "pinned", "missing", "rule"))
    for row in rows:
        print(
            "  %-8s %8d %7d %8d  %s"
            % (row["wing"], row["declared"], row["pinned"], len(row["missing"]), row["rule"])
        )
    for row in rows:
        for item in row["missing"]:
            print("  [MISSING] %s: %s" % (row["wing"], item))
        for item in row["orphans"]:
            print("  [ORPHAN ] %s: %s answers for no declared surface" % (row["wing"], item))
    for control in controls:
        print(
            "  [CONTROL] %s: removed %s for %s -> checker exit %d (%s)"
            % (control["wing"], control["deleted"], control["surface"], control["exit"], control["control"])
        )
        if control["control"] == "SILENT":
            print("            the wing's binding does not fire when a surface loses its pins")
    print("verdict: %s" % name)

    if args.json:
        out = Path(args.json)
        public = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
        payload = {"verdict": name, "wings": public, "controls": controls}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return exit_code_for(name)


if __name__ == "__main__":
    raise SystemExit(main())
