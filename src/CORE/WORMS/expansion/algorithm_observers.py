# coding: utf-8
"""
ALGORITHM OBSERVERS — Category: MONITOR (2 worms)

AlgorithmLabWorm  — reports the algorithm laboratory's evidence state
                    (state/algorithm_lab/current.json).
MetaEvolutionWorm — reports the meta-evolution control plane's state
                    (state/meta_evolution/current.json).

Why these are observers and not training worms
----------------------------------------------
The laboratory components (`POLICY_LAB`, `DIVERSITY_QD_NSGA3`) run in
PRODUCTION (operator decision 2026-09-24): the chain's `algolab` job executes
`scripts/algorithm_lab_cycle.py` hourly and `track_registry.LAB_COMPONENTS`
records the production callable. What the mesh still lacked was a view of any
of it: the census had no idea the laboratory and the meta-evolution control
plane existed at all (reconciliation 2026-09-24), so a dead algorithm lab or a
blocked meta-evolution cycle was invisible to every fleet view. These worms
close that gap by OBSERVING the two subsystems read-only: the chain RUNS the
lab, these worms REPORT it to the mesh. They never write, never train, and
never promote — one mechanism must not both produce and judge its own state.

Honesty contract (same as the monitors):
  - every field is optional; a missing or partial artifact renders as None /
    "unknown", never as a plausible invented number;
  - staleness is aged by the timestamp INSIDE the artifact, never by file mtime
    (a frozen copy must not look live);
  - `status` is one of "ok" / "stale" / "unknown" — "unknown" is not "ok".

Path contract: the artifact paths here are derived exactly as the producers
derive them (including `AZAZAEL_META_EVOLUTION_DIR`), and
`tests/test_algorithm_observers.py` imports the producer constants to pin the
agreement — the check that cannot drift.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:  # standalone import: keep the class usable without the mesh
    note_failure("algorithm_observers:L47", exc)
    _REGISTRY = False

    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):  # noqa: D103 - shim, see code_audit.py pattern
        return lambda cls: cls


# Repository root: .../src/CORE/WORMS/expansion/this_file.py -> parents[4].
_ROOT = Path(__file__).resolve().parents[4]

#: Producer paths — derived exactly as the producers derive them
#: (`scripts/algorithm_lab_cycle.py::DEFAULT_OUTPUT`,
#:  `scripts/meta_evolution_cycle.py::REPORT_PATH`); pinned by test.
ALGORITHM_LAB_STATE = _ROOT / "state" / "algorithm_lab" / "current.json"
META_EVOLUTION_STATE = (
    Path(os.environ.get("AZAZAEL_META_EVOLUTION_DIR",
                        str(_ROOT / "state" / "meta_evolution")))
    / "current.json"
)


def _age_min(iso: Optional[str]) -> Optional[float]:
    """Minutes since an ISO timestamp; None when absent or unreadable."""
    if not iso or not isinstance(iso, str):
        return None
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError as exc:
        note_failure("algorithm_observers:L74", exc)
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() / 60.0


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Read one artifact; None on any failure (reported as `unknown`, not ok)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError) as exc:
        note_failure("algorithm_observers:L87", exc)
        return None


class AlgorithmLabWorm:
    """Read-only status probe for the algorithm laboratory evidence."""

    VERSION = "1.0.0"
    #: The lab is evidence regenerated on demand (not a 5-min loop); older than
    #: this and the qualification evidence is what is stale, not the panel.
    STALE_AFTER_MIN = 24 * 60.0

    def __init__(self, state_path: (str | Path | None) = None):
        self.state_path = Path(state_path) if state_path else ALGORITHM_LAB_STATE

    def execute(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "worm": "AlgorithmLabWorm",
            "version": self.VERSION,
            "at": None,
            "age_min": None,
            "status": "unknown",
            "components": {},
            "policy_rows": None,
            "algorithms": [],
            "diversity": {},
        }
        data = _read_json(self.state_path)
        if data is None:
            return out
        out["at"] = data.get("generated_at")
        out["age_min"] = _age_min(out["at"])
        qual = data.get("qualification")
        if isinstance(qual, list):
            for row in qual:
                if isinstance(row, dict) and row.get("component"):
                    out["components"][str(row["component"])] = str(
                        row.get("status") or "unknown")
        policy = data.get("policy")
        if isinstance(policy, list):
            out["policy_rows"] = len(policy)
            out["algorithms"] = sorted(
                {str(r.get("algorithm")) for r in policy
                 if isinstance(r, dict) and r.get("algorithm")})
        div = data.get("diversity")
        if isinstance(div, dict):
            me = div.get("map_elites")
            if isinstance(me, dict):
                out["diversity"]["map_elites"] = {
                    k: me.get(k) for k in ("filled", "total_cells",
                                           "coverage", "qd_score")}
            if isinstance(div.get("novelty_archive"), (int, float)):
                out["diversity"]["novelty_archive"] = div["novelty_archive"]
            n3 = div.get("nsga3")
            if isinstance(n3, dict):
                out["diversity"]["nsga3"] = {k: n3.get(k)
                                             for k in ("input", "selected")}
        out["status"] = ("stale" if (out["age_min"] is not None
                                     and out["age_min"] > self.STALE_AFTER_MIN)
                         else "ok")
        return out


class MetaEvolutionWorm:
    """Read-only status probe for the meta-evolution control plane."""

    VERSION = "1.0.0"
    #: The chain runs `meta_evolution_cycle.py` on a 3600 s interval
    #: (`chain_controller.JOBS["meta"]`); 2.5 intervals without a report means
    #: the control plane, not the panel, has stopped.
    STALE_AFTER_MIN = 150.0

    def __init__(self, state_path: (str | Path | None) = None):
        self.state_path = (Path(state_path) if state_path
                           else META_EVOLUTION_STATE)

    def execute(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "worm": "MetaEvolutionWorm",
            "version": self.VERSION,
            "at": None,
            "age_min": None,
            "status": "unknown",
            "lifecycle": None,
            "candidate": None,
            "open_candidates": None,
            "apply": None,
        }
        data = _read_json(self.state_path)
        if data is None:
            return out
        out["at"] = data.get("at")
        out["age_min"] = _age_min(out["at"])
        life = data.get("lifecycle")
        if isinstance(life, dict):
            out["lifecycle"] = life.get("state")
        elif isinstance(life, str):
            out["lifecycle"] = life
        out["apply"] = bool(data.get("apply")) if "apply" in data else None
        cand = data.get("candidate")
        if isinstance(cand, dict):
            out["candidate"] = {
                "id": cand.get("id"),
                "path": cand.get("path"),
                "line": cand.get("line"),
                "detector": cand.get("detector"),
                "severity": cand.get("severity"),
                "why": cand.get("why"),
            }
        # An unapplied candidate is OPEN work, not a clean board — the count is
        # what a reader acts on, so it is stated explicitly.
        out["open_candidates"] = 1 if (cand and not out["apply"]) else 0
        out["status"] = ("stale" if (out["age_min"] is not None
                                     and out["age_min"] > self.STALE_AFTER_MIN)
                         else "ok")
        return out


if _REGISTRY:
    AlgorithmLabWorm = register_worm(
        category="monitor", priority=5, cooldown_seconds=600.0,
        description="Read-only observer: algorithm laboratory evidence "
                    "(state/algorithm_lab/current.json) — qualification "
                    "status, policy rows, diversity coverage. Never trains.",
    )(AlgorithmLabWorm)
    MetaEvolutionWorm = register_worm(
        category="monitor", priority=5, cooldown_seconds=600.0,
        description="Read-only observer: meta-evolution control plane "
                    "(state/meta_evolution/current.json) — lifecycle state "
                    "and open repair candidates. Never applies a patch.",
    )(MetaEvolutionWorm)


if __name__ == "__main__":
    print(json.dumps(AlgorithmLabWorm().execute(), indent=2, default=str))
    print(json.dumps(MetaEvolutionWorm().execute(), indent=2, default=str))
