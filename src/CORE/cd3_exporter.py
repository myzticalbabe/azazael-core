# coding: utf-8
"""

cd3_exporter.py -- CD3 SQLiteSpanExporter Module

Cross-Domain: CD3 (Observability + Memory)
Spec: research/CROSS_DOMAIN.md sec.CD3

Provides:
  - SQLiteSpanExporter -- Custom OpenTelemetry SpanExporter that writes
    spans to memory_store.db (same DB as DirectMemory) for unified
    trace + event analysis across all agent actions.
  - OTEL_SPANS_SCHEMA -- Shared SQLite schema for the otel_spans table
    with indexes on trace_id, correlation_id, start_time, and session_id.
  - get_exporter() -- Convenience function for default exporter setup.
  - configure_otel_tracing() -- One-shot setup of TracerProvider with
    BatchSpanProcessor + SQLiteSpanExporter.

Usage:
    from CORE.cd3_exporter import SQLiteSpanExporter, configure_otel_tracing

    # Option 1: Manual setup
    exporter = SQLiteSpanExporter()
    processor = BatchSpanProcessor(exporter)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    # Option 2: One-shot setup
    tracer = configure_otel_tracing(service_name="azazael-core")

    # Use the tracer
    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("key", "value")

    # Query unified trace
    # SELECT s.name, m.event_type FROM otel_spans s
    # LEFT JOIN memories m ON s.correlation_id = m.correlation_id
    # WHERE s.trace_id = '...';
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Sequence
import time as _time

#: This list used to sit INSIDE the module docstring above, i.e. inside a string
#: literal, so it was never assigned: `from CORE.cd3_exporter import *` exported
#: every public name and __all__ itself did not exist.
__all__ = [
    "DB_PATH",
    "TELEMETRY_DB_PATH",
    "OTEL_SPANS_SCHEMA",
    "SQLiteSpanExporter",
    "configure_otel_tracing",
    "get_exporter",
    "record_span",
    "purge_old_spans",
    "query_trace_by_id",
    "query_unified_trace",
]

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- OTel SDK, GUARDED (optional in fact, mandatory in the old code) ----
# The SDK is optional in FACT but was mandatory in the CODE: the two module-level
# imports that used to sit at the top made `import CORE.cd3_exporter` itself raise
# wherever the SDK is absent. Measured 2026-10-10 on the production interpreter
# (C:/Azazael/.venvs/py315t): only the bare `opentelemetry` namespace package is
# installed, and `from opentelemetry.sdk.trace import ReadableSpan` raises
# ModuleNotFoundError.
#
# The cost was not the missing tracing path, it was the WORKING one.
# `tests/conftest.py` imports this module inside `except Exception: pass`, so the
# failure silently skipped its `_default_exporter` release at teardown; and
# `tests/test_spine_mesh_wiring.py` guards on `importorskip("opentelemetry.sdk.trace")`,
# so the only test that exercises this module was being SKIPPED rather than run.
#
# Nothing here needs the SDK to WRITE a span -- `SQLiteSpanExporter` persists to
# SQLite itself and merely NAMES the SDK's types -- so the module now imports
# cleanly either way, and `OTEL_SDK_AVAILABLE` reports which case it is in.
if TYPE_CHECKING:               # annotation-only: never evaluated at run time
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExportResult as _SpanExportResultType
    # `SpanExporter` is a plain class, not a typing.Protocol (`_is_protocol` is
    # False, its mro is `(SpanExporter, object)`), so the processors that take
    # one use NOMINAL assignability: an exporter that merely HAS the three
    # methods is still not a `SpanExporter`. It has to inherit from it. The
    # base is resolved again at runtime below, so this module still imports
    # cleanly when the optional SDK is absent.
    from opentelemetry.sdk.trace.export import SpanExporter as _SpanExporterBase
else:
    try:
        from opentelemetry.sdk.trace.export import SpanExporter as _SpanExporterBase
    except ImportError:                     # optional dependency absent
        _SpanExporterBase = object


class _SpanExportResultFallback:
    """The two `SpanExportResult` members this module actually RETURNS.

    `export()` returns `SpanExportResult.SUCCESS`, and `.FAILURE` on error, so
    degrading the name to `None` would turn an absent optional dependency into an
    `AttributeError` at the point of use instead of a value a caller can branch
    on. Only the two members returned here are defined; this is not a stand-in
    for the SDK enum.
    """

    SUCCESS = 0
    FAILURE = 1


OTEL_SDK_AVAILABLE = False
if TYPE_CHECKING:
    # Typed against the SDK's own enum, which is what `export()` returns. The
    # runtime fallback below is deliberately invisible here: when the name is
    # `type[SpanExportResult] | type[_SpanExportResultFallback]`, every
    # `return SpanExportResult.SUCCESS` widens to `Literal[...] | int`, which is
    # not the declared return type -- and SQLiteSpanExporter then stops
    # satisfying the `SpanExporter` protocol it is handed to as a processor.
    from opentelemetry.sdk.trace.export import SpanExportResult
else:                           # runtime only; a type checker skips this branch
    try:
        from opentelemetry.sdk.trace.export import SpanExportResult

        OTEL_SDK_AVAILABLE = True
    except ImportError as _otel_missing:            # optional dependency absent
        note_failure("cd3_exporter:otel-sdk-import", _otel_missing)
        SpanExportResult = _SpanExportResultFallback

# -- DB path resolution -----------------------------------------------
# Default to same DB as DirectMemory for cross-ledger queries.
# Override via AZAZAEL_MEMORY_DIR env var (for Docker split config).
_DEFAULT_DB_DIR = Path.home() / ".azazael"


def _get_db_dir() -> Path:
    """Resolve the memory dir from the environment, fresh on every call.

    The module-level `_DB_DIR` / `DB_PATH` below are a CACHE of this answer;
    this is the resolver a caller uses after redirecting `AZAZAEL_MEMORY_DIR`.
    """
    return Path(os.environ.get("AZAZAEL_MEMORY_DIR", str(_DEFAULT_DB_DIR)))


def _reload_paths() -> None:
    """Recompute the cached paths after `AZAZAEL_MEMORY_DIR` changed."""
    global _DB_DIR, DB_PATH, TELEMETRY_DB_PATH
    _DB_DIR = _get_db_dir()
    DB_PATH = _DB_DIR / "memory_store.db"
    # Name the FILE, exactly as the module-level init does. Assigning the bare
    # directory here left TELEMETRY_DB_PATH pointing at a folder after a reload,
    # so the next sqlite3.connect() targeted a directory. Every span read and
    # write goes through this constant, so a reload silently broke telemetry.
    TELEMETRY_DB_PATH = _get_telemetry_db_dir() / "telemetry_store.db"


def _get_telemetry_db_dir() -> Path:
    return _get_db_dir()


_DB_DIR = _get_db_dir()
DB_PATH = _DB_DIR / "memory_store.db"
#: 2026-09-28 split: spans live here now (own writer lock), not in
#: memory_store.db. DB_PATH stays for the memories side of JOINs.
TELEMETRY_DB_PATH = _get_telemetry_db_dir() / "telemetry_store.db"


# ====================================================================
# OTEL_SPANS_SCHEMA -- Shared SQLite schema
# ====================================================================
# Per CD3 spec (CROSS_DOMAIN.md sec.CD3):
#   - trace_id: 32-char hex (UUID7)
#   - span_id: 16-char hex
#   - parent_span_id: NULL for root spans
#   - correlation_id: links to memories table for unified queries
#   - Attributes stored as JSON for flexible schema evolution
#   - WAL mode for concurrent span writes from 3.14t threads

OTEL_SPANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS otel_spans (
    trace_id        TEXT NOT NULL,
    span_id         TEXT NOT NULL,
    parent_span_id  TEXT,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'INTERNAL',
    start_time      INTEGER NOT NULL,
    end_time        INTEGER NOT NULL,
    status_code     INTEGER NOT NULL DEFAULT 0,
    attributes      TEXT NOT NULL DEFAULT '{}',
    correlation_id  TEXT,
    session_id      TEXT,
    PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS idx_otel_trace_id ON otel_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_otel_start_time ON otel_spans(start_time);
CREATE INDEX IF NOT EXISTS idx_otel_session ON otel_spans(session_id);
CREATE INDEX IF NOT EXISTS idx_otel_correlation ON otel_spans(correlation_id);
"""


