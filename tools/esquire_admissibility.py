#!/usr/bin/env python3
"""ESQUIRE-ADMISSIBILITY-v2: a proffer instrument for machine-produced opinions.

WHAT THIS IS
    A deterministic checker over a *proffer record*: the structured account a
    party would have to be able to give before offering a machine-produced
    opinion, output, or reproduction as evidence. For each admissibility gate it
    reports one of three states:

        PASS          the record carries what the gate requires
        GAP           the record addresses the gate and contradicts it
        UNVERIFIABLE  the record never addresses the gate

WHAT THIS IS NOT
    It does not decide admissibility. Admissibility is a ruling by a court after
    weighing facts this instrument cannot see (prejudice, confusion, the
    opponent's objections, the judge's gatekeeping discretion). The instrument
    therefore has no verdict named "admissible": its best state is READY, its
    worst reading of a well-formed record is GAPS, and a record it cannot read
    is BLIND, never "inadmissible". An instrument that could output
    "inadmissible" would be asserting a judicial conclusion from a form.

GATES AND AUTHORITY
    G1  qualification and disclosure      FRE 702 (preamble, (a))
    G2  sufficient facts or data          FRE 702(b)
    G3  reliable principles and methods   FRE 702(c); Daubert, 509 U.S. 579
    G4  reliable application + citations  FRE 702(d); Mata v. Avianca
    G5  authentication                     FRE 901(a), 901(b)(9), 902(13)-(14)
    G6  hearsay posture                    FRE 801, 803(6), 902(13)
    G7  human review and candor            Esquire E-1/E-5; ABA Formal Op. 512
    G8  testimonial character              U.S. Const. amend. VI; Melendez-Diaz
    G9  declarant and confrontation        Crawford, 541 U.S. 36; Bullcoming, 564 U.S. 647

    G8 and G9 apply ONLY when the record declares a criminal forum. A record that
    states no forum is read as civil, and the report prints that reading in
    `forum`, `forum_source` and `forum_note`. The default is stated, never
    silent: reading a criminal proffer as civil would drop the Confrontation
    Clause from the instrument without a word, which is the failure this pair of
    gates exists to prevent.

    Six points of legal accuracy the implementation is built to respect:

    * The Daubert factors (testing, error rate, peer review, general
      acceptance) are NON-EXHAUSTIVE. A test that required all four would
      encode an error of law as a gate, so peer review and general acceptance
      are reported as supporting notes and can never by themselves fail G3.
    * FRE 702 as amended in December 2023 puts the burden on the proponent to
      demonstrate reliability "more likely than not"; the gate asks whether the
      record makes that demonstration possible, not whether it succeeded.
    * A machine is not a witness. G1 requires a named natural person who has
      reviewed the output, which is the bridge to the Esquire canon's naming
      rule: the mandate installs a human where a machine did the work.
    * FRE 902(13) is an AUTHENTICATION rule, not a hearsay exception. It is read
      here as satisfying G6 only because the certification it requires must
      itself comply with 902(11)/(12) and so carries the 803(6) foundation
      (made at or near the time, by someone with knowledge, in the regular
      course of activity). A record that cannot meet that foundation is not
      made admissible by a certification, and this instrument cannot see the
      difference: a G6 PASS on the 902(13) route is a statement about the
      record's paperwork, never about the record's truth status.
    * In a criminal case that same certification is a TESTIMONIAL statement when
      it was prepared for litigation (Melendez-Diaz v. Massachusetts, 557 U.S.
      305 (2009)), so the document Foundation that satisfies G5 can create the
      confrontation problem G8/G9 test. The instrument reports that conflict as
      a GAP rather than letting a complete certificate read as a clean file.
    * An administrative adjudication and an arbitration are not criminal
      prosecutions, so the Sixth Amendment does not reach them, and the forum
      sets its own evidence law: 5 U.S.C. 556(d) admits any oral or documentary
      evidence and leaves exclusion to agency policy. An FRE hearsay gate in
      that posture would be a category error, so G6 is not applied there either.
      The posture drops G6, G8 and G9 and adds G10, which tests the standard the
      forum does apply and whether the party could contest the output.

EXIT CODES
    0  READY       every gate PASS
    1  GAPS        at least one gate contradicted
    1  INCOMPLETE  no contradiction, but at least one gate never addressed
    2  BLIND       the record cannot be read; this is not a pass

SCHEMA (v1)
    {
      "proffer":  {"case": str, "offered_by": str, "purpose": str,
                   "forum": "civil" | "criminal" | "administrative",
                   "ai_assistance_disclosed": bool},
      "witness":  {"kind": "human", "name": str, "qualifications": [str],
                   "reviewed_output": bool},
      "inputs":   {"records": [{"id": str, "source": str, "sha256": hex64}],
                   "sufficiency_statement": str},
      "method":   {"model": {"name": str, "version": str, "provenance": str},
                   "evaluation": {"error_rate": float, "test_set": str},
                   "peer_review": bool, "general_acceptance": str},
      "claims":   [{"id": str, "text": str, "inputs": [str],
                    "citations": [{"locator": str, "verification": str}]}],
      "authentication": {"output_sha256": hex64, "custody": [str],
                         "certification_902_13": {"certifier": str,
                                                  "qualified": bool,
                                                  "notice_given": bool}},
      "hearsay":  {"posture": str, "exception": str, "notice_given": bool,
                   "business_records": {"custodian": str,
                                        "regular_practice": bool}},
      "review":   {"name": str, "authority_to_change": bool,
                   "recorded_at": str, "changes_made": int},
      "confrontation": {"testimonial": bool, "basis": str, "declarant": str,
                        "declarant_available": bool,
                        "prior_opportunity_to_cross": bool,
                        "notice_and_demand_waiver": bool,
                        "surrogate_analyst": bool},
      "administrative": {"standard": str, "standard_source": str,
                         "disclosed_to_opposing_party": bool,
                         "opportunity_to_respond": bool}
    }

    `confrontation` is read only when `proffer.forum` is criminal.
    `administrative` is read only when the forum is administrative or arbitral,
    and in that posture `hearsay` (G6) and `confrontation` (G8, G9) are not read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "esquire-admissibility/3"
VERSION_LABEL = "ESQUIRE-ADMISSIBILITY-v" + SCHEMA_VERSION.rsplit("/", 1)[-1]

PASS = "PASS"
GAP = "GAP"
UNVERIFIABLE = "UNVERIFIABLE"

VERDICT_READY = "READY"
VERDICT_GAPS = "GAPS"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_BLIND = "BLIND"

EXIT_READY = 0
EXIT_GAPS = 1
EXIT_BLIND = 2

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# A citation is only load-bearing if something was actually read. "recalled",
# "model_generated", "unverified" and "fabricated" all land in G4 as gaps.
CITATION_VERIFIED = frozenset({"fetched", "primary_read"})
CITATION_DISQUALIFIED = frozenset({"fabricated", "hallucinated", "invented"})

HEARSAY_POSTURES = frozenset(
    {"not_offered_for_truth", "offered_for_truth_exception", "machine_record"}
)

FORUM_CIVIL = "civil"
FORUM_CRIMINAL = "criminal"
FORUM_ADMINISTRATIVE = "administrative"
CRIMINAL_FORUMS = frozenset(
    {"criminal", "criminal_case", "criminal_proceeding", "criminal_trial"}
)
CIVIL_FORUMS = frozenset({"civil", "civil_case", "civil_proceeding", "civil_trial"})

# A forum that sets its own evidence law: agency adjudication, or arbitration
# under the parties' agreement. Not a criminal prosecution, so the Sixth
# Amendment does not apply; not a court applying the FRE, so G6 does not either.
ADMINISTRATIVE_FORUMS = frozenset(
    {
        "administrative",
        "administrative_case",
        "administrative_proceeding",
        "administrative_hearing",
        "agency",
        "agency_adjudication",
        "regulatory",
        "arbitral",
        "arbitration",
        "arbitration_proceeding",
        "tribunal",
    }
)

DISCLAIMER = (
    "This instrument reports whether a proffer record carries what each "
    "admissibility gate requires. It is not a ruling: admissibility is decided "
    "by a court, and no output of this tool may be pleaded as a determination."
)

GATE_METADATA: tuple[dict[str, str], ...] = (
    {
        "id": "G1",
        "title": "Qualification and disclosure",
        "authority": "FRE 702 (preamble and (a)); Esquire E-1",
        "tier": "A (rule text read at source)",
        "falsifier": "A rule or decision allowing a machine, unnamed, to testify as "
        "a qualified expert in its own right.",
        "negative_control": "A named expert adopting the output, with qualifications "
        "on the record, must pass.",
    },
    {
        "id": "G2",
        "title": "Sufficient facts or data",
        "authority": "FRE 702(b)",
        "tier": "A",
        "falsifier": "An opinion admitted on inputs that were neither preserved nor "
        "identifiable.",
        "negative_control": "A sparse but fully hashed input set must pass; scarcity "
        "is not insufficiency.",
    },
    {
        "id": "G3",
        "title": "Reliable principles and methods",
        "authority": "FRE 702(c); Daubert v. Merrell Dow, 509 U.S. 579 (1993)",
        "tier": "A for the rule; A for the case, not re-fetched today",
        "falsifier": "A model with no version, no evaluation and no documented "
        "method admitted as reliable.",
        "negative_control": "A novel method with no peer review but a measured error "
        "rate must pass: the Daubert factors are non-exhaustive.",
    },
    {
        "id": "G4",
        "title": "Reliable application and citation integrity",
        "authority": "FRE 702(d); Mata v. Avianca, 678 F. Supp. 3d 443 (S.D.N.Y. 2023)",
        "tier": "A for the rule; C for the case (confirmed to exist, not read at source)",
        "falsifier": "An opinion resting on a citation that does not resolve, "
        "admitted without consequence.",
        "negative_control": "An opinion with no legal citations at all (a valuation, "
        "a measurement) must pass; citation integrity is conditional.",
    },
    {
        "id": "G5",
        "title": "Authentication",
        "authority": "FRE 901(a), 901(b)(9); 902(13), 902(14)",
        "tier": "A (rule text read at source)",
        "falsifier": "Machine output admitted with no hash, no custody and no "
        "certification of the process that produced it.",
        "negative_control": "Output authenticated by a 902(13) certification with "
        "notice must pass without any custody chain.",
    },
    {
        "id": "G6",
        "title": "Hearsay posture",
        "authority": "FRE 801, 803(6), 902(13)",
        "tier": "A",
        "falsifier": "Output offered for its truth with no statement of posture and "
        "no exception identified.",
        "negative_control": "Output offered only to show what the system emitted "
        "(not for truth) must pass.",
    },
    {
        "id": "G7",
        "title": "Human review and candor",
        "authority": "Esquire E-1, E-5; ABA Formal Opinion 512 (2024)",
        "tier": "B for the conduct rule (not re-fetched today)",
        "falsifier": "A reviewer with no power to alter the output treated as "
        "satisfying a human-review requirement.",
        "negative_control": "A reviewer who changed nothing but held authority must "
        "pass; the absence of changes is a cross-examination fact, not a defect.",
    },
    {
        "id": "G8",
        "title": "Testimonial character",
        "authority": "U.S. Const. amend. VI; Crawford v. Washington, 541 U.S. 36 "
        "(2004); Melendez-Diaz v. Massachusetts, 557 U.S. 305 (2009)",
        "tier": "A for the amendment; A for both cases, not re-fetched today",
        "applies_to": "criminal posture only",
        "falsifier": "A certificate prepared for litigation, offered in a criminal "
        "case, admitted as non-testimonial because a custodian signed it.",
        "negative_control": "A machine output that never left the regular course of "
        "business, characterised as non-testimonial with a stated basis, must pass.",
    },
    {
        "id": "G9",
        "title": "Declarant and opportunity to confront",
        "authority": "Crawford v. Washington, 541 U.S. 36 (2004); Bullcoming v. "
        "New Mexico, 564 U.S. 647 (2011)",
        "tier": "A for both cases, not re-fetched today",
        "applies_to": "criminal posture only",
        "falsifier": "A surrogate analyst who neither performed nor reviewed the "
        "work permitted to satisfy confrontation by reading a report.",
        "negative_control": "An unavailable declarant who was already cross-examined "
        "on the same statements must pass; unavailability alone is not the defect.",
    },
    {
        "id": "G10",
        "title": "Forum standard, disclosure and response",
        "authority": "5 U.S.C. 556(d)-(e) (text read at source, 2026-11-04); "
        "Mathews v. Eldridge, 424 U.S. 319 (1976)",
        "tier": "A for 556 (statute text read at source); C for Mathews (holding "
        "read as reported, full opinion not read)",
        "applies_to": "administrative and arbitration postures only",
        "falsifier": "An authority holding that an administrative or arbitral "
        "tribunal may rest a sanction on machine output the party was never shown, "
        "or that the record need not identify the standard applied.",
        "negative_control": "An arbitration record naming the agreement's evidentiary "
        "clause, with a disclosed output and a response window, must pass: "
        "arbitration has the least evidence law of any forum and must not be held "
        "to the FRE.",
    },
)


@dataclass
class Finding:
    """One gate's reading of the record."""

    gate: str
    state: str
    detail: str
    notes: list[str] = field(default_factory=list)


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    """Walk nested mappings without raising on a missing or wrongly typed key."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    """Return the non-empty strings from a list, ignoring any other element."""
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.match(value.strip().lower()))


def _cert_902_13(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the 902(13) certification if it is complete, else None."""
    cert = _get(record, "authentication", "certification_902_13")
    if not isinstance(cert, dict):
        return None
    if not _text(cert.get("certifier")):
        return None
    if cert.get("qualified") is not True:
        return None
    if cert.get("notice_given") is not True:
        return None
    return cert


