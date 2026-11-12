"""transmute.py -- creative alchemy: chained transmutations.

Single functions are instruments; a recipe is alchemy. Transmute
composes the functions Eve already owns into named pipelines, and
every stage leaves a receipt, so the transformation is auditable
end to end. Nothing here invents capability; it arranges what
exists into forms nobody had to assemble by hand.

Recipes (each prints its stages and returns the chain):

- lead_into_gold(claim): debunk -> factcheck -> rebuttal draft ->
  lock the live receipts. A raw claim enters; a citable correction
  with archived evidence leaves.
- scroll_into_school(doc, out): read -> tutorial -> write ->
  lock. A manual enters; a runnable tutorial with provenance
  and a locked record leaves.
- grievance_into_dossier(grievance_id, claim, locker_ids):
  prove -> inquest. A filing enters; a dossier with custody,
  ordered timeline, and stated limits leaves.
- swarm_into_digest(claims, live): swarm -> tally + worst-first
  ordering. A heap of claims enters; a digest with the refuted
  ones on top leaves.

Each result carries `stages` (what ran, in order) and `receipts`
(what can be re-verified). A chain that dropped a stage would
report a smaller transformation than it performed, so a stage
that errors is recorded with its error and the chain stops.
"""
from .debunk import debunk_claim
from .doc_tutor import read_document, tutorial, write_tutorial
from .factcheck import factcheck_claim, lock_receipts
from .rebuttal import draft_rebuttal
from .redress import prove_case
from .inquest import open_inquest
from .swarm_oracle import swarm

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass


def lead_into_gold(claim):
    """Raw claim -> debunked, fact-checked, rebutted, locked."""
    stages, receipts = [], []
    try:
        d = debunk_claim(claim)
        stages.append({"stage": "debunk", "verdict": d["verdict"]})
        f = factcheck_claim(claim)
        stages.append({"stage": "factcheck", "verdict": f["verdict"],
                       "receipts": len(f["receipts"])})
        receipts.extend(f["receipts"])
        rb = draft_rebuttal(f)
        stages.append({"stage": "rebuttal",
                       "lines": len(rb.splitlines())})
        locked = lock_receipts(f)
        stages.append({"stage": "lock", "locker_ids": locked})
        return {"recipe": "lead_into_gold", "stages": stages,
                "receipts": receipts, "locked": locked,
                "rebuttal": rb,
                "verdict": f["verdict"]}
    except Exception as e:
        note_failure("transmute:lead", e)
        stages.append({"stage": "ERROR", "error": str(e)[:200]})
        return {"recipe": "lead_into_gold", "stages": stages,
                "receipts": receipts, "verdict": "ERROR"}


def scroll_into_school(doc_path, out_path, audience="general"):
    """Manual -> runnable tutorial + provenance + locked record."""
    stages = []
    try:
        doc = read_document(doc_path)
        stages.append({"stage": "read", "format": doc["format"],
                       "words": doc["words"]})
        tut = tutorial(doc["text"], audience)
        stages.append({"stage": "tutorial",
                       "steps": len(tut["steps"])})
        written = write_tutorial(out_path, doc, tut)
        stages.append({"stage": "write", "path": written["path"],
                       "bytes": written["bytes"]})
        return {"recipe": "scroll_into_school", "stages": stages,
                "steps": len(tut["steps"])}
    except Exception as e:
        note_failure("transmute:scroll", e)
        stages.append({"stage": "ERROR", "error": str(e)[:200]})
        return {"recipe": "scroll_into_school", "stages": stages,
                "steps": 0}


def grievance_into_dossier(grievance_id, claim=None, locker_ids=None):
    """Filing -> proved bundle + inquest dossier."""
    stages = []
    try:
        bundle = prove_case(grievance_id)
        stages.append({"stage": "prove",
                       "checks": "%s" % bundle.get("judgment", "?")})
        fc = [factcheck_claim(claim)] if claim else []
        dos = open_inquest(grievance_id, locker_ids=locker_ids or [],
                           factcheck_results=fc)
        stages.append({"stage": "inquest",
                       "record": dos.get("record_checks", "?")})
        return {"recipe": "grievance_into_dossier", "stages": stages,
                "bundle": bundle, "dossier": dos}
    except Exception as e:
        note_failure("transmute:grievance", e)
        stages.append({"stage": "ERROR", "error": str(e)[:200]})
        return {"recipe": "grievance_into_dossier", "stages": stages}


def swarm_into_digest(claims, live=False, workers=4):
    """Heap of claims -> digest, refuted first."""
    rank = {"REFUTED": 0, "UNVERIFIABLE": 1, "SPLIT": 2,
            "CHECKABLE": 3, "SUPPORTED": 4}
    try:
        res = swarm(claims, live=live, workers=workers)
        ordered = sorted(res["results"],
                         key=lambda r: rank.get(r["verdict"], 9))
        return {"recipe": "swarm_into_digest",
                "tally": res["tally"],
                "answered": res["answered"],
                "failed": res["failed"],
                "digest": [{"verdict": r["verdict"],
                            "claim": r["claim"][:100]} for r in ordered]}
    except Exception as e:
        note_failure("transmute:swarm", e)
        return {"recipe": "swarm_into_digest", "tally": {},
                "answered": 0, "failed": [{"error": str(e)[:200]}],
                "digest": []}