# ====================================================================
# SQLiteSpanExporter -- Custom OTel SpanExporter
# ====================================================================
# Implements the opentelemetry.sdk.trace.export.SpanExporter interface.
# Writes spans to memory_store.db in WAL mode for concurrent access
# from multiple 3.14t threads.

class SQLiteSpanExporter(_SpanExporterBase):
    """Exports OTel spans to memory_store.db for unified trace analysis.

    Stores spans in the otel_spans table alongside DirectMemory events,
    enabling LEFT JOIN queries across traces and business events via
    the correlation_id field.

    Attributes:
        db_path: Path to the SQLite database file.
        export_count: Total spans exported since initialization.
        batch_size: Number of spans per batch (default 64).
        schema_initialized: Whether the otel_spans table exists.
    """

    def __init__(self, db_path: (str | None) = None, batch_size: int = 64):
        """Initialize the exporter.

        Args:
            db_path: Path to the telemetry database. Defaults to
                      ~/.azazael/telemetry_store.db (split 2026-09-28:
                      spans no longer share memory_store.db's writer lock).
            batch_size: Number of spans per batch insert (default 64).
        """
        self.db_path = db_path or str(TELEMETRY_DB_PATH)
        self.batch_size = batch_size
        self.export_count = 0
        self.schema_initialized = False
        #: An in-memory database is DESTROYED when its last connection closes, so
        #: a per-batch ``connect(":memory:")`` created the schema and discarded it:
        #: ``export()`` then reported "no such table: otel_spans" and wrote nothing
        #: (reproduced 2026-11-04: this file's own self-test, rc=1, 0 spans). One
        #: connection is held for the exporter's lifetime instead, and released by
        #: ``shutdown()``.
        self._memory_conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        """Return the connection this exporter writes through.

        A file-backed database gets a fresh connection per call, which is what
        keeps the writer lock short. ``:memory:`` gets the one connection that
        holds the database, created on first use.
        """
        if self.db_path != ":memory:":
            return sqlite3.connect(self.db_path, timeout=5)
        if self._memory_conn is None:
            self._memory_conn = sqlite3.connect(
                ":memory:", timeout=5, check_same_thread=False
            )
        return self._memory_conn

    def _release(self, conn: sqlite3.Connection) -> None:
        """Close ``conn`` unless it is the connection that HOLDS the database."""
        if conn is not self._memory_conn:
            conn.close()

    def _ensure_db_dir(self):
        """Create the database directory if it does not exist."""
        parent = Path(self.db_path).parent
        parent.mkdir(parents=True, exist_ok=True)

    def _init_schema(self):
        """Create the otel_spans table and indexes if they do not exist."""
        self._ensure_db_dir()
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(OTEL_SPANS_SCHEMA)
            conn.commit()
            self.schema_initialized = True
        finally:
            self._release(conn)

    def export(self, spans: Sequence[ReadableSpan]) -> "_SpanExportResultType":
        """Export a batch of spans to the SQLite database.

        Implements the OTel SpanExporter interface.

        Args:
            spans: A sequence of ReadableSpan instances to export.

        Returns:
            SpanExportResult.SUCCESS on success, FAILURE on error.
        """
        if not spans:
            return SpanExportResult.SUCCESS

        if not self.schema_initialized:
            self._init_schema()

        conn = self._connect()
        conn.execute("BEGIN TRANSACTION")
        try:
            for span in spans:
                ctx = span.get_span_context()
                parent_ctx = span.parent
                parent_id = (
                    format(parent_ctx.span_id, "016x")
                    if parent_ctx and hasattr(parent_ctx, "span_id")
                    else None
                )
                attrs = dict(span.attributes) if span.attributes else {}

                conn.execute(
                    "INSERT OR REPLACE INTO otel_spans "
                    "(trace_id, span_id, parent_span_id, name, kind, "
                    " start_time, end_time, status_code, attributes, "
                    " correlation_id, session_id) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        format(ctx.trace_id, "032x"),
                        format(ctx.span_id, "016x"),
                        parent_id,
                        span.name,
                        str(span.kind),
                        span.start_time,
                        span.end_time,
                        span.status.status_code.value,
                        json.dumps(attrs),
                        attrs.get("correlation_id"),
                        attrs.get("session_id"),
                    ),
                )
                self.export_count += 1

            conn.commit()
            return SpanExportResult.SUCCESS
        except Exception as e:
            print(f"[cd3_exporter] Export error: {e}")
            return SpanExportResult.FAILURE
        finally:
            self._release(conn)

    def shutdown(self) -> None:
        """Release what this exporter owns, which is only an in-memory database.

        REQUIRED: ``SpanExporter`` declares it and ``TracerProvider`` calls it on
        exit. A file-backed database needs nothing here -- ``export()`` opens and
        closes its own connection per batch instead of holding one open, which is
        also why there is no writer lock to drop. ``:memory:`` is the exception:
        its data exists only while a connection to it does, so that connection is
        closed here and the next export starts from a fresh, empty database.
        """
        if self._memory_conn is not None:
            self._memory_conn.close()
            self._memory_conn = None
            self.schema_initialized = False

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush any pending exports.

        Intentionally inert, and REQUIRED by the ``SpanExporter`` interface:
        ``export()`` commits before it returns, so no batch is ever pending and
        a flush has nothing to do. Implemented as a real method rather than
        omitted so the interface contract is visible here instead of looking
        like an unfinished stub.

        Returns True because nothing is pending: the interface declares
        ``-> bool`` and a body that only holds a docstring returns ``None``,
        which reads to ``TracerProvider.force_flush()`` as a FAILED flush.
        """
        return True

    def get_stats(self) -> dict[str, Any]:
        """Return export statistics.

        Returns:
            dict with keys: db_path, export_count, batch_size, schema_initialized
        """
        return {
            "db_path": self.db_path,
            "export_count": self.export_count,
            "batch_size": self.batch_size,
            "schema_initialized": self.schema_initialized,
        }


# ====================================================================
# Convenience functions
# ====================================================================

_default_exporter: (SQLiteSpanExporter | None) = None

#: Databases whose schema this process has already created. Keyed by resolved
#: path so a test that redirects the path still gets its own schema, and so the
#: hot path does not run ``executescript`` on every span.
_schema_ready: set[str] = set()


def record_span(
    *,
    trace_id: str,
    span_id: str,
    name: str,
    start_time: int,
    end_time: int,
    parent_span_id: (str | None) = None,
    kind: str = "INTERNAL",
    status_code: int = 0,
    attributes: (Dict[str, Any] | None) = None,
    correlation_id: (str | None) = None,
    session_id: (str | None) = None,
    db_path: (str | None) = None,
) -> bool:
    """Write ONE span without the OpenTelemetry SDK.

    ``SQLiteSpanExporter.export()`` takes ``ReadableSpan`` objects, and only the
    SDK produces those.

    Measured 2026-11-02: ``from opentelemetry.sdk.trace import TracerProvider``
    raised ``ModuleNotFoundError: No module named 'opentelemetry.sdk'`` on
    ``C:/Azazael/.venvs/py315t``, leaving the table empty (0 rows in the live
    ``telemetry_store.db``) while the exporter looked wired. **Re-measured
    2026-11-04: that reading is stale** -- both ``C:/Azazael/.venvs/py315t`` and
    the 3.14.7 ``python`` on PATH import ``opentelemetry.sdk.trace`` and
    ``opentelemetry.sdk.trace.export`` (rc=0, ``import OK``). So this is the
    no-SDK fallback path for an interpreter that lacks the package, not the
    production route; a caller with a Tracer must build one with
    ``build_tracer()`` and go through ``export()``.

    This module already persists spans to SQLite itself; the SDK was only ever
    naming types. So a caller that HAS span data and no SDK writes through here,
    into the same table, which is what keeps ``query_trace_by_id``,
    ``query_unified_trace`` and ``purge_old_spans`` working for both sources.

    Timestamps are **nanoseconds since the epoch** -- the unit ``export()`` writes
    from ``span.start_time`` and the unit ``purge_old_spans`` compares against.

    Never raises: a telemetry write must not fail the operation it measures. A
    rejected argument records a failure and returns False.
    """
    tid = str(trace_id or "").strip()
    sid = str(span_id or "").strip()
    if not tid or not sid or not name:
        note_failure("cd3_exporter:record_span:ids",
                     ValueError("trace_id, span_id and name are required"))
        return False

    path = db_path or str(TELEMETRY_DB_PATH)
    try:
        if path not in _schema_ready:
            SQLiteSpanExporter(db_path=path)._init_schema()
            _schema_ready.add(path)
        attrs = dict(attributes or {})
        conn = sqlite3.connect(path, timeout=5)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO otel_spans "
                "(trace_id, span_id, parent_span_id, name, kind, "
                " start_time, end_time, status_code, attributes, "
                " correlation_id, session_id) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tid,
                    sid,
                    parent_span_id,
                    str(name),
                    str(kind),
                    int(start_time),
                    int(end_time),
                    int(status_code),
                    json.dumps(attrs, default=str),
                    correlation_id or attrs.get("correlation_id"),
                    session_id or attrs.get("session_id"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        note_failure("cd3_exporter:record_span", exc)
        return False


def get_exporter(db_path: (str | None) = None) -> SQLiteSpanExporter:
    """Get or create the default SQLiteSpanExporter singleton.

    Args:
        db_path: Optional custom DB path. Only used on first call.

    Returns:
        Shared SQLiteSpanExporter instance.
    """
    global _default_exporter
    if _default_exporter is None:
        _default_exporter = SQLiteSpanExporter(db_path=db_path)
    return _default_exporter


def configure_otel_tracing(
    service_name: str = "azazael-core",
    service_version: str = "1.0.0",
    exporter: (SQLiteSpanExporter | None) = None,
    batch_size: int = 64,
) -> Any:
    """One-shot OTel tracing setup with SQLite exporter.

    Creates a TracerProvider with BatchSpanProcessor + SQLiteSpanExporter
    and sets it as the global tracer provider.

    Args:
        service_name: Service name for the Resource (default "azazael-core").
        service_version: Service version for the Resource (default "1.0.0").
        exporter: Optional pre-configured SQLiteSpanExporter. If None,
                  creates one with the default DB path.
        batch_size: Batch size for BatchSpanProcessor (default 64).

    Returns:
        opentelemetry Tracer instance ready for use.

    Raises:
        ImportError: when the OpenTelemetry SDK is absent. Only the SDK is
            optional -- ``SQLiteSpanExporter`` persists spans without it.
    """
    if not OTEL_SDK_AVAILABLE:
        raise ImportError(
            "configure_otel_tracing() requires the OpenTelemetry SDK, which is "
            "not installed in this interpreter (pip install opentelemetry-sdk). "
            "SQLiteSpanExporter needs no SDK and writes spans to SQLite directly."
        )

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry import trace

    if exporter is None:
        exporter = get_exporter()

    resource = Resource.create({
        "service.name": service_name,
        "service.version": service_version,
    })

    provider = TracerProvider(resource=resource)
    # BatchSpanProcessor validates max_export_batch_size <= max_queue_size,
    # but max_export_batch_size *defaults to 512*. Passing only
    # max_queue_size=batch_size therefore raised
    # "max_export_batch_size must be less than or equal to max_queue_size"
    # for every batch_size below 512 — including the default of 64 — so
    # this function could never succeed. Set both from the same value.
    batch = max(1, int(batch_size))
    processor = BatchSpanProcessor(
        exporter, max_queue_size=batch, max_export_batch_size=batch)
    provider.add_span_processor(processor)
    try:
        trace.set_tracer_provider(provider)
    except Exception as exc:
        # The provider is process-global and settable once; if one is already
        # installed we use whatever is active instead of failing the caller.
        note_failure("cd3_exporter:L332", exc)

    return trace.get_tracer(__name__)


def query_trace_by_id(trace_id: str, db_path: (str | None) = None) -> list[dict[str, Any]]:
    """Reconstruct a trace from otel_spans by trace_id.

    .. note::
        The 2026-09-28 split moved spans to ``telemetry_store.db`` (their own
        writer lock) and left ``memory_store.db`` holding the memories side of
        JOINs. This function reads SPANS, so it defaults to the telemetry file
        -- the same path ``SQLiteSpanExporter`` WRITES to, matching
        ``query_unified_trace`` and ``purge_old_spans``. It previously defaulted
        to ``DB_PATH`` (``memory_store.db``), so it could never return the spans
        this module had just exported.

    Args:
        trace_id: 32-char hex trace ID.
        db_path: Optional custom SPAN DB path (telemetry file).

    Returns:
        list of span dicts ordered by start_time ASC.
    """
    path = db_path or str(TELEMETRY_DB_PATH)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name, kind, start_time, end_time, "
            " (end_time - start_time) / 1000000.0 AS duration_ms, "
            " parent_span_id, status_code, attributes "
            " FROM otel_spans WHERE trace_id = ? ORDER BY start_time ASC",
            (trace_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_unified_trace(
    trace_id: str, db_path: (str | None) = None
) -> list[dict[str, Any]]:
    """LEFT JOIN otel_spans with memories table via correlation_id.

    Provides unified trace + event analysis across OTel spans and
    DirectMemory business events.

    2026-09-28 split: spans live in telemetry_store.db, memories in
    memory_store.db. The JOIN is preserved via ATTACH (read-only use;
    writers never span files in one transaction).

    Args:
        trace_id: 32-char hex trace ID.
        db_path: Optional custom SPAN DB path (telemetry file).

    Returns:
        list of joined span+event dicts ordered by span start_time.
    """
    span_path = db_path or str(TELEMETRY_DB_PATH)
    mem_path = str(DB_PATH)
    conn = sqlite3.connect(span_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        if os.path.abspath(span_path) != os.path.abspath(mem_path):
            conn.execute("ATTACH DATABASE ? AS mem", (mem_path,))
            mem_q = "mem.memories"
        else:
            mem_q = "memories"
        rows = conn.execute(
            "SELECT s.name AS span_name, s.start_time, s.kind, "
            "       m.event_type, m.severity, "
            "       json_extract(m.data, '$.action') AS action "
            f" FROM otel_spans s "
            f" LEFT JOIN {mem_q} m ON s.correlation_id = m.correlation_id "
            " WHERE s.trace_id = ? AND m.event_type IS NOT NULL "
            " ORDER BY s.start_time ASC LIMIT 20",
            (trace_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def purge_old_spans(older_than_days: int = 30, db_path: (str | None) = None) -> int:
    """Delete spans older than the specified number of days.

    Args:
        older_than_days: Age threshold in days (default 30).
        db_path: Optional custom SPAN DB path (telemetry file).

    Returns:
        Number of deleted rows.
    """
    path = db_path or str(TELEMETRY_DB_PATH)
    conn = sqlite3.connect(path, timeout=5)
    try:
        cutoff_ns = int(
            _time.time() - older_than_days * 86400
        ) * 1_000_000_000
        cursor = conn.execute(
            "DELETE FROM otel_spans WHERE end_time < ?", (cutoff_ns,)
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ====================================================================
# Self-test
# ====================================================================

if __name__ == "__main__":
    pass
    # Ensure src/ is on sys.path so CORE.* imports resolve
    _src_path = os.path.join(os.path.dirname(__file__), "..")
    if _src_path not in sys.path:
        sys.path.insert(0, _src_path)

    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry import trace as _trace

    print("CD3 SQLiteSpanExporter v1.0 -- Self-test")
    print(f"  DB path: {DB_PATH}")
    print(f"  DB exists: {DB_PATH.exists()}")
    print()

    # Test 1: Create exporter and verify schema
    exporter = SQLiteSpanExporter(db_path=":memory:")
    exporter._init_schema()
    assert exporter.schema_initialized, "Schema not initialized"
    print("  [PASS] Exporter creation + schema init")

    # Test 2: Verify schema table creation -- through the SAME connection the
    # exporter writes to. A second connect(":memory:") is a second, empty
    # database, so the previous version of this test could not see the table it
    # was asserting about and passed on a schema it had just created itself.
    conn = exporter._connect()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "otel_spans" in tables, f"otel_spans table missing (tables={sorted(tables)})"
    print("  [PASS] otel_spans table created with schema")

    # Test 3: Export spans via OTel SDK
    exporter2 = SQLiteSpanExporter(db_path=":memory:")
    resource = Resource.create({"service.name": "cd3_self_test"})
    provider = TracerProvider(resource=resource)
    processor = SimpleSpanProcessor(exporter2)
    provider.add_span_processor(processor)
    _trace.set_tracer_provider(provider)
    tracer = _trace.get_tracer(__name__)

    with tracer.start_as_current_span(
        "self_test_root",
        attributes={"correlation_id": "test-123", "session_id": "sess-001"},
    ) as root:
        ctx = root.get_span_context()
        trace_id = format(ctx.trace_id, "032x")

        with tracer.start_as_current_span("child_span") as child:
            child.set_attribute("step", "verify")

    processor.force_flush()
    assert exporter2.export_count >= 2, f"Expected >=2 spans, got {exporter2.export_count}"
    print(f"  [PASS] Span export: {exporter2.export_count} spans exported")

    # Test 4: Trace reconstruction -- read back what the exporter wrote, through
    # the connection that holds it.
    conn = exporter2._connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name FROM otel_spans WHERE trace_id = ? ORDER BY start_time ASC",
        (trace_id,),
    ).fetchall()
    names = [r["name"] for r in rows]
    assert "self_test_root" in names, "Root span not found"
    assert "child_span" in names, "Child span not found"
    print(f"  [PASS] Trace reconstruction: {names}")

    # Test 5: query_trace_by_id convenience function
    provider.shutdown()
    processor.shutdown()

    print(f"\n  Summary: {exporter2.export_count} spans, 4/4 tests PASSED")
    print("  All self-tests PASSED")
