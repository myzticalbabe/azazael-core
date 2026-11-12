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

import time
from typing import Any, Dict, List, Optional

# -- z3-solver availability detection --------------------------------
try:
    import z3
    Z3_AVAILABLE = True
except ImportError:
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
    except Exception:
        pass

    smt2 = ""
    try:
        smt2 = solver.to_smt2()
    except Exception as e:
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
    except ImportError:
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
        "time_ms": int(stats.get("time", 0)),
        "memory_mb": round(float(stats.get("memory", 0)) / 1024, 2) if "memory" in stats else 0.0,
    }

    # Extract model values if SAT
    if classification == "SAT":
        try:
            model = solver.model()
            output["model_values"] = {
                str(d): str(model.eval(d, model_completion=True))
                for d in model.decls()
            }
        except Exception:
            output["model_values"] = {}

    return output


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
                        f"; Required fields: {list(required_fields.keys())}\n"
                        f"; Numeric bounds: {list(numeric_bounds.keys())}\n"
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
                    f"; Required fields: {list(required_fields.keys())}\n"
                    f"; Numeric bounds: {list(numeric_bounds.keys())}\n"
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

    def judge_result(self, result: Dict[str, Any]) -> Dict[str, str]:
        """JUDGE step: evaluate a verification result and produce verdict.

        Args:
            result: The output of verify_constraints().

        Returns:
            dict with:
                verdict     - "ACCEPT" or "REJECT"
                confidence  - "high", "medium", or "low"
                rationale   - Explanation string
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
            return {
                "verdict": "REJECT",
                "confidence": "high",
                "rationale": (
                    f"Constraint violations found for model "
                    f"'{result.get('model', 'unknown')}': "
                    f"{'; '.join(violations[:3])}"
                ),
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
        judge = self.judge_result(verification)
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
            var = Int(field) if isinstance(expected, (int, float)) else Bool(field)
            val = values.get(field)
            if val is not None:
                z3_val = int(val) if isinstance(expected, (int, float)) else bool(val)
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

        return serialize_solver_result(solver, constraints.get("name", "z3_verify"))

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
        """Return summary statistics for this verifier session."""
        sat_count = sum(1 for r in self._history if r.get("result") == "SAT")
        unsat_count = sum(1 for r in self._history if r.get("result") == "UNSAT")

        return {
            "solver_type": self._solver_type,
            "z3_available": self.z3_available,
            "total_checks": self._pure.check_count,
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
    judge = v.judge_result(result)
    assert judge["verdict"] == "REJECT", f"Expected REJECT, got {judge['verdict']}"
    print(f"  [PASS] UNSAT test -> {judge['verdict']} (violations: {len(result['violations'])})")

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
