#!/usr/bin/env python3
"""verification_gate.py — Unified verification gate (SOTA blueprint P1, L3).

Composes six steals into one verify-before-accept entry point for every
layer transition (tool calls, memory writes, rollouts):

- CURE (EP413): uncertainty-focal repair — confidence margins locate fragile
  nodes; repair expands ONLY there. Never re-run whole chains.
- AsymSpec (EP461): asymmetric verify — full-context draft vs compressed-view
  verifier; accept iff divergence is under the gate.
- CONFIRMED (EP452): cost-bounded generate-critique-revise with early stop;
  zero gradients, prompt-level meta-rewards.
- PolicyGuide (EP453): workflow graphs + step remediation at boundaries —
  remediate, don't just allow/deny.
- SLEUTH (EP370): structured epistemic working set — Confirmed Facts grounded
  to sources / Active Hypotheses ranked by evidence / Open Questions, so the
  recall loop surfaces investigative state instead of raw rows.
- DeepVerifier (EP152): rubric decomposition into targeted sub-questions plus
  the failure taxonomy that builds each rubric — sized to the verifier's
  compressed view (the AsymSpec asymmetry), never the drafter's.

Deliberately stdlib-only and LLM-free: detectors operate on caller-supplied
confidences, claim sets, and workflow graphs. Existing heavy gates
(sentinel_enforcer 5-phase, ComplianceOrchestrator 13 components) are NOT
replaced — this module is the thin per-transition check that runs before
them and records a `verification_gate` ledger entry for every verdict.

Usage:
    from CORE.verification_gate import verify_before_accept

    verdict = verify_before_accept(
        subject="worm-edit:cluster_dispatch",
        confidences=[{"id": "plan", "confidence": 0.9}],
        draft_claims=["a", "b"], verified_claims=["a", "b"],
        workflow={"steps": [{"id": "edit", "requires": ["plan"],
                             "remediate": "write the plan first"}]},
        completed=["plan"],
    )
    # {"accepted": True, "reasons": [...], "remediations": [...], "citation": ...}
"""

from typing import Any, Callable, Dict, List, Optional

