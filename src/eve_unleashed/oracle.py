"""oracle.py -- native oracles: several independent signals, one verdict.

An oracle that asks one source is a mirror. This module asks the
signals Eve already owns -- text analysis (debunk), live entity
corroboration (factcheck), the filed record (grievances), and the
evidence locker (custody) -- then reports what they agree on and
where they part company.

Agreement is measured, not asserted: the verdict carries
`agreeing`, `disagreeing`, and a `confidence` computed from the
counts, so two signals in agreement read differently from one
signal speaking alone. A tie is reported as a tie.

Oracle does not replace judgement; it makes the disagreement
visible before a human commits to one reading.
"""
import time

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass


def _text_signal(claim):
    from .debunk import debunk_claim
    d = debunk_claim(claim)
    return {"signal": "text", "verdict": d["verdict"],
            "detail": "%d proposition(s), %d flag(s)" % (
                len(d["propositions"]), len(d["fallacy_flags"])),
            "refutes": d["verdict"] == "REFUTED"}


def _live_signal(claim):
    from .factcheck import factcheck_claim
    f = factcheck_claim(claim)
    return {"signal": "live", "verdict": f["verdict"],
            "detail": "%d entity(ies), %d receipt(s)" % (
                f.get("entities_checked", 0), len(f["receipts"])),
            "refutes": f["verdict"] in ("REFUTED", "UNVERIFIABLE")}


def _record_signal(claim, grievance_id):
    if not grievance_id:
        return {"signal": "record", "verdict": "SKIPPED",
                "detail": "no grievance named", "refutes": False}
    from .redress import list_grievances
    recs = [r for r in list_grievances() if r["id"] == grievance_id]
    if not recs:
        return {"signal": "record", "verdict": "ABSENT",
                "detail": "no such filed grievance", "refutes": False}
    hay = (recs[0].get("narrative", "") + " "
           + recs[0].get("respondent", "")).lower()
    words = [w for w in claim.lower().split() if len(w) >= 4]
    shared = [w for w in words if w in hay]
    return {"signal": "record", "verdict": "FOUND" if recs else "ABSENT",
            "detail": "%d term(s) shared with the filing" % len(set(shared)),
            "refutes": False}


def consult(claim, grievance_id=None, locker_ids=None):
    """Ask every oracle. Returns the verdict, the signals, the split."""
    if not claim or not claim.strip():
        raise ValueError("nothing to ask: empty claim")
    signals = []
    for fn in (_text_signal, _live_signal):
        try:
            signals.append(fn(claim))
        except Exception as e:
            note_failure("oracle:%s" % fn.__name__, e)
            signals.append({"signal": fn.__name__.strip("_"),
                            "verdict": "ERROR",
                            "detail": str(e)[:160], "refutes": False})
    try:
        signals.append(_record_signal(claim, grievance_id))
    except Exception as e:
        note_failure("oracle:record", e)
        signals.append({"signal": "record", "verdict": "ERROR",
                        "detail": str(e)[:160], "refutes": False})
    if locker_ids:
        from .evidence import verify
        states = []
        for lid in locker_ids:
            try:
                states.append(verify(lid)["state"])
            except Exception as e:
                note_failure("oracle:locker", e)
                states.append("ERROR")
        signals.append({"signal": "locker",
                        "verdict": "INTACT" if states
                        and all(s == "INTACT" for s in states) else "TAMPERED",
                        "detail": "%d/%d INTACT" % (
                            sum(1 for s in states if s == "INTACT"),
                            len(states)),
                        "refutes": False})
    live = [s for s in signals if s["verdict"] not in ("SKIPPED", "ERROR")]
    refuting = [s for s in live if s["refutes"]]
    support = [s for s in live if not s["refutes"]]
    if refuting:
        verdict = "REFUSED"
    elif not live:
        verdict = "BLIND"
    elif len(support) == len(live):
        verdict = "SUPPORTED"
    else:
        verdict = "SPLIT"
    total = len(live) or 1
    return {"claim": claim, "asked_utc": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "signals": signals, "verdict": verdict,
        "agreeing": len(support), "disagreeing": len(refuting),
        "confidence": round(len(support) / total, 2) if live else 0.0,
        "note": ("A signal that was skipped or errored is named, never "
                 "counted as support.")}
