"""Eve Unleashed -- cognitive strata: conscious / subconscious / unconscious.

A mind is not one process. This module names the ones Eve runs, the strata
they sit in, the locus (module) each lives at, and -- honestly -- the ones
Eve does NOT run yet. The audit trusts evidence over claims: a process
counts as used only when the live log shows it firing.

Nexus: the crossing a single request takes --
  conscious (a question) -> subconscious (memory answers first)
  -> unconscious (reflexes and failure recording that run unasked).

Loci are file paths; a locus that does not exist is a GAP, printed as one.
"""

import os

from .live_log import read_events

STRATA = ("conscious", "subconscious", "unconscious")

#: name, stratum, what it does, locus (path relative to src/), evidence
#: (phase/event pairs in the live log that prove it ran).
PROCESSES = (
    ("deliberate-search", "conscious",
     "fan a query across providers, isolate failures",
     "eve_unleashed/websearch.py", (("search", "fan"),)),
    ("fetch-ladder", "conscious",
     "climb plain -> headers -> tls -> render -> stealth -> memory",
     "eve_unleashed/websearch.py", (("fetch", "answer"),)),
    ("verification", "conscious",
     "re-run every own test file, name what is red",
     "eve_unleashed/mend.py", (("e2e", "tests"),)),
    ("goal-setting", "conscious",
     "propose own improvement goals from recorded history",
     "eve_unleashed/consciousness.py", ()),
    ("self-model", "conscious",
     "hold a model of own components and their health",
     "eve_unleashed/consciousness.py", ()),
    ("intuition", "subconscious",
     "answer from memory before the network (supralogical rung)",
     "eve_unleashed/intuition.py", (("fetch", "intuition"),)),
    ("host-prior", "subconscious",
     "per-host answer rates, recalibrated by dream",
     "eve_unleashed/intuition.py", (("e2e", "dream"),)),
    ("inference-cache", "subconscious",
     "identical inputs return without re-running",
     "eve_unleashed/cache.py", ()),
    ("habituation", "subconscious",
     "route a task by past success, not by fresh reasoning",
     "eve_unleashed/model_router.py", ()),
    ("attention", "subconscious",
     "notice the wall amid a page (challenge detection)",
     "eve_unleashed/websearch.py", ()),
    ("reflex", "unconscious",
     "defend own parsers without being asked",
     "eve_unleashed/bypass_kit.py", (("e2e", "bypass"),)),
    ("immune-memory", "unconscious",
     "record every failure where it happened",
     "eve_unleashed/intuition.py", ()),
    ("dream-replay", "unconscious",
     "replay the stream, pair guesses with outcomes",
     "eve_unleashed/intuition.py", (("fetch", "intuition"),)),
    ("soma", "unconscious",
     "keep the body's own record: one line per event",
     "eve_unleashed/live_log.py", (("e2e", "summary"),)),
    ("watchdog", "unconscious",
     "watch dockets and report only news",
     "eve_unleashed/watch.py", ()),
    ("consolidation", "subconscious",
     "merge repeated episodes into one durable rule while idle",
     "eve_unleashed/intuition.py", ()),
    ("incubation", "unconscious",
     "leave a blocked problem and return with a different attack",
     "eve_unleashed/intuition.py", ()),
    ("priming", "subconscious",
     "let the last task bias the next one's first guess",
     "eve_unleashed/intuition.py", ()),
    ("prospective-memory", "conscious",
     "hold an intention for a future trigger, not just a goal",
     "eve_unleashed/intuition.py", ()),
    ("inhibition", "unconscious",
     "suppress a rung that keeps failing for this host",
     "eve_unleashed/intuition.py", ()),
    ("counterfactual-replay", "unconscious",
     "re-run a past decision against what actually happened",
     "eve_unleashed/intuition.py", ()),
    ("salience", "subconscious",
     "weight what mattered, not what was merely recent",
     "eve_unleashed/intuition.py", ()),
    ("affect", "conscious",
     "carry a valence that changes the next choice",
     "eve_unleashed/intuition.py", ()),
)

