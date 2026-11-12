"""nsp_check.py -- NeuroSym-WSP verification for Eve's routing decisions.

Every time Eve routes an inference (native runner vs LLM-swarm provider),
she emits a symbolic trace event and checks it against the checked-in
inference_router spec with Z3. A routing that violates its own spec is a
defect with a name, not a vibe.

The NSP tree lives outside the repo (orphaned commit c7f21b86, extracted to
.build/nsp) because it is not an ancestor of HEAD; provenance is recorded
in every receipt. Verification must never break inference: any failure
of the checker itself returns "nsp-unavailable", never an exception.
"""
import json
import os
import sys
import time

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

NSP_SPEC = ("C:/Azazael/.build/nsp/Project_SHARDEX/neurosym_wsp"
            "/specs/inference_router.json")
NSP_VERIFIER = ("C:/Azazael/.build/nsp/Project_SHARDEX/neurosym_wsp"
                "/verifier/smt_verifier.py")


def _load_verifier():
    import importlib.util
    if not os.path.isfile(NSP_VERIFIER) or not os.path.isfile(NSP_SPEC):
        return None
    spec = importlib.util.spec_from_file_location("nsp_smt_verifier",
                                                  NSP_VERIFIER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nsp_smt_verifier"] = mod
    spec.loader.exec_module(mod)
    return mod


def verify_router_trace(events, spec_names=None):
    """Verify trace events against the inference_router spec.

    spec_names optionally restricts to the specs the trace actually
    covers (a trace that never records loser scores cannot honestly
    verify provider_score_monotonic; running it would FALSIFY on free
    variables). Returns (verdict, detail): verdict in VERIFIED /
    FALSIFIED / PRESENT_BUT_UNFALSIFIED / ERROR / nsp-unavailable.
    query_norm convention (documented, Eve's own encoding): min(len(prompt)/10, 50).
    """
    try:
        mod = _load_verifier()
        if mod is None:
            return "nsp-unavailable", "NSP tree or spec absent"
        with open(NSP_SPEC, encoding="utf-8") as f:
            full = json.load(f)
        specs = [s for s in full.get("specs", [])
                 if spec_names is None or s.get("name") in spec_names]
        ver = mod.SMTVerifier("z3")
        outcomes = [ver._verify_spec(s, events) for s in specs]
        counts = {}
        for o in outcomes:
            key = getattr(getattr(o, "result", ""), "value", o.result)
            counts[key] = counts.get(key, 0) + 1
        n_fals = counts.get("falsified", 0)
        n_err = counts.get("error", 0) + counts.get("timeout", 0)
        n_ver = counts.get("verified", 0)
        if n_fals > 0:
            overall = "falsified"
        elif n_err > 0:
            overall = "error"
        elif n_ver >= 3 and n_fals == 0:
            overall = "verified"
        else:
            overall = "present_but_unfalsified"
        class _Report:
            pass
        report = _Report()
        report.outcomes = outcomes
        report.overall = overall
        parts = []
        for o in report.outcomes:
            name = getattr(o, "spec_name", "spec")
            res = getattr(getattr(o, "result", ""), "value", o.result)
            parts.append("%s=%s" % (name, res))
        overall = getattr(getattr(report, "overall", ""), "value",
                          report.overall)
        return overall, "; ".join(parts)
    except Exception as e:
        note_failure("nsp_check:verify", e)
        return "nsp-unavailable", "checker failed: %s" % e
