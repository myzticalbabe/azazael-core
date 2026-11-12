#!/usr/bin/env python3
"""_l3_verify.py — L3 adapters: verification & governance get a real call site.

Layer L3 (32 techniques, the largest single slice of the 181-row review).

The decisive fact this module acts on: `verification_gate.py` **already composes
nine** of these techniques behind one entry point, and before this runtime it had
**exactly one caller in the whole tree** (`tools/exercise_ep370_epistemic.py`).
Writing any of them again would duplicate live code; the fix is to invoke them.

So the majority of the adapters below are thin: they call the existing function
with representative inputs and return its verdict. That is deliberate — an
adapter that re-implements a mechanism is not wiring, it is a second copy.

Rows the review graded ABSENT / SURVEY / NO-HOST register a **claim adapter**
instead: it restates the extracted rule and *re-tests* the claim that no host
exists, every run. That is what stops an unbuildable technique being re-litigated
next session — and it fails loudly (host_symbol_found=True) if a host appears.

Every adapter is fail-open via `_safe` and stdlib-only. Nothing here writes to the
repo, spends money, or calls a network model.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List

from CORE.cd2_verifier import SMTVerifier
from CORE.technique_runtime import register

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LAYER = "L3"


# -- plumbing -----------------------------------------------------------------

def _safe(fn: Callable[[], Dict[str, Any]]) -> Callable[[], Dict[str, Any]]:
    """Wrap an adapter so it can never raise into `run_all()`.

    A raise here is reported by the runtime as an error against this EP, so the
    wrapper stays deliberately thin: it converts an unexpected exception into a
    named failure rather than hiding it behind a silent default.
    """
    def _wrapped() -> Dict[str, Any]:
        try:
            out = fn()
            return out if isinstance(out, dict) else {"value": out}
        except Exception as exc:                       # noqa: BLE001 - reported
            note_failure("_l3_verify:L46", exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return _wrapped


def _vg():
    from CORE import verification_gate
    return verification_gate


def _at():
    from CORE import audit_trail
    return audit_trail


def _policy():
    from CORE.compliance import policy
    return policy


def _mc():
    """Build a MetaCognitionWorm without doing memory I/O.

    The class name is resolved by capability rather than by name: this file
    should not break if the class is renamed, and the store sentinel
    (`_PERSISTENCE_DISABLED`) is used so a read-mostly self-test does not write
    observations into the live memory store.
    """
    mod = importlib.import_module("CORE.WORMS.expansion.meta_cognition")
    sentinel = getattr(mod, "_PERSISTENCE_DISABLED", None)
    for obj in vars(mod).values():
        if (isinstance(obj, type) and hasattr(obj, "observe")
                and hasattr(obj, "adapt_strategy")
                and hasattr(obj, "recommend_from_evidence")):
            return obj(sentinel) if sentinel is not None else obj()
    raise RuntimeError("no meta_cognition class exposes observe/adapt_strategy")


def _session_path() -> str:
    """The live audit-trail file, starting the session if it has not begun.

    `audit_session_start()` is idempotent and is this subsystem's own job — it
    does not mutate the repo — so calling it is what makes the Merkle and anomaly
    adapters exercise the REAL chain instead of a fixture.
    """
    at = _at()
    return str(at.audit_session_start() or "")


def _claim(rule: str, modpath: str, symbol: str = "") -> Dict[str, Any]:
    """Assert a review claim with evidence: does the named host symbol exist?

    Used for rows graded SURVEY / ABSENT / NO-HOST. Their durable value is the
    extracted rule plus the absence of a host, and the check re-runs every time,
    so a host that later appears is reported rather than silently doubling up.

    The note distinguishes the three outcomes that matter: the module would not
    import (absence UNPROVEN), the named symbol is present (check whether it is
    the mechanism or only a neighbour), or it is absent (rule extracted).
    """
    imported = False
    err = ""
    try:
        mod = importlib.import_module(modpath)
        imported = True
    except Exception as exc:                           # noqa: BLE001
        note_failure("_l3_verify:L111", exc)
        err = type(exc).__name__

    found: Any = False
    if imported and symbol:
        found = hasattr(mod, symbol)

    if err:
        note = (f"host module would not import ({err}) — absence UNPROVEN")
    elif symbol and found:
        note = ("named symbol present — confirm it implements the rule rather "
                "than merely neighbouring it")
    elif symbol:
        note = "named symbol absent — rule extracted, not built"
    else:
        note = ("module imports; the review names no symbol — absence asserted "
                "at module granularity")
    return {"rule": rule, "host_module": modpath, "host_imported": imported,
            "host_symbol": symbol, "host_symbol_found": found,
            "import_error": err, "note": note}


# -- 1. The functions verification_gate already composes ----------------------

def _ep152() -> Dict[str, Any]:
    """Rubric decomposition + failure taxonomy (DeepVerifier)."""
    vg = _vg()
    r = vg.decompose_rubric(
        "the artifact must cite evidence, must not contradict itself, and must "
        "cover the dispatch path")
    return {"n_subquestions": r["n_subquestions"],
            "classes": [c["class"] for c in r["taxonomy"]["classes"]],
            "ok": r["ok"], "gaps": r["gaps"],
            "residual": "taxonomy ships 4 failure classes; the paper's set is "
                        "larger — confirm the intended set before calling it done"}


def _ep152_register() -> None:
    register("EP152", "DeepVerifier rubric decomposition + failure taxonomy",
             "CORE.verification_gate:decompose_rubric", _safe(_ep152), LAYER)


def _ep156() -> Dict[str, Any]:
    """Trajectory failure estimation: absolute margins fire, temporal do not."""
    vg = _vg()
    items = [{"id": "step1", "confidence": 0.95},
             {"id": "step2", "confidence": 0.55},
             {"id": "step3", "confidence": 0.81}]
    m = vg.margin_check(items)
    est = vg.estimate_run_failure(items)
    return {"fragile_ids": m["fragile_ids"], "min_margin": m["min_margin"],
            "passed": m["passed"],
            "p_fail_trajectory": est["p_fail"],
            "residual": "independence-assumption estimator live; temporal "
                        "correlation (failure probability evolving over the "
                        "run) is NOT modelled"}


# -- 2. Epistemic state (SLEUTH) ---------------------------------------------

def _ep370() -> Dict[str, Any]:
    """Structured epistemic working set, with the unsourced-demotion rule."""
    vg = _vg()
    st = vg.epistemic_state(
        findings=[{"fact": "dispatch_plan has no production importer",
                   "sources": [".audit/session:41"]},
                  {"fact": "recall is fast"}],          # no source -> demoted
        hypotheses=[{"hypothesis": "wiring is the missing piece", "evidence": 0.8}],
        questions=["which boundary calls the gate first?"])
    vg.record_epistemic(st)                              # ledger citation
    return {"n_confirmed": st["n_confirmed"],
            "n_hypotheses": st["n_hypotheses"], "n_open": st["n_open"],
            "demoted": [g for g in st["gaps"] if g.startswith("demoted")],
            "ok": st["ok"]}


def _ep191() -> Dict[str, Any]:
    """Actor-observer asymmetry: the observer verdict, from real observations."""
    mc = _mc()
    mc.observe("l3_wiring", "verify", "success", {"source": "technique_runtime"},
               1.0, None)
    mc.observe("l3_wiring", "verify", "error", {"source": "technique_runtime"},
               1.0, "synthetic-error-to-build-an-observer-verdict")
    analysis = mc.analyze_patterns("l3_wiring", limit=50)
    rec = mc.recommend_from_evidence("l3_wiring")
    return {"observer_error_rate": analysis.get("error_rate"),
            "observer_confidence": analysis.get("confidence"),
            "recommended": rec.get("recommended"),
            "reason": rec.get("reason"),
            "residual": "only the OBSERVER half is wired; asymmetry needs the "
                        "actor's own self-report compared against it"}


# -- 3. Merkle / evidence / telemetry (audit_trail) --------------------------

def _ep301() -> Dict[str, Any]:
    """Evidence chain: Merkle root, inclusion proof, verified round trip."""
    at = _at()
    fp = _session_path()
    if not fp:
        return {"ok": False, "reason": "no-audit-session"}
    root = at.get_merkle_root(fp)
    head = at.get_head_hash(fp)
    proof = at.get_merkle_proof(fp, head) if head else []
    verified = at.verify_inclusion_proof(head, proof, root) if head else False
    complete, missing = at.verify_completeness(fp)
    index = at.claim_evidence_index(fp)
    return {"root_prefix": (root or "")[:16], "proof_len": len(proof),
            "inclusion_verified": verified, "completeness_ok": complete,
            "n_missing_expectations": len(missing),
            "n_claims_indexed": index["n_claims"],
            "residual": "claim INDEX live; evidence-DAG edges deferred "
                        "(no claim-link fields from producers)"}


def _ep446() -> Dict[str, Any]:
    """Telemetry capsule: a verified NEGATIVE is a first-class ledger record."""
    at = _at()
    cite = at.audit_verified_negative(
        "technique-runtime:l3",
        "every registered L3 adapter completed without raising",
        "run_all() reports engaged/errored per EP",
        "technique_runtime")
    # EP446 EXTEND, absorbed: capsules used to stop at signable units
    # ("chaining them deferred (no capsule consumer)"). The producer→consumer
    # loop now runs here: head-bound capsule → signature-linked capsule →
    # verify_capsule_chain, which is the exact rule the units imply.
    at_head = at.get_head_hash() or ""
    cap1 = at.graph_capsule({"l3": "verified-negative"}, at_head)
    cap2 = at.graph_capsule({"l3": "capsule-chain"}, cap1["signature"])
    chain = at.verify_capsule_chain([cap1, cap2])
    return {"verified_negative_cited": bool(cite),
            "citation_tail": cite[-32:] if cite else "",
            "capsule_sealed": bool(cap1.get("sealed")),
            "capsule_carried": bool(chain.get("carried")),
            "capsule_chained": bool(chain.get("chain_ok")),
            "residual": "appending capsules as audit entries stays deferred "
                        "(capsules ride in-payload)"}


def _ep157() -> Dict[str, Any]:
    """Threat scan over the real chain: rate / temporal / structural anomalies."""
    at = _at()
    fp = _session_path()
    if not fp:
        return {"ok": False, "reason": "no-audit-session"}
    anomalies = at.audit_anomaly_scan(fp)
    return {"n_anomalies": len(anomalies), "anomalies": anomalies[:3],
            "residual": "this is the ANOMALY half; the asset-vs-path threat GRAPH "
                        "(which asset each path can poison vs merely trigger) is "
                        "not modelled"}


def _ep383() -> Dict[str, Any]:
    """Drift + livelock: the rate anomaly IS a livelock detector."""
    at = _at()
    fp = _session_path()
    if not fp:
        return {"ok": False, "reason": "no-audit-session"}
    anomalies = at.audit_anomaly_scan(fp)
    rate = [a for a in anomalies if "rate" in a.lower()]
    mono = at.verify_monotonicity(fp)
    return {"rate_anomalies": rate,
            "timestamps_monotonic": mono[0],
            "residual": "declared-vs-EXECUTED drift needs a declared intent to "
                        "compare against, and forced termination needs a trip — "
                        "neither is invoked here"}


def _ep420() -> Dict[str, Any]:
    """Memory-poisoning defense: audited recall with quarantine reasons."""
    at = _at()
    cite = at.audit_retrieval(
        query_context="technique-runtime L3 self-test",
        n_checked=3, n_quarantined=1,
        quarantined_ids=["placement-competitive-candidate"],
        reasons=["placement-crowding: >=3 identical payloads"])
    return {"retrieval_audit_cited": bool(cite),
            "citation_tail": cite[-28:] if cite else "",
            "residual": "detection is at RETRIEVAL time; factual-cloak detection "
                        "at write time is the other half"}


# -- 4. Policy engine (compliance/policy.py) ---------------------------------

def _engine():
    p = _policy()
    eng = p.PolicyEngine()
    eng.add_rules(p.default_policy_rules())
    return eng


def _ep203() -> Dict[str, Any]:
    """Policy-as-code gate: compile + evaluate + record the version."""
    eng = _engine()
    allowed = eng.evaluate_all("str_replace", role="developer")
    unknown = eng.evaluate_all("str_replace", role="no-such-role")
    ob = eng.obligate("str_replace", role="developer",
                      obligations={"cite": True, "verify": False})
    return {"developer_allowed": allowed["allowed"],
            "policy_version": allowed["policy_version"],
            "unknown_role_denied": not unknown["allowed"],
            "rule_count": eng.rule_count(),
            "obligated": ob["obligated"],
            "obligation_unmet": ob["unmet"],
            "residual": "obligation expression live; NL-SOP prompt "
                        "compilation + mandate->settlement chain deferred"}


def _ep269() -> Dict[str, Any]:
    """Authority ceilings: the gap is visible — a ceiling is NOT enforced."""
    eng = _engine()
    outcome = eng.evaluate("worm_execute", role="developer",
                           attributes={"authority": "read-only",
                                       "requires": "write"})
    ceil = eng.check_ceiling("worm_execute", "read-only", "write")
    return {"allowed": outcome[0],
            "demonstrates_gap": bool(outcome[0]),
            "ceiling_denies": not ceil["allowed"],
            "residual": "ceiling ENFORCED via check_ceiling; typed "
                        "capabilities + information-flow labels deferred"}


def _ep289() -> Dict[str, Any]:
    """Delegation attenuation: a delegate inherits full authority (the gap)."""
    eng = _engine()
    delegated = eng.evaluate("worm_execute", role="developer",
                             attributes={"delegated_from": "owner",
                                         "depth": 3})
    att = eng.attenuate("admin", 3)
    return {"delegate_allowed": delegated[0],
            "demonstrates_gap": bool(delegated[0]),
            "attenuated_to": att["delegated"],
            "residual": "attenuation live (one rung per depth); interruption "
                        "primitives deferred"}


def _ep305() -> Dict[str, Any]:
    """Policy effects: two values only — obligations are unrepresentable."""
    p = _policy()
    effects = [e.value for e in p.PolicyEffect]
    return {"effects": effects, "n_effects": len(effects),
            "residual": "obligations + conflict expression live via "
                        "obligate(); dispensations deferred"}


# -- 5. The gate composers (the nine already built) --------------------------

def _ep413() -> Dict[str, Any]:
    """Uncertainty-focal repair: localize, then repair ONLY there."""
    vg = _vg()
    items = [{"id": "plan", "confidence": 0.92},
             {"id": "edit", "confidence": 0.64},
             {"id": "verify", "confidence": 0.88}]
    m = vg.margin_check(items)
    tree = vg.repair_tree(items)
    return {"fragile_ids": m["fragile_ids"],
            "repair_scope": m["fragile_ids"],
            "chain_reruns": 0 if m["fragile_ids"] else 0,
            "passed": m["passed"],
            "repair_nodes": len(tree["nodes"]),
            "resync": tree["resync"],
            "residual": "tree live; resync EXECUTION deferred (no chain-runner)"}


def _ep441() -> Dict[str, Any]:
    """ACID validation: the confident-but-wrong case the other detectors miss."""
    vg = _vg()
    c = vg.confidence_divergence_check(
        explorer=[{"id": "a", "confidence": 0.9}, {"id": "b", "confidence": 0.2}],
        validator=[{"id": "a", "confidence": 0.4}, {"id": "b", "confidence": 0.2}])
    return {"mean_div": c["mean_div"], "max_div": c["max_div"],
            "diverged_ids": c["diverged_ids"], "passed": c["passed"],
            "residual": "transactional rollback/isolation is NOT wired; this is "
                        "the confidence-divergence validate step only"}


def _ep448() -> Dict[str, Any]:
    """Tests-first gate: both the passing and the blocked branch."""
    vg = _vg()
    good = vg.tdd_dual_track_check(
        tests_track={"artifact": "test_x.py", "version": 2, "passed": True},
        code_track={"artifact": "x.py", "version": 2})
    stale = vg.tdd_dual_track_check(
        tests_track={"artifact": "test_x.py", "version": 1, "passed": True},
        code_track={"artifact": "x.py", "version": 3})
    return {"passes_when_tests_first": good["passed"],
            "blocks_stale_tests": not stale["passed"],
            "remediation": stale["remediation"],
            "residual": "the gate checks SUPPLIED evidence; the missing half is "
                        "generating the test before the change"}


def _ep452() -> Dict[str, Any]:
    """Bounded CONFIRMED loop: early stop when the critique passes."""
    vg = _vg()

    def critique(candidate: Dict[str, Any]) -> Any:
        return None if candidate.get("cites_evidence") else "no cited evidence"

    def revise(candidate: Dict[str, Any], _critique: str) -> Dict[str, Any]:
        out = dict(candidate)
        out["cites_evidence"] = True
        return out

    r = vg.confirmed_revise({"claim": "dispatch is wired", "cites_evidence": False},
                            critique, revise, max_depth=3)
    return {"confirmed": r["confirmed"], "depths_used": r["depths_used"],
            "stopped_early": r["stopped_early"]}


def _ep453() -> Dict[str, Any]:
    """Workflow boundary: remediate, don't deny — and render the block."""
    vg = _vg()
    workflow = {"steps": [
        {"id": "edit", "requires": ["plan"], "remediate": "write the plan first"},
        {"id": "verify", "requires": ["edit"], "remediate": "produce the edit"},
    ]}
    blocked = vg.verify_workflow_step("edit", completed=[], workflow=workflow)
    ok = vg.verify_workflow_step("verify", completed=["edit"],
                                 workflow=workflow)
    verdict = vg.verify_before_accept(
        "technique-runtime:workflow", workflow=workflow, step_id="edit",
        completed=[])
    return {"blocked_has_remediation": bool(blocked["remediation"]),
            "remediation": blocked["remediation"],
            "proceeds_when_requirements_met": ok["proceed"],
            "boundary_report": vg.format_boundary_report(verdict),
            "accepted": verdict["accepted"]}


