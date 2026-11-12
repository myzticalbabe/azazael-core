"""inquest.py -- justice-grade forensic investigation dossier.

A grievance plus a bundle plus receipts plus custody touches is
still four piles. An inquest binds them into one dossier for one
grievance: the record, the machine checks, the locker custody for
every cited item, the live receipts, and the timeline in order.

Order of the timeline is by stamp, earliest first; anything
without a stamp sorts last and is labeled UNDATED rather than
placed. Counts are computed, never asserted: passed/total on the
checks present, intact/total on the custody states seen.

Eve investigates the record; it accuses no person. The dossier's
last line names its own limits, because a dossier that cannot say
what it is not is a brief, not an investigation.
"""
import time

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass


def open_inquest(grievance_id, locker_ids=None, factcheck_results=None):
    """Compile the dossier. Returns the dossier dict."""
    from . import evidence as _ev
    from .redress import prove_case
    locker_ids = locker_ids or []
    factcheck_results = factcheck_results or []
    try:
        bundle = prove_case(grievance_id)
    except ValueError as e:
        raise ValueError("inquest refused: %s" % e)
    custody_states = []
    for lid in locker_ids:
        try:
            v = _ev.verify(lid)
            touches = _ev.custody(lid)
            custody_states.append({"id": lid, "state": v.get("state"),
                                   "touches": len(touches)})
        except Exception as e:
            note_failure("inquest:custody", e)
            custody_states.append({"id": lid, "state": "ERROR",
                                   "touches": 0})
    receipts = []
    for fc in factcheck_results:
        if isinstance(fc, dict):
            receipts.extend(fc.get("receipts", []))
    timeline = []
    rec = bundle.get("grievance", {})
    if rec.get("filed_utc"):
        timeline.append((rec["filed_utc"], "grievance filed: %s"
                             % rec.get("id", "?")))
    for m in bundle.get("evidence_manifest", []):
        timeline.append(("manifest", "evidence %s present=%s" % (
            m.get("path", "?"), m.get("present", False))))
    for r in receipts:
        if r.get("status") == "ok" and r.get("fetched_utc"):
            timeline.append((r["fetched_utc"], "live receipt: %s"
                                 % (r.get("url", "?")[:80])))
    stamped = sorted([t for t in timeline if not t[0] == "manifest"])
    undated = [t for t in timeline if t[0] == "manifest"]
    checks = bundle.get("checks", [])
    passed = sum(1 for c in checks if c.get("passed"))
    intact = sum(1 for c in custody_states if c.get("state") == "INTACT")
    return {
        "grievance": grievance_id,
        "opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "record_checks": "%d/%d passed" % (passed, len(checks)),
        "custody": "%d/%d INTACT" % (intact, len(custody_states)),
        "custody_detail": custody_states,
        "live_receipts": len([r for r in receipts
                              if r.get("status") == "ok"]),
        "timeline": [{"stamp": s, "event": e}
                     for s, e in stamped + [("UNDATED", e) for _, e in
                                             undated]],
        "judgment": bundle.get("judgment", ""),
        "opinion": bundle.get("opinion", ""),
        "limits": ("This dossier covers the record, the locker, and the "
                   "receipts named above -- nothing else. It accuses no "
                   "person and files nothing. Cross-checks beyond these "
                   "sources were not run."),
    }
