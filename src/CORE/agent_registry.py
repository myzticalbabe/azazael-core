# coding: utf-8
"""


__all__ = [
    "AgentHealth",
    "AgentHealthReporter",
    "create_agent",
    "discover_agents",
    "get_agent_class",
    "get_agents_by_category",
    "get_all_agent_categories",
    "register_agent",
    "reset_agent_registry",
]

agent_registry — Centralized Agent Registry with Auto-Discovery & Health Reporting

Provides:
- @register_agent decorator for automatic FreeThreadedAgent registration
- Global AGENT_REGISTRY dict with full metadata
- Auto-discovery from CORE directory for agent subclasses
- AgentHealthReporter: periodic health checks, status aggregation, alerting

Usage:
    >>> from CORE.agent_registry import register_agent, AGENT_REGISTRY, AgentHealthReporter
    >>> @register_agent(category="rl", priority=8, description="SB3-based RL agent")
    ... class MyAgent(FreeThreadedAgent):
    ...     VERSION = "1.0.0"
    ...
    >>> reporter = AgentHealthReporter()
    >>> health = reporter.check_all_agents()
"""

from __future__ import annotations

import os
import sys
import time
import importlib
import inspect
import threading

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, List
from dataclasses import dataclass
from collections import defaultdict

if TYPE_CHECKING:
    from CORE.Agent import FreeThreadedAgent

# --- Global Registry -----------------------------------------------

AGENT_REGISTRY: dict[str, dict[str, Any]] = {}
"""Global agent type registry.
Key: agent class name (e.g. "SB3Agent")
Value: {
    "class": the actual class,
    "module": module name,
    "category": str,
    "priority": int (1-10),
    "description": str,
    "registered_at": iso timestamp,
    "health_check_interval": float,
    "tags": set of tags,
    "source_file": str,
    "version": str,
}
"""

_registry_lock = threading.RLock()
_discovery_done = False


# --- Decorator -----------------------------------------------------

def register_agent(
    category: str = "utility",
    priority: int = 5,
    description: str = "",
    health_check_interval: float = 60.0,
    tags: (set[str] | None) = None,
):
    """
    Decorator to register a FreeThreadedAgent subclass in AGENT_REGISTRY.

    Args:
        category: Agent category (e.g., "rl", "monitor", "orchestrator", "utility")
        priority: Priority 1-10 (10 = highest)
        description: Human-readable description
        health_check_interval: Seconds between health checks
        tags: Optional set of tags for filtered selection

    Example:
        @register_agent(category="rl", priority=8, description="PPO-based RL agent")
        class MyRLAgent(FreeThreadedAgent):
            VERSION = "1.0.0"
    """
    def decorator(cls):
        name = cls.__name__
        module = cls.__module__
        try:
            source_file = inspect.getfile(cls) if hasattr(inspect, 'getfile') else ""
        except (TypeError, OSError) as exc:
            note_failure("agent_registry:L105", exc)
            source_file = ""

        with _registry_lock:
            AGENT_REGISTRY[name] = {
                "class": cls,
                "module": module,
                "category": category,
                "priority": priority,
                "description": description or (cls.__doc__ or "").split("\n")[0].strip(),
                "registered_at": datetime.now().isoformat(),
                "tags": tags or set(),
                "source_file": source_file,
                "version": getattr(cls, "VERSION", "0.0.0"),
                "health_check_interval": health_check_interval,
                "last_health_check": None,
                "last_health_status": "unknown",
            }

        return cls

    return decorator


# --- Auto-Discovery -------------------------------------------------

