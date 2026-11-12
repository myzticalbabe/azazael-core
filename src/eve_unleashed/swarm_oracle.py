"""swarm_oracle.py -- native swarms over oracles.

One claim is an oracle question; a hundred claims is a swarm. This
module fans claims across a bounded pool of workers and returns
per-claim verdicts plus the aggregate split, so a body of claims is
read the way a single claim is: what agreed, what parted, and what
could not be asked.

Bounded by design -- the no-timeout broadcast experiment hung a
session for over a hundred seconds, so every worker here is an
explicit pool task with `shutdown(wait=False)` on the way out, and
the pool size is capped by the caller's own argument. A swarm that
can outlive its turn is not a swarm; it is a leak.

Live signals cost network calls, so `live=False` runs the text and
record oracles only. The default is conservative: text plus record,
because a swarm that silently hits the network a hundred times is
an outage in waiting.
"""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

MAX_WORKERS = 8
PER_CLAIM_TIMEOUT = 120


def _one(claim, grievance_id, live):
    from .oracle import consult
    if live:
        return consult(claim, grievance_id)
    from .debunk import debunk_claim
    d = debunk_claim(claim)
    return {"claim": claim, "verdict": d["verdict"],
            "signals": [{"signal": "text", "verdict": d["verdict"],
                         "detail": "%d flag(s)" % len(d["fallacy_flags"]),
                         "refutes": d["verdict"] == "REFUTED"}],
            "agreeing": 1 if d["verdict"] != "REFUTED" else 0,
            "disagreeing": 1 if d["verdict"] == "REFUTED" else 0,
            "confidence": 0.0 if d["verdict"] == "REFUTED" else 1.0,
            "note": "text oracle only (live=False)"}


def swarm(claims, grievance_id=None, live=False, workers=4):
    """Fan claims across a bounded pool. Returns results + aggregate."""
    claims = [c for c in (claims or []) if c and str(c).strip()]
    if not claims:
        raise ValueError("nothing to swarm: no claims")
    workers = max(1, min(int(workers), MAX_WORKERS, len(claims)))
    results, failures = [], []
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(_one, c, grievance_id, live): c
                   for c in claims}
        for fut, claim in futures.items():
            try:
                results.append(fut.result(timeout=PER_CLAIM_TIMEOUT))
            except FuturesTimeout as e:
                note_failure("swarm_oracle:timeout", e)
                failures.append({"claim": claim,
                                 "error": "timeout after %ds"
                                          % PER_CLAIM_TIMEOUT})
            except Exception as e:
                note_failure("swarm_oracle:claim", e)
                failures.append({"claim": claim,
                                 "error": str(e)[:160]})
    finally:
        pool.shutdown(wait=False)
    tally = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    return {"claims": len(claims), "answered": len(results),
            "failed": failures, "tally": tally,
            "workers": workers, "live": live,
            "results": results,
            "note": ("Every failed claim is named with its error. A "
                     "swarm that dropped work silently would report a "
                     "smaller world than the one it was given.")}
