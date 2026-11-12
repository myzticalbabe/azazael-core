#!/usr/bin/env python3
# coding: utf-8
"""metthew_families.py -- M-13: the catalogue's own gate, so its rules stop being prose.

``docs/METTHEW/highend_families.json`` carried a block that said the checker below was
``pending``: the vocabulary rule, the affects/determinism partition and the
"no claim without a gate" policy were enforced only by a pytest file, so the catalogue
could be edited by anything that did not run pytest and would then describe itself as
governed. This tool is that checker. ``enforcement.status`` is now ``enforced`` and F7
refuses the file if it ever goes back to claiming a gate that does not run.

The seven refusals, each named so a disagreement is a disagreement about a rule:

  F1 capability-single-definition  an id is defined once; a repeat must agree exactly
  F2 capability-domain             affects/determinism domain, and ``sim`` implies exact
  F3 family-required               identity, host, and a gate AND a negative control
  F4 lineage-cited                 each lineage entry carries a source tier and a url
  F5 payload-no-invented-bytes     a byte figure is either measured, or absent
  F6 reference-shard-proves        a listed shard exists, validates, and uses the family
  F7 enforcement-current           the file names the gate that actually runs

What F4 and F5 are for, in the estate's own language
----------------------------------------------------
A dated claim with no source tier is plausible; it is not checkable. The tier is the
FLOCK canon's, narrowed to this catalogue and written down here rather than assumed:

  A  the artefact's own page or paper, FETCHED and read (url + fetched_on required)
  B  an official index or registry entry (url required; the page is not the artefact)
  C  credible secondary reporting, FETCHED (url + fetched_on required)
  D  a search snippet, a social post, or a link nobody read -- a LEAD, not evidence

D is allowed and is counted out loud: ``--check`` prints how many entries are checkable
and how many are leads, because a catalogue where every row claims A without a fetch
date is the failure this gate exists to catch. Likewise F5: an invented
``payload_bytes_estimate`` is refused by name, and a ``measured`` claim must name the
command and the date that produced it. A number nobody measured is a wish with a unit.

What this tool does NOT establish
---------------------------------
It reads JSON. It does not run a family's gate, so a family whose gate has never been
executed still passes: the refusal available today is about the SHAPE of the claim.
``--check`` says so on every run instead of letting a green line imply more.

Usage
-----
  python tools/metthew_families.py                 # judge the shipped catalogue
  python tools/metthew_families.py --json
  python tools/metthew_families.py --selftest      # every control, both directions
  python tools/metthew_families.py --catalogue path/to/catalogue.json --shards DIR

Exit codes: 0 clean, 1 a refusal fired, 2 the catalogue or a shard could not be read.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
METTHEW = REPO / "docs" / "METTHEW"
CATALOG_PATH = METTHEW / "highend_families.json"
SHARDS_DIR = METTHEW / "reference-shards"
VALIDATOR_PATH = REPO / "tools" / "metthew_validate.py"

AFFECTS = ("sim", "presentation")
DETERMINISM = ("exact", "approximate")
HOSTS = ("python-resident", "wasm3-det", "native")
SOURCE_TIERS = ("A", "B", "C", "D")
#: Tiers whose claim is only honest if somebody actually opened the page.
FETCHED_TIERS = ("A", "C")
MIN_PROSE = 20
_URL_RE = re.compile(r"^https?://[^\s]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class CatalogError(Exception):
    """The catalogue (or a shard it points at) could not be read -- not a verdict."""


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise CatalogError("not found: %s" % path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CatalogError("%s is not readable JSON: %s" % (path, exc)) from exc


def load_validator(path: Path = VALIDATOR_PATH):
    """The shard validator, loaded by path so F6 judges with the shipped rules."""
    if not path.is_file():
        raise CatalogError("not found: %s" % path)
    spec = importlib.util.spec_from_file_location("metthew_validate_for_families", path)
    if spec is None or spec.loader is None:
        raise CatalogError("cannot import %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Reading the catalogue
# --------------------------------------------------------------------------- #

def families_of(data: Dict) -> List[Dict]:
    families = data.get("families")
    if not isinstance(families, list):
        raise CatalogError("catalogue has no families[] list")
    return [family for family in families if isinstance(family, dict)]


def shared_of(data: Dict) -> List[Dict]:
    shared = data.get("shared") or []
    if not isinstance(shared, list):
        raise CatalogError("catalogue shared[] is not a list")
    return [capability for capability in shared if isinstance(capability, dict)]


def capabilities_of(family: Dict) -> List[Dict]:
    entries = family.get("capabilities") or []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def vocabulary(data: Dict) -> Dict[str, Tuple[str, str]]:
    """id -> (affects, determinism), across shared[] and every family."""
    out: Dict[str, Tuple[str, str]] = {}
    for capability in shared_of(data):
        out[str(capability.get("id"))] = (capability.get("affects"),
                                          capability.get("determinism"))
    for family in families_of(data):
        for capability in capabilities_of(family):
            out[str(capability.get("id"))] = (capability.get("affects"),
                                              capability.get("determinism"))
    return out


def family_capability_ids(family: Dict) -> set:
    return {str(entry.get("id")) for entry in capabilities_of(family)}


# --------------------------------------------------------------------------- #
# F1 / F2 -- the vocabulary and the partition
# --------------------------------------------------------------------------- #

def check_vocabulary(data: Dict) -> List[str]:
    problems: List[str] = []
    seen: Dict[str, Tuple[str, str, str]] = {}
    owners: Dict[str, List[str]] = {}
    for capability in shared_of(data):
        entry = (str(capability.get("affects")), str(capability.get("determinism")),
                 str(capability.get("note")))
        identifier = str(capability.get("id"))
        owners.setdefault(identifier, []).append("shared[]")
        seen[identifier] = entry
    for family in families_of(data):
        for capability in capabilities_of(family):
            identifier = str(capability.get("id"))
            entry = (str(capability.get("affects")), str(capability.get("determinism")),
                     str(capability.get("note")))
            owners.setdefault(identifier, []).append(str(family.get("id")))
            previous = seen.get(identifier)
            if previous is None:
                seen[identifier] = entry
            elif previous[:2] != entry[:2]:
                problems.append("F1 capability-single-definition: %r is defined with "
                                "affects/determinism %s in %s and %s in %s; one id cannot "
                                "mean two things to a peer"
                                % (identifier, (previous[0], previous[1]),
                                   ", ".join(owners[identifier][:-1]) or "?",
                                   (entry[0], entry[1]), owners[identifier][-1]))
            elif not entry[2] or not previous[2]:
                problems.append("F1 capability-single-definition: %r is defined twice "
                                "(%s) and at least one definition has no note, so the "
                                "second definition adds nothing a peer can act on"
                                % (identifier, ", ".join(owners[identifier])))
    for identifier, locations in sorted(owners.items()):
        if not _ID_RE.match(identifier) or identifier == "None":
            problems.append("F1 capability-single-definition: %r is not a lowercase "
                            "hyphenated id (%s)" % (identifier, ", ".join(locations)))
    return problems


def check_domain(data: Dict) -> List[str]:
    problems: List[str] = []
    for capability in shared_of(data):
        problems.extend(_domain_problems(capability, "shared[]"))
    for family in families_of(data):
        for capability in capabilities_of(family):
            problems.extend(_domain_problems(capability, str(family.get("id"))))
    return problems


def _domain_problems(capability: Dict, where: str) -> List[str]:
    identifier = capability.get("id")
    affects = capability.get("affects")
    determinism = capability.get("determinism")
    problems: List[str] = []
    if affects not in AFFECTS:
        problems.append("F2 capability-domain: %r in %s has affects %r, not one of %s"
                        % (identifier, where, affects, ", ".join(AFFECTS)))
    if determinism not in DETERMINISM:
        problems.append("F2 capability-domain: %r in %s has determinism %r, not one of %s"
                        % (identifier, where, determinism, ", ".join(DETERMINISM)))
    if affects == "sim" and determinism != "exact":
        problems.append("F2 capability-domain: %r in %s affects the simulation with "
                        "determinism %r; an approximate value in the authoritative state "
                        "makes every golden run unreproducible"
                        % (identifier, where, determinism))
    if not isinstance(capability.get("note"), str) or len(capability.get("note") or "") < 8:
        problems.append("F2 capability-domain: %r in %s has no usable note, so a peer "
                        "cannot tell what the id promises" % (identifier, where))
    return problems


# --------------------------------------------------------------------------- #
# F3 -- a family must state what it is and how it would be caught
# --------------------------------------------------------------------------- #

def check_families(data: Dict) -> List[str]:
    problems: List[str] = []
    ids = set()
    for family in families_of(data):
        identifier = family.get("id")
        if identifier in ids:
            problems.append("F3 family-required: %r is declared twice" % (identifier,))
        ids.add(identifier)
        for field in ("id", "name", "pitch", "payload_shape"):
            value = family.get(field)
            if not isinstance(value, str) or len(value.strip()) < 4:
                problems.append("F3 family-required: %r has no usable %s"
                                % (identifier, field))
        host = family.get("host") or {}
        requires = host.get("requires") if isinstance(host, dict) else None
        if requires not in HOSTS:
            problems.append("F3 family-required: %r requires %r, not one of %s"
                            % (identifier, requires, ", ".join(HOSTS)))
        acceptance = family.get("acceptance") or {}
        gate = (acceptance.get("gate") or "") if isinstance(acceptance, dict) else ""
        control = (acceptance.get("negative_control") or "") if isinstance(
            acceptance, dict) else ""
        if len(gate.strip()) < MIN_PROSE:
            problems.append("F3 family-required: %r states no usable gate, so nothing "
                            "can fail" % (identifier,))
        if len(control.strip()) < MIN_PROSE:
            problems.append("F3 family-required: %r states no usable negative control, so "
                            "a green gate may be measuring its own harness" % (identifier,))
        if gate and gate.strip() == control.strip():
            problems.append("F3 family-required: %r uses one sentence as both gate and "
                            "negative control" % (identifier,))
        if not capabilities_of(family):
            problems.append("F3 family-required: %r declares no capabilities, so it is a "
                            "title rather than a family" % (identifier,))
        links = family.get("reference_shards")
        if links is not None and not isinstance(links, list):
            problems.append("F3 family-required: %r reference_shards must be a list"
                            % (identifier,))
    return problems


# --------------------------------------------------------------------------- #
# F4 -- a dated claim needs a tier, and a tier above D needs a page
# --------------------------------------------------------------------------- #

def check_lineage(data: Dict) -> Tuple[List[str], int, int]:
    """Refusals plus (checkable, total) so coverage can be printed, not claimed."""
    problems: List[str] = []
    checkable = 0
    total = 0
    for family in families_of(data):
        identifier = family.get("id")
        lineage = family.get("lineage")
        if not isinstance(lineage, list) or not lineage:
            problems.append("F4 lineage-cited: %r cites nothing, so its claim has no "
                            "origin" % (identifier,))
            continue
        for position, entry in enumerate(lineage):
            where = "%s.lineage[%d]" % (identifier, position)
            total += 1
            if not isinstance(entry, dict):
                problems.append("F4 lineage-cited: %s is not an object" % where)
                continue
            for field in ("source", "adaptation"):
                value = entry.get(field)
                if not isinstance(value, str) or len(value.strip()) < 4:
                    problems.append("F4 lineage-cited: %s has no usable %s"
                                    % (where, field))
            tier = entry.get("source_tier")
            if tier not in SOURCE_TIERS:
                problems.append("F4 lineage-cited: %s has source_tier %r, not one of %s"
                                % (where, tier, ", ".join(SOURCE_TIERS)))
                continue
            date = entry.get("date")
            # A RANGE is a lead's date, not evidence: "2023-2026" cannot be checked, so it
            # is allowed on tier D and refused on a tier that claims to have been opened.
            exact_date = isinstance(date, str) and bool(re.match(
                r"^\d{4}(-\d{2}(-\d{2})?)?$", date))
            span = isinstance(date, str) and bool(re.match(r"^\d{4}-\d{4}$", date))
            if span and tier != "D":
                problems.append("F4 lineage-cited: %s claims tier %s with the date range "
                                "%r; a source that was fetched has one date"
                                % (where, tier, date))
            elif not exact_date and not span:
                problems.append("F4 lineage-cited: %s has date %r, which is not YYYY, "
                                "YYYY-MM, YYYY-MM-DD or (tier D only) YYYY-YYYY"
                                % (where, date))
            if tier in ("A", "B", "C"):
                checkable += 1
            url = entry.get("url")
            local = isinstance(url, str) and bool(url) and not _URL_RE.match(url)
            if tier in ("A", "B", "C") and not (_URL_RE.match(url) if isinstance(url, str)
                                                   else False) and not local:
                problems.append("F4 lineage-cited: %s claims tier %s with url %r; a tier "
                                "above D is a promise the page exists and was found"
                                % (where, tier, url))
            if local:
                # A first-party document in this tree is a primary source, but only if it
                # is actually here: a path that does not resolve is a citation of nothing.
                if not (Path(REPO) / url).is_file():
                    problems.append("F4 lineage-cited: %s cites the local document %r, "
                                    "which is not in this tree" % (where, url))
                if not entry.get("evidence"):
                    problems.append("F4 lineage-cited: %s cites a local document without "
                                    "evidence: the command that produced the number"
                                    % (where,))
            elif tier in FETCHED_TIERS:
                fetched = entry.get("fetched_on")
                if not isinstance(fetched, str) or not _DATE_RE.match(fetched):
                    problems.append("F4 lineage-cited: %s claims tier %s without "
                                    "fetched_on; an unread page is tier D, whatever its "
                                    "url looks like" % (where, tier))
            if tier == "D" and not entry.get("evidence"):
                problems.append("F4 lineage-cited: %s is tier D and does not say what kind "
                                "of lead it is (evidence: search-snippet / not-located / "
                                "social)" % (where,))
            if isinstance(url, str) and url and not _URL_RE.match(url) and not local:
                problems.append("F4 lineage-cited: %s has malformed url %r (neither an "
                                "http(s) URL nor a repo-relative path)" % (where, url))
    return problems, checkable, total


# --------------------------------------------------------------------------- #
# F5 -- a byte figure is measured, or it is a wish with a unit
# --------------------------------------------------------------------------- #

def check_payload(data: Dict) -> List[str]:
    problems: List[str] = []
    for family in families_of(data):
        identifier = family.get("id")
        if "payload_bytes_estimate" in family:
            problems.append("F5 payload-no-invented-bytes: %r still carries "
                            "payload_bytes_estimate %r; the word 'estimate' is the "
                            "confession. Replace it with a measured payload_claim or "
                            "with none."
                            % (identifier, family.get("payload_bytes_estimate")))
        claim = family.get("payload_claim")
        if claim is None:
            problems.append("F5 payload-no-invented-bytes: %r declares no payload_claim, "
                            "so nobody knows whether a size was measured or imagined"
                            % (identifier,))
            continue
        if not isinstance(claim, dict):
            problems.append("F5 payload-no-invented-bytes: %r payload_claim is not an "
                            "object" % (identifier,))
            continue
        status = claim.get("status")
        if status == "measured":
            size = claim.get("bytes")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                problems.append("F5 payload-no-invented-bytes: %r claims a measured "
                                "payload with bytes %r" % (identifier, size))
            command = claim.get("command")
            if not isinstance(command, str) or len(command.strip()) < 8:
                problems.append("F5 payload-no-invented-bytes: %r claims a measured "
                                "payload without the command that produced it"
                                % (identifier,))
            measured_on = claim.get("measured_on")
            if not isinstance(measured_on, str) or not _DATE_RE.match(measured_on):
                problems.append("F5 payload-no-invented-bytes: %r claims a measured "
                                "payload without measured_on (YYYY-MM-DD)"
                                % (identifier,))
        elif status == "unmeasured":
            if isinstance(claim.get("reason"), str) and claim.get("reason").strip():
                continue
            problems.append("F5 payload-no-invented-bytes: %r declares the payload "
                            "unmeasured without saying why, which reads as 'not yet' "
                            "forever" % (identifier,))
        else:
            problems.append("F5 payload-no-invented-bytes: %r payload_claim.status is %r, "
                            "not measured or unmeasured" % (identifier, status))
    return problems


# --------------------------------------------------------------------------- #
# F6 -- a linked shard must exist, validate, and actually use the family
# --------------------------------------------------------------------------- #

def check_reference_shards(data: Dict, shards_root: Path) -> Tuple[List[str], int, int]:
    problems: List[str] = []
    validator = load_validator()
    schema = validator.load_schema()
    proven = 0
    families = families_of(data)
    for family in families:
        identifier = str(family.get("id"))
        links = family.get("reference_shards") or []
        if not isinstance(links, list):
            continue
        own_ids = family_capability_ids(family)
        for link in links:
            manifest_path = Path(shards_root) / str(link) / "shard.json"
            if not manifest_path.is_file():
                problems.append("F6 reference-shard-proves: %r lists %r, but %s does not "
                                "exist" % (identifier, link,
                                           manifest_path.relative_to(REPO)
                                           if str(manifest_path).startswith(str(REPO))
                                           else manifest_path))
                continue
            try:
                manifest = load_json(manifest_path)
            except CatalogError as exc:
                problems.append("F6 reference-shard-proves: %r lists %r, which cannot be "
                                "read: %s" % (identifier, link, exc))
                continue
            # V15 is given the shard directory on purpose: a family that cites a shard
            # whose files no longer hash to their declared values is citing a memory.
            refusals = validator.validate_manifest(manifest, schema, manifest_path.parent)
            if refusals:
                problems.append("F6 reference-shard-proves: %r lists %r, which the shard "
                                "validator refuses: %s"
                                % (identifier, link, "; ".join(refusals[:3])))
                continue
            declared = {str(entry.get("id")) for entry in manifest.get("capabilities") or []
                        if isinstance(entry, dict)}
            if not declared & own_ids:
                problems.append("F6 reference-shard-proves: %r lists %r, which declares "
                                "none of the family's capability ids (%s); the link is a "
                                "citation of a shard that does not exercise the family"
                                % (identifier, link,
                                   ", ".join(sorted(own_ids)) or "none defined"))
                continue
            proven += 1
    return problems, proven, len(families)


# --------------------------------------------------------------------------- #
# F7 -- the file may not claim a gate that does not run
# --------------------------------------------------------------------------- #

def check_enforcement(data: Dict) -> List[str]:
    problems: List[str] = []
    enforcement = data.get("enforcement") or {}
    if not isinstance(enforcement, dict):
        return ["F7 enforcement-current: enforcement is not an object"]
    checker = enforcement.get("checker")
    if checker != "tools/metthew_families.py":
        problems.append("F7 enforcement-current: enforcement.checker is %r; the gate is "
                        "tools/metthew_families.py" % (checker,))
    status = enforcement.get("status")
    if status != "enforced":
        problems.append("F7 enforcement-current: enforcement.status is %r while the "
                        "checker exists and runs; 'pending' is a claim that nothing "
                        "enforces this file, and it would be false."
                        % (status,))
    command = enforcement.get("command")
    if not isinstance(command, str) or "metthew_families.py" not in command:
        problems.append("F7 enforcement-current: enforcement.command %r does not name the "
                        "command a peer should run" % (command,))
    return problems


# --------------------------------------------------------------------------- #
# Judging the catalogue
# --------------------------------------------------------------------------- #

def check_catalogue(data: Dict, shards_root: Path = SHARDS_DIR) -> Dict[str, Any]:
    """Every refusal, plus the two coverage numbers, so nothing is implied."""
    lineage_problems, checkable, lineage_total = check_lineage(data)
    shard_problems, proven, family_total = check_reference_shards(data, shards_root)
    problems: List[str] = []
    problems.extend(check_vocabulary(data))
    problems.extend(check_domain(data))
    problems.extend(check_families(data))
    problems.extend(lineage_problems)
    problems.extend(check_payload(data))
    problems.extend(shard_problems)
    problems.extend(check_enforcement(data))
    return {
        "problems": problems,
        "lineage_checkable": checkable,
        "lineage_total": lineage_total,
        "families_proven": proven,
        "families_total": family_total,
    }


NOT_A_RUN = ("families: this tool judges the SHAPE of each claim, not whether any "
             "family gate has ever executed")


def report(path: Path, result: Dict[str, Any]) -> str:
    problems = result["problems"]
    head = "PASS %s" % path.name if not problems else "FAIL %s" % path.name
    lines = [head]
    lines.extend("     %s" % problem for problem in problems)
    lines.append("  lineage: %d of %d entries are checkable (tier A/B/C); %d are leads "
                 "(tier D)" % (result["lineage_checkable"], result["lineage_total"],
                               result["lineage_total"] - result["lineage_checkable"]))
    lines.append("  proven families: %d of %d list a reference shard that declares one "
                 "of their capabilities" % (result["families_proven"],
                                            result["families_total"]))
    lines.append("  %s" % NOT_A_RUN)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #
#: (name, mutation, expected refusal id). Every mutation is applied to a deep copy of
#: the REAL catalogue, so the control tracks the shipped data instead of a fixture that
#: can drift away from it. A control that stops firing means the gate went blind.

def _first_checkable(data: Dict) -> Optional[Dict]:
    for family in families_of(data):
        for entry in family.get("lineage") or []:
            if isinstance(entry, dict) and entry.get("source_tier") in ("A", "C"):
                return entry
    return None


def _mutations() -> List[Tuple[str, Any, str]]:
    def drop_fetch(data: Dict) -> None:
        entry = _first_checkable(data)
        if entry is None:
            raise CatalogError("no tier A/C lineage entry to un-fetch")
        entry.pop("fetched_on", None)

    def unquote_url(data: Dict) -> None:
        entry = _first_checkable(data)
        if entry is None:
            raise CatalogError("no tier A/C lineage entry to strip")
        entry["url"] = ""

    def reclassify_sim(data: Dict) -> None:
        families_of(data)[0]["capabilities"][0]["affects"] = "sim"
        families_of(data)[0]["capabilities"][0]["determinism"] = "approximate"

    def split_definition(data: Dict) -> None:
        shared = shared_of(data)
        if not shared:
            raise CatalogError("catalogue has no shared[] capability to redefine")
        twin = copy.deepcopy(shared[0])
        twin["affects"] = "presentation" if twin["affects"] == "sim" else "sim"
        twin["determinism"] = "approximate" if twin["determinism"] == "exact" else "exact"
        families_of(data)[0]["capabilities"].append(twin)

    def drop_control(data: Dict) -> None:
        families_of(data)[0]["acceptance"]["negative_control"] = ""

    def reinstate_estimate(data: Dict) -> None:
        families_of(data)[0]["payload_bytes_estimate"] = 50331648

    def measured_without_command(data: Dict) -> None:
        families_of(data)[0]["payload_claim"] = {
            "status": "measured", "bytes": 1024, "measured_on": "2026-11-01"}

    def point_at_a_ghost(data: Dict) -> None:
        families_of(data)[0]["reference_shards"] = ["highend_nonexistent"]

    def point_at_a_foreign_shard(data: Dict) -> None:
        # Valid shard, wrong family: it must be refused, which is the whole of F6.
        for family in families_of(data):
            if family.get("id") == "glassland":
                family["reference_shards"] = ["highend_duel"]
                return
        raise CatalogError("no glassland family to mislink")

    def unenforce(data: Dict) -> None:
        data["enforcement"]["status"] = "pending"

    return [
        ("unfetched tier", drop_fetch, "F4 lineage-cited"),
        ("url removed", unquote_url, "F4 lineage-cited"),
        ("sim made approximate", reclassify_sim, "F2 capability-domain"),
        ("id redefined", split_definition, "F1 capability-single-definition"),
        ("negative control removed", drop_control, "F3 family-required"),
        ("estimate reinstated", reinstate_estimate, "F5 payload-no-invented-bytes"),
        ("measured, no command", measured_without_command,
         "F5 payload-no-invented-bytes"),
        ("ghost reference shard", point_at_a_ghost, "F6 reference-shard-proves"),
        ("foreign reference shard", point_at_a_foreign_shard,
         "F6 reference-shard-proves"),
        ("status unenforced", unenforce, "F7 enforcement-current"),
    ]


def selftest(catalogue_path: Path, shards_root: Path) -> Tuple[int, List[str]]:
    """Both directions: the real catalogue must pass, each mutation must be refused."""
    lines: List[str] = []
    failures = 0
    try:
        data = load_json(catalogue_path)
    except CatalogError as exc:
        return 1, ["UNUSABLE: %s" % exc]
    if not isinstance(data, dict):
        return 1, ["UNUSABLE: catalogue is not an object"]

    healthy = check_catalogue(data, shards_root)["problems"]
    if healthy:
        failures += 1
        lines.append("FAIL healthy control: the real catalogue is refused, so every "
                     "refusal below is unattributable:")
        lines.extend("     %s" % problem for problem in healthy)
    else:
        lines.append("PASS healthy control: the shipped catalogue is clean")

    for name, mutate, expected in _mutations():
        mutated = copy.deepcopy(data)
        try:
            mutate(mutated)
        except (CatalogError, KeyError, IndexError, TypeError) as exc:
            failures += 1
            lines.append("FAIL %s: the mutation could not be applied (%s)" % (name, exc))
            continue
        problems = check_catalogue(mutated, shards_root)["problems"]
        hits = [problem for problem in problems if problem.startswith(expected)]
        if hits:
            lines.append("PASS %s -> %s" % (name, expected))
        else:
            failures += 1
            lines.append("FAIL %s: expected %r, got %s"
                         % (name, expected, problems[:2] or "no refusal"))

    # The measured branch has a healthy side too: a complete claim must NOT be refused.
    legal = copy.deepcopy(data)
    families_of(legal)[0]["payload_claim"] = {
        "status": "measured", "bytes": 15344,
        "command": "python tools/metthew_families.py --check",
        "measured_on": "2026-11-01"}
    problems = check_catalogue(legal, shards_root)["problems"]
    if [problem for problem in problems if problem.startswith("F5")]:
        failures += 1
        lines.append("FAIL measured claim refused although complete: %s"
                     % problems[:2])
    else:
        lines.append("PASS complete measured claim is accepted")
    return failures, lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Judge the Metthew high-end family catalogue (M-13).")
    parser.add_argument("--catalogue", default=str(CATALOG_PATH))
    parser.add_argument("--shards", default=str(SHARDS_DIR))
    parser.add_argument("--check", action="store_true",
                        help="judge the shipped catalogue (the default; named so the "
                             "hook and the enforcement block agree on one command)")
    parser.add_argument("--selftest", action="store_true",
                        help="run every control in both directions")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    catalogue_path = Path(args.catalogue)
    shards_root = Path(args.shards)

    if args.selftest:
        failures, lines = selftest(catalogue_path, shards_root)
        if args.json:
            print(json.dumps({"failures": failures, "lines": lines}, indent=1))
        else:
            for line in lines:
                print(line)
            print("")
            print("SELFTEST %s (%d control(s), %d failure(s))"
                  % ("OK" if not failures else "FAILED", len(lines), failures))
        return 0 if not failures else 1

    try:
        data = load_json(catalogue_path)
    except CatalogError as exc:
        print("UNUSABLE: %s" % exc, file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("UNUSABLE: %s is not a JSON object" % catalogue_path, file=sys.stderr)
        return 2
    try:
        result = check_catalogue(data, shards_root)
    except CatalogError as exc:
        print("UNUSABLE: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        print(report(catalogue_path, result))
    if result["problems"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
