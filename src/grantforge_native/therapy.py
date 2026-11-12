"""Live therapy: the diagnosis turned into a treatment plan the operator can act on.

**Why a separate verb.** `assess` says what is true, `review` says what is wrong, and an
operator holding both still has to work out what to *do*. That translation is the part
that gets skipped, so it is the part that becomes a button: `grantforge therapy`
re-runs the live checks and returns, per defect, a cause, a prescription, the evidence
the prescription needs, and the command that will re-check it.

"Live" means both halves run now, against the RFP and profile in hand — not a stored
plan from a previous session. It is one call because the value is in the coupling: a
missing section and an unjudged criterion are different diseases, and a plan that lists
them without ordering them by what blocks the submission is a list, not a treatment.

Prescriptions are **deterministic**, keyed by the finding's own code. The model is not
asked what to do about a defect: a model that invents a prescription is a second
opinion dressed as an instruction. Where a model helps, it helps the same way it does
everywhere else in this build — inside `assess`, behind a verbatim quote.

Every finding carries `recheck`, so therapy is falsifiable: run the prescription, then
run the command, and the finding either clears or it does not. A plan whose progress
cannot be checked is a mood, not a treatment.
"""
from __future__ import annotations

from . import grants

# code -> (cause, prescription, evidence_needed). Keyed by the finding's own code so a
# new check cannot appear in a plan without a treatment; `test_every_review_code_has_a
# _treatment` fails if one does.
TREATMENTS = {
    "MISSING_SECTION": (
        "the RFP requires this section and the proposal does not contain it",
        "write the section against the RFP's stated purpose; do not reuse text from "
        "another section, and stay within the declared word limit",
        "the section body, its word count, and the RFP sentence that defines it",
    ),
    "EMPTY_SECTION": (
        "the section key exists with no text, which reads as complete to a skimmer",
        "fill it or remove the key; an empty section is worse than an absent one because "
        "it looks answered",
        "non-empty prose, or the key deleted",
    ),
    "OVER_LIMIT": (
        "the section exceeds the RFP's word limit, which some funders truncate silently",
        "cut to the limit by removing restatement, not evidence; keep every number that "
        "already has a source",
        "a word count at or under the declared limit",
    ),
    "PLACEHOLDER": (
        "an unfilled template token survived into the draft",
        "replace the token with the real value or delete the clause that needed it",
        "no bracketed tokens in the section text",
    ),
    "UNSUPPORTED_NUMBER": (
        "a number in the draft has no counterpart in the RFP, profile or references",
        "attach the source, or restate the claim without the figure; an unsourced number "
        "is the first thing a reviewer checks",
        "a citation for the figure, or the figure removed",
    ),
    "UNKNOWN_SECTION": (
        "the proposal carries a section the RFP never asked for",
        "move the content into a required section, or drop it; unrequested sections cost "
        "reviewer attention and can trip page limits",
        "the section set matching the RFP",
    ),
}

# Assess statuses -> (cause, prescription, evidence_needed).
STATUS_TREATMENTS = {
    "ineligible": (
        "a rule decided this criterion fails on the facts supplied",
        "do not spend drafting effort here: confirm the fact, or change mechanism; a "
        "rule-decided failure is not a wording problem",
        "the corrected profile value, or an RFP clause that changes the test",
    ),
    "unjudged": (
        "nothing readable decided this criterion — no model, or the model's quote did "
        "not appear in the RFP's own text",
        "add the RFP sentence that decides the criterion (or start the model) and re-run; "
        "do not treat an unjudged row as a pass",
        "a verbatim RFP sentence that decides the question",
    ),
    "BLIND": (
        "no criterion in the RFP could be read by this build",
        "check the RFP shape against `grantforge demo --json`; if the criteria really are "
        "absent, the funder's guidance must be read by a human",
        "an RFP with readable criteria, or a human decision recorded",
    ),
}

_NEXT_CALL = {
    "HEALTHY": "grantforge review <proposal.json> <rfp.json> --json",
    "TREATABLE": "apply the first prescription, then run its `recheck`",
    "BLIND": "grantforge sections <rfp.json> --json",
}


def _finding(code, where, severity, treatment, recheck, detail=""):
    cause, prescription, evidence = treatment
    return {"code": code, "where": where, "severity": severity, "cause": cause,
            "prescription": prescription, "evidence_needed": evidence,
            "recheck": recheck, "detail": detail}


def diagnose(rfp, profile, proposal=None, llm=None, today=None):
    """Run the live checks and return an ordered treatment plan."""
    assessment = grants.assess(rfp, profile, llm=llm, today=today)
    findings = []

    status = assessment["status"]
    if status == "BLIND":
        findings.append(_finding("BLIND", "rfp", "error", STATUS_TREATMENTS["BLIND"],
                                 "grantforge assess <rfp> <profile> --offline --json",
                                 "status BLIND"))
    for row in assessment["rows"]:
        if row["verdict"] == "eligible":
            continue
        treatment = STATUS_TREATMENTS.get(row["verdict"])
        if treatment is None:
            continue
        findings.append(_finding(row["verdict"], row.get("criterion", "criterion"),
                                 "warn", treatment,
                                 "grantforge assess <rfp> <profile> --json",
                                 row.get("evidence", "")))

    review_report = None
    if proposal is not None:
        review_report = grants.review(proposal, rfp, profile)
        for item in review_report["findings"]:
            treatment = TREATMENTS.get(item["code"])
            if treatment is None:
                # A review code without a treatment is a hole in this module, not a
                # finding to skip silently: carry it as an unclassified prescription.
                findings.append(_finding(item["code"], item.get("section") or "proposal",
                                         item["severity"],
                                         ("no treatment is registered for %s" % item["code"],
                                          "add an entry to therapy.TREATMENTS",
                                          "a prescription and its recheck command"),
                                         "grantforge review <proposal> <rfp> --json",
                                         item.get("detail", "")))
                continue
            findings.append(_finding(item["code"], item.get("section") or "proposal",
                                     item["severity"], treatment,
                                     "grantforge review <proposal> <rfp> --json",
                                     item.get("detail", "")))

    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda row: (order.get(row["severity"], 3), row["where"]))
    if status == "BLIND":
        prognosis = "BLIND"
    elif findings:
        prognosis = "TREATABLE"
    else:
        prognosis = "HEALTHY"
    return {
        "rfp_id": assessment["rfp_id"],
        "prognosis": prognosis,
        "status": status,
        "verdict": assessment["verdict"],
        "findings": findings,
        "counts": {"error": sum(1 for f in findings if f["severity"] == "error"),
                   "warn": sum(1 for f in findings if f["severity"] == "warn")},
        "next_call": _NEXT_CALL[prognosis],
        "engine": assessment["engine"],
        "review": None if review_report is None else {
            "ok": review_report["ok"], "errors": review_report["errors"],
            "warnings": review_report["warnings"]},
    }
