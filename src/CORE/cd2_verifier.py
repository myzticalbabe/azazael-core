# coding: utf-8
"""
cd2_verifier.py — CD2 Z3 SMT Formal Verification Module

Cross-Domain: CD2 (Compliance + Governance)
Spec: research/CROSS_DOMAIN.md sec.CD2

Provides:
  - serialize_solver_result() — Canonical Z3 result serialization for
    inter-agent transfer and audit trail storage
  - SMTVerifier class — Worker-Verifier-Judge pipeline with pure-Python
    fallback when z3-solver is not installed
  - localize_violation() / localize_violations() — LogicLoc (EP183): turn a flat
    violation string into the SMALLEST implicated region plus a repair hint
  - Audit trail integration via DirectMemory (z3_verification event type)

Usage:
    from CORE.cd2_verifier import SMTVerifier, serialize_solver_result

    # Auto-detect z3-solver, falls back to pure-Python
    verifier = SMTVerifier()
    result = verifier.verify_constraints({...})
    print(result["result"])  # "SAT" or "UNSAT"

    # Explicit z3 usage
    if verifier.z3_available:
        import z3
        solver = z3.Solver()
        solver.add(z3.Int("x") > 0)
        serialized = serialize_solver_result(solver, "my_model")
"""

import re
import time
from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- z3-solver availability detection --------------------------------
try:
    import z3
    Z3_AVAILABLE = True
except ImportError as exc:
    note_failure("cd2_verifier:L38", exc)
    Z3_AVAILABLE = False


# ====================================================================
# serialize_solver_result() — Z3 Result Serialization
# ====================================================================
# Per CD2 spec (CROSS_DOMAIN.md sec.CD2):
#   - Serialize solver result for inter-agent transfer
#   - Include SMT2 constraint dump for reproducibility
#   - Extract model values (if SAT)
#   - Include solver statistics (time, memory)
def serialize_solver_result(solver: Any, model_name: str) -> Dict[str, Any]:
    """Serialize a Z3 solver result for inter-agent transfer and audit trail.

    Args:
        solver: A z3.Solver instance (or compatible object with check()
                and statistics() methods).
        model_name: Human-readable name for this verification model.

    Returns:
        dict with keys:
            model       - Model name
            result      - "SAT" / "UNSAT" / "UNKNOWN"
            smt2        - SMT-LIB2 constraint dump for reproducibility
            statistics  - Solver performance statistics
            time_ms     - Solver wall-clock time in milliseconds
            memory_mb   - Estimated memory usage in MB
            model_values - Variable assignments (if SAT)
            violations   - Counter-example details (if UNSAT)
    """
    result = solver.check()
    stats: Dict[str, str] = {}
    try:
        stats = {k: str(v) for k, v in solver.statistics()}
    except Exception as exc:
        note_failure("cd2_verifier:L73", exc)

    smt2 = ""
    try:
        smt2 = solver.to_smt2()
    except Exception as e:
        note_failure("cd2_verifier:L79", e)
        smt2 = f"; smt2 dump failed: {e}"

    # Classify the Z3 check result
    # Use z3 constants when available, fall back to string comparison
    classification = "UNKNOWN"
    try:
        from z3 import sat, unsat
        if result == sat:
            classification = "SAT"
        elif result == unsat:
            classification = "UNSAT"
    except ImportError as exc:
        note_failure("cd2_verifier:L91", exc)
        result_str = str(result)
        if result_str == "sat":
            classification = "SAT"
        elif result_str == "unsat":
            classification = "UNSAT"

    output: Dict[str, Any] = {
        "model": model_name,
        "result": classification,
        "smt2": smt2,
        "statistics": stats,
        # z3 reports statistics such as `time` as float-valued strings
        # (e.g. "0.001"); int() on that raises ValueError.
        "time_ms": int(float(stats.get("time", 0))),
        "memory_mb": round(float(stats.get("memory", 0)) / 1024, 2) if "memory" in stats else 0.0,
    }

    # Extract model values if SAT.
    #
    # `model.decls()` yields FuncDeclRef objects, NOT expressions. Passing one
    # straight to `model.eval()` raises Z3Exception("ast is not an
    # expression"), so this used to discard every model. The bare
    # `except Exception` then replaced the failure with `{}`, which a caller
    # cannot tell from a genuinely empty model -- so a satisfiable
    # constraint set reported SAT with no values and nothing looked wrong.
    #
    # Found 2026-09-29 by CD2's cross-domain check, which had been silently
    # passing because its `check()` helper only counted failures instead of
    # raising. The call is `d()` -- call the declaration to get the constant.
    if classification == "SAT":
        try:
            model = solver.model()
            output["model_values"] = {
                str(d): str(model.eval(d(), model_completion=True))
                for d in model.decls()
            }
        except Exception as exc:
            # Record why rather than returning a plausible-looking empty
            # model. An absent key is honest; a wrong one is not.
            # Record why rather than returning a plausible-looking empty
            # model. An absent key is honest; a wrong one is not.
            note_failure("cd2_verifier:L128", exc)
            output["model_values"] = {}
            output["model_values_error"] = "%s: %s" % (
                type(exc).__name__, exc)

    return output