def gate_1_qualification(record: dict[str, Any]) -> Finding:
    """G1: a named natural person, qualified and on the record."""
    witness = record.get("witness")
    if not isinstance(witness, dict):
        return Finding("G1", UNVERIFIABLE, "no witness is identified in the record")

    problems: list[str] = []
    if _text(witness.get("kind")).lower() != "human":
        problems.append("the witness is not identified as a natural person")
    if not _text(witness.get("name")):
        problems.append("the witness is unnamed")
    if not _strings(witness.get("qualifications")):
        problems.append("no qualifications are stated")
    if witness.get("reviewed_output") is not True:
        problems.append("the witness has not reviewed the output")

    if problems:
        return Finding("G1", GAP, "; ".join(problems))
    return Finding("G1", PASS, "named human witness, qualified and reviewing")


def gate_2_inputs(record: dict[str, Any]) -> Finding:
    """G2: inputs preserved, identified, and hashed."""
    inputs = record.get("inputs")
    if not isinstance(inputs, dict):
        return Finding("G2", UNVERIFIABLE, "the record does not describe its inputs")

    records = inputs.get("records")
    if not isinstance(records, list) or not records:
        return Finding("G2", UNVERIFIABLE, "no input records are listed")

    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            problems.append(f"input {index} is not an object")
            continue
        ident = _text(item.get("id"))
        if not ident:
            problems.append(f"input {index} has no id")
        elif ident in seen:
            problems.append(f"input id {ident!r} is duplicated")
        else:
            seen.add(ident)
        if not _text(item.get("source")):
            problems.append(f"input {index} has no source")
        if not _is_sha256(item.get("sha256")):
            problems.append(f"input {index} has no valid sha256")

    if not _text(inputs.get("sufficiency_statement")):
        problems.append("no statement of why the inputs are sufficient")

    if problems:
        return Finding("G2", GAP, "; ".join(problems[:4]))
    return Finding("G2", PASS, f"{len(records)} hashed input record(s), sufficiency stated")


