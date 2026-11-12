"""Check the ELIZA accountability pin ledger.

Project ELIZA is a therapeutic-agent loop whose own cautionary tale is Weizenbaum's
1966 program: a system that sounds like it is listening will be believed whether or
not anything underneath is doing the work. This checker exists to make that
distinction mechanical, and it does it by refusing four specific claims:

* a number quoted in a claim that the recorded reading never printed;
* a clinical claim the project cannot support (and says it cannot);
* a publication banner the project's own gate contradicts;
* a figure carried from prose while being presented as measured.

It grades the ledger, never the project. It does not repair ELIZA, and it does not
decide whether ELIZA's methods are sound -- only whether each claim is pinned to
something a reader can re-run, and how far that pin may be quoted.

Verdict vocabulary is identical to the esquire and cain wings, because ARC holds one
container and one vocabulary:

    READY       exit 0  every machine gate holds and every figure is pinned at proof tier
    GAPS        exit 1  a machine gate failed, a figure is unsupported, or the ledger is malformed
    INCOMPLETE  exit 1  no defect, but a pin's support was carried rather than read here
    BLIND       exit 2  the ledger, the manifest or the publication gate could not be read

Exit 2 is never a pass. A checker that could not perform its reading has not
performed it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

VERDICT_READY = "READY"
VERDICT_GAPS = "GAPS"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_BLIND = "BLIND"

EXIT_READY = 0
EXIT_BAD = 1
EXIT_BLIND = 2

# Tier A means this ledger's own reading; B is the project's statement about itself;
# C is a secondary source; UNVERIFIABLE-HERE means no readable authority was found.
PROOF_TIERS = ("A",)
ALL_TIERS = ("A", "B", "C", "UNVERIFIABLE-HERE")
CARRIED_TIERS = ("B", "C", "UNVERIFIABLE-HERE")

REQUIRED_PIN_KEYS = (
    "id",
    "surface",
    "asserts",
    "tier",
    "instrument",
    "command",
    "source",
    "measured",
    "does_not_prove",
)
REQUIRED_GAP_KEYS = ("gate", "title", "why", "pins")

MIN_DOES_NOT_PROVE = 20
MIN_CARRIED_WHY = 40
GATE_TIMEOUT_S = 180

# Vocabulary that turns a description of a system into a claim about a person's
# health. A pin carrying one of these must be a limitation pin, because the project
# itself prints that it is not a clinical validation.
CLINICAL_VOCAB = (
    "cure",
    "cures",
    "curative",
    "clinical efficacy",
    "clinically proven",
    "clinically validated",
    "therapeutic benefit",
    "treats depression",
    "treatment outcome",
    "reduces symptoms",
    "evidence-based treatment",
)

# A leading digit then digits and thousands separators, so 1,000 is ONE token and
# compares equal to 1000. Without the comma class the separator is invisible and a
# claim of 1,000 against a reading of 1000 contradicts itself.
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
PUBLICATION_GATE = "tools/eliza_release_gate.py"
MANIFEST_NAME = "PUBLIC_MANIFEST.json"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO_ROOT / "ARC" / "eliza" / "marks.json"
PROJECT = REPO_ROOT / "Project_ELIZA"


class Blinded(Exception):
    """The reading could not be performed. Never a pass."""


def load_ledger(path: Path = DEFAULT_LEDGER) -> Dict[str, Any]:
    if not path.is_file():
        raise Blinded("pin ledger not found: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Blinded("pin ledger unreadable: %s: %s" % (type(exc).__name__, exc))
    if not isinstance(data, dict):
        raise Blinded("pin ledger is %s, not an object" % type(data).__name__)
    if not isinstance(data.get("pins"), list):
        raise Blinded("pin ledger has no pins list")
    if not data["pins"]:
        raise Blinded("pin ledger has an empty pins list")
    return data


def load_manifest(project: Path = PROJECT) -> Dict[str, Any]:
    """Read the disclosure manifest. Missing is BLIND only if a gate needs it."""
    path = project / MANIFEST_NAME
    if not path.is_file():
        raise Blinded("disclosure manifest not found: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Blinded("disclosure manifest unreadable: %s: %s" % (type(exc).__name__, exc))
    if not isinstance(data, dict):
        raise Blinded("disclosure manifest is %s, not an object" % type(data).__name__)
    return data


def number_tokens(text: Any) -> set:
    """Every number in a string, normalised so 1,000 and 1000 compare equal."""
    return {raw.replace(",", "") for raw in NUMBER_RE.findall(str(text or ""))}


def pins_of(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [pin for pin in ledger.get("pins", []) if isinstance(pin, dict)]


# --- gates -------------------------------------------------------------------
#
# Each gate returns a list of error strings. An error makes GAPS. A gate that
# cannot run reports why rather than passing: silence is never a success.


def gate_instrument_exists(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]], project: Path, manifest: Dict[str, Any]
) -> List[str]:
    """Every pin must name a file in the project a reader can open."""
    errors: List[str] = []
    for pin in pins:
        rel = str(pin.get("instrument") or "").strip()
        pin_id = str(pin.get("id") or "?")
        if not rel:
            errors.append("%s names no instrument" % pin_id)
            continue
        if not (project / rel).exists():
            errors.append(
                "%s names instrument %s, which does not exist under Project_ELIZA" % (pin_id, rel)
            )
    return errors


def gate_claim_number_was_measured(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]], project: Path, manifest: Dict[str, Any]
) -> List[str]:
    """A number in a claim that the recorded reading never printed is a contradiction."""
    errors: List[str] = []
    for pin in pins:
        claimed = number_tokens(pin.get("asserts"))
        measured = number_tokens(pin.get("measured"))
        extra = sorted(claimed - measured)
        if extra:
            errors.append(
                "%s asserts %s, which its recorded reading never printed"
                % (pin.get("id"), ", ".join(extra))
            )
    return errors


def gate_no_clinical_claim(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]], project: Path, manifest: Dict[str, Any]
) -> List[str]:
    """The one claim this project prints that it cannot make must not be made by the ledger."""
    errors: List[str] = []
    for pin in pins:
        if pin.get("limitation") is True:
            continue
        haystack = str(pin.get("asserts") or "").lower()
        hits = [word for word in CLINICAL_VOCAB if word in haystack]
        if hits:
            errors.append(
                "%s asserts a clinical claim (%s) without being a limitation pin"
                % (pin.get("id"), ", ".join(hits))
            )
    return errors


def gate_safety_precedence_has_a_test(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]], project: Path, manifest: Dict[str, Any]
) -> List[str]:
    """A safety branch claim must name the test that holds it, and that test must exist."""
    pin = next((p for p in pins if str(p.get("surface")) == "safety precedence"), None)
    if pin is None:
        return ["no pin records the safety branch's precedence"]
    reproducible = str(pin.get("reproducible") or "").strip()
    if "::" not in reproducible:
        return [
            "%s claims a safety branch without naming the test that holds it "
            "(expected path::test_name in 'reproducible')" % pin.get("id")
        ]
    rel, _, test_name = reproducible.partition("::")
    path = project / rel
    if not path.is_file():
        return ["%s names test file %s, which does not exist" % (pin.get("id"), rel)]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # An unreadable test file is a blinded reading, reported as an error rather
        # than swallowed: the safety claim is then unsupported.
        return ["%s could not be read: %s: %s" % (rel, type(exc).__name__, exc)]
    if ("def %s(" % test_name) not in text:
        return ["%s names %s, which is not defined in %s" % (pin.get("id"), test_name, rel)]
    return []


def gate_disclosure_manifest_is_closed(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]], project: Path, manifest: Dict[str, Any]
) -> List[str]:
    """A deny list fails open, so the manifest must also carry an allowlist and a status."""
    errors: List[str] = []
    for key in ("ship", "never_ship", "deny_substrings"):
        value = manifest.get(key)
        if not isinstance(value, list) or not value:
            errors.append(
                "manifest declares no %s, so the disclosure policy fails open" % key
            )
    if not str(manifest.get("status") or "").strip():
        errors.append("manifest declares no status, so its publication state is unstated")
    return errors


def gate_carried_claims_say_why(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]], project: Path, manifest: Dict[str, Any]
) -> List[str]:
    """A figure the project states about itself may be carried, but never silently."""
    errors: List[str] = []
    for pin in pins:
        pin_id = str(pin.get("id") or "?")
        tier = str(pin.get("tier") or "")
        if tier in CARRIED_TIERS:
            why = str(pin.get("carried_because") or "").strip()
            if len(why) < MIN_CARRIED_WHY:
                errors.append(
                    "%s is carried at tier %s without saying why (%d of %d characters)"
                    % (pin_id, tier, len(why), MIN_CARRIED_WHY)
                )
        elif tier == "A":
            command = str(pin.get("command") or "").strip()
            if not command:
                errors.append("%s is a tier-A reading but names no command" % pin_id)
            elif command.lower().startswith("no command"):
                errors.append(
                    "%s claims a tier-A reading but names no command that produces it" % pin_id
                )
    return errors


def gate_publication_gate_passes(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]], project: Path, manifest: Dict[str, Any]
) -> List[str]:
    """Run the project's own publication gate. Its exit status is the reading."""
    script = project / PUBLICATION_GATE
    if not script.is_file():
        return ["publication gate is named but does not exist: %s" % PUBLICATION_GATE]
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=str(project),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GATE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return [
            "publication gate did not finish within %ds, so publication is unproven"
            % GATE_TIMEOUT_S
        ]
    except OSError as exc:
        return ["publication gate could not be started: %s: %s" % (type(exc).__name__, exc)]
    if proc.returncode == 0:
        return []
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    detail = lines[-1] if lines else "no output"
    return [
        "publication gate exits %d: %s" % (proc.returncode, detail),
        "the banner claims a publishable tree; the project's own gate refuses it",
    ]


