"""Pin checker for the CAIN obsolescence accountability mark.

ARC's entry rule (ARC/README.md) asks three things of an inhabitant: a mark, an
evidence state, and a checker.  The mark is
``src/ARC/cain_obsolescence_accountability/cain_obsolescence_mark.py``; the
evidence state is ``ARC/cain/marks.json``; this file is the third.

What it checks, and why in this order
-------------------------------------

The mark is a *claim about vendors*: seven architectures, each asserting a
mechanism and three figures (devices affected, years of life stolen, dollars per
device).  A court does not accept a total because a document contains one, so the
checker separates two questions that a single "score" would blur:

1. **Machine gates** (M1-M6) are arithmetic and integrity, answered by running the
   mark: does a mark exist for every pillar, does it serialize, do its aggregate
   totals equal the sum of the parts it publishes, is the seal a digest, is the
   witness dated before the act it witnesses, is the compliance score the
   arithmetic its own formula defines.  These need no ledger and cannot be argued
   with: a total that does not add up is a defect in the artifact.
2. **Evidence pins** (one per architecture) are the ledger's answer for each
   figure: the tier at which the authority was read, the date, and what the figure
   does *not* prove.  A count presented as fact requires a proof-tier read; an
   estimate requires a named method; a citation that does not resolve is recorded
   as a negative reading, never as support.

Drift runs both ways and both are fatal:

* a figure whose value changed in the mark while the ledger kept the old number
  (the ledger is answering for a mark that no longer exists);
* a ledger pin naming an architecture the mark no longer publishes, or a figure
  field the mark does not carry (the ledger invented a claim).

The seal deserves a note.  ``SHA256:CAIN-OBSOLESCENCE-MARK-v1.0-KILO-AUTHORIZED``
is a *label*, not a digest: it is the same string on every mark, it is not the hash
of anything, and a reader cannot recompute it.  This checker reports that as a
failed gate rather than repairing it, because the repair belongs to the mark's
owner and a checker that silently rewrites its subject is measuring itself.

Verdicts and exit codes are the instrument's, unchanged:

    0  READY       every machine gate passes and every figure is pinned at proof tier
    1  GAPS        a machine gate failed, a figure is unpinned or unsupported, or
                   the ledger is malformed
    1  INCOMPLETE  no defect, but a pin's support was carried rather than read here
    2  BLIND       the mark or the ledger cannot be read; not a pass

Usage:
    python tools/cain_obsolescence_pins.py            # --check
    python tools/cain_obsolescence_pins.py --gates    # architecture -> evidence table
    python tools/cain_obsolescence_pins.py --json logs/cain_pins.json
"""

from __future__ import annotations

import argparse
import datetime
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
PRESENTATIONS = ("fact", "estimate")

# The three figures every architecture publishes.  Named explicitly so a ledger
# cannot quietly stop answering for one of them.
FIGURE_FIELDS = ("affected_devices_count", "years_of_life_stolen", "financial_damage_usd")

STALE_MARKERS = ("not re-fetched", "not re-read", "not fetched", "not read at source")

# Short labels for the --gates table; the ledger keeps the field names verbatim.
FIGURE_LABELS = {
    "affected_devices_count": "devices",
    "years_of_life_stolen": "years",
    "financial_damage_usd": "usd",
}
NOT_READ_PREFIXES = ("not quoted", "not read", "not fetched", "not re-read", "none in this session")

# A reading that establishes nothing.  Recorded as evidence of a dead citation,
# which is different from evidence of the claim.
NEGATIVE_READING = re.compile(
    r"(^|\s)(http\s*)?(404|403|410|500|502|503|nxdomain|unresolvable|did not resolve|timeout)\b",
    re.IGNORECASE,
)
POSITIVE_READING = re.compile(r"\b(200|201)\b")
DATE_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")
SEAL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
ISO_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")

REQUIRED_LEDGER_KEYS = ("instrument", "read_at", "pins")
REQUIRED_PIN_KEYS = (
    "id",
    "architecture",
    "vendor",
    "mark",
    "mechanism",
    "figures",
    "does_not_prove",
)
REQUIRED_MECHANISM_KEYS = ("claim", "tier", "source", "verified", "does_not_prove")
REQUIRED_FIGURE_KEYS = ("value", "presented_as", "tier", "source", "verified", "does_not_prove")
REQUIRED_CONTEXT_KEYS = ("id", "tier", "authority", "source", "verified", "does_not_prove")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO_ROOT / "ARC" / "cain" / "marks.json"
MARK_MODULE = (
    REPO_ROOT / "src" / "ARC" / "cain_obsolescence_accountability" / "cain_obsolescence_mark.py"
)