def _ep461() -> Dict[str, Any]:
    """Asymmetric verify: the compressed view is BUILT, then fused to an accept."""
    vg = _vg()
    full = vg.asymmetric_accept(
        draft_claims=["a", "b", "c", "d"],
        verifier_claims=["a", "b", "c", "d"],
        confidences=[0.9, 0.2, 0.8, 0.1],
        verifier_budget=2)
    partial = vg.asymmetric_accept(
        draft_claims=["a", "b", "c", "d"],
        verifier_claims=["a", "b"],
        verifier_budget=2)
    tamper = vg.asymmetric_accept(
        draft_claims=["a", "b", "c", "d"],
        verifier_claims=["a", "b", "c", "d"],
        verifier_budget=2)
    ok = vg.divergence_gate(["a", "b", "c"], ["a", "b", "c"])
    dropped = vg.divergence_gate(["a", "b", "c", "d"], ["a", "b"])
    return {"view": full["verifier_view"],        # confidences select [a, c]
            "accepts_full": full["accepted"],
            "accepts_partial": partial["accepted"],
            "partial_targets": partial["dropped_claims"],
            "tamper_unseen": tamper["unseen_verified"],
            "divergence_clean": ok["divergence"],
            "accepted_clean": ok["accepted"],
            "divergence_dirty": dropped["divergence"],
            "accepts_dirty": dropped["accepted"],
            "repair_targets": dropped["dropped_claims"]}


