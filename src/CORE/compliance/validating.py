"""
RULE 0X-5 + 0X-6: Structured Output Validation + Compliance Scoring

SchemaValidator validates gate assertions against pydantic schemas.
ComplianceScorer tracks real-time metrics with automatic HALT thresholds.

Both fall back to stdlib dict-based validation if pydantic is unavailable.
"""

import threading
from datetime import datetime, timezone
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Optional pydantic import
try:
    from pydantic import BaseModel, Field, ValidationError  # pyright: ignore[reportAssignmentType] -- try-success binds the real pydantic class while the except fallback defines a structurally compatible local class behind the same name; selection is by import success, not substitutability
    HAS_PYDANTIC = True
except ImportError as exc:
    note_failure("validating:L18", exc)
    HAS_PYDANTIC = False
    BaseModel = object
    Field = None
    # The fallback mirrors the interface validate_model assumes (an errors()
    # method); without pydantic there is no detail to report, so it is empty.
    class ValidationError(Exception):
        def errors(self) -> list:
            return []


# ---------------------------------------------------------------------------
# Schema Validator
# ---------------------------------------------------------------------------

class SchemaValidator:
    """Validates gate assertions and data structures against schemas.

    Uses pydantic when available for rich validation (type checking, nested
    structures, custom validators). Falls back to dict-based validation.

    Usage:
        validator = SchemaValidator()
        schema = {
            "has_gate": bool,
            "code_reviewed": bool,
            "agent_count": (int, lambda x: x >= 3),
        }
        result = validator.validate({"has_gate": True, "code_reviewed": True, "agent_count": 5}, schema)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._validation_log: list[dict[str, Any]] = []
        self._schemas: dict[str, dict] = {}

    def register_schema(self, name: str, schema: dict[str, Any]):
        """Register a named schema for later validation."""
        with self._lock:
            self._schemas[name] = schema

    def validate(self, data: dict[str, Any], schema: dict[str, Any], name: str = "") -> dict[str, Any]:
        """Validate data against a schema.

        Schema format:
            {field_name: expected_type_or_validator}
            Where validator can be:
            - A Python type (str, int, bool, list, dict)
            - A tuple of (type, callable_validator_fn)
            - A callable that returns True/False

        Returns:
            dict with passed, errors, and validated_data
        """
        errors = []
        validated = {}

        for field, validator in schema.items():
            value = data.get(field)

            if isinstance(validator, tuple):
                # (type, custom_validator_fn)
                expected_type, custom_fn = validator
                if not isinstance(value, expected_type):
                    errors.append(f"Field '{field}': expected {expected_type.__name__}, got {type(value).__name__}")
                    continue
                try:
                    if not custom_fn(value):
                        errors.append(f"Field '{field}': custom validation failed for value {value!r}")
                        continue
                except Exception as e:
                    note_failure("validating:L84", e)
                    errors.append(f"Field '{field}': custom validator error: {e}")
                    continue
                validated[field] = value

            elif isinstance(validator, type):
                # Just type check
                if not isinstance(value, validator):
                    errors.append(f"Field '{field}': expected {validator.__name__}, got {type(value).__name__}")
                    continue
                validated[field] = value

            elif callable(validator):
                # Callable validator
                try:
                    if not validator(value):
                        errors.append(f"Field '{field}': validation failed")
                        continue
                except Exception as e:
                    note_failure("validating:L102", e)
                    errors.append(f"Field '{field}': validator error: {e}")
                    continue
                validated[field] = value

            else:
                errors.append(f"Field '{field}': unknown validator type {type(validator).__name__}")

        passed = len(errors) == 0
        result = {
            "passed": passed,
            "errors": errors,
            "validated_data": validated if passed else {},
            "schema_name": name or "unnamed",
            "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        self._log_validation(result)
        return result

    def validate_with_pydantic(self, data: dict[str, Any], model_class) -> dict[str, Any]:
        """Validate using a pydantic model class.

        Args:
            data: dict to validate
            model_class: A pydantic BaseModel subclass

        Returns:
            dict with passed, errors, and validated_data
        """
        if not HAS_PYDANTIC:
            # Fallback to dict validation using model's __annotations__
            schema = {}
            if hasattr(model_class, "__annotations__"):
                for field_name, field_type in model_class.__annotations__.items():
                    schema[field_name] = field_type
            return self.validate(data, schema, name=model_class.__name__)

        try:
            instance = model_class(**data)
            result = {
                "passed": True,
                "errors": [],
                "validated_data": instance.model_dump() if hasattr(instance, "model_dump") else data,
                "schema_name": model_class.__name__,
                "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        except ValidationError as e:
            note_failure("validating:L149", e)
            result = {
                "passed": False,
                "errors": [str(err) for err in e.errors()],
                "validated_data": {},
                "schema_name": model_class.__name__,
                "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }

        self._log_validation(result)
        return result

    def _log_validation(self, result: dict[str, Any]):
        with self._lock:
            self._validation_log.append(result)

    def get_validation_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._validation_log[-limit:])

    @property
    def validation_count(self) -> int:
        with self._lock:
            return len(self._validation_log)

    @property
    def pass_rate(self) -> float:
        with self._lock:
            if not self._validation_log:
                return 1.0
            passed = sum(1 for v in self._validation_log if v.get("passed"))
            return passed / len(self._validation_log)


# ---------------------------------------------------------------------------
# Gate Assertion Schema
# ---------------------------------------------------------------------------

def build_gate_schema() -> dict[str, Any]:
    """Build the standard PRE-ACTION GATE assertion schema.

    Returns a dict schema that validates the mandatory gate assertion fields.
    """
    return {
        "scope_check_ok": bool,
        "gemini_phase_ok": (bool, lambda x: True),  # Can be True or False (N/A)
        "anti_pattern_scanned": (bool, lambda x: True),
        "batch_script_ok": (bool, lambda x: True),
        "test_failure_handled": (bool, lambda x: True),
        "concurrent_inference_ok": (bool, lambda x: True),
        "post_phase_verified": (bool, lambda x: True),
        "system_reminders_obeyed": (bool, lambda x: True),
        "code_reviewer_spawned": (bool, lambda x: True),
        "agent_utilization_ok": (bool, lambda x: True),
        "gate_decision": str,  # PROCEED, HALT, etc.
    }


# ---------------------------------------------------------------------------
# Compliance Scorer
# ---------------------------------------------------------------------------

class ComplianceScorer:
    """Real-time compliance scoring with automatic HALT below threshold.

    Tracks metrics across multiple dimensions and computes a weighted score.
    Score below threshold triggers automatic HALT recommendation.

    Weighted Metrics:
        - gate_assertion_rate: How consistently gates are printed (weight: 25)
        - code_review_rate: How consistently code is reviewed (weight: 20)
        - agent_utilization: Agent spawn count vs minimum (weight: 15)
        - inference_usage: Inference agents spawned vs required (weight: 15)
        - error_recurrence: Repeated errors (penalty, weight: 10)
        - sync_compliance: Skills sync rate (weight: 10)
        - verification_completeness: How often verification is run (weight: 5)
    """

    METRIC_WEIGHTS = {
        "gate_assertion_rate": 25,
        "code_review_rate": 20,
        "agent_utilization": 15,
        "inference_usage": 15,
        "error_recurrence": 10,
        "sync_compliance": 10,
        "verification_completeness": 5,
    }

    def __init__(self, halt_threshold: float = 0.5):
        """
        Args:
            halt_threshold: Score below this triggers HALT (0.0 - 1.0)
        """
        self._halt_threshold = halt_threshold
        self._lock = threading.RLock()
        self._metrics: dict[str, dict[str, Any]] = {}
        self._score_history: list[dict[str, Any]] = []

        # Initialize all metrics
        for name in self.METRIC_WEIGHTS:
            self._metrics[name] = {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "score": 1.0,  # Start at perfect
            }

    @property
    def halt_threshold(self) -> float:
        return self._halt_threshold

    @halt_threshold.setter
    def halt_threshold(self, value: float):
        self._halt_threshold = max(0.0, min(1.0, value))

    def record_pass(self, metric_name: str):
        """Record a passing compliance event for a metric."""
        with self._lock:
            if metric_name not in self._metrics:
                self._metrics[metric_name] = {"total": 0, "passed": 0, "failed": 0, "score": 1.0}
            m = self._metrics[metric_name]
            m["total"] += 1
            m["passed"] += 1
            m["score"] = m["passed"] / max(m["total"], 1)

    def record_fail(self, metric_name: str):
        """Record a failing compliance event for a metric. Penalizes score."""
        with self._lock:
            if metric_name not in self._metrics:
                self._metrics[metric_name] = {"total": 0, "passed": 0, "failed": 0, "score": 1.0}
            m = self._metrics[metric_name]
            m["total"] += 1
            m["failed"] += 1
            m["score"] = m["passed"] / max(m["total"], 1)

    def get_weighted_score(self) -> float:
        """Compute the weighted compliance score across all metrics.

        Returns:
            Float between 0.0 (worst) and 1.0 (best).
        """
        with self._lock:
            total_weight = 0.0
            weighted_sum = 0.0

            for name, weight in self.METRIC_WEIGHTS.items():
                m = self._metrics.get(name, {"score": 1.0, "total": 0})
                if m["total"] > 0:
                    weighted_sum += m["score"] * weight
                    total_weight += weight
                else:
                    # Metric hasn't been recorded yet — assume perfect (1.0) with full weight
                    weighted_sum += 1.0 * weight
                    total_weight += weight

            return weighted_sum / max(total_weight, 1.0)

    def should_halt(self) -> tuple[bool, float, float]:
        """Check if compliance score is below halt threshold.

        Returns:
            (halt: bool, current_score: float, threshold: float)
        """
        score = self.get_weighted_score()
        return score < self._halt_threshold, score, self._halt_threshold

    def snapshot(self) -> dict[str, Any]:
        """Take a snapshot of current scores and history."""
        with self._lock:
            score = self.get_weighted_score()
            should_halt, _, _ = self.should_halt()

            snapshot = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "weighted_score": score,
                "halt_threshold": self._halt_threshold,
                "should_halt": should_halt,
                "metrics": {
                    name: dict(m) for name, m in self._metrics.items()
                },
            }
            self._score_history.append(snapshot)
            return snapshot

    def get_score_history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._score_history[-limit:])

    def reset(self):
        """Reset all metrics."""
        with self._lock:
            for name in self.METRIC_WEIGHTS:
                self._metrics[name] = {"total": 0, "passed": 0, "failed": 0, "score": 1.0}
            self._score_history.clear()

    def __repr__(self) -> str:
        score = self.get_weighted_score()
        return f"ComplianceScorer(score={score:.2f}, threshold={self._halt_threshold})"