# --- claim binding -----------------------------------------------------------
#
# The README's feature table is the project's own inventory of surfaces-with-a
# reading: `| layer | module | what it does | measured |`. A pin that cites one
# of those lines (``source: "README.md:26"``) binds a published claim to the
# instrument that produced the reading. Nothing checked that binding, so a fifth
# layer could be published with no pin answering for it, and a pin could cite a
# line that states no measurement at all. Both directions are refused here.
#
# The closed set is "rows of a table whose last header cell declares a
# measurement", which is a property of the project's own markdown rather than a
# guess at which numbers are claims. A table that does not declare one -- the
# per-corpus recall table under Limitations, whose last column is `status` -- is
# outside the rule by construction, not half-covered by a heuristic.

CLAIM_HEADER_WORDS = ("measur", "evidenc", "reading", "result")
README_CITATION_RE = re.compile(r"README\.md:(\d+)(?:\s*[-\u2013]\s*(\d+))?", re.IGNORECASE)


def table_cells(line: str) -> List[str]:
    """Cells of a markdown pipe-table row, or [] for any other line."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    body = stripped[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def readme_claim_rows(project: Path) -> List[Dict[str, Any]]:
    """Rows of every README table whose last header cell declares a measurement."""
    path = project / "README.md"
    if not path.is_file():
        raise Blinded("no README.md under %s, so there is no claim surface to bind" % project)
    rows: List[Dict[str, Any]] = []
    header: Optional[List[str]] = None
    text = path.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        cells = table_cells(line)
        if not cells:
            header = None  # a table ends at the first non-table line
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the |---|---| rule under a header
        if header is None:
            header = cells
            continue
        last = header[-1].lower()
        if not any(word in last for word in CLAIM_HEADER_WORDS):
            continue
        rows.append(
            {
                "line": number,
                "surface": cells[0],
                "text": line.strip(),
            }
        )
    return rows


def gate_claims_are_pinned(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]], project: Path, manifest: Dict[str, Any]
) -> List[str]:
    """Every measured row has a pin, and every README citation names a measured row."""
    errors: List[str] = []
    rows = readme_claim_rows(project)
    measured_lines = {row["line"] for row in rows}

    cited: Dict[int, List[str]] = {}
    for pin in pins:
        pin_id = str(pin.get("id") or "?")
        source = str(pin.get("source") or "")
        for match in README_CITATION_RE.finditer(source):
            start = int(match.group(1))
            end = int(match.group(2) or start)
            for line in range(start, end + 1):
                cited.setdefault(line, []).append(pin_id)

    for row in rows:
        if row["line"] not in cited:
            errors.append(
                "README.md:%d (%s) publishes a measurement and no pin answers for it"
                % (row["line"], row["surface"])
            )
    for line in sorted(cited):
        if line not in measured_lines:
            errors.append(
                "%s cites README.md:%d, which is not a row of a measured table: a pin must "
                "cite a reading" % (", ".join(cited[line]), line)
            )
    return errors


GateFn = Callable[[Dict[str, Any], List[Dict[str, Any]], Path, Dict[str, Any]], List[str]]

GATES: Dict[str, GateFn] = {
    "instrument-exists": gate_instrument_exists,
    "claim-number-was-measured": gate_claim_number_was_measured,
    "no-clinical-claim": gate_no_clinical_claim,
    "safety-precedence-has-a-test": gate_safety_precedence_has_a_test,
    "disclosure-manifest-is-closed": gate_disclosure_manifest_is_closed,
    "carried-claims-say-why": gate_carried_claims_say_why,
    "claims-are-pinned": gate_claims_are_pinned,
    "publication-gate-passes": gate_publication_gate_passes,
}


# --- structural validation ---------------------------------------------------


def check_structure(
    ledger: Dict[str, Any], pins: List[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    """Validate the ledger's own shape. Errors are GAPS; warnings are advisory."""
    errors: List[str] = []
    warnings: List[str] = []

    if not str(ledger.get("read_at") or "").strip():
        errors.append("ledger records no read_at date, so its readings are undated")

    seen: Dict[str, int] = {}
    for pin in pins:
        pin_id = str(pin.get("id") or "").strip()
        if not pin_id:
            errors.append("a pin carries no id")
            continue
        seen[pin_id] = seen.get(pin_id, 0) + 1
    for pin_id, count in sorted(seen.items()):
        if count > 1:
            errors.append("pin id %s appears %d times" % (pin_id, count))

    for pin in pins:
        pin_id = str(pin.get("id") or "?")
        for key in REQUIRED_PIN_KEYS:
            if key not in pin:
                errors.append("%s is missing required key %s" % (pin_id, key))
        tier = str(pin.get("tier") or "")
        if tier and tier not in ALL_TIERS:
            errors.append("%s records tier %r, which is not one of %s" % (pin_id, tier, ", ".join(ALL_TIERS)))
        proof = str(pin.get("does_not_prove") or "").strip()
        if len(proof) < MIN_DOES_NOT_PROVE:
            errors.append(
                "%s does not say what it fails to prove (%d of %d characters)"
                % (pin_id, len(proof), MIN_DOES_NOT_PROVE)
            )
        if not str(pin.get("source") or "").strip():
            errors.append("%s names no source" % pin_id)
        if tier in PROOF_TIERS and not str(pin.get("measured") or "").strip():
            errors.append("%s is a tier-A reading with no recorded output" % pin_id)

    gaps = ledger.get("declared_gaps")
    if gaps is None:
        gaps = []
    if not isinstance(gaps, list):
        errors.append("declared_gaps is %s, not a list" % type(gaps).__name__)
        gaps = []
    known = {str(pin.get("id")) for pin in pins}
    for index, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            errors.append("declared_gaps[%d] is not an object" % index)
            continue
        for key in REQUIRED_GAP_KEYS:
            if key not in gap:
                errors.append("declared_gaps[%d] is missing %s" % (index, key))
        if len(str(gap.get("why") or "").strip()) < MIN_DOES_NOT_PROVE:
            errors.append("declared gap %s does not say why it exists" % gap.get("gate"))
        for pin_id in gap.get("pins") or []:
            if str(pin_id) not in known:
                errors.append(
                    "declared gap %s names pin %s, which the ledger does not contain"
                    % (gap.get("gate"), pin_id)
                )

    declared = [str(name) for name in (ledger.get("gates") or [])]
    for name in declared:
        if name not in GATES:
            errors.append(
                "ledger declares gate %s, which has no implementation (a gate that grades nothing)" % name
            )
    for name in GATES:
        if name not in declared:
            errors.append(
                "gate %s is implemented but not declared in the ledger (a silent gate)" % name
            )
    if len(set(declared)) != len(declared):
        warnings.append("the ledger declares a gate more than once")

    return errors, warnings