def discover_agents(scan_dir: (str | None) = None) -> int:
    """
    Auto-discover and import all FreeThreadedAgent subclasses from CORE directory.

    Scans Python files for classes that inherit from FreeThreadedAgent
    and registers any that have the @register_agent decorator.

    Args:
        scan_dir: Directory to scan (defaults to CORE/)

    Returns:
        Number of newly discovered agent classes.
    """
    global _discovery_done

    if scan_dir is None:
        scan_dir = str(Path(__file__).resolve().parent)

    if not os.path.isdir(scan_dir):
        return 0

    with _registry_lock:
        before_count = len(AGENT_REGISTRY)

    # Add scan dir to sys.path temporarily (snapshot guard for 3.14t)
    if scan_dir not in sys.path:
        with _registry_lock:
            if scan_dir not in sys.path:
                sys.path.insert(0, scan_dir)

    for filename in sorted(os.listdir(scan_dir)):
        if filename.startswith("_") or filename.startswith("."):
            continue
        if not filename.endswith(".py"):
            continue

        module_name = filename[:-3]  # strip .py

        # Skip if we already have classes from this module
        # TOCTOU fix: keep lock held through import check
        with _registry_lock:
            if any(
                info.get("source_file", "").endswith(filename)
                for info in AGENT_REGISTRY.values()
            ):
                continue

        try:
            module = importlib.import_module(module_name)

            with _registry_lock:
                # Double-check after import (another thread may have added)
                if any(
                    info.get("source_file", "").endswith(filename)
                    for info in AGENT_REGISTRY.values()
                ):
                    continue

            # Find FreeThreadedAgent subclasses
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if not isinstance(obj, type):
                    continue
                if attr_name.startswith("_"):
                    continue

                # Check if it's a FreeThreadedAgent subclass
                if not hasattr(obj, "_run") or not hasattr(obj, "execute"):
                    continue

                # Check if it already has VERSION (class is a proper agent)
                if not hasattr(obj, "VERSION"):
                    continue

                # Skip base classes
                if attr_name in ("FreeThreadedAgent", "AgentConfig", "AgentTask"):
                    continue

                # Register with guessed category if not already registered
                with _registry_lock:
                    if attr_name not in AGENT_REGISTRY:
                        category = _guess_agent_category(attr_name, module_name)
                        register_fn = register_agent(
                            category=category,
                            priority=_guess_agent_priority(attr_name, module_name),
                            description=(obj.__doc__ or "").strip(),
                        )
                        register_fn(obj)

        except Exception as exc:
            # Failed to import module — skip with log for debugging
            import logging
            logging.debug(f"[agent_registry] Skipping {module_name}: {exc}")

    with _registry_lock:
        _discovery_done = True
        result = len(AGENT_REGISTRY) - before_count
    return result


def _guess_agent_category(class_name: str, module_name: str) -> str:
    """Heuristic category guessing for auto-discovered agents."""
    name_upper = class_name.upper()

    if any(kw in name_upper for kw in ["SB3", "RL", "PPO", "DQN", "A2C", "TRAIN", "LEARN"]):
        return "rl"
    if any(kw in name_upper for kw in ["ORCHESTRAT", "MANAGER", "COORDINATOR"]):
        return "orchestrator"
    if any(kw in name_upper for kw in ["MONITOR", "WATCH", "HEALTH", "INSPECT"]):
        return "monitor"
    if any(kw in name_upper for kw in ["ANALYZ", "DIAG", "SCAN"]):
        return "diagnostic"
    if any(kw in name_upper for kw in ["POOL", "BENCH", "EXEC"]):
        return "executor"
    if any(kw in name_upper for kw in ["BUS", "MEMORY", "STORE"]):
        return "infrastructure"

    return "utility"


def _guess_agent_priority(class_name: str, module_name: str) -> int:
    """Heuristic priority guessing for auto-discovered agents."""
    name_upper = class_name.upper()
    if any(kw in name_upper for kw in ["ORCHESTRAT", "META"]):
        return 9
    if any(kw in name_upper for kw in ["RL", "SB3", "PPO", "TRAIN"]):
        return 8
    if any(kw in name_upper for kw in ["POOL", "BENCH"]):
        return 7
    if any(kw in name_upper for kw in ["MONITOR", "HEALTH", "ANALYZ"]):
        return 6
    if any(kw in name_upper for kw in ["BUS", "MEMORY"]):
        return 5
    return 4


# --- Agent Health Reporter -----------------------------------------