# ====================================================================
# LogicLoc (EP183) — neurosymbolic localization of the failing region
# ====================================================================
# A verification already knows WHICH check failed, and reports it as a flat
# string. Repair work needs the SMALLEST implicated region instead: the field,
# the kind of check that failed, the declared constraint it contradicted, and a
# repair the caller can act on. LogicLoc maps the flat violation back onto that
# region, so a caller repairs one field instead of re-reading the whole set.
#
# The formats below are the ones `PurePythonVerifier.verify` actually emits --
# they are transcribed from that method, not invented, so a change to its
# wording fails these regexes loudly (located=False) rather than silently
# localizing the wrong region.

#: Narrowest-first. A value-specific check implicates one field's value against
#: one declared constraint; "field absent" implicates the entire field.
_KIND_RANK = {"bounds": 0, "logical": 0, "type": 1, "required-field": 2,
              "unknown": 3}

_RE_REQUIRED = re.compile(r"Required field '([^']*)' \(([^)]*)\) missing")
_RE_TYPE = re.compile(r"Field '([^']*)' type mismatch: expected ([^,]*), got (.*)$")
_RE_BOUNDS = re.compile(
    r"Field '([^']*)' = (.*) out of bounds \[([^,]*), ([^\]]*)\]")
_RE_LOGICAL = re.compile(r"Field '([^']*)' = (.*), expected ([<>=!]+) (.*)$")
_RE_ANY_FIELD = re.compile(r"'([^']*)'")


def _declared_constraint(constraints: Dict[str, Any], region: str,
                         kind: str) -> Any:
    """The declared constraint governing `region`, when the set is available.

    Returns None when the caller did not supply the constraint set, or when it
    contains nothing for this region -- an absent declaration is reported as
    absent, never substituted with a plausible-looking one.
    """
    if not region or not constraints:
        return None
    if kind in ("required-field", "type"):
        return (constraints.get("required_fields") or {}).get(region)
    if kind == "bounds":
        return (constraints.get("numeric_bounds") or {}).get(region)
    if kind == "logical":
        for expr in (constraints.get("logical_exprs") or []):
            if isinstance(expr, dict) and str(expr.get("field", "")) == region:
                return expr
        return None
    return None


def _repair_hint(kind: str, region: str, declared: Any) -> str:
    """A repair the caller can act on, naming the region it belongs to."""
    if not region:
        return "unlocated: the violation names no field; re-read the constraint set"
    if kind == "required-field":
        return "supply field '%s' (declared type %s)" % (region, declared)
    if kind == "type":
        return "coerce field '%s' to its declared type %s" % (region, declared)
    if kind == "bounds":
        return ("bring field '%s' inside its declared bounds %s"
                % (region, declared if declared is not None else "?"))
    if kind == "logical":
        return ("adjust field '%s' to satisfy its declared expression %s"
                % (region, declared if declared is not None else "?"))
    return ("region '%s' is implicated but no declared constraint was found for it"
            % region)