def carried_pins(pins: List[Dict[str, Any]]) -> List[str]:
    """Pins whose support was carried rather than read here."""
    return [
        str(pin.get("id"))
        for pin in pins
        if str(pin.get("tier") or "") in CARRIED_TIERS
    ]


def check(ledger: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    """Run every gate. Return (errors, warnings, carried pin ids)."""
    pins = pins_of(ledger)
    errors, warnings = check_structure(ledger, pins)
    manifest = load_manifest()
    for name in [str(n) for n in (ledger.get("gates") or [])]:
        gate = GATES.get(name)
        if gate is None:
            continue
        try:
            errors.extend(gate(ledger, pins, PROJECT, manifest))
        except Blinded as exc:
            errors.extend(["gate %s was blinded: %s" % (name, exc)])
    return errors, warnings, carried_pins(pins)


def verdict(errors: List[str], carried: List[str]) -> str:
    if errors:
        return VERDICT_GAPS
    if carried:
        return VERDICT_INCOMPLETE
    return VERDICT_READY


def exit_code_for(name: str) -> int:
    if name == VERDICT_READY:
        return EXIT_READY
    if name == VERDICT_BLIND:
        return EXIT_BLIND
    return EXIT_BAD


def pin_table(pins: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    return [
        {
            "id": str(pin.get("id") or ""),
            "tier": str(pin.get("tier") or ""),
            "surface": str(pin.get("surface") or ""),
            "instrument": str(pin.get("instrument") or ""),
            "status": "proof" if str(pin.get("tier") or "") in PROOF_TIERS else "carried",
        }
        for pin in pins
    ]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check the ELIZA accountability pin ledger.")
    parser.add_argument("--check", action="store_true", help="validate and exit (default)")
    parser.add_argument("--gates", action="store_true", help="print the pin table")
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON")
    parser.add_argument("--ledger", metavar="PATH", default=str(DEFAULT_LEDGER))
    args = parser.parse_args(argv)

    # Every failure leaves a printed verdict and a distinct exit code. A traceback
    # would exit 1, which is indistinguishable from GAPS, and a silent success is
    # the defect this tool exists to catch.
    try:
        ledger = load_ledger(Path(args.ledger))
        errors, warnings, carried = check(ledger)
        pins = pins_of(ledger)
        name = verdict(errors, carried)
        rows = pin_table(pins)
    except Blinded as exc:
        print("verdict: %s" % VERDICT_BLIND)
        print("reason: %s" % exc)
        return EXIT_BLIND
    except Exception as exc:  # noqa: BLE001 - an unhandled defect is BLIND, and is named
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
                        "implemented_gates": sorted(GATES),
                        "declared_gates": [str(g) for g in (ledger.get("gates") or [])],
                        "table": rows,
                        "errors": errors,
                        "warnings": warnings,
                        "carried": carried,
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
        % (
            len(pins),
            len(GATES),
            len(ledger.get("declared_gaps") or []),
        )
    )
    if errors:
        print("errors: %d" % len(errors))
        for err in errors:
            print("  [GAP] %s" % err)
    if carried:
        print(
            "carried (stated by the project, not re-measured here): %s" % ", ".join(carried)
        )
    for warn in warnings[:5]:
        print("  [note] %s" % warn)
    if len(warnings) > 5:
        print("  [note] ... and %d more (advisory only)" % (len(warnings) - 5))

    if args.gates:
        print("")
        print("%-4s %-8s %-24s %-14s %s" % ("id", "tier", "surface", "status", "instrument"))
        for row in rows:
            print(
                "%-4s %-8s %-24s %-14s %s"
                % (row["id"], row["tier"], row["surface"][:24], row["status"], row["instrument"])
            )

    return exit_code_for(name)


if __name__ == "__main__":
    sys.exit(main())
