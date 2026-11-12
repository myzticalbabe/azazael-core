#!/usr/bin/env python3
"""


__all__ = [
    "FreeThreadedAgent",
]

FreeThreadedAgent -- base class for all free-threaded agents.

Provides:
- Lifecycle hooks (*on_start*, *on_stop*, *on_error*)
- Access to MemoryBus for pub/sub
- Access to FreeThreadPool for parallelism
- Built-in logging with agent name
- Integration with PAMS for memory persistence
"""

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from CORE.freethreading import FreeThreadPool, is_free_threaded
else:
    try:
        from CORE.freethreading import FreeThreadPool, is_free_threaded
    except ImportError:
        import os
        import sys
        from concurrent.futures import ThreadPoolExecutor
        def is_free_threaded() -> bool:
            return hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()
        class FreeThreadPool:
            """Fallback FreeThreadPool for Python 3.12 (wraps ThreadPoolExecutor)."""
            def __init__(self, max_workers=0, thread_name_prefix="ft-agent"):
                self.max_workers = max_workers or (os.cpu_count() or 8)
                self.thread_name_prefix = thread_name_prefix
                self._executor = None
            def __enter__(self):
                self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name_prefix)
                return self
            def __exit__(self, *a):
                if self._executor:
                    self._executor.shutdown(wait=True)
                    self._executor = None
            def shutdown(self, wait=True):
                if self._executor:
                    self._executor.shutdown(wait=wait)
                    self._executor = None
            def submit(self, fn, *a, **kw):
                if self._executor is None:
                    raise RuntimeError("FreeThreadPool not entered")
                return self._executor.submit(fn, *a, **kw)
            def map(self, fn, *it, **kw):
                if self._executor is None:
                    raise RuntimeError("FreeThreadPool not entered")
                return self._executor.map(fn, *it, **kw)
from CORE.MemoryBus import MemoryBus

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here



class FreeThreadedAgent(ABC):
    """
    Abstract base class for every agent in the Azazael ecosystem.

    Subclasses **must** implement ``run()`` which contains the agent's
    main logic. Optional lifecycle hooks are ``on_start``, ``on_stop``,
    and ``on_error``.

    Usage::
        class MyAgent(FreeThreadedAgent):
            def run(self):
                self.log("info", "Agent is running")
    """

    def __init__(
        self,
        name: str,
        config: (dict[str, Any] | None) = None,
        memory_bus: (MemoryBus | None) = None,
        pool: (FreeThreadPool | None) = None,
    ):
        self._name = name
        self._config = config or {}
        self._memory_bus = memory_bus or MemoryBus()
        self._pool = pool or FreeThreadPool(max_workers=max(1, (pool.max_workers if pool else 4)))
        # Validate pool config
        if not isinstance(self._pool, FreeThreadPool):  # type: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"pool must be FreeThreadPool, got {type(self._pool).__name__}")
        self._running_event = threading.Event()
        self._thread: (threading.Thread | None) = None

        # Logger
        self._logger = logging.getLogger(f"agent.{name}")
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    "[%(asctime)s] [%(name)s] %(levelname)s: %(message)s"
                )
            )
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)

        ft = is_free_threaded()
        self.log("info", f"Agent initialized (free-threaded={ft})")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def memory_bus(self) -> MemoryBus:
        return self._memory_bus

    @property
    def pool(self) -> FreeThreadPool:
        return self._pool

    @property
    def is_running(self) -> bool:
        return self._running_event.is_set()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, level: str, message: str) -> None:
        """Log a message at the given level."""
        level_map = {
            "debug": self._logger.debug,
            "info": self._logger.info,
            "warning": self._logger.warning,
            "error": self._logger.error,
            "critical": self._logger.critical,
        }
        level_map.get(level, self._logger.info)(message)

    def tlog(self, level: str, message: object) -> None:
        """Log a message at the given level — safely handles Python 3.14 t-strings.

        Accepts both plain strings and Python 3.14 t-string Template objects.
        T-strings are converted to plain strings before logging to avoid
        serialization issues in the logging pipeline. This preserves the
        structured context that t-strings capture while remaining safe
        across Python versions.
        """
        # Convert t-string Template objects to plain string
        if hasattr(message, 'format'):
            # Python 3.14 t-string Template — format with its bound variables
            try:
                msg_str = str(message)
            except Exception as exc:
                note_failure("FreeThreadedAgent:L155", exc)
                msg_str = repr(message)
        else:
            msg_str = str(message)
        self.log(level, msg_str)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the agent in a background thread."""
        if self._running_event.is_set():
            self.log("warning", "Agent already running")
            return
        self._running_event.set()
        self.on_start()
        self._thread = threading.Thread(target=self._run_wrapper, daemon=True)
        self._thread.start()
        thread_name = self._thread.name
        self.tlog("info", f'Agent started (thread={thread_name})')

    def stop(self) -> None:
        """Signal the agent to stop."""
        self._running_event.clear()
        self.on_stop()
        self._pool.shutdown(wait=False)
        self.log("info", "Agent stopped")

    def wait(self, timeout: (float | None) = None) -> None:
        """Block until the agent's thread finishes."""
        if self._thread and self._thread.is_alive() and threading.current_thread() != self._thread:
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_wrapper(self) -> None:
        try:
            self.run()
        except Exception as exc:
            note_failure("FreeThreadedAgent:L196", exc)
            self.tlog("error", f'Agent crashed: {exc}')
            self.on_error(exc)
            import traceback
            traceback.print_exc()
        finally:
            self._running_event.clear()

    # ------------------------------------------------------------------
    # Abstract / Hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def run(self) -> None:
        """
        Main agent logic. Runs in a background thread after ``start()``.
        Check ``self.is_running`` periodically to honour stop requests.
        """
        ...

    def on_start(self) -> None:
        """Called when the agent is started (before the thread begins)."""

    def on_stop(self) -> None:
        """Called when the agent receives a stop signal."""

    def on_error(self, exception: Exception) -> None:
        """Called when an unhandled exception escapes ``run()``."""