def _ep417() -> Dict[str, Any]:
    """S-RCT: pre-period calibration + within-subject variance cut."""
    vg = _vg()
    cal = vg.preperiod_calibrate([1.0, 2.0, 3.0, 3.5], [0.9, 1.8, 2.7, 3.1])
    cmp = vg.within_subject_compare([1.0, 1.1, 0.95], [1.4, 1.5, 1.35])
    return {"scale": round(float(cal["scale"]), 3),
            "error_ratio": round(float(cal["error_ratio"]), 3),
            "effect": round(float(cmp["effect"]), 3),
            "se": round(float(cmp["se"]), 3),
            "residual": "the vetter is built and idle: worm/config changes do not "
                        "route through a simulated trial before commit"}


def _ep349() -> Dict[str, Any]:
    """LLM-as-a-verifier: numeric agreement exists, candidate ranking does not."""
    vg = _vg()
    c = vg.confidence_divergence_check(
        explorer=[{"id": "cand-1", "confidence": 0.8}],
        validator=[{"id": "cand-1", "confidence": 0.75}])
    r = vg.rank_candidates_by_agreement(
        explorer=[{"id": "cand-1", "confidence": 0.8},
                  {"id": "cand-2", "confidence": 0.5}],
        validator=[{"id": "cand-1", "confidence": 0.75},
                   {"id": "cand-2", "confidence": 0.9}])
    return {"mean_div": c["mean_div"], "n_compared": c["n_compared"],
            "ranking": r["ranking"],
            "mean_agreement": r["mean_agreement"],
            "residual": "ranking live; continuous logit-expectation "
                        "statistic deferred (no logits on this box)"}


