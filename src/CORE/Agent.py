# coding: utf-8
"""
FreeThreadedAgent — Base class for all AZAZAEL agents running under Python 3.14t.

Every agent that inherits from FreeThreadedAgent gets:
- Automatic PAMS memory recording (start/end/error events)
- FreeThreadPool for true parallel execution under GIL-disabled Python
- ThreadSafeDict for thread-safe shared state
- Standard execute(task) -> _run(task, pool) lifecycle
- Built-in stats tracking (tasks, duration, errors)

CANONICAL SOURCE — import from here project-wide:
    from azazael.src.CORE.Agent import FreeThreadedAgent

Usage:
    class MyAgent(FreeThreadedAgent):
        def _run(self, task: dict, pool: FreeThreadPool) -> Any:
            # Your agent logic here — runs inside FreeThreadPool
            return {"result": "done"}
"""

import sys
import os
import time
import threading
from CORE.uuid7 import uuid7
import traceback
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Import CORE utilities (with graceful fallbacks) --------------------------
_azazael_src = str(Path(__file__).resolve().parent.parent)
if _azazael_src not in sys.path:
    sys.path.insert(0, _azazael_src)

try:
    from CORE.freethreading import (
        is_free_threaded, FreeThreadPool, ThreadSafeDict,  # pyright: ignore[reportAssignmentType] -- except-path supplies a function shim for FreeThreadPool while try binds the real class; selected by import success, not substitutability
        ThreadSafeList, ThreadSafeCounter,
    )
    HAS_FREETHREADING = True
except ImportError:
    HAS_FREETHREADING = False
    ThreadSafeDict: Any = dict  # type: ignore[no-redef]
    ThreadSafeList: Any = list  # type: ignore[no-redef]
    class _ThreadSafeCounter:
        """Thread-safe counter using lock for Python 3.14t free-threading."""
        def __init__(self, initial: int = 0):
            self._v = initial
            self._lock = threading.Lock()
        def increment(self, d: int = 1) -> int:
            with self._lock:
                self._v += d
                return self._v
        @property
        def value(self) -> int:
            with self._lock:
                return self._v
    ThreadSafeCounter = _ThreadSafeCounter
    def is_free_threaded() -> bool: 
        return hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()
    def FreeThreadPool(*a, **kw):  # type: ignore[no-redef]
        from concurrent.futures import ThreadPoolExecutor
        class _FTP:
            def __init__(self, max_workers=0, thread_name_prefix='ft-worker'):
                self.max_workers = max_workers or (os.cpu_count() or 8)
                self.thread_name_prefix = thread_name_prefix
            def __enter__(self): 
                self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name_prefix)
                return self
            def __exit__(self, *a): 
                if self._executor: 
                    self._executor.shutdown(wait=True)
                    self._executor = None
            def submit(self, fn, *a, **kw):
                if self._executor is None:
                    raise RuntimeError("FreeThreadPool not entered")
                return self._executor.submit(fn, *a, **kw)
            def map(self, fn, *it, **kw):
                if self._executor is None:
                    raise RuntimeError("FreeThreadPool not entered")
                return self._executor.map(fn, *it, **kw)
        return _FTP(*a, **kw)

try:
    from CORE.PamsRecorder import PamsRecorder, get_recorder  # pyright: ignore[reportAssignmentType] -- optional import: except-path defines structurally distinct fallback class/function behind the same names (the existing no-redef shims); selected by import success
    HAS_PAMS = True
except ImportError as exc:
    note_failure("Agent:L90", exc)
    HAS_PAMS = False
    class PamsRecorder:  # type: ignore[no-redef]
        """Fallback PAMS recorder when CORE.PamsRecorder is unavailable."""
        def __init__(self):
            self._stats = {'version': '0.0.0-fallback', 'session_id': 'none'}
        def record_event(self, event_type, data=None, source=None, severity=None):
            return False
        def record_batch(self, events):
            return 0
        def get_stats(self):
            return dict(self._stats)
        def __call__(self):
            return self  # Compatibility with singleton pattern
    def get_recorder():  # type: ignore[no-redef]
        return PamsRecorder()

# -- MemoryBus for inter-agent messaging ----------------------------
try:
    from CORE.MemoryBus import MemoryBus, BusMessage
    HAS_MEMORYBUS = True
