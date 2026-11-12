# coding: utf-8
"""
PROCESSZ - Process Management Worm

Process monitoring and management for Azazael ecosystem.

Features:
- Process enumeration
- Process details (CPU, memory, threads)
- Process killing
- Process filtering
- Resource usage tracking
- Process tree visualization

Usage:
    >>> import processz
    >>> processz.list_processes()      # List all processes
    >>> processz.find_process("python") # Find by name
    >>> processz.kill_process(1234)     # Kill by PID
    >>> processz.get_summary()         # Get summary
"""

import json
from datetime import datetime
from typing import Any, List
from dataclasses import dataclass
import argparse

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
    note_failure("PROCESSZ:L30", exc)
    register_worm = lambda **kw: (lambda cls: cls)  # no-op fallback


@dataclass
class ProcessInfo:
    """Process information."""

    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    status: str
    create_time: str
    cmdline: list[str]
    num_threads: int
    memory_info: dict[str, float]


@register_worm(
    category="monitor",
    priority=7,
    cooldown_seconds=15.0,
    description="Process management and monitoring worm",
    tags={"system", "process", "resource"},
)
class PROCESSZ:
    """Process Management Worm."""

    VERSION = "1.0.0"

    def __init__(self):
        pass

    def _get_process_info(self, proc) -> (ProcessInfo | None):
        """Get detailed process info."""
        import psutil
        try:
            with proc.oneshot():
                mem_info = proc.memory_info()
                return ProcessInfo(
                    pid=proc.pid,
                    name=proc.name(),
                    cpu_percent=proc.cpu_percent(),
                    memory_percent=proc.memory_percent(),
                    status=proc.status(),
                    create_time=datetime.fromtimestamp(proc.create_time()).isoformat(),
                    cmdline=proc.cmdline(),
                    num_threads=proc.num_threads(),
                    memory_info={
                        "rss_mb": round(mem_info.rss / (1024**2), 2),
                        "vms_mb": round(mem_info.vms / (1024**2), 2),
                    },
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
            note_failure("PROCESSZ:L84", exc)
            return None

    def list_processes(
        self,
        limit: int = 50,
        sort_by: str = "cpu_percent",
        filter_name: str | None = None,
    ) -> list[ProcessInfo]:
        """list processes with optional filtering."""
        import psutil
        processes = []

        for proc in psutil.process_iter(
            [
                "pid",
                "name",
                "cpu_percent",
                "memory_percent",
                "status",
                "cmdline",
                "threads",
            ]
        ):
            try:
                info = self._get_process_info(proc)
                if info:
                    if filter_name:
                        if filter_name.lower() not in info.name.lower():
                            continue
                    processes.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, AttributeError) as exc:
                note_failure("PROCESSZ:L115", exc)

        sort_keys = {
            "cpu_percent": lambda x: x.cpu_percent,
            "memory_percent": lambda x: x.memory_percent,
            "pid": lambda x: x.pid,
            "name": lambda x: x.name,
        }
        sort_key = sort_keys.get(sort_by, lambda x: x.cpu_percent)
        processes.sort(key=sort_key, reverse=True)

        return processes[:limit]

    def find_process(self, name: str) -> list[ProcessInfo]:
        """Find processes by name."""
        return self.list_processes(limit=100, filter_name=name)

    def get_process(self, pid: int) -> (ProcessInfo | None):
        """Get single process by PID."""
        import psutil
        try:
            proc = psutil.Process(pid)
            return self._get_process_info(proc)
        except psutil.NoSuchProcess as exc:
            note_failure("PROCESSZ:L139", exc)
            return None

    def kill_process(self, pid: int, graceful: bool = True) -> bool:
        """Kill process by PID."""
        import psutil
        try:
            proc = psutil.Process(pid)
            if graceful:
                proc.terminate()
                proc.wait(timeout=5)
            else:
                proc.kill()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            note_failure("PROCESSZ:L153", exc)
            return False

    def kill_by_name(self, name: str, graceful: bool = True) -> int:
        """Kill all processes matching name."""
        import psutil
        killed = 0
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] and name.lower() in proc.info["name"].lower():
                    if self.kill_process(proc.pid, graceful):
                        killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                note_failure("PROCESSZ:L165", exc)
        return killed

    def get_summary(self) -> dict[str, Any]:
        """Get process summary."""
        import psutil
        total_cpu = 0
        total_memory = 0
        by_status = {"running": 0, "sleeping": 0, "other": 0}

        for proc in psutil.process_iter(["cpu_percent", "memory_percent", "status"]):
            try:
                info = proc.info
                if info["cpu_percent"]:
                    total_cpu += info["cpu_percent"]
                if info["memory_percent"]:
                    total_memory += info["memory_percent"]
                status = info.get("status", "unknown")
                if status == psutil.STATUS_RUNNING:
                    by_status["running"] += 1
                elif status == psutil.STATUS_SLEEPING:
                    by_status["sleeping"] += 1
                else:
                    by_status["other"] += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, AttributeError) as exc:
                note_failure("PROCESSZ:L190", exc)

        return {
            "total_processes": len(psutil.pids()),
            "total_cpu_percent": round(total_cpu, 1),
            "total_memory_percent": round(total_memory, 1),
            "by_status": by_status,
        }


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="PROCESSZ - Process Management Worm")
    parser.add_argument("--list", action="store_true", help="list processes")
    parser.add_argument("--find", type=str, help="Find process by name")
    parser.add_argument("--pid", type=int, help="Get process by PID")
    parser.add_argument("--kill", type=int, help="Kill process by PID")
    parser.add_argument("--kill-name", type=str, help="Kill processes by name")
    parser.add_argument("--summary", action="store_true", help="Process summary")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    pz = PROCESSZ()

    if args.json:
        if args.list:
            print(
                json.dumps(
                    [p.__dict__ for p in pz.list_processes()], indent=2, default=str
                )
            )
        elif args.find:
            print(
                json.dumps(
                    [p.__dict__ for p in pz.find_process(args.find)],
                    indent=2,
                    default=str,
                )
            )
        elif args.pid:
            proc = pz.get_process(args.pid)
            print(json.dumps(proc.__dict__ if proc else {}, indent=2, default=str))
        elif args.summary:
            print(json.dumps(pz.get_summary(), indent=2))
        else:
            print(
                json.dumps(
                    [p.__dict__ for p in pz.list_processes()[:10]],
                    indent=2,
                    default=str,
                )
            )
    else:
        if args.find:
            print(f"Processes matching '{args.find}':")
            for p in pz.find_process(args.find):
                print(
                    f"  PID {p.pid}: {p.name} - CPU: {p.cpu_percent}% | Mem: {p.memory_percent}%"
                )
        elif args.pid:
            proc = pz.get_process(args.pid)
            if proc:
                print(f"PID {proc.pid}: {proc.name}")
                print(f"  Status: {proc.status}")
                print(f"  CPU: {proc.cpu_percent}% | Memory: {proc.memory_percent}%")
                print(f"  Threads: {proc.num_threads}")
                print(f"  Created: {proc.create_time}")
            else:
                print("Process not found")
        elif args.kill:
            if pz.kill_process(args.kill):
                print(f"Killed process {args.kill}")
            else:
                print(f"Failed to kill process {args.kill}")
        elif args.kill_name:
            killed = pz.kill_by_name(args.kill_name)
            print(f"Killed {killed} processes")
        elif args.summary:
            s = pz.get_summary()
            print("Process Summary")
            print(f"  Total: {s['total_processes']}")
            print(
                f"  CPU: {s['total_cpu_percent']}% | Memory: {s['total_memory_percent']}%"
            )
            print(
                f"  Running: {s['by_status']['running']} | Sleeping: {s['by_status']['sleeping']}"
            )
        else:
            print(f"{'PID':>6} {'NAME':<20} {'CPU%':>6} {'MEM%':>6} {'STATUS':<10}")
            print("-" * 55)
            for p in pz.list_processes(limit=15):
                print(
                    f"{p.pid:>6} {p.name[:20]:<20} {p.cpu_percent:>6.1f} {p.memory_percent:>6.1f} {p.status:<10}"
                )


if __name__ == "__main__":
    main()
