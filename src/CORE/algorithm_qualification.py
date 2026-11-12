"""Evidence-based qualification for algorithm components.

An implementation is not a production algorithm merely because its name is
present.  This module keeps the promotion boundary explicit and fail-closed:
``qualified`` requires an executable entry point, a deterministic seed, a
result artifact, and non-vacuous passing regression evidence.  ``served`` adds
an explicitly recorded production callable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


STATUS_ORDER = ("name_only", "executable", "tested", "qualified", "served")


@dataclass(frozen=True)
class QualificationEvidence:
    """Evidence submitted for one algorithm component."""

    component: str
    executable_path: str = ""
    deterministic_seed: int | None = None
    result_artifact: str = ""
    regression_evidence: Mapping[str, Any] | None = None
    production_callable: str = ""
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["regression_evidence"] = dict(self.regression_evidence or {})
        return row


def _existing_file(value: str) -> bool:
    return bool(value) and Path(value).is_file()


def classify(evidence: QualificationEvidence) -> dict[str, Any]:
    """Return a transparent status decision without promoting by name alone."""

    regression = dict(evidence.regression_evidence or {})
    checks = {
        "executable": _existing_file(evidence.executable_path),
        "deterministic_seed": evidence.deterministic_seed is not None,
        "result_artifact": _existing_file(evidence.result_artifact),
        "regression_passed": (
            int(regression.get("passed", 0) or 0) > 0
            and not bool(regression.get("vacuous", False))
        ),
        "production_callable": bool(evidence.production_callable),
    }
    if not checks["executable"]:
        status = "name_only"
    elif not all(checks[key] for key in (
        "deterministic_seed", "result_artifact", "regression_passed"
    )):
        status = "executable"
    elif not checks["production_callable"]:
        status = "qualified"
    else:
        status = "served"

    missing = [name for name, passed in checks.items()
               if name != "production_callable" and not passed]
    if status == "served" and not checks["production_callable"]:
        missing.append("production_callable")
    return {
        "component": evidence.component,
        "status": status,
        "checks": checks,
        "missing": missing,
        "notes": evidence.notes,
        "evidence": evidence.as_dict(),
    }


def qualified_components(rows: list[Mapping[str, Any]]) -> list[str]:
    """Return only components whose evidence reaches ``qualified`` or above."""

    statuses = []
    for row in rows:
        evidence = row.get("evidence", row)
        if not isinstance(evidence, Mapping):
            continue
        decision = classify(QualificationEvidence(
            component=str(evidence.get("component", "")),
            executable_path=str(evidence.get("executable_path", "")),
            deterministic_seed=evidence.get("deterministic_seed"),
            result_artifact=str(evidence.get("result_artifact", "")),
            regression_evidence=evidence.get("regression_evidence"),
            production_callable=str(evidence.get("production_callable", "")),
            notes=str(evidence.get("notes", "")),
        ))
        if decision["status"] in {"qualified", "served"}:
            statuses.append(str(decision["component"]))
    return statuses


__all__ = ["STATUS_ORDER", "QualificationEvidence", "classify", "qualified_components"]