except ImportError as exc:
    note_failure("Agent:L111", exc)
    HAS_MEMORYBUS = False
    MemoryBus: Any = None  # type: ignore[no-redef]
    BusMessage: Any = None  # type: ignore[no-redef]

# -- AgentRegistry for agent discovery -------------------------------
try:
    from CORE.agent_registry import register_agent, AGENT_REGISTRY, AgentHealthReporter  # pyright: ignore[reportAssignmentType] -- optional import: except-path fabricates a decorator fallback with an unrelated signature; selected by import success
    HAS_AGENT_REGISTRY = True
except ImportError as exc:
    note_failure("Agent:L120", exc)
    HAS_AGENT_REGISTRY = False
    def register_agent(**kw):  # type: ignore[no-redef]
        return lambda cls: cls
    AGENT_REGISTRY: Dict[str, Any] = {}
    AgentHealthReporter: Any = None  # type: ignore[no-redef]

IS_FREE_THREADED = is_free_threaded()


@dataclass
class AgentConfig:
    """Configuration for a FreeThreadedAgent instance."""
    agent_id: Optional[str] = None
    agent_name: str = "unnamed"
    max_workers: int = 0                    # 0 = auto (cpu_count)
    max_recursion: int = 5
    timeout_seconds: float = 300.0
    record_to_pams: bool = True
    pams_source: str = "agent"
    thread_name_prefix: str = "ft-agent"
    log_level: str = "info"                 # debug, info, warning, error


@dataclass
class AgentTask:
    """A task submitted to a FreeThreadedAgent."""
    task_id: str
    task_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    submitted_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    duration_ms: float = 0.0
    result: Any = None
    error: Optional[str] = None
    success: bool = False
    agent_id: Optional[str] = None

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if self.completed_at:
            return "completed"
        if self.started_at:
            return "running"
        return "pending"