#: Fail-open swallows recorded when the runtime recorder is unavailable. An
#: unrecorded swallow is indistinguishable from success, so every handler in
#: this module records first.
_SWALLOWED: List[str] = []


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # runtime absent: stay importable
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        _SWALLOWED.append("%s:no-recorder" % where)


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow. Never raises."""
    try:
        note_failure(where, exc)
    except Exception as _e:
        _SWALLOWED.append("%s:%s" % (where, type(_e).__name__))


# -- CURE: uncertainty margins ---------------------------------------------
def margin_check(items: List[Dict[str, Any]],
                 threshold: float = 0.7) -> Dict[str, Any]:
    """Locate fragile nodes by confidence margin (CURE steal).

    Args:
        items: [{"id": str, "confidence": float 0..1}, ...].
        threshold: minimum acceptable confidence (mirrors ConfidenceGate 0.7).

    Returns:
        {"fragile_ids": [...], "min_margin": float, "n_fragile": int,
         "passed": bool} — margin = confidence - threshold; repair expands
        ONLY at fragile ids, never the whole chain.
    """
    fragile: List[str] = []
    min_margin = 1.0
    for it in items:
        try:
            conf = float(it.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            note_failure("verification_gate:L84", exc)
            conf = 0.0
        margin = conf - threshold
        if margin < min_margin:
            min_margin = margin
        if margin < 0:
            fragile.append(str(it.get("id", "?")))
    return {"fragile_ids": fragile, "min_margin": min_margin,
            "n_fragile": len(fragile), "passed": not fragile}


def estimate_run_failure(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Trajectory-as-stochastic-process failure estimator (EP156, half).

    p(fail) = 1 - prod(confidence): the independence-assumption estimator the
    residual names as missing. Temporal correlation (failure probability that
    evolves over the run) is NOT modelled — that half stays deferred.
    """
    p_ok = 1.0
    n = 0
    for it in items or []:
        try:
            conf = float(it.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            note_failure("verification_gate:L-est", exc)
            conf = 0.0
        p_ok *= max(0.0, min(1.0, conf))
        n += 1
    return {"p_fail": round(1.0 - p_ok, 6), "p_success": round(p_ok, 6),
            "n_steps": n,
            "assumption": ("step-independence; temporal correlation "
                           "NOT modelled")}


def rank_candidates_by_agreement(explorer: List[Dict[str, Any]],
                                 validator: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Candidate RANKING by validator agreement (EP349, ranking half).

    Joins explorer/validator vectors on id, ranks by 1-|e-v| descending.
    The continuous logit-expectation statistic stays deferred: no logits
    exist on this box, only supplied confidences.
    """
    val = {}
    for v in validator or []:
        try:
            val[str(v.get("id"))] = float(v.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            note_failure("verification_gate:L-rank", exc)
    scored = []
    for e in explorer or []:
        cid = str(e.get("id", "?"))
        if cid not in val:
            continue
        try:
            ec = float(e.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            note_failure("verification_gate:L-rank", exc)
            ec = 0.0
        scored.append((cid, round(1.0 - abs(ec - val[cid]), 6)))
    scored.sort(key=lambda kv: -kv[1])
    mean = round(sum(s for _, s in scored) / len(scored), 6) if scored else 0.0
    return {"ranking": [cid for cid, _ in scored],
            "agreements": {cid: s for cid, s in scored},
            "mean_agreement": mean, "n_compared": len(scored)}


def repair_tree(items: List[Dict[str, Any]],
                threshold: float = 0.7) -> Dict[str, Any]:
    """Bounded repair TREE with resync point (EP413, tree half).

    margin_check returns fragile IDS; this builds the tree: one node per
    fragile id ({id, margin, repair: re-verify}), one resync root (the
    highest-confidence non-fragile id, or "root" when all are fragile).
    Resync EXECUTION (re-running the chain from the root) stays deferred:
    no chain-runner exists at this layer — the tree names where it runs.
    """
    fragile: List[str] = []
    margins: Dict[str, float] = {}
    best_id, best_conf = "root", -1.0
    for it in items or []:
        try:
            conf = float(it.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            note_failure("verification_gate:L-tree", exc)
            conf = 0.0
        cid = str(it.get("id", "?"))
        margin = conf - threshold
        margins[cid] = round(margin, 6)
        if margin < 0:
            fragile.append(cid)
        elif conf > best_conf:
            best_id, best_conf = cid, conf
    nodes = [{"id": cid, "margin": margins[cid],
               "repair": f"re-verify:{cid}"} for cid in fragile]
    return {"nodes": nodes, "n_fragile": len(nodes),
            "resync": best_id, "passed": not nodes}


# -- AsymSpec: divergence gate ----------------------------------------------
def divergence_gate(draft_claims: List[str], verified_claims: List[str],
                    max_divergence: float = 0.1) -> Dict[str, Any]:
    """Accept iff the verifier's compressed view covers the draft (AsymSpec).

    Divergence = |draft - verified| / |draft| (0 when draft is empty).
    Returns {"divergence": float, "accepted": bool, "dropped_claims": [...]}
    — dropped claims are the repair targets, not a deny reason.
    """
    draft = [str(c) for c in draft_claims]
    verified = set(str(c) for c in verified_claims)
    dropped = [c for c in draft if c not in verified]
    divergence = (len(dropped) / len(draft)) if draft else 0.0
    return {"divergence": divergence,
            "accepted": divergence <= max_divergence,
            "dropped_claims": dropped}


# -- AsymSpec: asymmetric accept (EP461, accept half) -------------------------
def asymmetric_accept(draft_claims: List[str],
                      verifier_claims: Optional[List[str]] = None,
                      confidences: Optional[List[float]] = None,
                      verifier_budget: int = 2,
                      max_divergence: float = 0.1) -> Dict[str, Any]:
    """Full-context draft steers a COMPRESSED verifier; fuse into an accept.

    `divergence_gate` grades two caller-supplied views; it never BUILDS the
    asymmetry the technique is named for. This is the accept half:

    1. BUILD — the verifier's view is the budgeted top slice of the FULL
       draft (ordered by `confidences`, index-aligned to the draft, then
       first-seen tie-break). "Sized to the verifier's compressed view,
       never the drafter's" — the asymmetry named in this module's
       docstring, produced here instead of assumed.
    2. VERIFY — the verifier may only cover what its view showed it:
       ``divergence_gate(verifier_view, verifier_claims)`` is the compressed
       gate; a claim confirmed without being shown is a tamper signal,
       reported as ``unseen_verified``.
    3. FUSE — accept iff the compressed gate passes AND the full-coverage
       gate passes AND nothing unseen was verified.

    Empty draft fails open (nothing drafted, nothing to verify), matching
    divergence_gate's empty-draft rule. Stdlib-only and LLM-free.
    """
    draft = [str(c) for c in (draft_claims or [])]
    n = len(draft)
    try:
        budget = max(0, int(verifier_budget))
    except (TypeError, ValueError) as exc:
        _note_failure("verification_gate:asymspec-budget", exc)
        budget = 0
    order = list(range(n))
    if confidences is not None and len(confidences) == n:
        parsed: List[float] = []
        for v in confidences:
            try:
                parsed.append(float(v))
            except (TypeError, ValueError) as exc:
                _note_failure("verification_gate:asymspec-conf", exc)
                parsed.append(0.0)
        order.sort(key=lambda i: (-parsed[i], i))
    verifier_view = [draft[i] for i in order[:budget]]
    seen = set(verifier_view)
    verified_list = [str(c) for c in (verifier_claims or [])]
    unseen = [c for c in verified_list if c not in seen]

    compressed = divergence_gate(verifier_view, verified_list, max_divergence)
    full = divergence_gate(draft, verified_list, max_divergence)
    accepted = bool(compressed["accepted"] and full["accepted"] and not unseen)
    return {"verifier_view": verifier_view,
            "n_draft": n, "n_verifier_view": len(verifier_view),
            "unseen_verified": unseen,
            "compressed": {"divergence": compressed["divergence"],
                           "accepted": bool(compressed["accepted"])},
            "full": {"divergence": full["divergence"],
                     "accepted": bool(full["accepted"])},
            "dropped_claims": full["dropped_claims"],
            "accepted": accepted}


# -- CONFIRMED: cost-bounded revise loop -------------------------------------
def confirmed_revise(initial: Any,
                     critique_fn: Callable[[Any], Optional[str]],
                     revise_fn: Callable[[Any, str], Any],
                     max_depth: int = 3) -> Dict[str, Any]:
    """Generate-critique-revise until CONFIRMED or depth exhausted.

    critique_fn(candidate) returns None when CONFIRMED, else a critique
    string. revise_fn(candidate, critique) returns the next candidate.
    Returns {"final": candidate, "depths_used": int, "confirmed": bool,
    "stopped_early": bool} — early stop on CONFIRMED bounds cost without
    gradients (CONFIRMED steal: 82-88% stop at equal accuracy).
    """
    candidate = initial
    for depth in range(1, int(max_depth) + 1):
        critique = critique_fn(candidate)
        if critique is None:
            return {"final": candidate, "depths_used": depth - 1,
                    "confirmed": True, "stopped_early": depth > 1}
        if depth >= int(max_depth):
            break
        candidate = revise_fn(candidate, critique)
    return {"final": candidate, "depths_used": int(max_depth),
            "confirmed": critique_fn(candidate) is None,
            "stopped_early": False}


# -- PolicyGuide: workflow step check ----------------------------------------
def verify_workflow_step(step_id: str, completed: List[str],
                         workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Check one workflow-graph boundary; remediate, don't deny (PolicyGuide).

    workflow = {"steps": [{"id", "requires": [...], "remediate": str}]}.
    Returns {"proceed": bool, "remediation": str} — empty remediation when
    all requirements are completed.
    """
    steps = {s.get("id"): s for s in workflow.get("steps", [])
             if isinstance(s, dict)}
    step = steps.get(step_id)
    if step is None:
        return {"proceed": False,
                "remediation": f"unknown step '{step_id}': add it to the "
                               f"workflow graph before proceeding"}
    done = set(completed)
    missing = [r for r in step.get("requires", []) if r not in done]
    if missing:
        return {"proceed": False,
                "remediation": step.get("remediate", "") +
                f" (missing: {', '.join(missing)})"}
    return {"proceed": True, "remediation": ""}


# -- ACID: confidence-divergence validation ----------------------------------
def confidence_divergence_check(explorer: List[Dict[str, Any]],
                                validator: List[Dict[str, Any]],
                                max_mean_div: float = 0.15,
                                max_max_div: float = 0.30) -> Dict[str, Any]:
    """Paired explorer-vs-validator disagreement (ACID validate step, EP441).

    Distinct from margin_check (absolute, single-source) and divergence_gate
    (string-coverage, no numbers): joins two numeric confidence vectors on
    node id and gates on mean/max |explorer - validator|. Fires exactly when
    the other two pass — the confident-but-wrong case.

    Ids missing from validator are coverage's job (not penalized here);
    validator-only ids are reported, never fail alone. Empty intersection
    fails open (passed=True), matching divergence_gate's empty-draft rule.
    Non-numeric confidences coerce to 0.0 (margin_check rule).
    """
    def _conf(item: Dict[str, Any]) -> float:
        try:
            return float(item.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:        # noqa: BLE001 - malformed input
            _note_failure("verification_gate:confidence-parse", exc)
            return 0.0

    val = {str(v.get("id", "?")): _conf(v) for v in validator}
    exp_ids = {str(e.get("id", "?")) for e in explorer}
    per_id: Dict[str, float] = {}
    validator_only = [i for i in val if i not in exp_ids]
    for e in explorer:
        eid = str(e.get("id", "?"))
        if eid in val:
            per_id[eid] = abs(_conf(e) - val[eid])
    diverged = [i for i, dd in per_id.items() if dd > max_max_div]
    mean_div = (sum(per_id.values()) / len(per_id)) if per_id else 0.0
    max_div = max(per_id.values()) if per_id else 0.0
    passed = (mean_div <= max_mean_div) and (max_div <= max_max_div)
    return {"per_id": per_id, "mean_div": mean_div, "max_div": max_div,
            "diverged_ids": diverged, "n_compared": len(per_id),
            "n_validator_only": len(validator_only), "passed": passed}


# -- EP383: the drift verdict must terminate, not just be recorded ------------
#: Bound as the MODULE, not as `from CORE.circuit_breaker import supervise_drift`.
#: This closes a cycle -- circuit_breaker imports `confidence_divergence_check`
#: from this module at module level -- and binding the module OBJECT is what
#: survives whichever side is imported first: the attribute is resolved at CALL
#: time, so it is reachable even when circuit_breaker is still mid-import here.
#: The block deliberately sits BELOW that function's definition, because when
#: circuit_breaker is the entry point it must find the symbol already defined.
#: An in-function import would work but is banned: the repo's anti-pattern
#: ratchet counts `import_inside_function`.
try:
    from CORE import circuit_breaker as _circuit_breaker
except ImportError as exc:                       # breaker absent: gate still works
    _note_failure("verification_gate:circuit-breaker-missing", exc)
    _circuit_breaker = None


def _terminate_on_drift(subject: str, explorer: List[Dict[str, Any]],
                        validator: List[Dict[str, Any]]) -> str:
    """Trip the named breaker on a failed divergence check (EP383).

    Why this exists: `confidence_divergence_check` detects the
    confident-but-wrong case, but a detected drift terminated nothing -- the
    finding was a verdict with no consequence. `CircuitBreaker.force_trip` is
    the consequence, so the gate reaches it here.

    Returns a reason string for the gate's own record, because a trip is
    evidence the safety-drift signal reached a terminator and a missing breaker
    is a NAMED gap rather than a silent pass. Never raises, and never trips on
    uncertainty -- an unproven drift is not a drift.
    """
    hook = getattr(_circuit_breaker, "supervise_drift", None)
    if hook is None:
        return "drift-trip-skipped(no-circuit-breaker)"
    try:
        out = hook(subject, explorer, validator)
    except Exception as exc:                      # noqa: BLE001 - supervision is best-effort
        _note_failure("verification_gate:drift-trip", exc)
        return "drift-trip-failed(%s)" % type(exc).__name__
    if out.get("tripped"):
        return "drift-tripped(%s)" % (out.get("reason") or "safety-drift")
    return "drift-held(gaps=%s)" % (",".join(out.get("gaps") or []) or "none")


# -- TDD-Agent: dual-track gate -------------------------------------------------
def tdd_dual_track_check(tests_track: Optional[Dict[str, Any]],
                         code_track: Optional[Dict[str, Any]],
                         require_tests_first: bool = True) -> Dict[str, Any]:
    """Tests-as-evolving-artifacts gate (TDD-Agent, EP448).

    Checks caller-supplied evidence only — NEVER executes tests (keeps this
    module stdlib/LLM-free). Tracks: {"artifact": str, "version": int,
    "passed": bool, "feedback_ref": str}. Passed iff both tracks present,
    tests passing, and tests version >= code version (tests-first order).
    """
    if not tests_track:
        return {"tests_first": False, "dual_present": False,
                "tests_passing": False, "passed": False,
                "remediation": "write the executable test first; "
                               "tests-track evidence missing"}
    if not code_track:
        return {"tests_first": True, "dual_present": False,
                "tests_passing": bool(tests_track.get("passed", False)),
                "passed": False,
                "remediation": "code-track evidence missing for "
                               f"{tests_track.get('artifact', '?')}"}
    try:
        tv = int(tests_track.get("version", 0))
        cv = int(code_track.get("version", 0))
    except (TypeError, ValueError) as exc:
        note_failure("verification_gate:L231", exc)
        tv, cv = 0, 0
    first = (tv >= cv) if require_tests_first else True
    passing = bool(tests_track.get("passed", False))
    ok = first and passing
    rem = ""
    if not ok:
        if not passing:
            rem = (f"make {tests_track.get('artifact', '?')} green first "
                   f"({tests_track.get('feedback_ref', 'no feedback ref')})")
        else:
            rem = (f"tests-track v{tv} older than code-track v{cv}: "
                   f"evolve the test first, then the code")
    return {"tests_first": first, "dual_present": True,
            "tests_passing": passing, "passed": ok, "remediation": rem}


# -- Unified entry ------------------------------------------------------------
def verify_before_accept(subject: str,
                         confidences: Optional[List[Dict[str, Any]]] = None,
                         draft_claims: Optional[List[str]] = None,
                         verified_claims: Optional[List[str]] = None,
                         workflow: Optional[Dict[str, Any]] = None,
                         step_id: Optional[str] = None,
                         completed: Optional[List[str]] = None,
                         record: bool = True,
                         confidence_pairs: Optional[Any] = None,
                         tdd_tracks: Optional[Dict[str, Any]] = None,
                         verifier_budget: Optional[int] = None) -> Dict[str, Any]:
    """One acceptance gate with detectors + remediation (P1 slices 1+2).

    Runs the margin check, the divergence gate, the confidence-divergence
    check (when confidence_pairs is given), the TDD dual-track check (when
    tdd_tracks is given), and (when a step_id is given) the workflow
    boundary check. Accepted iff ALL detectors pass.
    Every verdict is written to the hash-chained ledger as a
    `verification_gate` entry (lazy import, fails open) — the citation is
    returned for gate assertions.

    Remediation rule: a rejection ALWAYS carries at least one remediation
    string (repair targets), never a bare deny.
    """
    reasons: List[str] = []
    remediations: List[str] = []
    accepted = True

    if confidences is not None:
        m = margin_check(confidences)
        if m["passed"]:
            reasons.append(f"margins-ok(min={m['min_margin']:.2f})")
        else:
            accepted = False
            reasons.append(f"fragile-nodes:{','.join(m['fragile_ids'])}")
            remediations.append(
                "repair ONLY at fragile nodes "
                f"({', '.join(m['fragile_ids'])}); do not re-run the chain")
            # EP413 (CURE) tree half, absorbed: `repair_tree` used to be built
            # only by the exercise layer (_l3_verify) and its own tests, so the
            # technique was registered while the live gate named no resync point.
            # The tree's docstring says resync EXECUTION is deferred because no
            # chain-runner exists here -- so the tree's job at this seam is to
            # NAME where re-verification starts. That is what this adds.
            _tree = repair_tree(confidences)
            remediations.append(
                f"EP413 resync-root={_tree['resync']} "
                f"({_tree['n_fragile']} fragile node(s) to re-verify)")

    if draft_claims is not None or verified_claims is not None:
        if verifier_budget is not None:
            # EP461 (AsymSpec) accept half, absorbed: the compressed verifier
            # view is BUILT here from the supplied draft and the accept fuses
            # compressed coverage + full coverage + no unseen verification.
            # Opt-in: verifier_budget=None keeps the plain divergence gate
            # above byte-for-byte.
            a = asymmetric_accept(draft_claims or [], verified_claims or [],
                                  verifier_budget=verifier_budget)
            if a["accepted"]:
                reasons.append(f"asym-ok(view={a['n_verifier_view']}/"
                               f"{a['n_draft']},"
                               f"div={a['full']['divergence']:.2f})")
            else:
                accepted = False
                reasons.append(f"asym-blocked(view={a['n_verifier_view']}/"
                               f"{a['n_draft']},"
                               f"div={a['full']['divergence']:.2f})")
                targets: List[str] = list(a["dropped_claims"])
                rem = ("run the compressed verifier over: "
                       f"{', '.join(a['verifier_view']) or 'empty draft'}")
                if a["unseen_verified"]:
                    rem += (f"; verifier confirmed unviewed claims "
                            f"{', '.join(a['unseen_verified'])}")
                if targets:
                    rem += f"; reconcile dropped claims: {', '.join(targets)}"
                remediations.append(rem)
        else:
            d = divergence_gate(draft_claims or [], verified_claims or [])
            if d["accepted"]:
                reasons.append(f"divergence-ok({d['divergence']:.2f})")
            else:
                accepted = False
                reasons.append(f"divergence-high({d['divergence']:.2f})")
                remediations.append(
                    "reconcile dropped claims: "
                    f"{', '.join(d['dropped_claims'])}")

    if confidence_pairs is not None:
        if isinstance(confidence_pairs, dict):
            exp, val = (confidence_pairs.get("explorer", []),
                        confidence_pairs.get("validator", []))
        else:
            exp, val = confidence_pairs
        c = confidence_divergence_check(exp, val)
        if c["passed"]:
            reasons.append(
                f"confdiv-ok(mean={c['mean_div']:.2f},max={c['max_div']:.2f})")
        else:
            accepted = False
            reasons.append(
                f"confdiv-high(mean={c['mean_div']:.2f},max={c['max_div']:.2f})")
            remediations.append(
                "reconcile diverged nodes "
                f"({', '.join(c['diverged_ids'])}): explorer vs validator gap")
            # EP383: detection is not supervision -- make the drift terminate.
            reasons.append(_terminate_on_drift(subject, exp, val))

        # EP417 (S-RCT) calibration half, absorbed: `preperiod_calibrate` fits a
        # least-squares scale+bias over pre-period predicted/actual pairs and
        # reports the squared-error cut (the paper's ~77x). It was registered as
        # EP417's mechanism (technique_wiring/_l3_verify.py) with ZERO callers
        # repo-wide. The gate already holds exactly the predicted (explorer) /
        # actual (validator) pair, so the fit runs on the SAME id-join the
        # divergence check uses rather than on a synthetic vector. The result is
        # not a dangling report: it enters `reasons`, i.e. the gate's verdict and
        # (via the EP446 hook above) the audited evidence.
        _vmap = {str(v.get("id", "?")): v for v in (val or [])}
        _x: List[float] = []
        _y: List[float] = []
        for _e in (exp or []):
            _pair = _vmap.get(str(_e.get("id", "?")))
            if _pair is None:
                continue
            try:
                _x.append(float(_e.get("confidence", 0.0)))
                _y.append(float(_pair.get("confidence", 0.0)))
            except (TypeError, ValueError) as exc:    # noqa: BLE001 - malformed input
                _note_failure("verification_gate:ep417-calib-parse", exc)
                continue
        _cal = preperiod_calibrate(_x, _y)
        if _cal["n_pairs"] >= 2:
            # `error_ratio` is the host's own contract and is +inf when the fit is
            # exact (calibrated SSE == 0). Render that as "perfect" rather than
            # letting "inf" leak into a verdict string.
            _cut = _cal["error_ratio"]
            _cut_txt = "perfect" if _cut == float("inf") else f"{_cut:.2f}x"
            reasons.append(
                f"s-rct-calib(scale={_cal['scale']:.3f},"
                f"bias={_cal['bias']:.3f},error-cut={_cut_txt})")

    if tdd_tracks is not None:
        t = tdd_dual_track_check(tdd_tracks.get("tests"),
                                 tdd_tracks.get("code"))
        if t["passed"]:
            reasons.append(
                f"tdd-ok({tdd_tracks.get('tests', {}).get('artifact', '?')})")
        else:
            accepted = False
            reasons.append("tdd-blocked")
            remediations.append(t["remediation"])

    if step_id is not None:
        w = verify_workflow_step(step_id, completed or [], workflow or {})
        if w["proceed"]:
            reasons.append(f"workflow-ok({step_id})")
        else:
            accepted = False
            reasons.append(f"workflow-blocked({step_id})")
            remediations.append(w["remediation"])

    citation = ""
    if record:
        try:
            from CORE import audit_trail as _at  # noqa: PLC0415 (lazy: no cycle)
            citation = _at.audit_verification_gate(
                subject, accepted, reasons, remediations)
            # EP446 (ATP capsules/negatives), absorbed: `audit_verified_negative`
            # is the documented home for "proof that something did NOT occur"
            # (audit_trail.py:691) and had ZERO callers outside its own module --
            # registered while the mechanism ran nowhere. An ACCEPTED gate is
            # exactly that proof: every supplied condition was tested and the
            # failure did not occur. Recorded on the same hash chain, behind the
            # same `record` opt-in, so nothing is written when recording is off.
            if accepted:
                _at.audit_verified_negative(
                    subject,
                    claim="verification gate conditions held; no failure detected",
                    evidence="; ".join(reasons) or "no checks supplied",
                    checked_by="CORE.verification_gate:verify_before_accept")
        except Exception as exc:                      # noqa: BLE001 - ledger best-effort
            _note_failure("verification_gate:gate-cite", exc)

    return {"accepted": accepted, "reasons": reasons,
            "remediations": remediations, "citation": citation}


# -- S-RCT: simulated-trial rollout vetting (EP417) ---------------------------
def preperiod_calibrate(predicted: List[float],
                        actual: List[float]) -> Dict[str, Any]:
    """Two-phase pre-period calibration: fit scale+bias, report error cut.

    S-RCT steal: agent-simulated outcomes overshoot magnitudes; calibrating
    on pre-period pairs (least-squares scale+bias) cuts squared error (~77x
    in the paper). Pure stdlib. Returns {"scale", "bias", "raw_sse",
    "calibrated_sse", "error_ratio"} — error_ratio = raw/calibrated.
    Pairs are truncated to the shorter list; <2 pairs fails open.
    """
    n = min(len(predicted), len(actual))
    if n < 2:
        return {"scale": 1.0, "bias": 0.0, "raw_sse": 0.0,
                "calibrated_sse": 0.0, "error_ratio": 1.0, "n_pairs": n}
    xs = [float(v) for v in predicted[:n]]
    ys = [float(v) for v in actual[:n]]
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    scale = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom) \
        if denom else 0.0
    bias = my - scale * mx
    raw_sse = sum((x - y) ** 2 for x, y in zip(xs, ys))
    cal_sse = sum((scale * x + bias - y) ** 2 for x, y in zip(xs, ys))
    ratio = (raw_sse / cal_sse) if cal_sse else float("inf")
    return {"scale": scale, "bias": bias, "raw_sse": raw_sse,
            "calibrated_sse": cal_sse, "error_ratio": ratio, "n_pairs": n}


def within_subject_compare(arm_a: List[float],
                           arm_b: List[float]) -> Dict[str, Any]:
    """Within-subject A/B comparison: each subject sees both arms (S-RCT).

    Paired differences kill between-subject variance (~2.4x SE cut in the
    paper). Returns {"effect" (mean b-a), "se", "n", "direction"} where
    direction is +1/-1/0 by sign of effect. <2 pairs fails open (se=inf).
    """
    n = min(len(arm_a), len(arm_b))
    if n < 2:
        return {"effect": 0.0, "se": float("inf"), "n": n, "direction": 0}
    diffs = [float(b) - float(a)
             for a, b in zip(arm_a[:n], arm_b[:n])]
    effect = sum(diffs) / n
    var = sum((d - effect) ** 2 for d in diffs) / (n - 1)
    se = (var ** 0.5) / (n ** 0.5)
    direction = 1 if effect > 0 else (-1 if effect < 0 else 0)
    return {"effect": effect, "se": se, "n": n, "direction": direction}


# -- Boundary adapter: gate verdict -> tool-call remediation block -----------
def format_boundary_report(verdict: Dict[str, Any]) -> str:
    """Render a verify_before_accept verdict as a boundary decision block.

    PolicyGuide shape: PROCEED with reasons, or DENIED with step
    remediations — the text a tool-call boundary (sentinel_enforcer,
    PreActionGate, opencode plugin) attaches to its decision instead of a
    bare deny. Wiring those call sites to CALL verify_before_accept is the
    remaining integration step; this function is the shared output format.
    """
    if verdict.get("accepted"):
        lines = ["GATE: PROCEED"] + \
            [f"  ok: {r}" for r in verdict.get("reasons", [])]
    else:
        lines = ["GATE: DENIED — remediate, do not retry blindly"] + \
            [f"  fail: {r}" for r in verdict.get("reasons", [])] + \
            [f"  fix: {m}" for m in verdict.get("remediations", [])]
    if verdict.get("citation"):
        lines.append(f"  ledger: {verdict['citation']}")
    return "\n".join(lines)


# -- SLEUTH: structured epistemic working set (EP370) --------------------------
def _evidence_of(item: Dict[str, Any]) -> float:
    """Read an evidence weight, coercing anything non-numeric to 0.0.

    Same coercion rule margin_check uses for confidences, so a malformed
    weight ranks last instead of raising.
    """
    try:
        return float(item.get("evidence", item.get("weight", 0.0)))
    except (TypeError, ValueError) as exc:            # noqa: BLE001 - malformed input
        _note_failure("verification_gate:evidence-weight-parse", exc)
        return 0.0


def epistemic_state(findings: Optional[List[Any]] = None,
                    hypotheses: Optional[List[Any]] = None,
                    questions: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Build the three-bucket working set: confirmed / hypotheses / open.

    SLEUTH steal (EP370). The gate could already commit-or-early-stop on a
    subject, but it had no structured investigative state to trigger on --
    recall returned raw rows. This is that state.

    Args:
        findings: candidate facts. Each is a dict
            {"fact"|"claim", "sources": [str], "evidence": float} or a bare
            string. A finding WITH at least one named source is confirmed;
            one WITHOUT is DEMOTED to a hypothesis and the demotion is named
            in `gaps` (an unsourced fact is a hypothesis, not a fact).
        hypotheses: additional hypotheses, same item shape ("hypothesis" or
            "fact" key). These are never promoted — promotion requires a
            source, and a source is what `findings` carries.
        questions: unresolved questions (str, or {"question"/"text"}).

    Returns:
        {"confirmed": [{"fact", "sources"}], "hypotheses":
        [{"hypothesis", "evidence"}] ranked by evidence desc (ties broken by
        text for determinism), "open": [str], counts, "gaps", "ok"}.
        `ok` is False only when the working set is entirely empty.
    """
    confirmed: List[Dict[str, Any]] = []
    ranked: List[Dict[str, Any]] = []
    gaps: List[str] = []

    for raw in (findings or []):
        item = raw if isinstance(raw, dict) else {"fact": str(raw)}
        fact = str(item.get("fact", item.get("claim", "")) or "").strip()
        if not fact:
            gaps.append("empty-finding")
            continue
        sources = [str(s).strip() for s in (item.get("sources") or [])
                   if str(s).strip()]
        if sources:
            confirmed.append({"fact": fact, "sources": sources})
        else:
            ranked.append({"hypothesis": fact,
                           "evidence": _evidence_of(item)})
            gaps.append(f"demoted-unsourced:{fact}")

    for raw in (hypotheses or []):
        item = raw if isinstance(raw, dict) else {"hypothesis": str(raw)}
        text = str(item.get("hypothesis", item.get("fact", "")) or "").strip()
        if not text:
            gaps.append("empty-hypothesis")
            continue
        ranked.append({"hypothesis": text, "evidence": _evidence_of(item)})

    ranked.sort(key=lambda h: (-h["evidence"], h["hypothesis"]))

    open_q: List[str] = []
    for raw in (questions or []):
        text = str(raw.get("question", raw.get("text", ""))
                   if isinstance(raw, dict) else raw).strip()
        if text:
            open_q.append(text)

    ok = bool(confirmed or ranked or open_q)
    if not ok:
        gaps.append("no-working-set")
    return {"confirmed": confirmed, "hypotheses": ranked, "open": open_q,
            "n_confirmed": len(confirmed), "n_hypotheses": len(ranked),
            "n_open": len(open_q), "gaps": gaps, "ok": ok}


# -- DeepVerifier: rubric decomposition + failure taxonomy (EP152) --------------
#: The failure classes a rubric can be built from. Each sub-question is
#: assigned one, so a rubric is a top-down decomposition into checkable parts
#: rather than one holistic judgement (the DeepVerifier asymmetry).
_RUBRIC_TAXONOMY: Dict[str, str] = {
    "omission": "the artifact never addresses the sub-question",
    "unsupported-claim": "the sub-question is asserted with no cited evidence",
    "inconsistency": "two sub-question answers contradict each other",
    "incompleteness": "the sub-question is partly answered, with a named gap",
}


def _split_criterion(criterion: str) -> List[str]:
    """Split a criterion into atomic clauses (order-preserving, deduped)."""
    text = str(criterion or "").strip()
    if not text:
        return []
    parts: List[str] = []
    for chunk in text.replace(";", ",").split(","):
        for piece in chunk.split(" and "):
            piece = piece.strip().strip(".").strip()
            if piece:
                parts.append(piece)
    out: List[str] = []
    seen = set()
    for p in parts:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def decompose_rubric(criterion: str,
                     failure_modes: Optional[Dict[str, str]] = None,
                     max_subquestions: int = 6) -> Dict[str, Any]:
    """Decompose one rubric criterion into targeted, checkable sub-questions.

    DeepVerifier steal (EP152). The gate already had a unified verdict, but a
    single holistic judgement cannot say WHICH part failed. This splits the
    criterion on its atomic clauses and assigns each sub-question a failure
    class from the taxonomy — rubrics are BUILT from the taxonomy, not
    hand-written per subject.

    The verifier's view is the compressed side of the AsymSpec asymmetry, so
    the sub-question count is capped: an unbounded decomposition would hand
    the verifier more view than the drafter ever had.

    Args:
        criterion: the rubric criterion text.
        failure_modes: optional extra/override classes {name: description}.
        max_subquestions: cap on the decomposition (6 by default).

    Returns:
        {"criterion", "subquestions": [str], "taxonomy": {"classes":
        [{"class", "detects", "subquestions": [idx]}], "by_subquestion":
        {idx: class}}, "n_subquestions", "gaps", "ok"}. An empty criterion
        is a named gap, not a crash.
    """
    gaps: List[str] = []
    clauses = _split_criterion(criterion)
    if not clauses:
        gaps.append("criterion-empty")
    subquestions = [f"Does the artifact satisfy: {c}?" for c in clauses]
    try:
        cap = int(max_subquestions)
    except (TypeError, ValueError) as exc:
        note_failure("verification_gate:L571", exc)
        cap = 0
    if cap and len(subquestions) > cap:
        dropped = len(subquestions) - cap
        subquestions = subquestions[:cap]
        gaps.append(f"subquestions-capped:{dropped}")

    classes = dict(_RUBRIC_TAXONOMY)
    for k, v in (failure_modes or {}).items():
        classes[str(k)] = str(v)
    names = sorted(classes)
    by_subquestion = ({i: names[i % len(names)] for i in range(len(subquestions))}
                      if subquestions and names else {})
    taxonomy = {
        "classes": [{"class": c, "detects": classes[c],
                     "subquestions": [i for i, cc in by_subquestion.items()
                                      if cc == c]} for c in names],
        "by_subquestion": by_subquestion,
    }
    if subquestions and not by_subquestion:
        gaps.append("taxonomy-empty")
    ok = bool(subquestions) and bool(by_subquestion)
    return {"criterion": str(criterion or "").strip(),
            "subquestions": subquestions, "taxonomy": taxonomy,
            "n_subquestions": len(subquestions), "gaps": gaps, "ok": ok}


def record_epistemic(result: Dict[str, Any]) -> str:
    """Cite an epistemic working-set verdict on the ledger (fails open).

    Subject "epistemic:<n-confirmed>" — the count is the trigger SLEUTH needs
    ("only works on structured state").
    """
    result = result or {}
    try:
        n = int(result.get("n_confirmed", len(result.get("confirmed", []) or [])))
    except (TypeError, ValueError) as exc:
        note_failure("verification_gate:L607", exc)
        n = 0
    accepted = bool(result.get("ok", False))
    gaps = list(result.get("gaps", []) or [])
    citation = ""
    try:
        from CORE import audit_trail as _at  # noqa: PLC0415 (lazy: no cycle)
        citation = _at.audit_verification_gate(
            f"epistemic:{n}", accepted,
            [f"epistemic-confirmed:{n}",
             f"epistemic-hypotheses:{result.get('n_hypotheses', 0)}",
             f"epistemic-open:{result.get('n_open', 0)}"],
            [f"resolve: {g}" for g in gaps])
    except Exception as exc:                          # noqa: BLE001 - ledger best-effort
        _note_failure("verification_gate:epistemic-cite", exc)
    return citation
