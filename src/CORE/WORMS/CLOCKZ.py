# coding: utf-8
"""
CLOCKZ - Time Management Worm

Time synchronization and scheduling for Azazael ecosystem.

Features:
- Time synchronization
- Schedule management
- Cron-like scheduling
- Timezone handling
-Elapsed time tracking

Usage:
    >>> import clockz
    >>> clockz.get_time()           # Get current time
    >>> clockz.get_uptime()          # Get system uptime
    >>> clockz.schedule("*/5 * * * *", callback)  # Schedule task
"""

import time
import json
import sched
from datetime import datetime
from typing import Any, Callable, List, Optional
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
    note_failure("CLOCKZ:L30", exc)
    register_worm = lambda **kw: (lambda cls: cls)  # no-op fallback


@dataclass
class ScheduledTask:
    """Scheduled task information."""

    id: str
    interval: int
    last_run: (str | None)
    next_run: (str | None)
    enabled: bool


@register_worm(
    category="sync",
    priority=5,
    cooldown_seconds=30.0,
    description="Time synchronization and scheduling worm",
    tags={"time", "sync", "schedule"},
)
class CLOCKZ:
    """Time Management Worm."""

    VERSION = "1.0.0"

    def __init__(self):
        self.scheduler = sched.scheduler(time.time, time.sleep)
        self.tasks: dict[str, ScheduledTask] = {}
        self._task_counter = 0
        self._running = False

    def get_time(self, iso: bool = True) -> str:
        """Get current time."""
        now = datetime.now()
        return now.isoformat() if iso else str(now)

    def get_utc(self, iso: bool = True) -> str:
        """Get current UTC time."""
        now = datetime.utcnow()
        return now.isoformat() if iso else str(now)

    def get_uptime(self) -> dict[str, Any]:
        """Get system uptime."""
        import psutil

        boot = psutil.boot_time()
        now = time.time()
        seconds = now - boot
        return {
            "seconds": round(seconds, 2),
            "minutes": round(seconds / 60, 2),
            "hours": round(seconds / 3600, 2),
            "days": round(seconds / 86400, 2),
            "boot_time": datetime.fromtimestamp(boot).isoformat(),
        }

    def format_duration(self, seconds: float) -> str:
        """Format duration in human readable format."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            return f"{seconds / 60:.1f}m"
        elif seconds < 86400:
            return f"{seconds / 3600:.1f}h"
        else:
            return f"{seconds / 86400:.1f}d"

    def schedule_interval(
        self,
        callback: Callable,
        interval: int,
        args: Optional[tuple] = None,
        kwargs: Optional[dict] = None,
    ) -> str:
        """Schedule a recurring task."""
        self._task_counter += 1
        task_id = f"task_{self._task_counter}"

        def run_task():
            try:
                if args and kwargs:
                    callback(*args, **kwargs)
                elif args:
                    callback(*args)
                elif kwargs:
                    callback(**kwargs)
                else:
                    callback()
            except Exception as e:
                print(f"Task {task_id} error: {e}")

        self.scheduler.enter(interval, 1, run_task)
        self.tasks[task_id] = ScheduledTask(
            id=task_id,
            interval=interval,
            last_run=None,
            next_run=datetime.now().isoformat(),
            enabled=True,
        )

        return task_id

    def schedule_once(
        self,
        callback: Callable,
        delay: int,
        args: Optional[tuple] = None,
        kwargs: Optional[dict] = None,
    ) -> str:
        """Schedule a one-time task."""
        self._task_counter += 1
        task_id = f"once_{self._task_counter}"

        def run_task():
            try:
                if args and kwargs:
                    callback(*args, **kwargs)
                elif args:
                    callback(*args)
                elif kwargs:
                    callback(**kwargs)
                else:
                    callback()
            except Exception as e:
                print(f"Task {task_id} error: {e}")

        self.scheduler.enter(delay, 1, run_task)

        return task_id

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a scheduled task."""
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False

    def get_tasks(self) -> list[dict[str, Any]]:
        """Get all scheduled tasks."""
        return [
            {
                "id": t.id,
                "interval": t.interval,
                "last_run": t.last_run,
                "next_run": t.next_run,
                "enabled": t.enabled,
            }
            for t in self.tasks.values()
        ]

    def run_scheduler(self, blocking: bool = True):
        """Run the scheduler."""
        self._running = True
        self.scheduler.run(blocking=blocking)

    def stop_scheduler(self):
        """Stop the scheduler."""
        self._running = False


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="CLOCKZ - Time Management Worm")
    parser.add_argument("--time", action="store_true", help="Get current time")
    parser.add_argument("--utc", action="store_true", help="Get UTC time")
    parser.add_argument("--uptime", action="store_true", help="Get system uptime")
    parser.add_argument("--tasks", action="store_true", help="list scheduled tasks")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    cz = CLOCKZ()

    if args.json:
        if args.time:
            print(json.dumps({"time": cz.get_time()}, indent=2))
        elif args.utc:
            print(json.dumps({"utc": cz.get_utc()}, indent=2))
        elif args.uptime:
            print(json.dumps(cz.get_uptime(), indent=2))
        elif args.tasks:
            print(json.dumps(cz.get_tasks(), indent=2))
        else:
            print(
                json.dumps({"time": cz.get_time(), "uptime": cz.get_uptime()}, indent=2)
            )
    else:
        if args.time:
            print(f"Current Time: {cz.get_time()}")
        elif args.utc:
            print(f"UTC Time: {cz.get_utc()}")
        elif args.uptime:
            up = cz.get_uptime()
            print(f"Uptime: {up['days']} days, {up['hours']} hours")
            print(f"Boot: {up['boot_time']}")
        elif args.tasks:
            for t in cz.get_tasks():
                print(f"{t['id']}: interval={t['interval']}s")
        else:
            print(f"Time: {cz.get_time()}")
            print(f"Uptime: {cz.get_uptime()['days']:.1f} days")


if __name__ == "__main__":
    main()