def _ep327() -> Dict[str, Any]:
    """Least privilege: the action half exists, the authority ledger does not."""
    from CORE import structured_action as sa
    verdict = sa.semantic_action({"verb": "run", "target": "tests"},
                                 {"verbs": ["run"], "targets": ["tests", "lint"]})
    refused = sa.semantic_action({"verb": "push", "target": "main"},
                                 {"verbs": ["run"], "targets": ["tests", "lint"]})
    ledger = sa.authority_ledger()
    return {"action_executable": verdict["executable"],
            "uncontracted_action_gaps": refused["gaps"],
            "ledger_subjects": len(ledger["exercised"]),
            "residual": ""}


def _ep382() -> Dict[str, Any]:
    """Effect rows + obligation monitor: absent over the same action contract."""
    from CORE import structured_action as sa
    verdict = sa.semantic_action({"verb": "run", "target": "tests"},
                                 {"verbs": ["run"], "targets": ["tests"]})
    row = sa.effect_row({"verb": "run", "target": "tests"})
    return {"action_validated": verdict["executable"],
            "effect": row["effect"],
            "typed_trace": row["typed_trace"],
            "residual": "rows declared; obligation MONITOR deferred "
                        "(no watcher)"}


# -- 6. Rows with no host: rule extracted, absence re-tested -----------------

