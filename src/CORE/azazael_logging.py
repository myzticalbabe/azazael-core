# coding: utf-8
"""


__all__ = [
    "JsonFormatter",
    "LogEntry",
    "StructuredLogger",
    "configure_logging",
    "get_logger",
]

CORE/logging.py — Structured JSON logging for Azazael.

Provides JSON-formatted logging with severity levels, source tagging,
and optional file output. Designed for production observability.

Usage:
    from CORE.azazael_logging import get_logger
    log = get_logger("CLUSTERWORMS")
    log.info("cluster_executed", {"worms": 5, "duration_ms": 1200})
    log.error("worm_failed", {"worm": "SystemZ", "error": "timeout"})

Features:
- JSON structured output (machine-parseable)
- Severity levels: debug, info, warning, error, critical
- Source tagging for distributed tracing
- Optional file output with rotation
- Compatible with Python 3.14t free-threading
"""

import sys
import os
import json
import threading
from types import ModuleType
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, List, TYPE_CHECKING
from dataclasses import dataclass, field

#: Shadowing guard: when src/CORE is on sys.path, a bare ``import logging``
#: resolves to CORE/logging.py instead of the stdlib. Force the stdlib module.
import importlib.util
import sysconfig

if TYPE_CHECKING:  # static view only; annotations below are quoted strings
    from logging import LogRecord as _StdLogRecord

def _import_stdlib_logging() -> ModuleType:
    # find_spec respects sys.path, so when src/CORE is on sys.path it returns
    # CORE/logging.py -> infinite recursion (verified Oct 20 2026). Load from
    # the stdlib dir explicitly, bypassing sys.path shadowing.
    try:
        stdlib = sysconfig.get_path("stdlib")
        for cand in (f"{stdlib}/logging/__init__.py", f"{stdlib}/logging.py"):
            if os.path.exists(cand):
                spec = importlib.util.spec_from_file_location("_stdlib_logging", cand)
                if spec is None or spec.loader is None:
                    # stdlib layout changed or spec unresolvable: fall through
                    # to the sys.path fallback below instead of passing None
                    # into module_from_spec (which would TypeError at runtime).
                    break
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
    except Exception as exc:
        print("CORE/azazael_logging: stdlib load failed (%s); using fallback" % exc, file=sys.stderr)
    import logging as _fallback  # sys.path already safe here
    return _fallback

logging = _import_stdlib_logging()

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# -- Python 3.14t LogRecord compatibility -----------------------------
# Python 3.14 hardened LogRecord to prevent overwriting reserved attrs
# like 'name', 'msg', 'args', etc. from the `extra` dict. We patch
# logging.Logger._log to safely filter extra keys.
_ORIGINAL_LOG = logging.Logger._log


def _safe_log(self, level, msg, args, exc_info=None, extra=None, stack_info=False, stacklevel=1):
    """Safe _log wrapper that filters reserved keys from extra dict."""
    if extra is not None:
        # Keys that are always reserved in LogRecord
        _RESERVED = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
            'filename', 'module', 'exc_info', 'exc_text', 'stack_info',
            'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
            'thread', 'threadName', 'process', 'processName', 'message', 'asctime',
        }
        filtered = {}
        for k, v in extra.items():
            if k in _RESERVED:
                # Prefix reserved keys to avoid overwrite errors
                filtered[f'_{k}'] = v
            else:
                filtered[k] = v
        extra = filtered
    return _ORIGINAL_LOG(self, level, msg, args, exc_info, extra, stack_info, stacklevel)


# Apply patch only if LogRecord name is protected (Python 3.14+)
try:
    _test_record = logging.LogRecord('test', logging.INFO, '', 0, '', (), None)
    try:
        _test_record.name = 'override_test'
        # Python < 3.14: name is not protected
    except AttributeError as exc:
        # Python >= 3.14: name is protected, apply patch
        # Python >= 3.14: name is protected, apply patch
        note_failure("azazael_logging:L76", exc)
        logging.Logger._log = _safe_log
except Exception as exc:
    note_failure("azazael_logging:L79", exc)


@dataclass
class LogEntry:
    """A single structured log entry."""
    timestamp: str
    level: str
    source: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    thread_id: int = 0
    pid: int = 0


