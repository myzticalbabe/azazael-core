# coding: utf-8
"""
CONTROLLED CHAOS — Category: DIAGNOSTIC (1 worm)
Safe failure injection so the homeostat has a failure gradient to learn
from. Every drill runs real code paths and records truthful outcomes
(success reflects the OPERATION, never the drill verdict).

Drills: worker-prune path, partition detect/heal path, controlled DNS
failure (guaranteed fail-grade sample). Outcomes must be recorded under
this worm's registered name ("ChaosWorm") or HomeostatWorm skips them
(it only proposes on registry-known worms).

ChaosWorm
"""

import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("chaos:L24", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(category="diagnostic", priority=5, cooldown_seconds=60.0,
    description="Safe chaos drills: prune/partition/controlled-fail with truthful outcomes")
class ChaosWorm:
    """Failure injection for training. Safe by construction: no state is
    destroyed (prune uses fake PIDs, partition only detects, DNS failure
    targets a reserved-invalid name)."""

    VERSION = "1.0.0"

    def __init__(self):
        self._drills = 0
        self._lock = threading.Lock()

    def drill(self, kind: str = "controlled-fail") -> dict[str, Any]:
        """Run one drill. Returns {ok, ms} where ok = operation success."""
        t0 = time.perf_counter()
        try:
            if kind == "prune":
                from CORE.WORMS.expansion.glia import MicrogliaWorm
                r = MicrogliaWorm().prune_report(dead_pids=[999991], dlq_depth=0)
                ok = r.get("total_pruned", 0) >= 1
            elif kind == "partition":
                from CORE.WORMS.expansion.mesh import PartitionHealerWorm
                det = PartitionHealerWorm().detect()
                h = PartitionHealerWorm().heal()
                ok = isinstance(det.get("partitioned"), bool) and h.get("success", False)
            elif kind == "controlled-fail":
                try:
                    socket.gethostbyname("nonexistent.invalid.")
                    ok = False  # resolved?! should never happen
                except Exception as exc:
                    note_failure("chaos:L59", exc)
                    ok = False  # operation failed, truthfully
            else:
                return {"success": False, "error": f"unknown drill {kind}"}
        except Exception as e:
            note_failure("chaos:L63", e)
            return {"success": False, "error": str(e)[:800]}
        ms = round((time.perf_counter() - t0) * 1000, 1)
        with self._lock:
            self._drills += 1
            n = self._drills
        return {"success": True, "result": {"kind": kind, "ok": ok, "ms": ms,
                                            "drills": n,
                                            "at": datetime.now(timezone.utc).isoformat()}}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.drill()}
