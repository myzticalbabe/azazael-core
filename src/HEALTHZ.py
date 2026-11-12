# coding: utf-8
"""
HEALTHZ - Health Check Meta-Worm

System health monitoring and reporting.

Features:
- Health status reporting
- Service checks
- Dependency validation
- Uptime monitoring
- Health history tracking

Usage:
    >>> import healthz
    >>> healthz.check()            # Full health check
    >>> healthz.get_status()       # Get current status
    >>> healthz.history()          # Get health history
"""

import os
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("HEALTHZ:L33", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# Portability: use env vars with local fallbacks
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
_DEFAULT_OUTPUT = os.path.join(_PROJECT_ROOT, "output")


@dataclass
class HealthStatus:
    """System health status."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    healthy: bool = True
    cpu_status: str = "OK"
    memory_status: str = "OK"
    disk_status: str = "OK"
    network_status: str = "OK"
    process_status: str = "OK"
    issues: list[str] = field(default_factory=list)
    uptime_hours: float = 0.0


@register_worm(category="defense", priority=7, cooldown_seconds=30.0, description="System health check meta-worm — CPU, memory, disk, network, process monitoring")
class HEALTHZ:
    """Health Check Meta-Worm."""

    VERSION = "1.0.0"
    _OUTPUT_DIR = os.environ.get("AZAZAEL_OUTPUT_PATH", os.path.join(_PROJECT_ROOT, "output"))
    STATE_FILE = os.path.join(_OUTPUT_DIR, "healthz_state.json")

    def __init__(self):
        try:
            import psutil as _psutil
            self.boot_time = _psutil.boot_time()
        except Exception as exc:
            note_failure("HEALTHZ:L70", exc)
            self.boot_time = time.time()
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load health state."""
        try:
            if Path(self.STATE_FILE).exists():
                return json.loads(Path(self.STATE_FILE).read_text(encoding="utf-8"))
        except Exception as exc:
            note_failure("HEALTHZ:L79", exc)
        return {"checks": 0, "last_healthy": True, "history": []}

    def _save_state(self):
        """Save health state."""
        try:
            Path(self.STATE_FILE).parent.mkdir(exist_ok=True)
            Path(self.STATE_FILE).write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        except Exception as exc:
            note_failure("HEALTHZ:L88", exc)

    def _check_cpu(self) -> str:
        """Check CPU health."""
        import psutil
        percent = psutil.cpu_percent(interval=1)
        if percent > 90:
            return "CRITICAL"
        elif percent > 70:
            return "WARNING"
        return "OK"

    def _check_memory(self) -> str:
        """Check memory health."""
        import psutil
        mem = psutil.virtual_memory()
        if mem.percent > 90:
            return "CRITICAL"
        elif mem.percent > 70:
            return "WARNING"
        return "OK"

    def _check_disk(self) -> str:
        """Check disk health."""
        import psutil
        disk = psutil.disk_usage("/")
        if disk.percent > 90:
            return "CRITICAL"
        elif disk.percent > 70:
            return "WARNING"
        return "OK"

    def _check_network(self) -> str:
        """Check network health."""
        try:
            import psutil
            counters = psutil.net_io_counters()
            if counters.errin > 100 or counters.errout > 100:
                return "WARNING"
            return "OK"
        except (ImportError, Exception) as exc:
            note_failure("HEALTHZ:L129", exc)
            return "UNKNOWN"

    def _check_processes(self) -> str:
        """Check process health."""
        import psutil
        count = len(psutil.pids())
        if count > 500:
            return "WARNING"
        return "OK"

    def check(self) -> HealthStatus:
        """Perform full health check."""
        cpu = self._check_cpu()
        mem = self._check_memory()
        disk = self._check_disk()
        net = self._check_network()
        proc = self._check_processes()

        issues = []
        healthy = True

        for status, name in [
            (cpu, "CPU"),
            (mem, "Memory"),
            (disk, "Disk"),
            (net, "Network"),
            (proc, "Processes"),
        ]:
            if status == "CRITICAL":
                issues.append(f"CRITICAL: {name} is at critical levels")
                healthy = False
            elif status == "WARNING":
                issues.append(f"WARNING: {name} usage is high")
                if healthy:
                    healthy = True

        uptime = (time.time() - self.boot_time) / 3600

        self.state["checks"] += 1
        self.state["last_healthy"] = healthy
        self.state["history"].append(
            {
                "timestamp": datetime.now().isoformat(),
                "healthy": healthy,
                "issues": len(issues),
            }
        )
        self.state["history"] = self.state["history"][-100:]
        self._save_state()

        return HealthStatus(
            healthy=healthy,
            cpu_status=cpu,
            memory_status=mem,
            disk_status=disk,
            network_status=net,
            process_status=proc,
            issues=issues,
            uptime_hours=round(uptime, 2),
        )

    def get_status(self) -> dict[str, Any]:
        """Get current status."""
        status = self.check()
        return {
            "healthy": status.healthy,
            "checks_run": self.state["checks"],
            "uptime_hours": status.uptime_hours,
            "issues": status.issues,
        }

    def history(self, limit: int = 10) -> list[dict]:
        """Get health history."""
        return self.state["history"][-limit:]


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="HEALTHZ - Health Check Meta-Worm")
    parser.add_argument("--check", action="store_true", help="Full health check")
    parser.add_argument("--status", action="store_true", help="Current status")
    parser.add_argument("--history", action="store_true", help="Health history")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    hz = HEALTHZ()

    if args.json:
        if args.check:
            print(json.dumps(hz.check().__dict__, indent=2))
        elif args.status:
            print(json.dumps(hz.get_status(), indent=2))
        elif args.history:
            print(json.dumps(hz.history(), indent=2))
        else:
            print(json.dumps(hz.check().__dict__, indent=2))
    else:
        if args.check:
            s = hz.check()
            print(f"Health Check: {'HEALTHY' if s.healthy else 'UNHEALTHY'}")
            print(
                f"  CPU: {s.cpu_status} | Memory: {s.memory_status} | Disk: {s.disk_status}"
            )
            print(f"  Network: {s.network_status} | Processes: {s.process_status}")
            print(f"  Uptime: {s.uptime_hours:.1f}h")
            for issue in s.issues:
                print(f"  [!] {issue}")
        elif args.status:
            st = hz.get_status()
            print(f"Status: {'HEALTHY' if st['healthy'] else 'UNHEALTHY'}")
            print(f"Checks: {st['checks_run']} | Uptime: {st['uptime_hours']}h")
        elif args.history:
            for h in hz.history():
                print(
                    f"{h['timestamp']}: {'OK' if h['healthy'] else 'ISSUES'} ({h['issues']})"
                )
        else:
            s = hz.check()
            print(f"Health: {'OK' if s.healthy else 'ISSUES'}")


if __name__ == "__main__":
    main()