# Importlib.loader doesn't auto-register modules in sys.modules, but the
# @dataclass decorator's internal _is_type() needs sys.modules[cls.__module__]
# to exist. Register a shim to prevent AttributeError on importlib loads.
import sys as _sys
import types as _types

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
if __name__ not in _sys.modules:
    _sys.modules[__name__] = _types.ModuleType(__name__)

@dataclass
class AgentHealth:
    """Health status for a single agent instance."""
    agent_id: str
    agent_name: str
    agent_type: str
    category: str
    status: str  # "healthy", "degraded", "unhealthy", "unknown"
    tasks_submitted: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    success_rate: float = 100.0
    sub_agent_count: int = 0
    last_activity: (str | None) = None
    error_message: (str | None) = None
    version: str = ""
    free_threaded: bool = False
    last_seen: (str | None) = None


class AgentHealthReporter:
    """
    Periodic health checker for all registered FreeThreadedAgent instances.

    Maintains a registry of active agent instances, collects health status,
    and provides aggregated health reports.

    Features:
    - Register live agent instances for tracking
    - Periodic health checks with configurable intervals
    - Status aggregation across categories
    - Alert generation for unhealthy agents
    """

    def __init__(self, check_interval: float = 60.0):
        self._instances: dict[str, FreeThreadedAgent] = {}
        self._health_cache: dict[str, AgentHealth] = {}
        self._lock = threading.Lock()
        self.check_interval = check_interval
        self._last_check_time = 0.0

    def register_instance(self, agent: 'FreeThreadedAgent') -> None:
        """Register a live agent instance for health tracking.

        Validates that the agent has a valid agent_id before registration.
        Thread-safe via instance lock.
        """
        agent_id = getattr(agent, 'agent_id', None)
        if not agent_id or not isinstance(agent_id, str):
            return  # Silently skip invalid agents
        with self._lock:
            self._instances[agent_id] = agent

    def unregister_instance(self, agent_id: str) -> None:
        """Remove an agent instance from health tracking.

        Validates agent_id before attempting removal.
        Thread-safe via instance lock.
        """
        if not agent_id or not isinstance(agent_id, str):  # type: ignore[reportUnnecessaryIsInstance]
            return
        with self._lock:
            self._instances.pop(agent_id, None)
            self._health_cache.pop(agent_id, None)

    def check_agent(self, agent: 'FreeThreadedAgent') -> AgentHealth:
        """Check health of a single agent instance."""
        try:
            stats = agent.get_stats()
            completed = stats.get('tasks_completed', 0)
            failed = stats.get('tasks_failed', 0)
            total = completed + failed
            rate = (completed / total * 100) if total > 0 else 100.0

            sub_count = stats.get('sub_agent_count', 0)
            agent_type = type(agent).__name__
            reg_info = AGENT_REGISTRY.get(agent_type, {})
            category = reg_info.get('category', 'utility')

            if failed > 5 and total > 10 and rate < 50:
                status = "unhealthy"
            elif failed > 2 and rate < 80:
                status = "degraded"
            else:
                status = "healthy"

            last_errors = stats.get('errors', [])
            error_msg = last_errors[-1].get('error', '')[:200] if isinstance(last_errors, list) and last_errors else None

            return AgentHealth(
                agent_id=agent.agent_id,
                agent_name=agent.config.agent_name,
                agent_type=agent_type,
                category=category,
                status=status,
                tasks_submitted=stats.get('tasks_submitted', 0),
                tasks_completed=completed,
                tasks_failed=failed,
                success_rate=round(rate, 1),
                sub_agent_count=sub_count,
                last_activity=datetime.now().isoformat(),
                error_message=error_msg if status != "healthy" else None,
                version=stats.get('version', ''),
                free_threaded=stats.get('free_threaded', False),
                last_seen=datetime.now().isoformat(),
            )
        except Exception as e:
            note_failure("agent_registry:L383", e)
            return AgentHealth(
                agent_id=agent.agent_id,
                agent_name=getattr(agent.config, 'agent_name', 'unknown'),
                agent_type=type(agent).__name__,
                category='utility',
                status="unhealthy",
                error_message=str(e)[:200],
                last_seen=datetime.now().isoformat(),
            )

    def check_all_agents(self, force: bool = False) -> dict[str, list[AgentHealth]]:
        """
        Check health of all registered agent instances.

        Args:
            force: If True, bypass check_interval throttle.

        Returns:
            dict with 'healthy', 'degraded', 'unhealthy', 'unknown' lists.
        """
        now = time.time()
        if not force and now - self._last_check_time < self.check_interval:
            # Return cached results
            by_status: dict[str, list[AgentHealth]] = defaultdict(list)
            for h in self._health_cache.values():
                by_status[h.status].append(h)
            return dict(by_status)

        with self._lock:
            instances = dict(self._instances)

        results: list[AgentHealth] = []
        for agent in instances.values():
            health = self.check_agent(agent)
            with self._lock:
                self._health_cache[agent.agent_id] = health
            results.append(health)

        self._last_check_time = now

        by_status = defaultdict(list)
        for h in results:
            by_status[h.status].append(h)

        return dict(by_status)

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of the entire agent fleet health."""
        by_status = self.check_all_agents()
        total = sum(len(v) for v in by_status.values())

        # Aggregate by category
        by_category: dict[str, dict] = {}
        all_health = [h for lst in by_status.values() for h in lst]
        for h in all_health:
            if h.category not in by_category:
                by_category[h.category] = {'total': 0, 'healthy': 0, 'degraded': 0, 'unhealthy': 0}
            by_category[h.category]['total'] += 1
            by_category[h.category][h.status] += 1

        error_count = sum(1 for h in all_health if h.error_message)

        return {
            'total_agents': total,
            'healthy': len(by_status.get('healthy', [])),
            'degraded': len(by_status.get('degraded', [])),
            'unhealthy': len(by_status.get('unhealthy', [])),
            'unknown': len(by_status.get('unknown', [])),
            'agents_with_errors': error_count,
            'by_category': by_category,
            'last_check': datetime.now().isoformat(),
        }


# --- Convenience Functions -----------------------------------------

def get_agents_by_category(category: str) -> dict[str, dict]:
    """Get all registered agent types in a category."""
    with _registry_lock:
        return {
            name: info for name, info in AGENT_REGISTRY.items()
            if info.get("category") == category
        }


def get_all_agent_categories() -> set[str]:
    """Get all unique categories in the agent registry."""
    with _registry_lock:
        return set(info.get("category", "utility") for info in AGENT_REGISTRY.values())


def get_agent_class(name: str) -> (type | None):
    """Get an agent class by name from the registry."""
    with _registry_lock:
        info = AGENT_REGISTRY.get(name)
    return info.get("class") if info else None


def create_agent(agent_type: str, **kwargs):
    """
    Create an agent instance from the registry.

    Args:
        agent_type: Class name registered in AGENT_REGISTRY
        **kwargs: Passed to the agent constructor.

    Returns:
        FreeThreadedAgent instance or None if type not found.
    """
    with _registry_lock:
        info = AGENT_REGISTRY.get(agent_type)
        if not info:
            return None
        cls = info.get("class")
        if not cls:
            return None
    try:
        return cls(**kwargs)
    except Exception as exc:
        import logging
        logging.debug(f"[agent_registry] Failed to create agent {agent_type}: {exc}")
        return None


def reset_agent_registry():
    """Reset the agent registry (for testing)."""
    global _discovery_done
    with _registry_lock:
        AGENT_REGISTRY.clear()
        _discovery_done = False


# AgentHealthReporter uses FreeThreadedAgent as a TYPE_CHECKING import (circular-safe)

# Auto-discover on import (opt-in via AZAZAEL_AUTO_DISCOVER_AGENTS to avoid ~38s overhead)
def _auto_discover():
    if os.environ.get('AZAZAEL_AUTO_DISCOVER_AGENTS', '').lower() in ('1', 'true', 'yes'):
        try:
            discover_agents()
        except Exception as exc:
            import logging
            logging.debug(f"[agent_registry] Auto-discover failed: {exc}")