class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: "_StdLogRecord") -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "source": getattr(record, "source", "root"),
            "message": record.getMessage(),
            "data": getattr(record, "data", {}),
            "thread_id": record.thread or 0,
            "pid": record.process or 0,
        }
        # Safe exc_info access (per inference model finding)
        exc_info = record.exc_info
        if exc_info and isinstance(exc_info, (tuple, list)) and len(exc_info) >= 2 and exc_info[0]:  # type: ignore[reportUnnecessaryIsInstance]
            try:
                entry["exception"] = {
                    "type": exc_info[0].__name__,
                    "message": str(exc_info[1]) if exc_info[1] else "",
                }
            except (TypeError, AttributeError) as exc:
                note_failure("azazael_logging:L116", exc)
                entry["exception"] = {"type": "unknown", "message": str(exc_info)}
        return json.dumps(entry, default=str)


class StructuredLogger:
    """Structured JSON logger with source tagging."""

    def __init__(self, name: str, level: str = "INFO", log_file: (str | None) = None):
        self.name = name
        self._logger = logging.getLogger(f"azazael.{name}")
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self._logger.handlers.clear()

        # Console handler (JSON lines to stderr)
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(JsonFormatter())
        self._logger.addHandler(console)

        # File handler (optional)
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
            file_handler.setFormatter(JsonFormatter())
            self._logger.addHandler(file_handler)

        self._lock = threading.Lock()

    def _log(self, level: int, message: str, data: (dict | None) = None, **kwargs):
        """Internal: log with extra data."""
        extra = {
            "source": self.name,
            "data": data or kwargs or {},
        }
        self._logger.log(level, message, extra=extra)

    def debug(self, message: str, data: (dict | None) = None, **kwargs):
        self._log(logging.DEBUG, message, data, **kwargs)

    def info(self, message: str, data: (dict | None) = None, **kwargs):
        self._log(logging.INFO, message, data, **kwargs)

    def warning(self, message: str, data: (dict | None) = None, **kwargs):
        self._log(logging.WARNING, message, data, **kwargs)

    def error(self, message: str, data: (dict | None) = None, **kwargs):
        self._log(logging.ERROR, message, data, **kwargs)

    def critical(self, message: str, data: (dict | None) = None, **kwargs):
        self._log(logging.CRITICAL, message, data, **kwargs)


# -- Logger Registry --------------------------------------------------

_loggers: dict[str, StructuredLogger] = {}
_registry_lock = threading.Lock()
_global_log_file: (str | None) = None


def configure_logging(level: str = "INFO", log_file: (str | None) = None):
    """Configure global logging settings."""
    global _global_log_file
    _global_log_file = log_file
    # Reconfigure all existing loggers
    with _registry_lock:
        for logger in _loggers.values():
            logger._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
            if log_file:
                log_path = Path(log_file)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                # Atomically check and add file handler (prevents double-add race on 3.14t)
                with logger._lock:
                    has_file = any(
                        isinstance(h, logging.FileHandler)
                        for h in logger._logger.handlers
                    )
                    if not has_file:
                        fh = logging.FileHandler(str(log_path), encoding="utf-8")
                        fh.setFormatter(JsonFormatter())
                        logger._logger.addHandler(fh)


def get_logger(name: str) -> StructuredLogger:
    """Get or create a structured logger."""
    with _registry_lock:
        if name not in _loggers:
            _loggers[name] = StructuredLogger(name, log_file=_global_log_file)
        return _loggers[name]


# -- Auto-configure from config if available -------------------------
try:
    from CORE.config import get_config
    _cfg = get_config()
    configure_logging(
        level=_cfg.observability.logging.level,
        log_file=_cfg.observability.logging.file,
    )
except Exception as exc:
    note_failure("azazael_logging:L215", exc)


if __name__ == "__main__":
    log = get_logger("test")
    log.info("logger_test", {"status": "ok", "version": "1.0.0"})
    log.warning("deprecation_notice", {"module": "old_thing", "migrate_to": "new_thing"})
    try:
        _ = 1 / 0
    except ZeroDivisionError:
        log.error("division_by_zero", {"operation": "1/0"})
    print("\nJSON logging test complete. Check stderr for output.", flush=True)