MACHINE_GATES = (
    ("M1", "mark-computes", "every pillar yields a mark"),
    ("M2", "mark-serializes", "a mark's JSON is an object tree, not repr strings"),
    ("M3", "aggregate-arithmetic", "vendor totals equal the sum of the parts published"),
    ("M4", "seal-is-a-digest", "the seal is a recomputable digest"),
    ("M5", "witness-before-mark", "the witness is not dated after the mark it witnesses"),
    ("M6", "compliance-is-arithmetic", "the compliance score matches its own formula"),
)


class Blinded(Exception):
    """The reading could not be performed. Never a pass."""


def load_mark_module(path: Path = MARK_MODULE) -> Any:
    """Load the mark module by path, without importing the package."""
    if not path.is_file():
        raise Blinded("mark module not found: %s" % path)
    spec = importlib.util.spec_from_file_location("_cain_obsolescence_mark_pins", path)
    if spec is None or spec.loader is None:
        raise Blinded("mark module could not be loaded: %s" % path)
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE executing.  ``dataclasses`` resolves a field's string
    # annotation through ``sys.modules[cls.__module__].__dict__``; an unregistered
    # module makes that lookup ``None`` and the whole mark module dies with
    # ``AttributeError: 'NoneType' object has no attribute '__dict__'`` -- a
    # loader bug reported as if the artifact were blind.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a load failure is BLIND, and is named
        sys.modules.pop(spec.name, None)
        raise Blinded("mark module raised on load: %s: %s" % (type(exc).__name__, exc))
    if not hasattr(module, "compute_all_marks"):
        raise Blinded("mark module exposes no compute_all_marks")
    return module


def load_marks(module: Any) -> List[Any]:
    """Compute the marks. A mark that cannot be computed is BLIND, not empty."""
    try:
        marks = module.compute_all_marks()
    except Exception as exc:  # noqa: BLE001 - named, and fatal to the reading
        raise Blinded("compute_all_marks raised: %s: %s" % (type(exc).__name__, exc))
    if not isinstance(marks, (list, tuple)) or not marks:
        raise Blinded("compute_all_marks returned no marks")
    return list(marks)


