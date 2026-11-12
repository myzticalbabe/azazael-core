"""Pin checker for the Esquire admissibility pin ledger.

The ledger (ARC/esquire/pins.json) is the evidence side of the admissibility
instrument: for each demand a court makes, it records the operative text that
makes the demand, the tier at which that text was read, and the artifact that
answers it.  This tool checks the ledger against the instrument's own
GATE_METADATA, so a gate cannot lose its pins and a pin cannot invent a gate.

It deliberately does not hold a second copy of the gate table: HED-8 says extend
the default, do not shadow it.  The table is loaded from
tools/esquire_admissibility.py by path.

Verdicts and exit codes follow the instrument:

    0  READY       every gate has a proof-tier pin, no ledger defect
    1  INCOMPLETE  no defect, but at least one gate's only support is a carried
                   citation that was not re-read (addressed, not current)
    1  GAPS        a gate has no proof-tier pin, a pin invents a gate, or the
                   ledger is malformed
    2  BLIND       the ledger or the instrument cannot be read; not a pass

Usage:
    python tools/esquire_pins.py            # --check
    python tools/esquire_pins.py --gates    # gate -> pins table
    python tools/esquire_pins.py --json logs/esquire_pins.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERDICT_READY = "READY"
VERDICT_GAPS = "GAPS"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_BLIND = "BLIND"

EXIT_READY = 0
EXIT_BAD = 1
EXIT_BLIND = 2

PROOF_TIERS = ("A", "B")
ALL_TIERS = ("A", "B", "C", "UNVERIFIABLE-HERE")
STALE_MARKERS = ("not re-fetched", "not re-read", "not fetched", "not read at source")
NOT_READ_PREFIXES = ("not quoted", "not read", "not fetched", "not re-read", "none in this session")

REQUIRED_PIN_KEYS = (
    "id",
    "demand",
    "authority",
    "cite",
    "text",
    "tier",
    "verified",
    "source",
    "satisfied_by",
    "covers",
    "does_not_prove",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO_ROOT / "ARC" / "esquire" / "pins.json"
INSTRUMENT = REPO_ROOT / "tools" / "esquire_admissibility.py"


class Blinded(Exception):
    """The reading could not be performed. Never a pass."""


def load_instrument_gates(path: Path = INSTRUMENT) -> List[Dict[str, str]]:
    """Load GATE_METADATA from the instrument by path, without importing a package."""
    if not path.is_file():
        raise Blinded("instrument not found: %s" % path)
    spec = importlib.util.spec_from_file_location("_esquire_admissibility_pins", path)
    if spec is None or spec.loader is None:
        raise Blinded("instrument could not be loaded: %s" % path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a load failure is BLIND, and is named
        raise Blinded("instrument raised on load: %s: %s" % (type(exc).__name__, exc))
    gates = getattr(module, "GATE_METADATA", None)
    if not gates:
        raise Blinded("instrument exposes no GATE_METADATA")
    if not isinstance(gates, (list, tuple)):
        raise Blinded("GATE_METADATA is %s, not a list" % type(gates).__name__)
    rows: List[Dict[str, Any]] = []
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            raise Blinded("GATE_METADATA[%d] is not an object" % index)
        if not str(gate.get("id", "")).strip():
            raise Blinded("GATE_METADATA[%d] names no gate id" % index)
        rows.append(dict(gate))
    return rows


def load_ledger(path: Path = DEFAULT_LEDGER) -> Dict[str, Any]:
    if not path.is_file():
        raise Blinded("pin ledger not found: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Blinded("pin ledger unreadable: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("pins"), list):
        raise Blinded("pin ledger has no pins list")
    return data


def is_stale(pin: Dict[str, Any]) -> bool:
    """A pin whose provenance says it was carried rather than read here."""
    haystack = ("%s %s" % (pin.get("verified", ""), pin.get("text", ""))).lower()
    return any(marker in haystack for marker in STALE_MARKERS)


def check(ledger: Dict[str, Any], gates: List[Dict[str, str]]) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings). Errors make GAPS; warnings do not change the verdict."""
    errors: List[str] = []
    warnings: List[str] = []

    gate_ids: List[str] = []
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict) or not str(gate.get("id", "")).strip():
            errors.append("gates[%d] is not a named gate" % index)
            continue
        gate_ids.append(str(gate["id"]))

    declared = ledger.get("declared_gaps") or []
    if not isinstance(declared, list):
        errors.append("declared_gaps is %s, not a list" % type(declared).__name__)
        declared = []
    gap_ids = [g.get("gate") for g in declared if isinstance(g, dict)]
    known_gate_ids = set(gate_ids) | {g for g in gap_ids if g}

    pins = ledger.get("pins") or []
    seen: Dict[str, int] = {}
    for index, pin in enumerate(pins):
        if not isinstance(pin, dict):
            errors.append("pins[%d] is not an object" % index)
            continue
        pid = str(pin.get("id", "pins[%d]" % index))

        missing = [k for k in REQUIRED_PIN_KEYS if k not in pin]
        if missing:
            errors.append("%s missing required keys: %s" % (pid, ", ".join(missing)))
            continue

        if pid in seen:
            errors.append("%s is a duplicate id (first at pins[%d])" % (pid, seen[pid]))
        seen[pid] = index

        tier = pin["tier"]
        if tier not in ALL_TIERS:
            errors.append("%s has unknown tier %r" % (pid, tier))

        text = pin["text"] if isinstance(pin["text"], str) else ""
        authority = pin["authority"] if isinstance(pin["authority"], str) else ""
        if tier == "A":
            # Tier A means the operative text was read at source. Requiring an
            # embedded quotation mark was a bad proxy: 901(a) and 1002 contain
            # none, and their whole text IS the quote. What is testable is that
            # operative language is present (not a sentinel saying it was never
            # read) and that its source is named.
            if text.strip().lower().startswith(NOT_READ_PREFIXES):
                errors.append(
                    "%s is tier A but its text says it was not read; that is tier B or C" % pid
                )
            elif len(text.strip()) < 40:
                errors.append("%s is tier A but carries under 40 characters of text" % pid)
            if not authority.strip():
                errors.append("%s is tier A but names no authority" % pid)
            if not str(pin.get("source", "")).strip():
                errors.append("%s is tier A but names no source" % pid)
        if tier in PROOF_TIERS:
            for key in ("verified", "source"):
                if not str(pin.get(key, "")).strip():
                    errors.append("%s is tier %s but has an empty %s" % (pid, tier, key))

        covers = pin["covers"]
        if not isinstance(covers, list):
            errors.append("%s has non-list covers" % pid)
            covers = []
        for gate_id in covers:
            if gate_id not in known_gate_ids:
                errors.append("%s covers unknown gate %r" % (pid, gate_id))

        pending = pin.get("pending_gate")
        if pending:
            if covers:
                errors.append("%s declares pending_gate %s and also claims covers" % (pid, pending))
            if pending not in gap_ids:
                errors.append("%s names pending_gate %s, which is not a declared gap" % (pid, pending))

        if not covers and not pending and not pin.get("context"):
            errors.append("%s answers no gate and is not marked context" % pid)

    # every implemented gate must be covered, and covered by a proof-tier pin
    for gate in gates:
        if not isinstance(gate, dict) or not str(gate.get("id", "")).strip():
            continue  # already reported above; there is nothing to cover
        gid = str(gate["id"])
        supporters = [p for p in pins if isinstance(p, dict) and gid in (p.get("covers") or [])]
        if not supporters:
            errors.append("%s (%s) has no pin" % (gid, gate.get("title", "")))
            continue
        proof = [p for p in supporters if p.get("tier") in PROOF_TIERS]
        if not proof:
            errors.append(
                "%s is supported only by tiers %s"
                % (gid, ", ".join(sorted({str(p.get("tier")) for p in supporters})))
            )

    # declared gaps must stay pinned, at a tier that can carry them
    for gap in declared:
        if not isinstance(gap, dict):
            errors.append("declared_gaps contains a non-object")
            continue
        gid = gap.get("gate", "<unnamed>")
        pin_ids = gap.get("pins") or []
        if not pin_ids:
            errors.append("declared gap %s names no pins" % gid)
        for pid in pin_ids:
            pin = next((p for p in pins if isinstance(p, dict) and p.get("id") == pid), None)
            if pin is None:
                errors.append("declared gap %s names missing pin %s" % (gid, pid))
            elif pin.get("tier") not in ("A", "B", "C"):
                errors.append(
                    "declared gap %s names %s at tier %s, below C"
                    % (gid, pid, pin.get("tier"))
                )

    # advisory (never fatal): a pin whose citation tokens appear in no gate's
    # authority string. Compared by token, because gate strings interleave
    # citations ("FRE 901(a), 901(b)(9); 902(13), 902(14)") and a literal
    # substring test would call every one of them drift.
    gate_authority = " ".join(str(g.get("authority", "")) for g in gates)
    gate_tokens = set(re.findall(r"\d{3,4}", gate_authority)) | set(
        re.findall(r"E-\d+", gate_authority)
    )
    for pin in pins:
        if not isinstance(pin, dict) or pin.get("context") or pin.get("pending_gate"):
            continue
        authority = str(pin.get("authority", ""))
        tokens = set(re.findall(r"\d{3,4}", authority)) | set(re.findall(r"E-\d+", authority))
        if tokens and not (tokens & gate_tokens):
            warnings.append(
                "%s cites %s, named in no gate authority (pin is extra, not drift)"
                % (pin.get("id"), ", ".join(sorted(tokens)))
            )

    return errors, warnings


