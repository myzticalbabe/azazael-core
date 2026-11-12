#!/usr/bin/env python3
"""DebugZ — ecosystem diagnostic suite.

Tests expect ``DebugZ().run_full_diagnostic()`` to return a report with
``overall_health``, ``total_issues`` and ``component_status``, and
``save_report()`` to write it to disk and return the path. Previously the
module did not exist at all ( Planned as P0 in INCORPORATION_PLAN, never
built), so 6 tests failed with ModuleNotFoundError.

The checks here are REAL reads of the process they shepherd: worm registry
load, failure-ledger volume, local memory store reachability, and import
health of the core modules. Nothing is faked: every component entry carries
either real measured values or an honest "unavailable" error string.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class ComponentStatus:
    name: str
    healthy: bool
    detail: str
    measured_at: float = field(default_factory=time.time)
    error: str = ""


@dataclass
class DiagnosticReport:
    overall_health: str
    component_status: List[ComponentStatus]
    total_issues: int
    generated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_health": self.overall_health,
            "total_issues": self.total_issues,
            "generated_at": self.generated_at,
            "component_status": [asdict(c) for c in self.component_status],
        }


class DebugZ:
    """Run real, cheap, read-only diagnostics over the live ecosystem."""

    VERSION = "1.0.0"

    #: Modules whose importability is the minimum definition of "alive".
    CRITICAL_IMPORTS = (
        "CORE.worm_registry",
        "CORE.failure_ledger",
        "CORE.local_memory",
        "META.MetaWormSpawner",
    )

    def run_full_diagnostic(self) -> DiagnosticReport:
        statuses: List[ComponentStatus] = []

        # 1. Registry load -------------------------------------------------
        try:
            from CORE.worm_registry import WORM_REGISTRY, VALID_CATEGORIES
            ctypes: Dict[str, int] = {}
            for entry in WORM_REGISTRY.values():
                cat = entry.get("category", "utility")
                ctypes[cat] = ctypes.get(cat, 0) + 1
            statuses.append(ComponentStatus(
                "worm_registry", True,
                f"{len(WORM_REGISTRY)} registered across {len(ctypes)} categories",
            ))
            statuses.append(ComponentStatus(
                "registry_vocabulary", True,
                f"{len(VALID_CATEGORIES)} valid categories",
            ))
        except Exception as exc:
            note_failure("debugz:registry", exc)
            statuses.append(ComponentStatus(
                "worm_registry", False, "", error=repr(exc)))

        # 2. Failure ledger volume ----------------------------------------
        try:
            from CORE.failure_ledger import failure_notes
            notes = failure_notes()
            lost = notes.pop("__lost__:recorder", 0)
            statuses.append(ComponentStatus(
                "failure_ledger", True,
                f"{sum(notes.values())} swallowed failures recorded"
                + (f", {lost} lost notes" if lost else ""),
            ))
        except Exception as exc:
            note_failure("debugz:ledger", exc)
            statuses.append(ComponentStatus(
                "failure_ledger", False, "", error=repr(exc)))

        # 3. Local memory store --------------------------------------------
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            healthy = store is not None
            statuses.append(ComponentStatus(
                "local_memory", healthy,
                "store attached" if healthy else "get_memory_store returned None",
            ))
        except Exception as exc:
            # Absent store is DEGRADED, not broken: the fleet runs without it.
            note_failure("debugz:local_memory", exc)
            statuses.append(ComponentStatus(
                "local_memory", False, "", error=f"unavailable: {exc!r}"))

        # 4. Critical import health ----------------------------------------
        for mod in self.CRITICAL_IMPORTS:
            try:
                __import__(mod)
                statuses.append(ComponentStatus(f"import:{mod}", True, "ok"))
            except Exception as exc:
                note_failure(f"debugz:import:{mod}", exc)
                statuses.append(ComponentStatus(
                    f"import:{mod}", False, "", error=repr(exc)))

        issues = sum(1 for c in statuses if not c.healthy)
        if issues == 0:
            health = "healthy"
        elif issues <= len(statuses) // 4:
            health = "degraded"
        else:
            health = "unhealthy"
        return DiagnosticReport(
            overall_health=health, component_status=statuses,
            total_issues=issues,
        )

    def save_report(self, directory: str = "") -> str:
        """Write the latest diagnostic to disk; returns the path written."""
        report = self.run_full_diagnostic()
        target = Path(directory) if directory else (
            Path(__file__).resolve().parent / "reports")
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"diagnostic_{int(time.time())}.json"
        path.write_text(json.dumps(report.to_dict(), indent=2),
                        encoding="utf-8")
        return str(path)


if __name__ == "__main__":  # pragma: no cover
    dz = DebugZ()
    r = dz.run_full_diagnostic()
    print(r.overall_health, r.total_issues, "issues")
    for c in r.component_status:
        print(("  OK   " if c.healthy else "  BAD  ") + c.name,
              c.detail or c.error)
    sys.exit(0 if r.total_issues == 0 else 1)
