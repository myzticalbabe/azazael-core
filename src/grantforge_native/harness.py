"""The agent harness: what an AI client is *pushed* to do, derived from live seams.

**Why a module and not a README.** A document that tells a model how to use a tool is
read once, if at all, and drifts silently from the tool. This build exposes the same
instructions as data: `grantforge harness --json` on the CLI and `grantforge_harness`
over MCP. Both are generated from the running build's own tables — `grants.RULES`,
`mcp.TOOLS`, `errors.ERRORS`/`errors.NEXT`, the exit contract — so an agent that calls
it cannot be handed a stale harness, and a new error code without a next action fails
a test instead of shipping a hole.

The push is a **sequence with stopping conditions**, not a list of capabilities:
an agent that knows which call comes next, and when to stop, is harnessed; an agent
holding a tool catalogue is merely equipped. Each step therefore carries `when`,
`stop_if` and the evidence it must quote back.

Interop rules this encodes (each bought by a real defect in this build's history):

* a verdict without a verbatim, located quote is `unjudged` — never "likely";
* a quote taken from the criterion text is not evidence (the question is ours);
* `BLIND` (exit 2) is a result, not a pass, and not a failure of the applicant;
* custody is append-only; a case is re-verified by recomputation, never by trust.
"""
from __future__ import annotations

from . import __version__, errors, grants, mcp

EXIT_CODES = {
    "0": "the command ran and produced its result (a review may still report findings)",
    "1": "a finding: review failed the proposal, or a custody chain is broken",
    "2": "BLIND: the RFP carried no criterion this build could read — not a pass",
    "3": "the local model was unreachable or answered unusably",
    "4": "an input could not be read as JSON",
}

INVARIANTS = (
    "a model verdict counts only with a verbatim quote located in the RFP's own text; "
    "otherwise the row is `unjudged`",
    "a quote that appears only in the criterion/question block is rejected: it proves "
    "the question was asked, not that the RFP decides",
    "sections belong to the RFP; drafting an undeclared section is refused by name",
    "custody is append-only: `case init` refuses to overwrite, `case verify` recomputes",
    "a failure never exits 0, and BLIND is never reported as a pass",
)


def _step(number, call, when, stop_if, evidence):
    return {"step": number, "call": call, "when": when, "stop_if": stop_if,
            "return_evidence": evidence}


def push_sequence():
    """The ordered calls an agent should make, with the condition that ends each."""
    return [
        _step(1, "grantforge_doctor",
              "always first: the instrument must be live before it judges anything",
              "stop if `ok` is false — report `[model_unreachable]` and its remedy; do "
              "not proceed to a model-judged call",
              "`ok`, `base`, `model`, `roundtrip_ms`"),
        _step(2, "grantforge_capabilities",
              "before planning any wider loop: the seams define what may be added",
              "never stop here; this is how an agent avoids re-deriving the build",
              "rules, tools, error_codes, seams"),
        _step(3, "grantforge_sections",
              "once an RFP is in hand, before drafting or reviewing",
              "stop and report the `unbounded` list if the section you were asked to "
              "draft is not declared — do not invent a section set",
              "per-section `state` (bounded/unbounded/unreadable) and `encodable`"),
        _step(4, "grantforge_assess",
              "after doctor is ok and the RFP's sections are known",
              "stop if `status` is BLIND — say so with exit 2's meaning; continue while "
              "`status` is GAPS only by naming what stayed unjudged",
              "`status`, per-row `verdict` and `pin` (`kind`, `quote`, `offset`)"),
        _step(5, "grantforge_draft",
              "only for sections the index declared, and only after GAPS are known",
              "stop if the draft returns `unsupported` spans — report them; do not "
              "silently rewrite the prose",
              "`unsupported` list, word count against the declared limit, `seal`"),
        _step(6, "grantforge_review",
              "after a draft exists, before anything is sent",
              "stop on `ok: false` and return the findings verbatim to the operator",
              "findings, `ok`, `seal`"),
        _step(7, "grantforge_verify",
              "last: re-check a seal or a custody chain you are about to rely on",
              "stop if the digest disagrees — the artefact changed after it was sealed",
              "recomputed digest vs sealed digest, chain `entries` and `head`"),
    ]


def contract():
    """The whole harness as one JSON object: sequence, codes, recovery, invariants."""
    return {
        "product": "grantforge",
        "version": __version__,
        "role": ("a local-model grant instrument an agent drives: assess, draft, review, "
                 "verify — every judgement re-checkable from the RFP text"),
        "sequence": push_sequence(),
        "exit_codes": EXIT_CODES,
        "on_error": {code: {"meaning": errors.ERRORS[code][0],
                           "remedy": errors.ERRORS[code][1],
                           "next": errors.NEXT[code]} for code in sorted(errors.ERRORS)},
        "invariants": list(INVARIANTS),
        "custody": {
            "init": "grantforge case init <dir> <rfp> <profile> — hashes each input as read",
            "record": "assess/draft --case <dir> — every result enters the chain",
            "verify": "grantforge case verify <dir> — recomputes; first broken link named",
            "append_only": "case init refuses an existing case rather than resetting a chain",
        },
        "tools": [{"name": tool["name"], "when": tool["description"]}
                  for tool in mcp.TOOLS],
        "rules": [rule.__name__.lstrip("_") for rule in grants.RULES],
        "ledger": {"path": str(errors.ledger_path()),
                   "last_write_error": errors.LAST_WRITE_ERROR,
                   "read": "grantforge errors --json"},
    }
