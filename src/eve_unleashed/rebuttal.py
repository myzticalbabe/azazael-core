"""rebuttal.py -- counter-myth generation with receipts attached.

A REFUTED or uncorroborated claim left standing keeps working.
A rebuttal turns a debunk/factcheck result into a citable
correction paragraph: what was claimed, what the words themselves
show, what the live record shows, what stays opinion -- with every
receipt hash attached so the correction can be checked the way the
claim was checked.

Eve drafts; only you publish, and only under your name. A rebuttal
names claims, never people: no ad-hominem in, none out.
"""
import time

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass


def draft_rebuttal(factcheck_result):
    """Draft a correction paragraph from a factcheck_claim result."""
    if not isinstance(factcheck_result, dict) or "verdict" not in factcheck_result:
        raise ValueError("not a factcheck result: need verdict + receipts")
    deb = factcheck_result.get("debunk", {})
    lines = []
    lines.append("CORRECTION -- %s" % time.strftime(
        "%Y-%m-%d", time.gmtime()))
    lines.append("Claim: %s" % factcheck_result.get("claim", "")[:300])
    lines.append("Finding: %s." % factcheck_result.get("verdict", "?"))
    for flag in deb.get("fallacy_flags", []):
        lines.append("The claim's own words show %s (%s)." % (
            flag.get("flag", "?").replace("_", " "),
            ", ".join(flag.get("matched", []))))
    for contra in deb.get("contradictions", [])[:2]:
        lines.append("It contradicts itself here: %s" % contra[:200])
    found = [lk for lk in factcheck_result.get("entity_lookups", [])
             if lk.get("found")]
    if found:
        lines.append("The live record confirms: %s." % "; ".join(
            "%s -- %s" % (lk.get("topic", "?"),
                          (lk.get("description") or lk.get("title") or ""))
            for lk in found[:4]))
    missing = [lk.get("topic", "?") for lk in
               factcheck_result.get("entity_lookups", [])
               if not lk.get("found")]
    if missing:
        lines.append("The live record does not confirm: %s." % ", ".join(
            missing[:4]))
    for r in factcheck_result.get("receipts", [])[:6]:
        if r.get("status") == "ok":
            lines.append("Seen %s -- %d bytes, sha256 %s." % (
                r.get("fetched_utc", "?"), r.get("bytes", 0),
                (r.get("sha256") or "")[:16]))
    opinion = factcheck_result.get("opinion", "")
    if opinion:
        lines.append("%s" % opinion)
    lines.append("Compiled by Eve; publish only under your own name, "
                 "after counsel review for filing contexts.")
    return "\n".join(lines)