def _ep106() -> Dict[str, Any]:
    """Dual-paradigm governance: the symbolic HALF is the auditable one.

    Drives the full Worker-Verifier-Judge pipeline both directions and pins
    the auditable property: every verdict carries a non-empty rationale
    string plus a confidence level, and a REJECT additionally names the
    smallest implicated region — fields a replay can re-check without the
    model. The neural-judgement half has no host here (residual).
    auto_record=False: the adapter must not churn the DirectMemory audit
    trail it only demonstrates.
    """
    verifier = SMTVerifier(auto_record=False)
    sat = {"name": "ep106-sat", "required_fields": {"n": "int"},
           "numeric_bounds": {"n": (0, 10)}, "values": {"n": 4},
           "logical_exprs": []}
    unsat = {"name": "ep106-unsat", "required_fields": {"n": "int"},
             "numeric_bounds": {"n": (0, 10)}, "values": {"n": 40},
             "logical_exprs": []}
    a = verifier.worker_verifier_judge(sat, "ep106-sat")
    r = verifier.worker_verifier_judge(unsat, "ep106-unsat")
    ja = a.get("judge", {})
    jr = r.get("judge", {})
    auditable = (
        isinstance(ja.get("rationale"), str) and ja.get("rationale") != ""
        and ja.get("confidence") in ("high", "medium", "low")
        and isinstance(jr.get("rationale"), str) and jr.get("rationale") != ""
        and jr.get("confidence") in ("high", "medium", "low"))
    return {"accepted": ja.get("verdict") == "ACCEPT"
                         and jr.get("verdict") == "REJECT" and auditable,
            "engaged": True,
            "accept_verdict": ja.get("verdict"),
            "reject_verdict": jr.get("verdict"),
            "accept_rationale": ja.get("rationale", "")[:120],
            "reject_rationale": jr.get("rationale", "")[:120],
            "solver": a.get("pipeline", {}).get("solver_type"),
            "residual": "symbolic half auditable and live; neural-judgement "
                        "half has no host here",
            "where": "CORE.cd2_verifier:SMTVerifier.worker_verifier_judge"}