def staleness(pins: List[Dict[str, Any]], gates: List[Dict[str, str]]) -> List[str]:
    """Gates whose only proof-tier support is a carried citation."""
    stale_gates: List[str] = []
    for gate in gates:
        if not isinstance(gate, dict) or not str(gate.get("id", "")).strip():
            continue
        gid = str(gate["id"])
        supporters = [
            p for p in pins
            if isinstance(p, dict)
            and gid in (p.get("covers") or [])
            and p.get("tier") in PROOF_TIERS
        ]
        if supporters and all(is_stale(p) for p in supporters):
            stale_gates.append(gid)
    return stale_gates


def verdict(errors: List[str], stale_gates: List[str]) -> str:
    if errors:
        return VERDICT_GAPS
    if stale_gates:
        return VERDICT_INCOMPLETE
    return VERDICT_READY


def exit_code_for(name: str) -> int:
    if name == VERDICT_READY:
        return EXIT_READY
    if name == VERDICT_BLIND:
        return EXIT_BLIND
    return EXIT_BAD


def gate_table(ledger: Dict[str, Any], gates: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    pins = [p for p in ledger.get("pins", []) if isinstance(p, dict)]
    rows: List[Dict[str, Any]] = []
    for gate in gates:
        if not isinstance(gate, dict) or not str(gate.get("id", "")).strip():
            continue
        gate_id = str(gate["id"])
        supporters = [p for p in pins if gate_id in (p.get("covers") or [])]
        rows.append(
            {
                "gate": gate_id,
                "title": gate.get("title", ""),
                "pins": [p["id"] for p in supporters],
                "proof_pins": [p["id"] for p in supporters if p.get("tier") in PROOF_TIERS],
                "stale": all(is_stale(p) for p in supporters) if supporters else True,
            }
        )
    for gap in ledger.get("declared_gaps") or []:
        if isinstance(gap, dict):
            rows.append(
                {
                    "gate": gap.get("gate", ""),
                    "title": gap.get("title", "") + " [declared, not implemented]",
                    "pins": list(gap.get("pins") or []),
                    "proof_pins": list(gap.get("pins") or []),
                    "stale": False,
                }
            )
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check the Esquire admissibility pin ledger.")
    parser.add_argument("--check", action="store_true", help="validate and exit (default)")
    parser.add_argument("--gates", action="store_true", help="print gate -> pin mapping")
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON")
    parser.add_argument("--ledger", metavar="PATH", default=str(DEFAULT_LEDGER))
    args = parser.parse_args(argv)

    # Every failure below must leave the process with a printed verdict and a
    # non-zero exit. A traceback exits 1, which is indistinguishable from GAPS, and
    # so is a silent success.
    try:
        gates = load_instrument_gates()
        ledger = load_ledger(Path(args.ledger))
        errors, warnings = check(ledger, gates)
        pins = [p for p in ledger.get("pins", []) if isinstance(p, dict)]
        stale_gates = staleness(pins, gates)
        name = verdict(errors, stale_gates)
        rows = gate_table(ledger, gates)
    except Blinded as exc:
        print("verdict: %s" % VERDICT_BLIND)
        print("reason: %s" % exc)
        return EXIT_BLIND
    except Exception as exc:  # noqa: BLE001 - an unhandled defect is BLIND, named
        print("verdict: %s" % VERDICT_BLIND)
        print("reason: checker failed: %s: %s" % (type(exc).__name__, exc))
        return EXIT_BLIND

    if args.json:
        out = Path(args.json)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        "verdict": name,
                        "pins": len(pins),
                        "gates": rows,
                        "errors": errors,
                        "warnings": warnings,
                        "stale_gates": stale_gates,
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

    print("verdict: %s" % name)
    print(
        "ledger: %d pins, %d implemented gates, %d declared gaps"
        % (len(pins), len(gates), len(ledger.get("declared_gaps") or []))
    )
    if errors:
        print("errors: %d" % len(errors))
        for err in errors:
            print("  [GAP] %s" % err)
    if stale_gates:
        print(
            "carried (addressed, not re-read here): %s"
            % ", ".join(stale_gates)
        )
    if warnings:
        for warn in warnings[:5]:
            print("  [note] %s" % warn)
        if len(warnings) > 5:
            print("  [note] ... and %d more (advisory only)" % (len(warnings) - 5))

    if args.gates:
        print("")
        print("%-5s %-8s %-34s %s" % ("gate", "proof", "pins", "title"))
        for row in rows:
            proof = "stale" if row["stale"] else ("tier A/B" if row["proof_pins"] else "none")
            print("%-5s %-8s %-34s %s" % (row["gate"], proof, ",".join(row["pins"]), row["title"]))

    return exit_code_for(name)


if __name__ == "__main__":
    sys.exit(main())