def describe(mark: Any) -> Dict[str, Any]:
    """Normalize one mark into the flat shape the ledger is checked against."""
    acc = getattr(mark, "vendor_accountability", None)
    if acc is None:
        raise Blinded("mark has no vendor_accountability")
    architectures = []
    for arch in getattr(acc, "architectures", []) or []:
        row = {"name": str(getattr(arch, "name", ""))}
        for field_name in FIGURE_FIELDS:
            row[field_name] = getattr(arch, field_name, None)
        row["evidence_url"] = str(getattr(arch, "evidence_url", "") or "")
        row["vendor"] = str(getattr(arch, "vendor", "") or "")
        architectures.append(row)
    return {
        "mark_id": str(getattr(mark, "mark_id", "")),
        "mark_date": str(getattr(mark, "mark_date", "")),
        "system_name": str(getattr(mark, "system_name", "")),
        "vendor": str(getattr(mark, "vendor", "")),
        "seal": str(getattr(mark, "seal", "")),
        "witness_signature": str(getattr(mark, "witness_signature", "")),
        "covenant_compliance_score": getattr(mark, "covenant_compliance_score", None),
        "architectures": architectures,
        "totals": {
            "devices": getattr(acc, "total_devices_affected", None),
            "years": getattr(acc, "total_years_stolen", None),
            "damage": getattr(acc, "total_financial_damage_usd", None),
        },
        "violation_count": sum(
            int(v) for v in (getattr(acc, "covenant_violations", {}) or {}).values()
        ),
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num_equal(left: Any, right: Any) -> bool:
    """Figures are money, counts and device-years: equal to the cent, not to a unit.

    A whole-unit tolerance is not a rounding allowance here -- it made a 0.19 drift
    in a compliance score read as agreement, which is how a checker stops checking.
    """
    if not (_is_number(left) and _is_number(right)):
        return False
    return abs(float(left) - float(right)) <= 0.005


def _score_equal(left: Any, right: Any) -> bool:
    """Compliance scores live in 0..1, where a hundredth is a visible difference."""
    if not (_is_number(left) and _is_number(right)):
        return False
    return abs(float(left) - float(right)) <= 1e-6


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return "%.2f" % value
    if isinstance(value, int):
        return "{:,}".format(value)
    return str(value)


def _parse_stamp(text: str) -> Tuple[Optional[datetime.datetime], str]:
    """Return (stamp, reason). The reason names why no stamp was read."""
    match = ISO_STAMP.search(text or "")
    if not match:
        return None, "no ISO timestamp in %r" % (text or "")[:40]
    raw = match.group(1).replace(" ", "T")
    try:
        return datetime.datetime.fromisoformat(raw), ""
    except ValueError as exc:
        reason = "%r is not a calendar value: %s" % (raw, exc)
        return None, reason


def _is_negative_reading(text: str) -> bool:
    return bool(NEGATIVE_READING.search(text or ""))


def _is_stale(text: str) -> bool:
    haystack = (text or "").lower()
    return haystack.strip().startswith(NOT_READ_PREFIXES) or any(
        marker in haystack for marker in STALE_MARKERS
    )


def machine_gates(module: Any, marks: List[Any], described: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run the gates that need no ledger. A gate that raises is a failed gate."""
    results: List[Dict[str, Any]] = []

    # M1 — a mark exists for every pillar, carrying identity and an architecture.
    ok = True
    detail = "%d marks computed" % len(described)
    for row in described:
        if not row["mark_id"] or not row["vendor"]:
            ok, detail = False, "a mark carries no id or vendor"
            break
        if not row["architectures"]:
            ok, detail = False, "%s publishes no architecture" % row["mark_id"]
            break
    results.append({"gate": "M1", "title": "mark-computes", "ok": ok, "detail": detail})

    # M2 — the product path's own JSON. A repr string is not a record.
    ok, detail = True, "every mark serializes as an object tree"
    engine_class = getattr(module, "CainObsolescenceAccountability", None)
    if engine_class is None:
        ok, detail = False, "module exposes no CainObsolescenceAccountability to serialize through"
    else:
        try:
            engine = engine_class()
            for mark in marks:
                data = json.loads(engine.format_mark_report(mark, "json"))
                acc = data.get("vendor_accountability")
                if not isinstance(acc, dict):
                    ok = False
                    detail = "vendor_accountability serialized as %s, not an object" % type(
                        acc
                    ).__name__
                    break
                if not isinstance(acc.get("architectures"), list):
                    ok = False
                    detail = "architectures serialized as %s, not a list" % type(
                        acc.get("architectures")
                    ).__name__
                    break
        except Exception as exc:  # noqa: BLE001 - a serialization defect is the finding
            ok = False
            detail = "serialization raised: %s: %s" % (type(exc).__name__, exc)
    results.append({"gate": "M2", "title": "mark-serializes", "ok": ok, "detail": detail})

    # M3 — totals must be the arithmetic of the parts, or the parts do not add up.
    ok, detail = True, "vendor totals equal the sum of their architectures"
    for row in described:
        devices = sum(
            a["affected_devices_count"] for a in row["architectures"]
            if _is_number(a["affected_devices_count"])
        )
        damage = sum(
            a["financial_damage_usd"] * a["affected_devices_count"]
            for a in row["architectures"]
            if _is_number(a["financial_damage_usd"]) and _is_number(a["affected_devices_count"])
        )
        years = sum(
            a["years_of_life_stolen"] * a["affected_devices_count"]
            for a in row["architectures"]
            if _is_number(a["years_of_life_stolen"]) and _is_number(a["affected_devices_count"])
        )
        if not _num_equal(row["totals"]["devices"], devices):
            ok = False
            detail = "%s publishes %s devices; its architectures sum to %s" % (
                row["mark_id"],
                _fmt(row["totals"]["devices"]),
                _fmt(int(devices)),
            )
            break
        if not _num_equal(row["totals"]["damage"], damage):
            ok = False
            detail = "%s publishes %s in damage; count x damage sums to %s" % (
                row["mark_id"],
                _fmt(row["totals"]["damage"]),
                _fmt(damage),
            )
            break
        if not _num_equal(row["totals"]["years"], years):
            ok = False
            detail = "%s publishes %s device-years; count x years sums to %s" % (
                row["mark_id"],
                _fmt(row["totals"]["years"]),
                _fmt(years),
            )
            break
    results.append({"gate": "M3", "title": "aggregate-arithmetic", "ok": ok, "detail": detail})

    # M4 — a seal a reader cannot recompute seals nothing: shape, uniqueness, and
    # then the digest recomputed from the module that built the mark. Shape alone
    # is not evidence: a per-mark counter dressed in hex passes it.
    ok, detail = True, "every seal is a sha256 digest over the mark it seals"
    seen: dict[str, str] = {}
    for row in described:
        seal = str(row["seal"]).strip()
        if not SEAL_DIGEST.match(seal):
            ok = False
            detail = (
                "%s seals with %r: a label, not a digest over any bytes; a reader cannot "
                "recompute it and it is identical on every mark"
                % (row["mark_id"], seal)
            )
            break
        if seal in seen:
            ok = False
            detail = (
                "%s and %s carry the same seal %s: a digest over different bytes cannot repeat"
                % (seen[seal], row["mark_id"], seal)
            )
            break
        seen[seal] = row["mark_id"]

    compute_seal = getattr(module, "compute_seal", None)
    if ok and compute_seal is None:
        ok = False
        detail = (
            "the mark module exposes no compute_seal: the seal's input cannot be recomputed, "
            "so a well-formed digest string is only a label wearing a digest's shape"
        )
    if ok and marks:
        for mark, row in zip(marks, described):
            try:
                recomputed = str(compute_seal(mark))
            except Exception as exc:
                ok = False
                detail = "%s: compute_seal raised %s: %s" % (
                    row["mark_id"],
                    type(exc).__name__,
                    exc,
                )
                break
            if recomputed != str(row["seal"]).strip():
                ok = False
                detail = (
                    "%s seals %s but its own bytes hash to %s: the seal is not a digest of "
                    "this mark" % (row["mark_id"], str(row["seal"]).strip(), recomputed)
                )
                break
    results.append({"gate": "M4", "title": "seal-is-a-digest", "ok": ok, "detail": detail})

    # M5 — a witness dated after the act is a forecast, not a witness.
    ok, detail = True, "every witness stamp is at or before its mark"
    for row in described:
        witness, why = _parse_stamp(row["witness_signature"])
        marked, _ = _parse_stamp(row["mark_date"])
        if witness is None:
            ok, detail = False, "%s carries no parseable witness timestamp: %s" % (
                row["mark_id"],
                why,
            )
            break
        if marked is not None and witness > marked:
            ok = False
            detail = "%s is witnessed at %s, after the mark it witnesses (%s)" % (
                row["mark_id"],
                witness.isoformat(),
                marked.isoformat(),
            )
            break
    results.append({"gate": "M5", "title": "witness-before-mark", "ok": ok, "detail": detail})

    # M6 — the score is defined as 1 - violations/5. Anything else is a hand value.
    ok, detail = True, "every compliance score equals 1 - violations/5"
    for row in described:
        score = row["covenant_compliance_score"]
        if not _is_number(score):
            ok, detail = False, "%s carries no numeric compliance score" % row["mark_id"]
            break
        if not 0.0 <= float(score) <= 1.0:
            ok, detail = False, "%s scores %.4f, outside 0..1" % (row["mark_id"], float(score))
            break
        expected = max(0.0, 1.0 - (row["violation_count"] / 5.0))
        if not _score_equal(score, expected):
            ok = False
            detail = "%s scores %.4f; its own formula gives %.4f for %d violation(s)" % (
                row["mark_id"],
                float(score),
                expected,
                row["violation_count"],
            )
            break
    results.append({"gate": "M6", "title": "compliance-is-arithmetic", "ok": ok, "detail": detail})

    return results


def load_ledger(path: Path = DEFAULT_LEDGER) -> Dict[str, Any]:
    if not path.is_file():
        raise Blinded("evidence ledger not found: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Blinded("evidence ledger unreadable: %s" % exc)
    if not isinstance(data, dict):
        raise Blinded("evidence ledger is %s, not an object" % type(data).__name__)
    for key in REQUIRED_LEDGER_KEYS:
        if key not in data:
            raise Blinded("evidence ledger has no %r" % key)
    if not isinstance(data.get("pins"), list):
        raise Blinded("evidence ledger has no pins list")
    return data


def _check_mechanism(pid: str, mechanism: Any, errors: List[str]) -> None:
    if not isinstance(mechanism, dict):
        errors.append("%s mechanism is %s, not an object" % (pid, type(mechanism).__name__))
        return
    missing = [k for k in REQUIRED_MECHANISM_KEYS if k not in mechanism]
    if missing:
        errors.append("%s mechanism missing keys: %s" % (pid, ", ".join(missing)))
        return
    tier = mechanism["tier"]
    if tier not in ALL_TIERS:
        errors.append("%s mechanism names unknown tier %r" % (pid, tier))
        return
    claim = str(mechanism["claim"] or "").strip()
    source = str(mechanism["source"] or "").strip()
    verified = str(mechanism["verified"] or "")
    does_not_prove = str(mechanism["does_not_prove"] or "").strip()
    if len(claim) < 20:
        errors.append("%s mechanism states no testable claim" % pid)
    if not source:
        errors.append("%s mechanism names no source" % pid)
    if len(does_not_prove) < 20:
        errors.append("%s mechanism does not say what it fails to prove" % pid)
    if not DATE_IN_TEXT.search(verified):
        errors.append("%s mechanism records no read date; ARC requires a date per assertion" % pid)
    if tier not in PROOF_TIERS:
        # The pillar's premise is the mechanism. If nothing the ledger records
        # supports it at proof tier, the premise is recorded as unproven, which
        # ARC treats as a gap in the evidence state rather than a footnote.
        errors.append(
            "%s mechanism is recorded at tier %s: the pillar's premise is unproven by "
            "any source this ledger names" % (pid, tier)
        )
    else:
        # A proof tier means the source was read and answered. A 404 recorded
        # beside tier A is the exact assertion this checker exists to refuse.
        if _is_negative_reading(verified) or not POSITIVE_READING.search(verified):
            errors.append(
                "%s claims tier %s but its reading records no successful source read (%s)"
                % (pid, tier, verified.strip()[:80] or "empty")
            )


def _check_figure(
    pid: str, field_name: str, figure: Any, live_value: Any, errors: List[str]
) -> None:
    label = "%s.%s" % (pid, field_name)
    if not isinstance(figure, dict):
        errors.append("%s is %s, not an object" % (label, type(figure).__name__))
        return
    missing = [k for k in REQUIRED_FIGURE_KEYS if k not in figure]
    if missing:
        errors.append("%s missing keys: %s" % (label, ", ".join(missing)))
        return

    if not _is_number(figure["value"]):
        errors.append("%s records a non-numeric value %r" % (label, figure["value"]))
    elif not _num_equal(figure["value"], live_value):
        errors.append(
            "%s records %s but the mark publishes %s: the ledger answers for a mark "
            "that no longer exists" % (label, _fmt(figure["value"]), _fmt(live_value))
        )

    presented = figure["presented_as"]
    tier = figure["tier"]
    source = str(figure["source"] or "").strip()
    verified = str(figure["verified"] or "")
    method = str(figure.get("estimation_method") or "").strip()
    does_not_prove = str(figure["does_not_prove"] or "").strip()

    if presented not in PRESENTATIONS:
        errors.append("%s is presented as %r; expected one of %s" % (label, presented, PRESENTATIONS))
    if tier not in ALL_TIERS:
        errors.append("%s names unknown tier %r" % (label, tier))
    if not source:
        errors.append("%s names no source" % label)
    if not DATE_IN_TEXT.search(verified):
        errors.append("%s records no read date" % label)
    if len(does_not_prove) < 20:
        errors.append("%s does not say what the figure fails to prove" % label)

    if presented == "fact":
        if tier not in PROOF_TIERS:
            errors.append(
                "%s is presented as fact at tier %s: a count asserted as fact needs a "
                "source read at proof tier" % (label, tier)
            )
        elif _is_negative_reading(verified) or not POSITIVE_READING.search(verified):
            errors.append(
                "%s is presented as fact at tier %s but its reading records no "
                "successful source read (%s)" % (label, tier, verified.strip()[:80] or "empty")
            )
    if presented == "estimate" and len(method) < 20:
        errors.append(
            "%s is presented as an estimate without a method; an estimate that does "
            "not name how it was derived is a guess with a citation next to it" % label
        )


TIER_STRENGTH = {"A": 4, "B": 3, "C": 2, "D": 1}


def pin_tier(pin: Dict[str, Any]) -> Optional[str]:
    """The strongest tier a pin carries, wherever the pin records it.

    A declaration pin (X1) carries one top-level ``tier``.  An architecture pin
    (C1-C7) carries a tier per claim instead: ``mechanism.tier`` plus one per
    figure.  Reading only the top-level field scored every architecture pin at
    ``None`` and reported a declared gap as if the gap were a tier defect.
    """
    recorded = [pin.get("tier")]
    mechanism = pin.get("mechanism")
    if isinstance(mechanism, dict):
        recorded.append(mechanism.get("tier"))
    for figure in pin.get("figures") or []:
        if isinstance(figure, dict):
            recorded.append(figure.get("tier"))
    tiers = [t for t in recorded if isinstance(t, str) and t in TIER_STRENGTH]
    if not tiers:
        return None
    return max(tiers, key=lambda t: TIER_STRENGTH[t])


def check(
    ledger: Dict[str, Any], described: List[Dict[str, Any]], machine: List[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings). Errors make GAPS; warnings never change a verdict."""
    errors: List[str] = []
    warnings: List[str] = []

    # A failed machine gate is a defect in the artifact, whatever the ledger says.
    for gate in machine:
        if not gate.get("ok"):
            errors.append(
                "%s %s: %s" % (gate.get("gate"), gate.get("title"), gate.get("detail"))
            )

    read_at = str(ledger.get("read_at") or "")
    if not DATE_IN_TEXT.search(read_at):
        errors.append("ledger read_at is %r; a ledger with no date cannot be aged" % read_at)

    live: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in described:
        for arch in row["architectures"]:
            live[(row["vendor"], arch["name"])] = {"mark": row, "arch": arch}

    pins = ledger.get("pins") or []
    seen: Dict[str, int] = {}
    matched: set = set()

    for index, pin in enumerate(pins):
        if not isinstance(pin, dict):
            errors.append("pins[%d] is not an object" % index)
            continue
        pid = str(pin.get("id") or "pins[%d]" % index)

        if pin.get("context"):
            missing = [k for k in REQUIRED_CONTEXT_KEYS if k not in pin]
            if missing:
                errors.append("%s is context but missing keys: %s" % (pid, ", ".join(missing)))
                continue
            if pid in seen:
                errors.append("%s is a duplicate id (first at pins[%d])" % (pid, seen[pid]))
            seen[pid] = index
            if pin.get("architecture"):
                errors.append("%s is context and names an architecture; a pin is one or the other" % pid)
            if pin["tier"] not in ALL_TIERS:
                errors.append("%s names unknown tier %r" % (pid, pin["tier"]))
            if not str(pin["source"] or "").strip():
                errors.append("%s is context but names no source" % pid)
            if not DATE_IN_TEXT.search(str(pin["verified"] or "")):
                errors.append("%s is context but records no read date" % pid)
            if len(str(pin["does_not_prove"] or "").strip()) < 20:
                errors.append("%s is context but does not say what it fails to prove" % pid)
            continue

        missing = [k for k in REQUIRED_PIN_KEYS if k not in pin]
        if missing:
            errors.append("%s missing required keys: %s" % (pid, ", ".join(missing)))
            continue
        if pid in seen:
            errors.append("%s is a duplicate id (first at pins[%d])" % (pid, seen[pid]))
        seen[pid] = index

        vendor = str(pin["vendor"] or "")
        architecture = str(pin["architecture"] or "")
        key = (vendor, architecture)
        entry = live.get(key)
        if entry is None:
            errors.append(
                "%s names vendor %r / architecture %r, which the mark does not publish"
                % (pid, vendor, architecture)
            )
            continue
        matched.add(key)

        mark_id = entry["mark"]["mark_id"]
        pin_mark = str(pin["mark"] or "")
        if not pin_mark or not mark_id.startswith(pin_mark):
            errors.append(
                "%s binds to mark %r, but the live mark is %r" % (pid, pin_mark, mark_id)
            )

        if len(str(pin["does_not_prove"] or "").strip()) < 20:
            errors.append("%s does not say what the pillar fails to prove" % pid)

        _check_mechanism(pid, pin["mechanism"], errors)

        # The mark's own citation. A substitute source is allowed, but the
        # substitution must be visible: silently citing something the mark does
        # not cite hides that the mark's citation is dead.
        evidence_url = entry["arch"]["evidence_url"]
        mechanism = pin["mechanism"] if isinstance(pin["mechanism"], dict) else {}
        source = str(mechanism.get("source") or "").strip()
        recorded_url = str(pin.get("mark_evidence_url") or "").strip()
        status = str(pin.get("mark_evidence_status") or "").strip()
        if recorded_url and evidence_url and recorded_url != evidence_url:
            errors.append(
                "%s records mark_evidence_url %r but the mark cites %r"
                % (pid, recorded_url, evidence_url)
            )
        if source and evidence_url and source != evidence_url:
            if not recorded_url:
                errors.append(
                    "%s cites %r instead of the mark's own citation %r without recording "
                    "mark_evidence_url; the substitution is invisible" % (pid, source, evidence_url)
                )
            if not DATE_IN_TEXT.search(status):
                errors.append(
                    "%s substitutes a source and records no dated status for the mark's own "
                    "citation" % pid
                )

        figures = pin["figures"]
        if not isinstance(figures, dict):
            errors.append("%s figures is %s, not an object" % (pid, type(figures).__name__))
            continue
        unknown = [f for f in figures if f not in FIGURE_FIELDS]
        if unknown:
            errors.append(
                "%s pins figures the mark does not carry: %s" % (pid, ", ".join(sorted(unknown)))
            )
        for field_name in FIGURE_FIELDS:
            if field_name not in figures:
                errors.append("%s pins no %s" % (pid, field_name))
                continue
            _check_figure(pid, field_name, figures[field_name], entry["arch"][field_name], errors)

    for (vendor, architecture), entry in live.items():
        if (vendor, architecture) not in matched:
            errors.append(
                "%s / %s is published and unpinned: an assertion without an evidence state"
                % (vendor, architecture)
            )

    # Declared gaps keep the shape honest: a gap is named, justified, and pinned
    # at a tier that can carry the declaration.
    declared = ledger.get("declared_gaps") or []
    if not isinstance(declared, list):
        errors.append("declared_gaps is %s, not a list" % type(declared).__name__)
        declared = []
    by_id = {str(p.get("id")): p for p in pins if isinstance(p, dict) and p.get("id")}
    for gap in declared:
        if not isinstance(gap, dict):
            errors.append("declared_gaps contains a non-object")
            continue
        gid = str(gap.get("gate") or "<unnamed>")
        if len(str(gap.get("why") or "").strip()) < 40:
            errors.append("declared gap %s does not say why it is unimplemented" % gid)
        pin_ids = gap.get("pins") or []
        if not pin_ids:
            errors.append("declared gap %s names no pins" % gid)
        for pid in pin_ids:
            pin = by_id.get(str(pid))
            if pin is None:
                errors.append("declared gap %s names missing pin %s" % (gid, pid))
            elif pin_tier(pin) not in ("A", "B", "C"):
                errors.append(
                    "declared gap %s names %s at tier %s, below C" % (gid, pid, pin_tier(pin))
                )

    return errors, warnings


def staleness(pins: List[Dict[str, Any]]) -> List[str]:
    """Pins whose support is carried rather than read here."""
    stale: List[str] = []
    for pin in pins:
        if not isinstance(pin, dict) or not pin.get("id"):
            continue
        mechanism = pin.get("mechanism")
        if isinstance(mechanism, dict) and _is_stale(str(mechanism.get("verified") or "")):
            stale.append(str(pin["id"]))
            continue
        if pin.get("context") and _is_stale(str(pin.get("verified") or "")):
            stale.append(str(pin["id"]))
    return stale


def verdict(errors: List[str], stale: List[str]) -> str:
    if errors:
        return VERDICT_GAPS
    if stale:
        return VERDICT_INCOMPLETE
    return VERDICT_READY


def exit_code_for(name: str) -> int:
    if name == VERDICT_READY:
        return EXIT_READY
    if name == VERDICT_BLIND:
        return EXIT_BLIND
    return EXIT_BAD


def gate_table(ledger: Dict[str, Any], described: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Architecture -> the evidence state that answers for it."""
    pins = [p for p in ledger.get("pins", []) if isinstance(p, dict)]
    rows: List[Dict[str, Any]] = []
    for mark in described:
        for arch in mark["architectures"]:
            pin = next(
                (
                    p
                    for p in pins
                    if str(p.get("vendor")) == mark["vendor"]
                    and str(p.get("architecture")) == arch["name"]
                ),
                None,
            )
            mechanism = (pin or {}).get("mechanism") if pin else None
            figures = (pin or {}).get("figures") if pin else None
            figure_tiers: List[str] = []
            if isinstance(figures, dict):
                for field_name in FIGURE_FIELDS:
                    entry = figures.get(field_name)
                    if isinstance(entry, dict):
                        figure_tiers.append(
                            "%s:%s" % (FIGURE_LABELS.get(field_name, field_name), entry.get("tier"))
                        )
            rows.append(
                {
                    "architecture": arch["name"],
                    "vendor": mark["vendor"],
                    "pin": (pin or {}).get("id"),
                    "mechanism_tier": (mechanism or {}).get("tier") if isinstance(mechanism, dict) else None,
                    "figures": figure_tiers,
                    "stale": _is_stale(str((mechanism or {}).get("verified") or ""))
                    if isinstance(mechanism, dict)
                    else True,
                }
            )
    for gap in ledger.get("declared_gaps") or []:
        if isinstance(gap, dict):
            rows.append(
                {
                    "architecture": gap.get("title", "") + " [declared, not implemented]",
                    "vendor": "",
                    "pin": ",".join(gap.get("pins") or []),
                    "mechanism_tier": "-",
                    "figures": [],
                    "stale": False,
                }
            )
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the CAIN obsolescence mark against its evidence ledger."
    )
    parser.add_argument("--check", action="store_true", help="validate and exit (default)")
    parser.add_argument("--gates", action="store_true", help="print architecture -> evidence table")
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON")
    parser.add_argument("--ledger", metavar="PATH", default=str(DEFAULT_LEDGER))
    args = parser.parse_args(argv)

    # Every failure leaves a printed verdict and a non-zero exit. A traceback exits
    # 1, which is indistinguishable from GAPS, and a silent success is worse.
    try:
        module = load_mark_module()
        marks = load_marks(module)
        described = [describe(mark) for mark in marks]
        machine = machine_gates(module, marks, described)
        ledger = load_ledger(Path(args.ledger))
        errors, warnings = check(ledger, described, machine)
        pins = [p for p in ledger.get("pins", []) if isinstance(p, dict)]
        stale = staleness(pins)
        name = verdict(errors, stale)
        rows = gate_table(ledger, described)
    except Blinded as exc:
        print("verdict: %s" % VERDICT_BLIND)
        print("reason: %s" % exc)
        return EXIT_BLIND
    except Exception as exc:  # noqa: BLE001 - an unhandled defect is BLIND, and is named
        print("verdict: %s" % VERDICT_BLIND)
        print("reason: checker failed: %s: %s" % (type(exc).__name__, exc))
        return EXIT_BLIND

    figure_count = 0
    for pin in pins:
        figures = pin.get("figures")
        if not pin.get("context") and isinstance(figures, dict):
            figure_count += len(figures)
    passing = [g for g in machine if g.get("ok")]

    if args.json:
        out = Path(args.json)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        "verdict": name,
                        "read_at": ledger.get("read_at"),
                        "marks": len(described),
                        "pins": len(pins),
                        "figures": figure_count,
                        "machine_gates": machine,
                        "architectures": rows,
                        "errors": errors,
                        "warnings": warnings,
                        "stale_pins": stale,
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
        "ledger: %d marks, %d pins, %d figures, %d/%d machine gates, %d declared gaps"
        % (
            len(described),
            len(pins),
            figure_count,
            len(passing),
            len(machine),
            len(ledger.get("declared_gaps") or []),
        )
    )
    if errors:
        print("errors: %d" % len(errors))
        for err in errors:
            print("  [GAP] %s" % err)
    if stale:
        print("carried (addressed, not re-read here): %s" % ", ".join(stale))
    if warnings:
        for warn in warnings[:5]:
            print("  [note] %s" % warn)
        if len(warnings) > 5:
            print("  [note] ... and %d more (advisory only)" % (len(warnings) - 5))

    if args.gates:
        print("")
        print("%-5s %-9s %-10s %s" % ("pin", "mechanism", "figures", "architecture"))
        for row in rows:
            print(
                "%-5s %-9s %-10s %s"
                % (
                    row["pin"] or "none",
                    row["mechanism_tier"] or "-",
                    ",".join(row["figures"]) or "-",
                    row["architecture"],
                )
            )

    return exit_code_for(name)


if __name__ == "__main__":
    sys.exit(main())