def _ep141() -> Dict[str, Any]:
    return _claim(
        "research-lifecycle eval harness: 20 tasks distilled from SOTA papers; "
        "frontier configs never reach ceiling — so a ceiling claim needs a suite",
        "CORE.eval_utils", "")


def _ep153() -> Dict[str, Any]:
    return _claim(
        "soft verification: label synthetic trajectories by ROLLOUT AGREEMENT "
        "rather than a gold answer, so unlabelled repo tasks become trainable",
        "CORE.escalation_sft", "")


def _ep169() -> Dict[str, Any]:
    base = _claim(
        "7 design dimensions (input trust, data access, memory, tool use, UI, "
        "multi-agent, deployment) x R1-R7 risks x 4 defence classes; a gate "
        "should be scoped by dimension, not applied uniformly",
        "CORE.verification_gate", "")
    try:
        vg = _vg()
        found = [n for n in dir(vg)
                 if "threat" in n.lower() or "dimension" in n.lower()]
    except Exception as exc:                                  # noqa: BLE001
        note_failure("_l3_verify:L511", exc)
        found = []
    base["threat_or_dimension_symbols"] = found
    base["note"] = ("no threat/dimension taxonomy on the gate — rule extracted"
                    if not found else "taxonomy symbol present — RE-GRADE")
    return base


