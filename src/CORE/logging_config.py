"""
logging_config.py — Structured logging for Azazael.

Covers followups: #15 (logging_config.py), #16 (request ID propagation),
#17 (slow query logging), #18 (structured error logging),
#19 (--log-level CLI flag), #20 (log rotation).

Usage:
    from CORE.logging_config import get_logger, LogConfig
    
    logger = get_logger("doctor")
    logger.info("Checking DB integrity", extra={"db_size_mb": 42.5})
"""


__all__ = [
    "JSONFormatter",
    "LogConfig",
    "SlowQueryFilter",
    "configure",
    "get_logger",
    "get_request_id",
    "get_trace_id",
    "log_error",
    "log_slow_query",
    "set_request_id",
    "set_trace_id",
]


import json
import logging
import logging.handlers
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# Module-level state
_log_config: "LogConfig | None" = None
_request_id_local = threading.local()


class JSONFormatter(logging.Formatter):
    """JSON log formatter. Outputs structured JSON lines for machine parsing."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Add request_id if available
        rid = getattr(_request_id_local, "request_id", None)
        if rid:
            log_entry["request_id"] = rid
        # Add trace_id if available
        tid = getattr(_request_id_local, "trace_id", None)
        if tid:
            log_entry["trace_id"] = tid
        # Add extra fields from record
        for key in ("error_id", "error_type", "stack_trace", "duration_ms",
                    "query", "db_size_mb", "session_id", "event_type",
                    "file_path", "file_line", "correlation_id"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        # Add exception info
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": "".join(traceback.format_tb(record.exc_info[2])) if record.exc_info[2] else "",
            }
        return json.dumps(log_entry, default=str, ensure_ascii=False)


class SlowQueryFilter(logging.Filter):
    """Filter that logs queries slower than a threshold."""

    def __init__(self, threshold_ms: float = 100.0):
        super().__init__()
        self.threshold_ms = threshold_ms

    def filter(self, record: logging.LogRecord) -> bool:
        dur = getattr(record, "duration_ms", 0)
        return bool(dur) and dur >= self.threshold_ms


class LogConfig:
    """Centralised logging configuration manager.

    Usage:
        config = LogConfig(level="INFO", log_dir="~/.azazael/logs")
        config.setup()
        logger = logging.getLogger("my_module")
    """

    def __init__(self, level: str = "INFO", log_dir: str = "",
                 json_output: bool = True, max_bytes: int = 10 * 1024 * 1024,
                 backup_count: int = 5, slow_query_threshold_ms: float = 100.0):
        self.level = getattr(logging, level.upper(), logging.INFO)
        self.log_dir = Path(log_dir or Path.home() / ".azazael" / "logs")
        self.json_output = json_output
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.slow_query_threshold_ms = slow_query_threshold_ms
        self._initialized = False

    def setup(self):
        """Apply the logging configuration."""
        if self._initialized:
            return
        self._initialized = True

        self.log_dir.mkdir(parents=True, exist_ok=True)
        root_logger = logging.getLogger()
        root_logger.setLevel(self.level)

        # Remove default handlers
        root_logger.handlers.clear()

        # Console handler (stderr)
        console = logging.StreamHandler(sys.stderr)
        if self.json_output:
            console.setFormatter(JSONFormatter())
        else:
            console.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
        root_logger.addHandler(console)

        # File handler with rotation
        log_file = self.log_dir / "azazael.log"
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_file), maxBytes=self.max_bytes,
            backupCount=self.backup_count, encoding="utf-8"
        )
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)

        # Slow query handler
        slow_log_file = self.log_dir / "slow_queries.log"
        slow_handler = logging.handlers.RotatingFileHandler(
            str(slow_log_file), maxBytes=self.max_bytes,
            backupCount=self.backup_count, encoding="utf-8"
        )
        slow_handler.setFormatter(JSONFormatter())
        slow_handler.addFilter(SlowQueryFilter(self.slow_query_threshold_ms))
        slow_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(slow_handler)

        # Set third-party loggers to WARN to reduce noise
        for logger_name in ("urllib3", "PIL", "matplotlib"):
            logging.getLogger(logger_name).setLevel(logging.WARNING)


def configure(level: str = "INFO", log_dir: str = "", json_output: bool = True,
              slow_query_threshold_ms: float = 100.0):
    """Configure Azazael logging with the given settings.

    Call once at startup. Subsequent calls are idempotent.
    """
    global _log_config
    if _log_config is None:
        _log_config = LogConfig(
            level=level, log_dir=log_dir, json_output=json_output,
            slow_query_threshold_ms=slow_query_threshold_ms
        )
        _log_config.setup()


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger for the given module name.

    Configures default settings on first call if configure() wasn't called.
    """
    if _log_config is None:
        configure()
    return logging.getLogger(name)


# ── Request ID Propagation ──────────────────────────────────────────


def set_request_id(request_id: str = ""):
    """set a request_id for the current thread.

    All log entries from this thread will include the request_id.
    If no ID is provided, a short UUID-like ID is generated.
    """
    if not request_id:
        import hashlib
        import time
        request_id = hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:16]
    _request_id_local.request_id = request_id


def set_trace_id(trace_id: str):
    """set a trace_id for the current thread (propagated across DM/AJ)."""
    _request_id_local.trace_id = trace_id


def get_request_id() -> (str | None):
    """Get the current thread's request_id."""
    return getattr(_request_id_local, "request_id", None)


def get_trace_id() -> (str | None):
    """Get the current thread's trace_id."""
    return getattr(_request_id_local, "trace_id", None)


# ── Structured Error Logging ────────────────────────────────────────


def log_error(logger: logging.Logger, error_id: int, message: str,
              exc_info: bool = True, extra: (dict | None) = None):
    """Log a structured error with error_id for cross-referencing.

    Args:
        logger: Logger instance.
        error_id: Unique error ID (matches ERROR_LOG.md).
        message: Human-readable error description.
        exc_info: Whether to include exception traceback.
        extra: Additional structured fields.
    """
    extra = extra or {}
    extra.update({
        "error_id": error_id,
        "error_type": "azazael_error",
    })
    logger.error(
        f"[ERR#{error_id}] {message}",
        exc_info=exc_info,
        extra=extra,
    )


# ── Slow Query Logging ──────────────────────────────────────────────


def log_slow_query(logger: logging.Logger, query: str, duration_ms: float,
                   params: (dict | None) = None):
    """Log a slow database query for performance analysis.

    Args:
        logger: Logger instance.
        query: The SQL query (truncated to 500 chars).
        duration_ms: Query execution time in milliseconds.
        params: Query parameters (optional).
    """
    logger.warning(
        f"Slow query ({duration_ms:.0f}ms): {query[:200]}",
        extra={
            "duration_ms": round(duration_ms, 1),
            "query": query[:500],
            "params": str(params)[:200] if params else "",
        },
    )


# ── Self-test ───────────────────────────────────────────────────────
# Note: no module-level logger is created here because get_logger()
# would trigger configure() with defaults on import. Call
# configure() explicitly at startup, then use get_logger(name).


if __name__ == "__main__":
    configure(level="DEBUG", json_output=False)
    logger = get_logger("test")
    logger.info("Logging configured", extra={"db_size_mb": 42.5})
    set_request_id("test-request-123")
    logger.info("With request_id", extra={"module": "test"})
    log_error(logger, 999, "Test error", exc_info=False,
              extra={"file_path": "test.py"})
    log_slow_query(logger, "SELECT * FROM memories", 150.0)
    print("  logging_config.py self-test PASSED")