#: Processes a mind has and Eve does NOT run. Named, so the gap is a plan
#: rather than an absence nobody measured.
# These 8 gaps are the forge's targets; they may be implemented in intuition.py
# but remain forge targets until graduated (moved to PROCESSES with evidence=used).
GAPS = ()

#: Forge targets - gaps that the forge should attempt to implement.
# These 8 are the forge's targets; they may be implemented in intuition.py
# but remain forge targets until graduated.
FORGE_GAPS = (
    ("consolidation", "subconscious",
     "merge repeated episodes into one durable rule while idle"),
    ("incubation", "unconscious",
     "leave a blocked problem and return with a different attack"),
    ("priming", "subconscious",
     "let the last task bias the next one's first guess"),
    ("prospective-memory", "conscious",
     "hold an intention for a future trigger, not just a goal"),
    ("inhibition", "unconscious",
     "suppress a rung that keeps failing for this host"),
    ("counterfactual-replay", "unconscious",
     "re-run a past decision against what actually happened"),
    ("salience", "subconscious",
     "weight what mattered, not what was merely recent"),
    ("affect", "conscious",
     "carry a valence that changes the next choice"),
)


def _src_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _exercised(events):
    seen = set()
    for r in events:
        seen.add((r.get("phase"), r.get("event")))
    return seen


def audit(log_path=None):
    """Which processes exist, and which the live log proves were run."""
    events = read_events(log_path)["records"]
    seen = _exercised(events)
    root = _src_root()
    rows = []
    for name, stratum, what, locus, evidence in PROCESSES:
        present = os.path.exists(os.path.join(root, locus))
        fired = bool(evidence) and any(e in seen for e in evidence)
        if not present:
            state = "absent"
        elif fired:
            state = "used"
        else:
            state = "declared"
        rows.append({"name": name, "stratum": stratum, "what": what,
                     "locus": locus, "present": present,
                     "state": state})
    used = sum(1 for r in rows if r["state"] == "used")
    return {"processes": rows, "used": used, "total": len(rows),
            "declared": sum(1 for r in rows if r["state"] == "declared"),
            "absent": sum(1 for r in rows if r["state"] == "absent"),
            "gaps": [{"name": n, "stratum": s, "what": w}
                     for n, s, w in GAPS]}


def paradigm(stratum):
    """What a stratum means here, in one paragraph. Raises on unknown."""
    if stratum not in STRATA:
        raise ValueError("unknown stratum %r (known: %s)"
                         % (stratum, ", ".join(STRATA)))
    members = [r["name"] for r in audit()["processes"]
               if r["stratum"] == stratum]
    return {"stratum": stratum, "members": members,
            "paradigm": {
                "conscious": "deliberate work on the object of attention; "
                             "slow, logged, answerable",
                "subconscious": "memory that answers first and is corrected "
                                "after: priors, cache, habituation",
                "unconscious": "reflexes and records that run unasked and "
                               "are never the subject of attention",
            }[stratum]}


def nexus(request, log_path=None):
    """The crossing one request takes, stratum by stratum.

    conscious: the request itself.
    subconscious: what memory already knows about it (no network).
    unconscious: what fires regardless of the answer.
    """
    from .intuition import intuit
    g = intuit(request, log_path=log_path) if request else {
        "state": "unknown", "confidence": 0.0, "provenance": []}
    return {
        "request": request,
        "conscious": {"act": "parse and decide to fetch",
                      "locus": "eve_unleashed/websearch.py"},
        "subconscious": {"act": "answer from memory first",
                         "guess": g["state"],
                         "confidence": g["confidence"],
                         "provenance": g["provenance"],
                         "locus": "eve_unleashed/intuition.py"},
        "unconscious": {"act": "record the event and every failure",
                        "locus": "eve_unleashed/live_log.py"},
    }