def localize_violation(violation: str,
                       constraints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """LogicLoc (EP183): map ONE violation to the smallest region it implicates.

    Args:
        violation: one entry of a result's `violations` list, in the format
                   `PurePythonVerifier.verify` emits.
        constraints: the verified constraint set, when the caller still holds
                   it, so the DECLARED constraint can be reported alongside
                   what was observed.

    Returns:
        {"region", "kind", "declared", "observed", "expected", "repair",
        "located", "violation"}. `located` is False when the string is not in a
        recognised format -- reported rather than guessed at.
    """
    text = str(violation or "")
    region = ""
    kind = "unknown"
    observed: Any = None
    expected: Any = None

    match = _RE_REQUIRED.search(text)
    if match:
        region, kind = match.group(1), "required-field"
        expected = match.group(2)
    else:
        match = _RE_TYPE.search(text)
        if match:
            region, kind = match.group(1), "type"
            expected, observed = match.group(2).strip(), match.group(3).strip()
        else:
            match = _RE_BOUNDS.search(text)
            if match:
                region, kind = match.group(1), "bounds"
                # The declared bounds carry the producer's own spacing
                # ("[0, 150]" -> "0", " 150"); stripped so a consumer does not
                # have to know that.
                observed = match.group(2).strip()
                expected = (match.group(3).strip(), match.group(4).strip())
            else:
                match = _RE_LOGICAL.search(text)
                if match:
                    region, kind = match.group(1), "logical"
                    observed = match.group(2).strip()
                    expected = (match.group(3).strip(), match.group(4).strip())
                else:
                    # Last resort: the field is still the smallest region named.
                    match = _RE_ANY_FIELD.search(text)
                    if match:
                        region = match.group(1)

    declared = _declared_constraint(constraints or {}, region, kind)
    return {
        "region": region,
        "kind": kind,
        "declared": declared,
        "observed": observed,
        "expected": expected,
        "repair": _repair_hint(kind, region, declared),
        "located": bool(region),
        "violation": text,
    }


def localize_violations(constraints: Dict[str, Any],
                        violations: List[str]) -> Dict[str, Any]:
    """Localize every violation and rank them narrowest-region-first.

    `smallest` is the single region a caller should repair first: value checks
    (bounds, logical) ahead of shape checks (type, presence). Python's sort is
    stable, so equal ranks keep the order the verifier reported.
    """
    regions = [localize_violation(v, constraints) for v in (violations or [])]
    ranked = sorted(regions,
                    key=lambda r: _KIND_RANK.get(str(r.get("kind")), 3))
    unlocated = [r["violation"] for r in regions if not r.get("located")]
    return {
        "regions": ranked,
        "smallest": ranked[0] if ranked else None,
        "n": len(ranked),
        "unlocated": unlocated,
    }


# ====================================================================
# PurePythonVerifier — Constraint Verifier Fallback
# ====================================================================

class PurePythonVerifier:
    """Pure-Python constraint verifier (fallback when z3-solver not installed).

    Demonstrates the same SAT/UNSAT semantics as Z3 for common constraint
    patterns: required fields, numeric bounds, type checking, and logical
    assertions.

    SAT means all constraints are satisfiable. UNSAT means a violation was
    found, with details in the 'violations' list.
    """

    def __init__(self):
        self._check_count = 0

    def verify(self, constraints: Dict[str, Any]) -> Dict[str, Any]:
        """Verify a set of constraints.

        Args:
            constraints: dict with keys:
                name            - Model name (str)
                required_fields - {field_name: type_name} dict
                numeric_bounds  - {field_name: (lo, hi)} dict
                values          - {field_name: value} dict
                logical_exprs   - [(field, operator, expected), ...] list
                                  where operator is one of: "eq", "ne", "gt",
                                  "gte", "lt", "lte"

        Returns:
            dict with same format as serialize_solver_result()
        """
        self._check_count += 1
        model_values: Dict[str, Any] = {}
        violations: List[str] = []
        checked = 0

        name = constraints.get("name", "cd2_verify")

        # Check required fields
        required_fields = constraints.get("required_fields", {})
        values = constraints.get("values", {})
        for field, field_type in required_fields.items():
            checked += 1
            if field not in values:
                violations.append(
                    f"Required field '{field}' ({field_type}) missing"
                )
            else:
                actual_type = type(values[field]).__name__
                if actual_type != field_type:
                    violations.append(
                        f"Field '{field}' type mismatch: "
                        f"expected {field_type}, got {actual_type}"
                    )
                else:
                    model_values[field] = values[field]

        # Check numeric bounds
        numeric_bounds = constraints.get("numeric_bounds", {})
        for field, bounds in numeric_bounds.items():
            checked += 1
            val = values.get(field)
            if val is not None:
                lo, hi = bounds
                if not (lo <= val <= hi):
                    violations.append(
                        f"Field '{field}' = {val} out of bounds [{lo}, {hi}]"
                    )
                else:
                    model_values[field] = val

        # Check logical expressions
        logical_exprs = constraints.get("logical_exprs", [])
        for expr in logical_exprs:
            checked += 1
            field = expr.get("field", "")
            operator = expr.get("operator", "eq")
            expected = expr.get("expected", None)
            val = values.get(field)

            if val is not None:
                if operator == "eq" and val != expected:
                    violations.append(
                        f"Field '{field}' = {val}, expected == {expected}"
                    )
                elif operator == "ne" and val == expected:
                    violations.append(
                        f"Field '{field}' = {val}, expected != {expected}"
                    )
                elif operator == "gt" and not (val > expected):
                    violations.append(
                        f"Field '{field}' = {val}, expected > {expected}"
                    )
                elif operator == "gte" and not (val >= expected):
                    violations.append(
                        f"Field '{field}' = {val}, expected >= {expected}"
                    )
                elif operator == "lt" and not (val < expected):
                    violations.append(
                        f"Field '{field}' = {val}, expected < {expected}"
                    )
                elif operator == "lte" and not (val <= expected):
                    violations.append(
                        f"Field '{field}' = {val}, expected <= {expected}"
                    )

        if violations:
            return {
                "model": name,
                "result": "UNSAT",
                "violations": violations,
                "smt2": f"; Pure-Python verifier: {name}\n"
                        f"; Required fields: {list(required_fields)}\n"
                        f"; Numeric bounds: {list(numeric_bounds)}\n"
                        f"; Logical exprs: {len(logical_exprs)}\n"
                        f"; Checked: {checked}, Violations: {len(violations)}",
                "statistics": {
                    "constraints_checked": str(checked),
                    "violations_found": str(len(violations)),
                },
                "time_ms": 0,
                "memory_mb": 0.0,
            }

        return {
            "model": name,
            "result": "SAT",
            "model_values": model_values,
            "smt2": f"; Pure-Python verifier: {name}\n"
                    f"; Required fields: {list(required_fields)}\n"
                    f"; Numeric bounds: {list(numeric_bounds)}\n"
                    f"; Logical exprs: {len(logical_exprs)}\n"
                    f"; Checked: {checked}, All satisfied",
            "statistics": {
                "constraints_checked": str(checked),
                "violations_found": "0",
            },
            "time_ms": 0,
            "memory_mb": 0.0,
        }

    @property
    def check_count(self) -> int:
        return self._check_count


# ====================================================================
# SMTVerifier — Worker-Verifier-Judge Pipeline
# ====================================================================

class SMTVerifier:
    """CD2 Worker-Verifier-Judge pipeline for SMT constraint verification.

    Integrates with z3-solver if available. Falls back to PurePythonVerifier
    for zero-dependency operation. Stores results in DirectMemory audit trail
    via z3_verification event type.

    Pipeline flow:
      1. WORKER: Accepts constraints from caller (the "worker" agent)
      2. VERIFIER: Checks constraints with Z3 or pure-Python fallback
      3. JUDGE: Evaluates result and produces verdict (ACCEPT/REJECT)
      4. RECORD: Stores full verification chain in DirectMemory
    """

    def __init__(self, auto_record: bool = True):
        """Initialize the SMTVerifier.

        Args:
            auto_record: If True (default), automatically records
                         verification results to DirectMemory.
        """
        self.z3_available = Z3_AVAILABLE
        self._pure = PurePythonVerifier()
        self._auto_record = auto_record
        self._history: List[Dict[str, Any]] = []
        self._dm = None
        if self.z3_available:
            self._solver_type = "z3"
        else:
            self._solver_type = "pure_python"

    @property
    def solver_type(self) -> str:
        return self._solver_type

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def verify_constraints(self, constraints: Dict[str, Any],
                           model_name: Optional[str] = None) -> Dict[str, Any]:
        """Worker-Verifier pipeline: verify a set of constraints.

        This is the VERIFIER step. Returns the raw verification result.

        Args:
            constraints: Constraint dict (see PurePythonVerifier.verify)
            model_name: Optional override for model name.

        Returns:
            Verification result dict with result, model_values, violations.
        """
        if model_name:
            constraints["name"] = model_name

        if self.z3_available:
            result = self._z3_verify(constraints)
        else:
            result = self._pure.verify(constraints)

        self._history.append(result)

        if self._auto_record:
            self._record_to_audit(result)

        return result

    def judge_result(self, result: Dict[str, Any],
                     constraints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """JUDGE step: evaluate a verification result and produce verdict.

        Args:
            result: The output of verify_constraints().
            constraints: The set that produced `result`, when the caller still
                         holds it. Optional: it only enriches the localization
                         with the DECLARED constraint for the implicated region;
                         without it that field is reported absent, not guessed.

        Returns:
            dict with:
                verdict      - "ACCEPT" or "REJECT"
                confidence   - "high", "medium", or "low"
                rationale    - Explanation string; a REJECT names the smallest
                               implicated region and its repair (EP183 LogicLoc)
                localization - {"regions", "smallest", "n", "unlocated"} on a
                               REJECT; absent on ACCEPT
        """
        if result["result"] == "SAT":
            return {
                "verdict": "ACCEPT",
                "confidence": "high",
                "rationale": (
                    f"All constraints satisfied for model "
                    f"'{result.get('model', 'unknown')}' "
                    f"({result.get('statistics', {}).get('constraints_checked', '?')} checks)"
                ),
            }
        elif result["result"] == "UNSAT":
            violations = result.get("violations", [])
            model = result.get("model", "unknown")
            # EP183 LogicLoc. The JUDGE step is where a rejection becomes a
            # finding, so this is where the flat violation list becomes the
            # smallest implicated region -- the caller can then repair one field
            # rather than re-read the whole constraint set. Guarded because a
            # localizer failure must not turn a REJECT into an exception.
            localization: Dict[str, Any] = {"regions": [], "smallest": None,
                                            "n": 0, "unlocated": []}
            try:
                localization = localize_violations(constraints or {}, violations)
            except Exception as exc:  # noqa: BLE001 -- never break the verdict
                note_failure("cd2_verifier:localize", exc)
                localization["localize_error"] = "%s: %s" % (
                    type(exc).__name__, exc)
            smallest = localization.get("smallest") or {}
            region = str(smallest.get("region") or "")
            rationale = (
                f"Constraint violations found for model '{model}': "
                f"{'; '.join(violations[:3])}")
            if region:
                rationale = (
                    f"Smallest implicated region for model '{model}': "
                    f"field '{region}' ({smallest.get('kind')}) -- "
                    f"{smallest.get('repair')}. "
                    f"Violations: {'; '.join(violations[:3])}")
            return {
                "verdict": "REJECT",
                "confidence": "high",
                "rationale": rationale,
                "localization": localization,
            }
        else:
            return {
                "verdict": "REJECT",
                "confidence": "low",
                "rationale": f"Solver returned UNKNOWN for model '{result.get('model', 'unknown')}'",
            }

    def worker_verifier_judge(self, constraints: Dict[str, Any],
                              model_name: Optional[str] = None) -> Dict[str, Any]:
        """Run the full Worker-Verifier-Judge pipeline.

        Args:
            constraints: Constraint dict.
            model_name: Optional model name.

        Returns:
            dict with:
                verification  - Raw verification result from VERIFIER
                judge         - JUDGE verdict
                pipeline      - Pipeline metadata
        """
        t0 = time.perf_counter()
        verification = self.verify_constraints(constraints, model_name)
        # The constraints are in hand here, which is what lets the JUDGE step
        # report the DECLARED constraint for the implicated region (EP183).
        judge = self.judge_result(verification, constraints)
        dt = round((time.perf_counter() - t0) * 1000, 1)

        return {
            "verification": verification,
            "judge": judge,
            "pipeline": {
                "solver_type": self._solver_type,
                "total_ms": dt,
                "model": verification.get("model", model_name or "unknown"),
            },
        }

    def _z3_verify(self, constraints: Dict[str, Any]) -> Dict[str, Any]:
        """Verify constraints using real Z3 solver when available."""
        from z3 import Int, Bool, Solver

        solver = Solver()
        values = constraints.get("values", {})

        # Add constraints for numeric bounds
        numeric_bounds = constraints.get("numeric_bounds", {})
        for field, bounds in numeric_bounds.items():
            lo, hi = bounds
            var = Int(field)
            solver.add(var >= lo)
            solver.add(var <= hi)
            val = values.get(field)
            if val is not None:
                solver.add(var == int(val))

        # Add constraints for boolean fields
        bool_fields = constraints.get("bool_fields", [])
        for field in bool_fields:
            var = Bool(field)
            val = values.get(field)
            if val is not None:
                solver.add(var == bool(val))

        # Add custom logical expressions
        logical_exprs = constraints.get("logical_exprs", [])
        for expr in logical_exprs:
            field = expr.get("field", "")
            operator = expr.get("operator", "eq")
            expected = expr.get("expected", None)
            val = values.get(field)
            if isinstance(expected, (int, float)):
                var = Int(field)
                if val is not None:
                    z3_val = int(val)
                    if operator == "eq":
                        solver.add(var == z3_val)
                    elif operator == "ne":
                        solver.add(var != z3_val)
                    elif operator == "gt":
                        solver.add(var > z3_val)
                    elif operator == "gte":
                        solver.add(var >= z3_val)
                    elif operator == "lt":
                        solver.add(var < z3_val)
                    elif operator == "lte":
                        solver.add(var <= z3_val)
            else:
                var = Bool(field)
                if val is not None:
                    z3_val = bool(val)
                    if operator == "eq":
                        solver.add(var == z3_val)
                    elif operator == "ne":
                        solver.add(var != z3_val)

        result = serialize_solver_result(
            solver, constraints.get("name", "z3_verify"))

        # `verify_constraints` documents a `violations` key on UNSAT, and the
        # pure-Python fallback fills it. The Z3 path -- the one that runs
        # whenever z3-solver is installed, which is the normal case -- never
        # set it, so the contract held only on the fallback.
        #
        # Z3 proves the formula unsatisfiable but does not say which input
        # constraint is responsible, so the counter-example is derived from
        # the supplied values using the fallback's own rules. That keeps the
        # two paths reporting the same violations for the same input, and
        # keeps them non-empty whenever the values are actually wrong.
        #
        # Found 2026-09-29 by the CD2 cross-domain check, which had been
        # reporting PASS while this key was always None.
        if result.get("result") == "UNSAT" and not result.get("violations"):
            try:
                detail = PurePythonVerifier().verify(constraints)
                result["violations"] = detail.get("violations", [])
            except Exception as exc:
                note_failure("cd2_verifier:L496", exc)
                result["violations"] = []
                result["violations_error"] = "%s: %s" % (
                    type(exc).__name__, exc)
        return result

    def _record_to_audit(self, result: Dict[str, Any]):
        """Record verification result to DirectMemory audit trail."""
        try:
            from CORE.direct_memory import DirectMemory

            if self._dm is None:
                self._dm = DirectMemory(session_id="cd2_verifier")

            record_data = {
                "verification_result": result.get("result", "UNKNOWN"),
                "model": result.get("model", ""),
                "solver_type": self._solver_type,
                "constraints_checked": result.get("statistics", {}).get("constraints_checked", "0"),
                "violations_count": len(result.get("violations", [])),
                "model_values_count": len(result.get("model_values", {})),
            }

            tags = ["cross-domain", "cd2", "z3", "verification"]
            if result.get("result") == "UNSAT":
                tags.append("violation")

            self._dm.record("z3_verification", record_data, tags=tags)
        except Exception as e:
            print(f"[cd2_verifier] Audit recording failed: {e}")

    def get_summary(self) -> Dict[str, Any]:
        """Return summary statistics for this verifier session.

        `total_checks` used to read `self._pure.check_count`, which only
        counts checks made by the pure-Python FALLBACK. With z3-solver
        installed -- the normal case -- the fallback is never consulted, so
        the counter stayed at 0 no matter how much had been verified, and
        the session summary reported zero work done. It now counts the
        verifications actually performed, falling back to the finer-grained
        fallback counter when the fallback is the active solver.
        """
        sat_count = sum(1 for r in self._history if r.get("result") == "SAT")
        unsat_count = sum(1 for r in self._history if r.get("result") == "UNSAT")
        performed = max(len(self._history), self._pure.check_count)

        return {
            "solver_type": self._solver_type,
            "z3_available": self.z3_available,
            "total_checks": performed,
            "sat": sat_count,
            "unsat": unsat_count,
            "unknown": len(self._history) - sat_count - unsat_count,
        }


# ====================================================================
# Module-level convenience
# ====================================================================

_default_verifier: Optional[SMTVerifier] = None
def get_verifier() -> SMTVerifier:
    """Get or create the default SMTVerifier singleton."""
    global _default_verifier
    if _default_verifier is None:
        _default_verifier = SMTVerifier(auto_record=True)
    return _default_verifier
def verify(constraints: Dict[str, Any],
           model_name: Optional[str] = None) -> Dict[str, Any]:
    """Quick one-shot verification using the default verifier."""
    return get_verifier().verify_constraints(constraints, model_name)
def wvj(constraints: Dict[str, Any],
        model_name: Optional[str] = None) -> Dict[str, Any]:
    """Quick one-shot Worker-Verifier-Judge pipeline using the default verifier."""
    return get_verifier().worker_verifier_judge(constraints, model_name)


# ====================================================================
# Self-test
# ====================================================================

if __name__ == "__main__":
    print(f"CD2 Verifier v1.0 — Self-test")
    print(f"  z3-solver available: {Z3_AVAILABLE}")
    print(f"  Solver type: {'z3' if Z3_AVAILABLE else 'pure_python'}")
    print()

    v = SMTVerifier(auto_record=False)

    # Test 1: SAT — all fields present, valid bounds
    sat_constraints = {
        "name": "sat_test",
        "required_fields": {"name": "str", "age": "int", "active": "bool"},
        "numeric_bounds": {"age": (0, 150)},
        "values": {"name": "Alice", "age": 30, "active": True},
    }
    result = v.verify_constraints(sat_constraints)
    assert result["result"] == "SAT", f"Expected SAT, got {result['result']}"
    judge = v.judge_result(result)
    assert judge["verdict"] == "ACCEPT", f"Expected ACCEPT, got {judge['verdict']}"
    print(f"  [PASS] SAT test -> {judge['verdict']} (confidence: {judge['confidence']})")

    # Test 2: UNSAT — missing required field, out of bounds
    unsat_constraints = {
        "name": "unsat_test",
        "required_fields": {"name": "str", "age": "int", "active": "bool"},
        "numeric_bounds": {"age": (0, 150)},
        "values": {"name": "Bob", "age": 200},
    }
    result = v.verify_constraints(unsat_constraints)
    assert result["result"] == "UNSAT", f"Expected UNSAT, got {result['result']}"
    judge = v.judge_result(result, unsat_constraints)
    assert judge["verdict"] == "REJECT", f"Expected REJECT, got {judge['verdict']}"
    print(f"  [PASS] UNSAT test -> {judge['verdict']} (violations: {len(result['violations'])})")

    # Test 2b: LogicLoc (EP183) localizes the rejection to its smallest region.
    # The fixture violates a bound AND omits a field, so the ranking is the
    # discriminating part: the value-level check must be named first.
    located = judge["localization"]["smallest"]
    assert located["region"] == "age", f"Expected region 'age', got {located}"
    assert located["kind"] == "bounds", f"Expected bounds, got {located}"
    assert located["declared"] == (0, 150), f"Expected declared bounds, got {located}"
    print(f"  [PASS] LogicLoc -> region '{located['region']}' "
          f"({located['kind']}): {located['repair']}")

    # Test 3: Worker-Verifier-Judge pipeline
    wvj_result = v.worker_verifier_judge(sat_constraints, "wvj_test")
    assert "verification" in wvj_result
    assert "judge" in wvj_result
    assert "pipeline" in wvj_result
    assert wvj_result["judge"]["verdict"] == "ACCEPT"
    print(f"  [PASS] WVJ pipeline: {wvj_result['pipeline']['total_ms']}ms")

    # Test 4: PurePythonVerifier logical expressions
    pv = PurePythonVerifier()
    logic_constraints = {
        "name": "logic_test",
        "required_fields": {"count": "int"},
        "values": {"count": 42},
        "logical_exprs": [
            {"field": "count", "operator": "gt", "expected": 10},
            {"field": "count", "operator": "lt", "expected": 100},
        ],
    }
    result = pv.verify(logic_constraints)
    assert result["result"] == "SAT", f"Expected SAT, got {result['result']}"
    print(f"  [PASS] Logical SAT: count=42, 10 < count < 100")

    # Test 5: PurePythonVerifier UNSAT logical expressions
    unsat_logic = {
        "name": "logic_unsat",
        "required_fields": {"count": "int"},
        "values": {"count": 5},
        "logical_exprs": [
            {"field": "count", "operator": "gt", "expected": 10},
        ],
    }
    result = pv.verify(unsat_logic)
    assert result["result"] == "UNSAT", f"Expected UNSAT, got {result['result']}"
    print(f"  [PASS] Logical UNSAT: count=5, expected > 10")

    # Summary
    summary = v.get_summary()
    print(f"\n  Summary: {summary['total_checks']} checks, "
          f"{summary['sat']} SAT, {summary['unsat']} UNSAT")
    print(f"  All self-tests PASSED")