def gate_3_method(record: dict[str, Any]) -> Finding:
    """G3: a versioned, evaluated method. Factors are reported, not required."""
    method = record.get("method")
    if not isinstance(method, dict):
        return Finding("G3", UNVERIFIABLE, "the record does not describe the method")

    problems: list[str] = []
    for key in ("name", "version", "provenance"):
        if not _text(_get(method, "model", key, default="")):
            problems.append(f"model {key} is not stated")

    evaluation = method.get("evaluation")
    if not isinstance(evaluation, dict):
        problems.append("no evaluation is reported")
    else:
        rate = evaluation.get("error_rate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            problems.append("no numeric error rate")
        elif not 0.0 <= float(rate) <= 1.0:
            problems.append("the error rate is outside 0..1")
        if not _text(evaluation.get("test_set")):
            problems.append("the evaluation does not name its test set")

    notes: list[str] = []
    peer = method.get("peer_review")
    if peer is False:
        notes.append("no peer review: a Daubert factor a court may weigh, not a defect")
    acceptance = _text(method.get("general_acceptance")).lower()
    if acceptance in {"novel", "contested"}:
        notes.append(f"general acceptance is {acceptance}: a factor, not a defect")

    if problems:
        return Finding("G3", GAP, "; ".join(problems[:4]), notes)
    return Finding("G3", PASS, "versioned, evaluated method with a measured error rate", notes)


def gate_4_application(record: dict[str, Any]) -> Finding:
    """G4: claims tied to inputs; every citation either verified or a gap."""
    claims = record.get("claims")
    if not isinstance(claims, list) or not claims:
        return Finding("G4", UNVERIFIABLE, "no claims are stated")

    known_inputs = {
        _text(item.get("id"))
        for item in _get(record, "inputs", "records", default=[]) or []
        if isinstance(item, dict) and _text(item.get("id"))
    }

    problems: list[str] = []
    citation_count = 0
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            problems.append(f"claim {index} is not an object")
            continue
        label = _text(claim.get("id")) or f"claim {index}"
        if not _text(claim.get("text")):
            problems.append(f"{label} has no text")
        linked = _strings(claim.get("inputs"))
        if not linked:
            problems.append(f"{label} is not tied to any input")
        for ident in linked:
            if ident not in known_inputs:
                problems.append(f"{label} cites unknown input {ident!r}")

        citations = claim.get("citations")
        if citations is None:
            continue
        if not isinstance(citations, list):
            problems.append(f"{label} citations are not a list")
            continue
        for citation in citations:
            citation_count += 1
            if not isinstance(citation, dict):
                problems.append(f"{label} has a citation that is not an object")
                continue
            locator = _text(citation.get("locator")) or "an unnamed citation"
            if not _text(citation.get("locator")):
                problems.append(f"{label} has a citation with no locator")
            state = _text(citation.get("verification")).lower()
            if state in CITATION_DISQUALIFIED:
                problems.append(
                    f"{locator} is marked {state}: a fabricated authority cannot "
                    "carry an opinion (Mata v. Avianca)"
                )
            elif state not in CITATION_VERIFIED:
                problems.append(
                    f"{locator} is {state or 'unmarked'}: no fetched or "
                    "primary-read verification is recorded"
                )

    if problems:
        return Finding("G4", GAP, "; ".join(problems[:4]))
    detail = f"{len(claims)} claim(s) tied to inputs"
    if citation_count:
        detail += f", {citation_count} citation(s) verified"
    return Finding("G4", PASS, detail)


def gate_5_authentication(record: dict[str, Any]) -> Finding:
    """G5: the output is identified, and its provenance is certified or held."""
    auth = record.get("authentication")
    if not isinstance(auth, dict):
        return Finding(
            "G5", UNVERIFIABLE, "the record does not authenticate the machine output"
        )

    problems: list[str] = []
    if not _is_sha256(auth.get("output_sha256")):
        problems.append("the output has no valid sha256")

    custody = _strings(auth.get("custody"))
    cert = _cert_902_13(record)
    if not custody and cert is None:
        problems.append(
            "neither a custody chain nor a complete 902(13) certification is present"
        )
        incomplete = auth.get("certification_902_13")
        if isinstance(incomplete, dict):
            for key in ("certifier", "qualified", "notice_given"):
                if not incomplete.get(key):
                    problems.append(f"the 902(13) certification lacks {key}")

    if problems:
        return Finding("G5", GAP, "; ".join(problems[:4]))

    if cert is not None:
        return Finding("G5", PASS, "output hashed and certified under FRE 902(13)")
    return Finding("G5", PASS, f"output hashed with a {len(custody)}-step custody chain")


def gate_6_hearsay(record: dict[str, Any]) -> Finding:
    """G6: the record says what the output is offered to prove."""
    hearsay = record.get("hearsay")
    if not isinstance(hearsay, dict):
        return Finding("G6", UNVERIFIABLE, "no hearsay posture is stated")

    posture = _text(hearsay.get("posture")).lower()
    if posture not in HEARSAY_POSTURES:
        return Finding(
            "G6",
            GAP,
            f"posture {posture!r} is not one of {sorted(HEARSAY_POSTURES)}",
        )

    if posture == "not_offered_for_truth":
        return Finding("G6", PASS, "offered for a non-truth purpose")

    if posture == "offered_for_truth_exception":
        problems = []
        if not _text(hearsay.get("exception")):
            problems.append("no exception is identified")
        if hearsay.get("notice_given") is not True:
            problems.append("no notice of the exception is recorded")
        if problems:
            return Finding("G6", GAP, "; ".join(problems))
        return Finding("G6", PASS, f"exception stated: {_text(hearsay.get('exception'))}")

    business = hearsay.get("business_records")
    complete_business = isinstance(business, dict) and bool(
        _text(business.get("custodian"))
    ) and business.get("regular_practice") is True
    if _cert_902_13(record) is not None or complete_business:
        return Finding("G6", PASS, "machine record with a qualifying predicate")
    return Finding(
        "G6",
        GAP,
        "machine record without a 902(13) certification or business-records predicate",
    )


def gate_7_review(record: dict[str, Any]) -> Finding:
    """G7: disclosure, and a reviewer with authority to change the output."""
    proffer = record.get("proffer")
    review = record.get("review")
    problems: list[str] = []
    notes: list[str] = []

    if isinstance(proffer, dict) and proffer.get("ai_assistance_disclosed") is not True:
        problems.append("AI assistance is not disclosed")

    if not isinstance(review, dict):
        if problems:
            return Finding("G7", GAP, "; ".join(problems))
        return Finding("G7", UNVERIFIABLE, "no human review is recorded")

    if not _text(review.get("name")):
        problems.append("the reviewer is unnamed")
    if review.get("authority_to_change") is not True:
        problems.append("the reviewer had no authority to change the output")
    if not _text(review.get("recorded_at")):
        problems.append("the review is undated")

    changes = review.get("changes_made")
    if isinstance(changes, int) and not isinstance(changes, bool) and changes == 0:
        notes.append(
            "the reviewer changed nothing while holding authority: a fact for "
            "cross-examination, not a defect in the record"
        )

    if problems:
        return Finding("G7", GAP, "; ".join(problems[:4]), notes)
    return Finding("G7", PASS, "disclosed, reviewed by an authorised named human", notes)


def gate_8_testimonial(record: dict[str, Any]) -> Finding:
    """G8: whether the output, or a certificate about it, is testimonial."""
    confrontation = record.get("confrontation")
    if not isinstance(confrontation, dict):
        return Finding(
            "G8",
            UNVERIFIABLE,
            "the record does not address whether the output or a certificate "
            "about it is testimonial",
        )

    testimonial = confrontation.get("testimonial")
    cert = _cert_902_13(record)
    notes: list[str] = []
    if cert is not None:
        notes.append(
            "a 902(13) certificate prepared for litigation is itself a testimonial "
            "statement (Melendez-Diaz v. Massachusetts, 557 U.S. 305 (2009)) and "
            "needs its own declarant, not only a custodian's signature"
        )

    if not isinstance(testimonial, bool):
        return Finding(
            "G8",
            GAP,
            "the record addresses confrontation but does not state whether the "
            "output is testimonial",
            notes,
        )

    if testimonial:
        return Finding(
            "G8", PASS, "characterised as testimonial; G9 tests the declarant", notes
        )

    if cert is not None:
        return Finding(
            "G8",
            GAP,
            "the record calls the output non-testimonial while relying on a "
            "902(13) certificate, which is testimonial when prepared for "
            "litigation (Melendez-Diaz)",
            notes,
        )

    basis = _text(confrontation.get("basis"))
    if not basis:
        return Finding(
            "G8",
            GAP,
            "the record asserts the output is not testimonial but states no basis "
            "for that character",
            notes,
        )
    return Finding("G8", PASS, f"characterised as non-testimonial: {basis}", notes)


def gate_9_confrontation(record: dict[str, Any]) -> Finding:
    """G9: a testimonial statement's declarant, and the chance to confront them."""
    confrontation = record.get("confrontation")
    if not isinstance(confrontation, dict):
        return Finding("G9", UNVERIFIABLE, "no confrontation posture is stated")

    testimonial = confrontation.get("testimonial")
    if testimonial is False:
        return Finding(
            "G9",
            PASS,
            "not triggered: the record states the output is non-testimonial (G8)",
        )
    if testimonial is not True:
        return Finding(
            "G9",
            UNVERIFIABLE,
            "the testimonial character of the output is unresolved, so the "
            "declarant analysis cannot be performed",
        )

    declarant = _text(confrontation.get("declarant"))
    problems: list[str] = []
    if not declarant:
        problems.append("a testimonial statement is offered with no named declarant")
    if confrontation.get("surrogate_analyst") is True:
        problems.append(
            "the certifying witness is a surrogate who neither performed nor "
            "reviewed the work (Bullcoming v. New Mexico, 564 U.S. 647 (2011))"
        )
    if problems:
        return Finding("G9", GAP, "; ".join(problems[:3]))

    satisfiers: list[str] = []
    if confrontation.get("declarant_available") is True:
        satisfiers.append("available for cross-examination at trial")
    if confrontation.get("prior_opportunity_to_cross") is True:
        satisfiers.append("already cross-examined on the same statements")
    if confrontation.get("notice_and_demand_waiver") is True:
        satisfiers.append(
            "defence notice-and-demand waiver recorded; the demand window is a "
            "fact to be established, not assumed"
        )
    if satisfiers:
        return Finding("G9", PASS, f"declarant {declarant}: " + "; ".join(satisfiers))

    if confrontation.get("declarant_available") is False:
        return Finding(
            "G9",
            GAP,
            f"declarant {declarant} is unavailable and the record shows no prior "
            "opportunity to cross-examine (Crawford v. Washington, 541 U.S. 36 (2004))",
        )
    return Finding(
        "G9",
        UNVERIFIABLE,
        f"declarant {declarant} is named, but availability and any prior "
        "cross-examination are not stated",
    )


def gate_10_forum_standard(record: dict[str, Any]) -> Finding:
    """G10: the forum's own evidentiary standard, and a chance to contest it.

    An agency adjudication admits any oral or documentary evidence and leaves
    exclusion to agency policy (5 U.S.C. 556(d)); the sanction must rest on
    "reliable, probative, and substantial evidence" on the whole record, a party
    may submit rebuttal and cross-examine as the facts require, and a party is
    entitled on timely request to show the contrary of a fact noticed outside
    the record (556(e)). So the question is not which FRE exception applies but
    whether the record names the standard that does, and whether the output
    could be contested at all.
    """
    admin = record.get("administrative")
    if not isinstance(admin, dict):
        return Finding(
            "G10",
            UNVERIFIABLE,
            "the record does not state the evidentiary standard this forum applies "
            "to machine output",
        )

    standard = _text(admin.get("standard"))
    source = _text(admin.get("standard_source"))
    gaps: list[str] = []
    unstated: list[str] = []

    if not standard:
        unstated.append("the forum's evidentiary standard is not stated")
    elif not source:
        unstated.append(
            f"the standard {standard!r} is named without the rule, agreement or "
            "guidance it comes from"
        )

    disclosed = admin.get("disclosed_to_opposing_party")
    if disclosed is False:
        gaps.append(
            "the output was not disclosed to the opposing party, so there is "
            "nothing to contest"
        )
    elif disclosed is not True:
        unstated.append("disclosure of the output to the opposing party is not stated")

    respond = admin.get("opportunity_to_respond")
    if respond is False:
        gaps.append(
            "the record shows no opportunity to respond to the output "
            "(Mathews v. Eldridge, 424 U.S. 319 (1976))"
        )
    elif respond is not True:
        unstated.append("an opportunity to respond to the output is not stated")

    if gaps:
        return Finding("G10", GAP, "; ".join((gaps + unstated)[:4]))
    if unstated:
        return Finding("G10", UNVERIFIABLE, "; ".join(unstated[:3]))

    return Finding(
        "G10",
        PASS,
        f"standard {standard!r} from {source!r}, with disclosure and a chance to "
        "respond",
        [
            "this forum sets its own evidence law: the FRE hearsay gate (G6) and "
            "the confrontation gates (G8, G9) are not applied in this posture"
        ],
    )


CIVIL_GATES = (
    gate_1_qualification,
    gate_2_inputs,
    gate_3_method,
    gate_4_application,
    gate_5_authentication,
    gate_6_hearsay,
    gate_7_review,
)

CRIMINAL_GATES = CIVIL_GATES + (gate_8_testimonial, gate_9_confrontation)

# A forum that sets its own evidence law keeps the seven universal gates except
# G6, whose question (which FRE hearsay route applies) has no referent there, and
# adds G10 for the standard the forum does apply.
ADMINISTRATIVE_GATES = (
    gate_1_qualification,
    gate_2_inputs,
    gate_3_method,
    gate_4_application,
    gate_5_authentication,
    gate_7_review,
    gate_10_forum_standard,
)

# The universal set, kept as the name the document and the audit ledger cite.
GATES = CIVIL_GATES


def forum_of(record: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve the forum, and say in the report how it was resolved.

    A record that states no forum is read as civil. That default is printed: a
    missing key must not silently remove the confrontation gates.
    """
    stated = _text(_get(record, "proffer", "forum")).lower()
    if stated in CRIMINAL_FORUMS:
        return (
            FORUM_CRIMINAL,
            "stated",
            f"forum stated as {stated!r}: confrontation gates G8 and G9 apply",
        )
    if stated in ADMINISTRATIVE_FORUMS:
        return (
            FORUM_ADMINISTRATIVE,
            "stated",
            f"forum stated as {stated!r}, read under the administrative posture: "
            "the forum sets its own evidence law, so G6 (FRE hearsay) and G8/G9 "
            "(Sixth Amendment confrontation) are NOT applied, and G10 tests the "
            "forum's own standard instead",
        )
    if not stated:
        return (
            FORUM_CIVIL,
            "unstated",
            "the record states no forum, so it is read as civil and confrontation "
            "gates G8 and G9 were NOT applied",
        )
    if stated in CIVIL_FORUMS:
        return (
            FORUM_CIVIL,
            "stated",
            f"forum stated as {stated!r}: confrontation gates G8 and G9 do not apply",
        )
    return (
        FORUM_CIVIL,
        "unrecognised",
        f"forum {stated!r} is not a recognised value, so it is read as civil and "
        "confrontation gates G8 and G9 were NOT applied; recognised criminal "
        f"values are {sorted(CRIMINAL_FORUMS)}, and recognised administrative "
        f"values are {sorted(ADMINISTRATIVE_FORUMS)}",
    )


def applicable_gates(forum: str) -> tuple[Any, ...]:
    """The gate set for a forum. Civil keeps the seven universal gates."""
    if forum == FORUM_CRIMINAL:
        return CRIMINAL_GATES
    if forum == FORUM_ADMINISTRATIVE:
        return ADMINISTRATIVE_GATES
    return CIVIL_GATES


def canonical(record: Any) -> str:
    """Stable serialisation, so the record hash does not depend on key order."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def record_sha256(record: Any) -> str:
    return hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()


def evaluate(record: Any) -> dict[str, Any]:
    """Read a proffer record and return a report. Never raises on bad input."""
    if not isinstance(record, dict) or not isinstance(record.get("proffer"), dict):
        return {
            "schema": SCHEMA_VERSION,
            "verdict": VERDICT_BLIND,
            "reason": "the record is not an object with a 'proffer' section",
            "gates": [],
            "gates_applied": [],
            "counts": {PASS: 0, GAP: 0, UNVERIFIABLE: 0},
            "disclaimer": DISCLAIMER,
        }

    forum, forum_source, forum_note = forum_of(record)
    findings = [gate(record) for gate in applicable_gates(forum)]
    counts = {PASS: 0, GAP: 0, UNVERIFIABLE: 0}
    for finding in findings:
        counts[finding.state] += 1

    if counts[GAP]:
        verdict = VERDICT_GAPS
    elif counts[UNVERIFIABLE]:
        verdict = VERDICT_INCOMPLETE
    else:
        verdict = VERDICT_READY

    return {
        "schema": SCHEMA_VERSION,
        "verdict": verdict,
        "case": _text(_get(record, "proffer", "case")),
        "forum": forum,
        "forum_source": forum_source,
        "forum_note": forum_note,
        "record_sha256": record_sha256(record),
        "gates": [
            {
                "gate": finding.gate,
                "state": finding.state,
                "detail": finding.detail,
                "notes": finding.notes,
            }
            for finding in findings
        ],
        "gates_applied": [finding.gate for finding in findings],
        "counts": counts,
        "disclaimer": DISCLAIMER,
    }


def render(report: dict[str, Any]) -> str:
    """One screen: verdict, then one line per gate, then notes."""
    verdict = report["verdict"]
    lines = [f"{VERSION_LABEL}  {verdict}"]
    case = report.get("case")
    if case:
        lines.append(f"case: {case}")
    if verdict == VERDICT_BLIND:
        lines.append(f"reason: {report.get('reason', 'unreadable record')}")
        lines.append(DISCLAIMER)
        return "\n".join(lines)

    lines.append(f"forum: {report['forum']} ({report['forum_source']})")
    lines.append(f"forum note: {report['forum_note']}")
    counts = report["counts"]
    lines.append(
        f"gates: {counts[PASS]} pass, {counts[GAP]} gap, "
        f"{counts[UNVERIFIABLE]} unaddressed, over {len(report['gates_applied'])} applied"
    )
    lines.append("")
    for gate in report["gates"]:
        lines.append(f"{gate['gate']}  {gate['state']:<12} {gate['detail']}")
        for note in gate["notes"]:
            lines.append(f"                          note: {note}")
    lines.append("")
    lines.append(f"record sha256: {report['record_sha256']}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def render_gates() -> str:
    """The gate table, for audit and for the document that cites it."""
    lines = [f"{VERSION_LABEL} gate table", ""]
    for meta in GATE_METADATA:
        lines.append(f"{meta['id']}  {meta['title']}")
        lines.append(f"    applies to:       {meta.get('applies_to', 'any forum')}")
        lines.append(f"    authority:        {meta['authority']}")
        lines.append(f"    evidence tier:    {meta['tier']}")
        lines.append(f"    falsifier:        {meta['falsifier']}")
        lines.append(f"    negative control: {meta['negative_control']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def load_record(path: str) -> Any:
    """Read JSON from a path, or from stdin for '-'."""
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Test a proffer record against the Esquire admissibility gates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes: 0 READY, 1 GAPS or INCOMPLETE, 2 BLIND.\n"
            "This instrument never reports a record as admissible or inadmissible."
        ),
    )
    parser.add_argument("record", nargs="?", help="proffer JSON path, or '-' for stdin")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--gates", action="store_true", help="print the gate table and exit"
    )
    args = parser.parse_args(argv)

    if args.gates:
        print(render_gates())
        return EXIT_READY

    if not args.record:
        parser.error("a record path is required unless --gates is used")

    try:
        record = load_record(args.record)
    except (OSError, json.JSONDecodeError) as error:
        report = {
            "schema": SCHEMA_VERSION,
            "verdict": VERDICT_BLIND,
            "reason": f"{type(error).__name__}: {error}",
            "gates": [],
            "gates_applied": [],
            "counts": {PASS: 0, GAP: 0, UNVERIFIABLE: 0},
            "disclaimer": DISCLAIMER,
        }
        print(json.dumps(report, indent=2) if args.json else render(report))
        return EXIT_BLIND

    report = evaluate(record)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))

    if report["verdict"] == VERDICT_BLIND:
        return EXIT_BLIND
    if report["verdict"] == VERDICT_READY:
        return EXIT_READY
    return EXIT_GAPS


if __name__ == "__main__":
    raise SystemExit(main())
