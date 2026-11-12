# coding: utf-8
"""
SYSTEMZ - System Information Worm

System monitoring and management worm for Azazael ecosystem.

Features:
- CPU usage monitoring
- Memory usage tracking
- Disk space analysis
- System uptime and load
- Process enumeration
- Temperature monitoring (if available)

Usage:
    >>> from WORMZ.SYSTEMZ import SystemZ
    >>> sz = SystemZ()
    >>> status = sz.get_status()
"""

VERSION = "1.0.0"

import json
from datetime import datetime
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
    note_failure("SYSTEMZ:L32", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

__all__ = ["SystemZ", "SystemStatus", "VERSION"]


@dataclass
class SystemStatus:
    """Complete system status."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    cpu_percent: float = 0.0
    cpu_count: int = 0
    memory_percent: float = 0.0
    memory_used_gb: float = 0.0
    memory_total_gb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    boot_time: str = ""
    uptime_seconds: float = 0.0
    process_count: int = 0


@register_worm(category="monitor", priority=8, cooldown_seconds=15.0, description="System resource monitor (CPU, memory, disk, processes)")
class SystemZ:
    """System Information Worm."""

    VERSION = "1.0.0"

    def __init__(self):
        try:
            import psutil
            self.boot_time = datetime.fromtimestamp(psutil.boot_time())
        except Exception as exc:
            note_failure("SYSTEMZ:L68", exc)
            self.boot_time = datetime.now()

    def get_cpu(self, interval: float = 1.0) -> dict[str, Any]:
        """Get CPU information."""
        import psutil
        return {
            "percent": psutil.cpu_percent(interval=interval),
            "count": psutil.cpu_count(),
            "count_logical": psutil.cpu_count(logical=True),
            "freq": psutil.cpu_freq(),
            "times": psutil.cpu_times_percent(interval=interval),
        }

    def get_memory(self) -> dict[str, Any]:
        """Get memory information."""
        import psutil
        mem = psutil.virtual_memory()
        return {
            "percent": mem.percent,
            "used_gb": round(mem.used / (1024**3), 2),
            "total_gb": round(mem.total / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "used_percent": mem.percent,
        }

    def get_disk(self) -> dict[str, Any]:
        """Get disk information."""
        import psutil
        disk = psutil.disk_usage("/")
        return {
            "percent": disk.percent,
            "used_gb": round(disk.used / (1024**3), 2),
            "total_gb": round(disk.total / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
        }

    def get_processes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get top processes by CPU usage."""
        import psutil
        processes = []
        for proc in psutil.process_iter(
            ["pid", "name", "cpu_percent", "memory_percent"]
        ):
            try:
                info = proc.info
                if info["cpu_percent"] is not None:
                    processes.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                note_failure("SYSTEMZ:L116", exc)
        processes.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
        return processes[:limit]

    def get_boot_time(self) -> dict[str, Any]:
        """Get boot time information."""
        return {
            "boot_time": self.boot_time.isoformat(),
            "uptime_seconds": (datetime.now() - self.boot_time).total_seconds(),
            "uptime_hours": round(
                (datetime.now() - self.boot_time).total_seconds() / 3600, 2
            ),
        }

    def get_status(self) -> SystemStatus:
        """Get complete system status."""
        import psutil
        cpu_count = psutil.cpu_count() or 4
        return SystemStatus(
            cpu_percent=self.get_cpu()["percent"],
            cpu_count=cpu_count,
            memory_percent=self.get_memory()["percent"],
            memory_used_gb=self.get_memory()["used_gb"],
            memory_total_gb=self.get_memory()["total_gb"],
            disk_percent=self.get_disk()["percent"],
            disk_used_gb=self.get_disk()["used_gb"],
            disk_total_gb=self.get_disk()["total_gb"],
            boot_time=self.boot_time.isoformat(),
            uptime_seconds=(datetime.now() - self.boot_time).total_seconds(),
            process_count=len(psutil.pids()),
        )

    def get_summary(self) -> dict[str, Any]:
        """Get system summary — uses interval=0 for instant CPU reading."""
        cpu = self.get_cpu(interval=0)
        mem = self.get_memory()
        disk = self.get_disk()
        boot = self.get_boot_time()
        return {
            "cpu_pct": cpu["percent"],
            "cpu_count": cpu["count"],
            "mem_pct": mem["percent"],
            "disk_pct": disk["percent"],
            "uptime_h": round(boot["uptime_hours"], 1),
        }


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="SYSTEMZ - System Information Worm")
    parser.add_argument("--status", action="store_true", help="Full status")
    parser.add_argument("--cpu", action="store_true", help="CPU info")
    parser.add_argument("--memory", action="store_true", help="Memory info")
    parser.add_argument("--disk", action="store_true", help="Disk info")
    parser.add_argument("--processes", action="store_true", help="Top processes")
    parser.add_argument("--summary", action="store_true", help="Summary")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    sz = SystemZ()

    if args.json:
        if args.status:
            print(json.dumps(sz.get_status().__dict__, indent=2))
        elif args.cpu:
            print(json.dumps(sz.get_cpu(), indent=2))
        elif args.memory:
            print(json.dumps(sz.get_memory(), indent=2))
        elif args.disk:
            print(json.dumps(sz.get_disk(), indent=2))
        elif args.processes:
            print(json.dumps(sz.get_processes(), indent=2))
        elif args.summary:
            print(json.dumps(sz.get_summary(), indent=2))
        else:
            print(json.dumps(sz.get_status().__dict__, indent=2))
    else:
        if args.cpu:
            cpu = sz.get_cpu()
            print(f"CPU: {cpu['percent']}% | Cores: {cpu['count']}")
        elif args.memory:
            mem = sz.get_memory()
            print(
                f"Memory: {mem['used_gb']}GB / {mem['total_gb']}GB ({mem['percent']}%)"
            )
        elif args.disk:
            disk = sz.get_disk()
            print(
                f"Disk: {disk['used_gb']}GB / {disk['total_gb']}GB ({disk['percent']}%)"
            )
        elif args.processes:
            for p in sz.get_processes():
                print(f"PID {p['pid']}: {p['name']} - CPU: {p['cpu_percent']}%")
        elif args.summary:
            summary = sz.get_summary()
            print(
                f"CPU: {summary['cpu']['percent']}% | Memory: {summary['memory']['percent']}% | Disk: {summary['disk']['percent']}%"
            )
        else:
            status = sz.get_status()
            print("SYSTEMZ Status")
            print(f"  CPU: {status.cpu_percent}% ({status.cpu_count} cores)")
            print(f"  Memory: {status.memory_used_gb}GB / {status.memory_total_gb}GB")
            print(f"  Disk: {status.disk_used_gb}GB / {status.disk_total_gb}GB")
            print(f"  Uptime: {status.uptime_seconds / 3600:.1f} hours")
            print(f"  Processes: {status.process_count}")


if __name__ == "__main__":
    main()