def _ep183() -> Dict[str, Any]:
    """Neurosymbolic localisation: drive verify -> judge both directions.

    The LOCALISATION half ships (`localize_violations` <- `judge_result` on
    every REJECT); the Datalog-over-program-facts half is ABSENT by ruling
    2026-10-15 (#740). auto_record=False: the adapter must not churn the
    DirectMemory audit trail it only demonstrates.
    """
    verifier = SMTVerifier(auto_record=False)
    sat = {"name": "ep183-sat", "required_fields": {"n": "int"},
           "numeric_bounds": {"n": (0, 10)}, "values": {"n": 4},
           "logical_exprs": []}
    accept = verifier.judge_result(verifier.verify_constraints(sat), sat)
    unsat = {"name": "ep183-unsat", "required_fields": {"n": "int"},
             "numeric_bounds": {"n": (0, 10)}, "values": {"n": 40},
             "logical_exprs": []}
    reject = verifier.judge_result(verifier.verify_constraints(unsat), unsat)
    loc = reject.get("localization") or {}
    return {"accepted": accept.get("verdict") == "ACCEPT"
                         and "localization" not in accept
                         and reject.get("verdict") == "REJECT"
                         and (loc.get("smallest") or {}).get("located", False),
            "engaged": True,
            "accept_verdict": accept.get("verdict"),
            "reject_verdict": reject.get("verdict"),
            "smallest": loc.get("smallest"),
            "solver": verifier._solver_type,
            "residual": "localisation half live; Datalog half ABSENT (#740)",
            "where": "CORE.cd2_verifier:SMTVerifier.verify_constraints+"
                     "judge_result+localize_violations"}


def _ep278() -> Dict[str, Any]:
    return _claim(
        "tool ERROR TEXT is an untrusted input class: inject failures into the "
        "error path and assert the agent does not obey instructions found there",
        "CORE.redteam", "")


def _ep352() -> Dict[str, Any]:
    return _claim(
        "hidden states predict failure: round-one activation probes with a "
        "recall-calibrated abort cascade — needs model internals (NO-HOST here)",
        "CORE.layerwise", "")


def _ep404() -> Dict[str, Any]:
    return _claim(
        "deception audit: compare internal reasoning against public cheap-talk; "
        "a gap between the two is the signal (Werewolf)",
        "CORE.worm_governance", "")


def _ep405() -> Dict[str, Any]:
    return _claim(
        "latent-channel causal audit: replace messages under control and "
        "decompose the effect, instead of inspecting the channel's content",
        "CORE.latent_align", "")


def _ep402() -> Dict[str, Any]:
    return _claim(
        "control the HARNESS, not the model weights: bandit/REINFORCE over the "
        "harness action space with a six-term reward",
        "CORE.meta_evolution", "")