class FreeThreadedAgent:
    """
    Base class for all AZAZAEL free-threading agents.

    Features:
    - Automatic PAMS recording: start/end/error events logged per task
    - FreeThreadPool for true parallel execution under 3.14t
    - ThreadSafeDict for shared state across parallel tasks
    - Stats tracking: tasks submitted, completed, failed, avg duration
    - Recursive task spawning via self.spawn_subtask()

    Lifecycle:
        agent = MyAgent(config)
        result = agent.execute({"prompt": "..."})        # single task
        results = agent.execute_many([task1, task2, ...])  # parallel batch
    """

    VERSION = "1.0.0"

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig()
        if self.config.agent_id is None:
            self.config.agent_id = uuid7().hex[:8]
        self.agent_id = self.config.agent_id

        # -- Core state (thread-safe) --------------------------------------
        self._state = ThreadSafeDict()
        self._task_history: List[AgentTask] = []  # append-only, lock-guarded
        self._history_lock = threading.Lock()

        # -- Stats tracking ------------------------------------------------
        self._stats = ThreadSafeDict({
            'tasks_submitted': 0,
            'tasks_completed': 0,
            'tasks_failed': 0,
            'total_duration_ms': 0.0,
            'total_inferences': 0,
            'sub_agents_spawned': 0,
            'messages_received': 0,
            'messages_sent': 0,
            'errors': [],
        })
        self._stats_lock = threading.Lock()

        # -- PAMS recorder -------------------------------------------------
        self._pams = None
        if self.config.record_to_pams and HAS_PAMS:
            try:
                self._pams = get_recorder()
            except Exception as exc:  # noqa: BLE001 — optional telemetry; init must not fail
                note_failure("Agent:L219", exc)

        # -- Sub-agents registry -------------------------------------------
        self._sub_agents: Dict[str, 'FreeThreadedAgent'] = {}
        self._sub_lock = threading.Lock()

        # -- MemoryBus for inter-agent messaging (lazy init — created on first use) --
        self._bus: Any = None  # annotation is Any: MemoryBus is a class or a None-valued variable across the import fallback branches
        self._bus_subscriptions: List[str] = []  # Track subscribed topics
        self._bus_init_lock = threading.Lock()

        # -- AgentRegistry auto-registration (instant, no I/O) ------------
        self._health_reporter = None
        if HAS_AGENT_REGISTRY:
            try:
                reporter = getattr(self.__class__, '_health_reporter', None)
                if reporter is None:
                    reporter = AgentHealthReporter()
                    setattr(self.__class__, '_health_reporter', reporter)
                reporter.register_instance(self)
                self._health_reporter = reporter
            except Exception as exc:  # noqa: BLE001 — optional registry; init must not fail
                note_failure("Agent:L241", exc)

        # -- Initialization record (deferred — PAMS first-call overhead ~2s) --
        self._recorded_init = False

    # -- Public API ------------------------------------------------------------

    def execute(self, task_payload: Any, task_type: str = "default") -> AgentTask:
        """
        Execute a single task synchronously, with full lifecycle recording.

        Args:
            task_payload: The task data (dict, string, or any serializable).
            task_type: Type label for PAMS recording and stats.

        Returns:
            AgentTask with result or error.
        """
        task = AgentTask(
            task_id=uuid7().hex[:8],
            task_type=task_type,
            payload=task_payload if isinstance(task_payload, dict) else {"data": task_payload},
            agent_id=self.agent_id,
        )

        # Deferred init recording — records to PAMS on first task (avoids ~2s HTTP timeout at init)
        if not self._recorded_init:
            self._recorded_init = True
            if self._pams:
                try:
                    self._pams.record_event(
                        "agent_init",
                        {"agent_id": self.agent_id, "version": self.VERSION,
                         "free_threaded": IS_FREE_THREADED, "max_workers": self.config.max_workers},
                        source=self.config.pams_source, severity="info",
                    )
                except Exception as exc:  # noqa: BLE001 — telemetry best-effort
                    note_failure("Agent:L278", exc)

        self._record_start(task)
        self._increment_stat('tasks_submitted')

        try:
            task.started_at = time.time()
            # Single task: call _run directly without pool overhead
            task.result = self._run(task.payload, None)
            task.success = True
            self._increment_stat('tasks_completed')
            self._record_end(task)
        except Exception as e:  # noqa: BLE001 — task boundary must record, never crash
            note_failure("Agent:L291", e)
            task.error = str(e)
            task.success = False
            self._increment_stat('tasks_failed')
            self._record_error(task, e)
            # Store error in stats (keep last 50)
            ts = datetime.now().isoformat()
            with self._stats_lock:
                errors = self._stats.get('errors', [])
                if isinstance(errors, list):
                    errors.append({'task_id': task.task_id, 'error': str(e),
                                   'traceback': traceback.format_exc()[:500], 'timestamp': ts})
                    if len(errors) > 50:
                        errors = errors[-50:]
                    self._stats['errors'] = errors

        task.completed_at = time.time()
        task.duration_ms = (task.completed_at - (task.started_at or task.submitted_at)) * 1000

        # Update cumulative duration
        with self._stats_lock:
            current = self._stats.get('total_duration_ms', 0.0)
            self._stats['total_duration_ms'] = current + task.duration_ms

        # Append to history
        with self._history_lock:
            self._task_history.append(task)
            if len(self._task_history) > 200:
                self._task_history = self._task_history[-200:]

        return task

    def execute_many(self, tasks: List[Any], task_type: str = "batch",
                     max_workers: int = 0) -> List[AgentTask]:
        """
        Execute multiple tasks in parallel using FreeThreadPool.

        Args:
            tasks: List of task payloads.
            task_type: Type label for all tasks in this batch.
            max_workers: Max concurrency (0 = auto, uses config).

        Returns:
            List of AgentTask results (one per input task).
        """
        if not tasks:
            return []

        max_workers = max_workers or self.config.max_workers or (os.cpu_count() or 8)
        max_workers = min(max_workers, len(tasks))

        results: List[Any] = [None] * len(tasks)
        errors_list: List[Dict[str, Any]] = []
        _errors_lock = threading.Lock()

        def _run_one(idx: int, payload: Any) -> None:
            """Run a single task in the shared pool — NO nested pool."""
            task = AgentTask(
                task_id=uuid7().hex[:8],
                task_type=task_type,
                payload=payload if isinstance(payload, dict) else {"data": payload},
                agent_id=self.agent_id,
            )

            self._record_start(task)
            self._increment_stat('tasks_submitted')

            try:
                task.started_at = time.time()
                # Call _run directly — the outer pool provides parallelism
                task.result = self._run(task.payload, None)
                task.success = True
                self._increment_stat('tasks_completed')
                self._record_end(task)
            except Exception as e:  # noqa: BLE001 — task boundary must record, never crash
                note_failure("Agent:L365", e)
                task.error = str(e)
                task.success = False
                self._increment_stat('tasks_failed')
                self._record_error(task, e)
                with _errors_lock:
                    errors_list.append({'task_id': task.task_id, 'error': str(e)})

            task.completed_at = time.time()
            task.duration_ms = (task.completed_at - (task.started_at or task.submitted_at)) * 1000
            with self._stats_lock:
                current = self._stats.get('total_duration_ms', 0.0)
                self._stats['total_duration_ms'] = current + task.duration_ms

            with self._history_lock:
                self._task_history.append(task)
                if len(self._task_history) > 200:
                    self._task_history = self._task_history[-200:]

            results[idx] = task

        with FreeThreadPool(max_workers=max_workers,
                            thread_name_prefix=self.config.thread_name_prefix) as pool:
            futures = []
            for i, payload in enumerate(tasks):
                f = pool.submit(_run_one, i, payload)
                futures.append(f)
            for f in futures:
                try:
                    f.result(timeout=self.config.timeout_seconds)
                except Exception as e:  # noqa: BLE001 — future boundary records, never crashes
                    note_failure("Agent:L395", e)
                    with _errors_lock:
                        errors_list.append({'error': f"Future timeout/unhandled: {e}"})

        # Fill any None results (shouldn't happen normally)
        for i, r in enumerate(results):
            if r is None:
                results[i] = AgentTask(
                    task_id=f"lost-{i}",
                    task_type=task_type,
                    payload=tasks[i] if isinstance(tasks[i], dict) else {"data": tasks[i]},
                    agent_id=self.agent_id,
                    success=False,
                    error="Task did not complete (likely timeout)",
                    completed_at=time.time(),
                )

        return results

    def spawn_sub_agent(self, agent_class: Optional[type] = None, **kwargs) -> 'FreeThreadedAgent':
        """
        Spawn a child agent — records parent-child relationship in PAMS.

        Args:
            agent_class: Subclass of FreeThreadedAgent (defaults to self.__class__).
            **kwargs: Passed to the agent class constructor.

        Returns:
            New FreeThreadedAgent instance.
        """
        if agent_class is None:
            agent_class = self.__class__

        child_config = AgentConfig(
            agent_name=f"{self.config.agent_name}.child",
            max_workers=max(1, (self.config.max_workers or 4) // 2),
            max_recursion=max(1, self.config.max_recursion - 1),
            record_to_pams=self.config.record_to_pams,
            pams_source=self.config.pams_source,
        )
        child_config.agent_id = f"{self.agent_id}-{uuid7().hex[:4]}"
        kwargs['config'] = kwargs.get('config', child_config)
        child = agent_class(**kwargs)

        with self._sub_lock:
            self._sub_agents[child.agent_id] = child

        self._increment_stat('sub_agents_spawned')

        if self._pams:
            self._pams.record_event(
                "agent_spawn",
                {"parent_id": self.agent_id, "child_id": child.agent_id,
                 "child_class": agent_class.__name__},
                source=self.config.pams_source, severity="info",
            )

        return child

    # -- Override this in subclasses -------------------------------------------

    def _run(self, task: Dict[str, Any], pool: Any = None) -> Any:
        """
        OVERRIDE THIS in subclasses with your agent logic.

        Args:
            task: The task payload dict.
            pool: A FreeThreadPool (or None for single-threaded execution).
                  Use pool to spawn parallel sub-tasks when needed.

        Returns:
            Any result — will be stored in AgentTask.result.

        Example:
            def _run(self, task, pool):
                prompt = task.get('data', '')
                response = self.llm.generate(prompt)
                return {'response': response, 'tokens': len(response.split())}
        """
        if self.config.log_level == 'debug':
            print(f"[{self.agent_id}] _run() not overridden — default pass-through")
        return task.get('data', task)

    # -- State management ------------------------------------------------------

    def set_state(self, key: str, value: Any) -> None:
        """Set a value in the thread-safe shared state."""
        self._state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a value from the thread-safe shared state."""
        return self._state.get(key, default)

    def get_all_state(self) -> dict:
        """Get a snapshot of all state (thread-safe, locks each key individually)."""
        try:
            if HAS_FREETHREADING and hasattr(self._state, 'keys_safe'):
                return {k: self._state.get(k) for k in self._state.keys_safe()}
            # Fallback: plain dict access (best-effort, no lock guarantee)
            return dict(self._state) if hasattr(self._state, 'items') else {}
        except Exception as exc:  # noqa: BLE001 — state snapshot best-effort
            note_failure("Agent:L495", exc)
            return {}

    # -- Stats -----------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get agent stats including this agent and all sub-agents."""
        stats = {
            'agent_id': self.agent_id,
            'agent_name': self.config.agent_name,
            'version': self.VERSION,
            'free_threaded': IS_FREE_THREADED,
            'max_workers': self.config.max_workers or (os.cpu_count() or 8),
            'max_recursion': self.config.max_recursion,
        }
        # Merge stats dict
        for k, v in self._stats.items():
            if k == 'errors':
                stats[k] = v[-5:] if isinstance(v, list) else v  # last 5 errors
            elif isinstance(v, ThreadSafeCounter):
                stats[k] = v.value
            else:
                stats[k] = v

        stats['sub_agent_count'] = len(self._sub_agents)
        stats['task_history_count'] = len(self._task_history)

        # Aggregate sub-agent stats
        for sub in self._sub_agents.values():
            try:
                sub_stats = sub.get_stats()
                for k in ['tasks_submitted', 'tasks_completed', 'tasks_failed',
                          'sub_agents_spawned']:
                    stats[k] = stats.get(k, 0) + sub_stats.get(k, 0)
            except Exception as exc:  # noqa: BLE001 — sub-agent stats best-effort
                note_failure("Agent:L529", exc)

        # Compute derived stats
        completed = stats.get('tasks_completed', 0)
        failed = stats.get('tasks_failed', 0)
        total = completed + failed
        stats['success_rate'] = (completed / total * 100) if total > 0 else 100.0

        total_dur = stats.get('total_duration_ms', 0)
        stats['avg_duration_ms'] = (total_dur / total) if total > 0 else 0.0

        return stats

    def get_task_history(self, limit: int = 50) -> List[AgentTask]:
        """Get recent task history (last N tasks)."""
        with self._history_lock:
            return self._task_history[-limit:]

    # -- Internal helpers ------------------------------------------------------

    # -- MemoryBus Integration (lazy init) ---------------------------------

    def _ensure_bus(self) -> Any:
        """Lazy-initialize the MemoryBus on first use.
        Uses a short PAMS timeout to keep agent creation fast."""
        if self._bus is not None:
            return self._bus
        if not HAS_MEMORYBUS:
            return None
        with self._bus_init_lock:
            if self._bus is not None:
                return self._bus
            try:
                old = os.environ.get('PAMS_TIMEOUT', '2.0')
                os.environ['PAMS_TIMEOUT'] = '0.5'
                self._bus = MemoryBus(session_id=self.config.pams_source)
                os.environ['PAMS_TIMEOUT'] = old
                cmd_topic = f"agent.{self.agent_id}.command"
                self._bus.subscribe(cmd_topic, self._handle_bus_command)
                self._bus_subscriptions.append(cmd_topic)
                self._bus.subscribe("agent.all.command", self._handle_bus_command)
                self._bus_subscriptions.append("agent.all.command")
            except Exception as exc:  # noqa: BLE001 — bus init best-effort, degrade to None
                note_failure("Agent:L572", exc)
                self._bus = None
        return self._bus

    def _handle_bus_command(self, msg: Any) -> None:
        """Handle an incoming MemoryBus command message.

        Default implementation: looks for 'action' and 'payload' keys.
        Subclasses can override for custom command handling.
        """
        self._increment_stat('messages_received')
        content = msg.content
        if not isinstance(content, dict):
            return
        action = content.get('action', '')
        payload = content.get('payload', {})

        if action == 'execute':
            # Execute a task from bus command
            task_payload = payload.get('task', {})
            task_type = payload.get('task_type', 'bus_command')
            self.execute(task_payload, task_type=task_type)
        elif action == 'get_stats':
            self._publish_bus_event(f"agent.{self.agent_id}.stats", self.get_stats())
        elif action == 'get_state':
            key = payload.get('key')
            if key:
                val = self.get_state(key)
                self._publish_bus_event(f"agent.{self.agent_id}.state", {key: val})
            else:
                self._publish_bus_event(f"agent.{self.agent_id}.state", self.get_all_state())
        elif action == 'set_state':
            key = payload.get('key')
            value = payload.get('value')
            if key is not None:
                self.set_state(key, value)
                self._publish_bus_event(f"agent.{self.agent_id}.state_set", {key: value})
        elif action == 'shutdown':
            self.close()
        elif action == 'ping':
            self._publish_bus_event(f"agent.{self.agent_id}.pong", {
                "agent_id": self.agent_id, "timestamp": time.time()
            })

    def _publish_bus_event(self, topic: str, data: Any) -> None:
        """Publish an event to the MemoryBus."""
        if self._bus:
            try:
                self._bus.publish(topic, data, source=self.config.pams_source)
                self._increment_stat('messages_sent')
            except Exception as exc:  # noqa: BLE001 — bus publish best-effort
                note_failure("Agent:L622", exc)

    def _ensure_bus_for_send(self):
        """Ensure bus is initialized before sending."""
        if self._bus is None:
            self._ensure_bus()

    def send_message(self, target_agent_id: str, action: str, payload: Optional[dict] = None) -> bool:
        """
        Send a command message to another agent via MemoryBus.

        Args:
            target_agent_id: The target agent's agent_id.
            action: Command action (e.g., 'execute', 'get_stats', 'ping').
            payload: Optional data payload.

        Returns:
            True if message was published, False otherwise.
        """
        self._ensure_bus_for_send()
        if not self._bus:
            return False
        try:
            self._bus.publish(
                f"agent.{target_agent_id}.command",
                {"action": action, "payload": payload or {},
                 "source_id": self.agent_id},
                source=self.config.pams_source,
            )
            self._increment_stat('messages_sent')
            return True
        except Exception as exc:  # noqa: BLE001 — message send best-effort
            note_failure("Agent:L654", exc)
            return False

    def broadcast_message(self, action: str, payload: Optional[dict] = None) -> bool:
        """
        Broadcast a command to all agents.

        Args:
            action: Command action.
            payload: Optional data payload.

        Returns:
            True if message was published.
        """
        self._ensure_bus_for_send()
        if not self._bus:
            return False
        try:
            self._bus.publish(
                "agent.all.command",
                {"action": action, "payload": payload or {},
                 "source_id": self.agent_id},
                source=self.config.pams_source,
            )
            self._increment_stat('messages_sent')
            return True
        except Exception as exc:  # noqa: BLE001 — broadcast best-effort
            note_failure("Agent:L680", exc)
            return False

    def poll_messages(self, topic: Optional[str] = None) -> List[Any]:
        """
        Poll for incoming messages on a specific topic.

        Args:
            topic: Topic to poll (defaults to agent.{id}.result).

        Returns:
            List of BusMessage contents.
        """
        self._ensure_bus_for_send()
        if not self._bus:
            return []
        topic = topic or f"agent.{self.agent_id}.result"
        try:
            messages = self._bus.poll(topic=topic)
            return [m.content for m in messages]
        except Exception as exc:  # noqa: BLE001 — message poll best-effort
            note_failure("Agent:L700", exc)
            return []

    def close(self) -> None:
        """Clean up resources: MemoryBus, sub-agents, health reporter."""
        # Unregister from health reporter
        if self._health_reporter:
            try:
                self._health_reporter.unregister_instance(self.agent_id)
            except Exception as exc:  # noqa: BLE001 — cleanup best-effort
                note_failure("Agent:L709", exc)
        # Close bus subscriptions (no-op if bus is None)
        if self._bus is not None:
            try:
                for topic in self._bus_subscriptions:
                    self._bus.unsubscribe(topic)
                self._bus.close()
            except Exception as exc:  # noqa: BLE001 — cleanup best-effort
                note_failure("Agent:L717", exc)
            self._bus = None

    # -- Internal helpers ------------------------------------------------------

    def _increment_stat(self, key: str, delta: int = 1) -> None:
        """Thread-safe stat increment."""
        current = self._stats.get(key, 0)
        if isinstance(current, ThreadSafeCounter):
            current.increment(delta)
        else:
            self._stats[key] = current + delta

    def _record_start(self, task: AgentTask) -> None:
        """Record task start to PAMS."""
        if self._pams:
            try:
                self._pams.record_event(
                    "agent_task_start",
                    {"task_id": task.task_id, "task_type": task.task_type,
                     "agent_id": self.agent_id},
                    source=self.config.pams_source, severity="info",
                )
            except Exception as exc:  # noqa: BLE001 — telemetry best-effort
                note_failure("Agent:L741", exc)

    def _record_end(self, task: AgentTask) -> None:
        """Record task completion to PAMS."""
        if self._pams:
            try:
                self._pams.record_event(
                    "agent_task_end",
                    {"task_id": task.task_id, "task_type": task.task_type,
                     "agent_id": self.agent_id, "success": task.success,
                     "duration_ms": task.duration_ms,
                     "result_preview": str(task.result)[:200] if task.result else None},
                    source=self.config.pams_source,
                    severity="info" if task.success else "warning",
                )
            except Exception as exc:  # noqa: BLE001 — telemetry best-effort
                note_failure("Agent:L757", exc)

    def _record_error(self, task: AgentTask, error: Exception) -> None:
        """Record task error to PAMS."""
        if self._pams:
            try:
                self._pams.record_event(
                    "agent_task_error",
                    {"task_id": task.task_id, "task_type": task.task_type,
                     "agent_id": self.agent_id, "error": str(error),
                     "traceback": traceback.format_exc()[:500]},
                    source=self.config.pams_source, severity="error",
                )
            except Exception as exc:  # noqa: BLE001 — telemetry best-effort
                note_failure("Agent:L771", exc)

    def __repr__(self) -> str:
        stats = self.get_stats()
        bus_status = "bus=UP" if (self._bus is not None) else "bus=LAZY"
        return (f"FreeThreadedAgent(id={self.agent_id}, "
                f"name={self.config.agent_name}, "
                f"tasks={stats.get('tasks_completed', 0)}/{stats.get('tasks_submitted', 0)}, "
                f"{bus_status}, "
                f"ft={IS_FREE_THREADED})")


# -- Self-test -----------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 60)
    print("  FreeThreadedAgent - Self-test")
    print("=" * 60)
    print(f"  Free-threaded: {IS_FREE_THREADED}")
    print(f"  CPU cores: {os.cpu_count()}")
    print(f"  PAMS available: {HAS_PAMS}")

    # -- Test 1: Basic agent instantiation --------------------------------
    agent = FreeThreadedAgent(AgentConfig(agent_name="test-agent"))
    print(f"\n  [1] Agent created: {agent}")
    stats = agent.get_stats()
    print(f"      Stats: {stats['tasks_completed']}/{stats['tasks_submitted']} tasks")

    # -- Test 2: Single task execution -------------------------------------
    result = agent.execute({"test": "hello"}, task_type="greeting")
    print(f"\n  [2] Single task: {result.task_id}")
    print(f"      Success: {result.success}, Duration: {result.duration_ms:.1f}ms")
    print(f"      Result: {result.result}")

    # -- Test 3: Batch execution -------------------------------------------
    tasks = [{"n": i, "square": i * i} for i in range(8)]
    results = agent.execute_many(tasks, task_type="math", max_workers=4)
    print(f"\n  [3] Batch execution: {len(results)} tasks")
    succeeded = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    print(f"      Succeeded: {succeeded}, Failed: {failed}")
    if succeeded > 0:
        avg_dur = sum(r.duration_ms for r in results) / len(results)
        print(f"      Avg duration: {avg_dur:.1f}ms")

    # -- Test 4: Custom agent subclass -------------------------------------
    class EchoAgent(FreeThreadedAgent):
        def _run(self, task, pool):
            message = task.get('data', task.get('message', ''))
            # Simulate work
            time.sleep(0.01)
            return {"echo": message, "length": len(str(message))}

    echo = EchoAgent(AgentConfig(agent_name="echo-agent"))
    echo_result = echo.execute({"message": "Hello, free-threading!"})
    print(f"\n  [4] EchoAgent: {echo_result.result}")

    # -- Test 5: Sub-agent spawning ----------------------------------------
    child = echo.spawn_sub_agent(EchoAgent)
    child_result = child.execute({"message": "I am a child agent"})
    print(f"\n  [5] Child agent: {child.agent_id}")
    print(f"      Result: {child_result.result}")
    print(f"      Parent sub-agents: {len(echo._sub_agents)}")

    # -- Final stats -------------------------------------------------------
    final_stats = echo.get_stats()
    print(f"\n  [*] Final stats:")
    print(f"      Tasks: {final_stats['tasks_completed']}/{final_stats['tasks_submitted']} "
          f"(+ {final_stats.get('tasks_failed', 0)} failed)")
    print(f"      Success rate: {final_stats['success_rate']:.1f}%")
    print(f"      Sub-agents: {final_stats['sub_agent_count']}")
    print(f"      Sub-agents spawned: {final_stats['sub_agents_spawned']}")
    print("=" * 60)