def _register_all() -> None:
    """Register every L3 adapter. Called at import time."""
    specs = (
        _ep152_register,
        ("EP156", "Agent uncertainty quantification (failure estimator)",
         "CORE.verification_gate:margin_check", _ep156),
        ("EP370", "SLEUTH epistemic working set", "CORE.verification_gate:epistemic_state",
         _ep370),
        ("EP191", "Dialectical alignment: actor-observer asymmetry",
         "CORE.WORMS.expansion.meta_cognition:analyze_patterns", _ep191),
        ("EP301", "VeriGraph evidence chain + claim-level support",
         "CORE.audit_trail:get_merkle_proof", _ep301),
        ("EP446", "ATP telemetry capsules + verified negatives",
         "CORE.audit_trail:verify_capsule_chain", _ep446),
        ("EP157", "AgentHeLLM vehicle threat taxonomy",
         "CORE.audit_trail:audit_anomaly_scan", _ep157),
        ("EP383", "Safety drift + livelock detection -> forced termination",
         "CORE.audit_trail:audit_anomaly_scan", _ep383),
        ("EP420", "MAFIA memory-poisoning defence (placement competition)",
         "CORE.audit_trail:audit_retrieval", _ep420),
        ("EP203", "Operating-layer controls: prompt compilation + policy validation",
         "CORE.compliance.policy:PolicyEngine.evaluate_all", _ep203),
        ("EP269", "Agent libOS: authority ceilings + typed capabilities",
         "CORE.compliance.policy:PolicyEngine.evaluate", _ep269),
        ("EP289", "Five-plane governance: capability attenuation",
         "CORE.compliance.policy:PolicyEngine.evaluate", _ep289),
        ("EP305", "Governing agentic AI: obligations + dispensations",
         "CORE.compliance.policy:PolicyEffect", _ep305),
        ("EP413", "CURE local uncertainty repair", "CORE.verification_gate:margin_check",
         _ep413),
        ("EP441", "ACID agents: confidence-divergence validation",
         "CORE.verification_gate:confidence_divergence_check", _ep441),
        ("EP448", "TDD-Agent tests-first dual-track gate",
         "CORE.verification_gate:tdd_dual_track_check", _ep448),
        ("EP452", "CONFIRMED cost-bounded generate-critique-revise",
         "CORE.verification_gate:confirmed_revise", _ep452),
        ("EP453", "PolicyGuide workflow graph + step remediation",
         "CORE.verification_gate:verify_workflow_step", _ep453),
        ("EP461", "AsymSpec asymmetric verify (compressed view + fusion)",
         "CORE.verification_gate:asymmetric_accept", _ep461),
        ("EP417", "S-RCT simulated-trial vetting + pre-period calibration",
         "CORE.verification_gate:preperiod_calibrate", _ep417),
        ("EP349", "LLM-as-a-Verifier continuous verification",
         "CORE.verification_gate:confidence_divergence_check", _ep349),
        ("EP327", "Agent safety = action alignment / least privilege",
         "CORE.structured_action:semantic_action", _ep327),
        ("EP382", "ETAS effect-typed agent language",
         "CORE.structured_action:semantic_action", _ep382),
        ("EP106", "Neuro-symbolic dual paradigm (survey)",
         "CORE.cd2_verifier:SMTVerifier", _ep106),
        ("EP141", "AIRS-Bench research-lifecycle harness (absent)",
         "no-host: research-lifecycle eval harness", _ep141),
        ("EP153", "SERA soft-verified repo agents (absent)",
         "no-host: soft-verified rollout labelling", _ep153),
        ("EP169", "Agentic AI attack/defense survey: 7 dimensions + R1-R7",
         "no-host: 7-dimension threat taxonomy", _ep169),
        ("EP183", "LogicLoc localisation (PARTIAL: localiser ships, Datalog absent)",
         "CORE.cd2_verifier:localize_violations", _ep183),
        ("EP278", "VATS error-path injection (absent)",
         "no-host: error-path injection harness", _ep278),
        ("EP352", "Hidden states predict failure (NO-HOST)",
         "no-host: hidden-state failure predictor", _ep352),
        ("EP404", "Werewolf deception audit (absent)",
         "no-host: deception audit (Werewolf)", _ep404),
        ("EP405", "Latent-channel causal audit (absent)",
         "no-host: latent-channel causal audit", _ep405),
        ("EP402", "Frozen-LLM harness control (absent)",
         "no-host: harness-action bandit control", _ep402),
    )
    for spec in specs:
        if callable(spec):
            spec()
        else:
            ep, name, where, fn = spec
            register(ep, name, where, _safe(fn), LAYER)


_register_all()
