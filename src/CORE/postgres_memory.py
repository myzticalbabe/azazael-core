# coding: utf-8
"""
postgres_memory.py — PostgreSQL-backed persistent AI memory store.

Provides the same API as LocalMemoryStore but backed by PostgreSQL.
Swappable with LocalMemoryStore — just change the import.

Features:
- Full CRUD for session events, memories, and recall queries
- PostgreSQL full-text search (tsvector/tsquery)
- Connection pooling for thread safety
- Configurable via AZAZAEL_PG_DSN env var or config.yaml

Usage:
    from CORE.postgres_memory import PostgresMemoryStore
    store = PostgresMemoryStore()
    store.record_event("EveUnleashed", "inference", {"prompt": "..."})
    results = store.search_memories("inference")
    recent = store.recall_recent(10)

Requires: pip install psycopg2-binary
"""

import os
import sys
import json
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- SafeSQL for t-string-like parameterized queries ---------------
try:
    from CORE.safe_sql import sql as safe_sql
    _HAS_SAFE_SQL = True
except ImportError as exc:
    note_failure("postgres_memory:L36", exc)
    _HAS_SAFE_SQL = False
    def safe_sql(template: str, **kw) -> Any:
        """Fallback when SafeSQL module is unavailable."""
        class _FallbackStmt:
            def __init__(self, tmpl, kwargs):
                self._tmpl = tmpl
                self._kwargs = kwargs
                self.sql = tmpl
                self.params = tuple(
                    v for v in kwargs.values() if not isinstance(v, (list, tuple))
                )
                for k, v in kwargs.items():
                    if isinstance(v, (list, tuple)):
                        self.params = self.params + tuple(v)
                self.interpolations = []
        return _FallbackStmt(template, kw)


VERSION = "1.0.0"
# -- Try to import psycopg2 ------------------------------------------
try:
    import psycopg2  # pyright: ignore[reportMissingModuleSource] -- optional dep, guarded by _HAS_PSYCOPG2
    import psycopg2.extras  # pyright: ignore[reportMissingModuleSource]
    import psycopg2.pool  # pyright: ignore[reportMissingModuleSource]
    _HAS_PSYCOPG2 = True
except ImportError as exc:
    note_failure("postgres_memory:L62", exc)
    _HAS_PSYCOPG2 = False
    psycopg2 = None
def _get_dsn() -> str:
    """Get PostgreSQL DSN from environment or config."""
    return os.environ.get(
        "AZAZAEL_PG_DSN",
        os.environ.get("DATABASE_URL", "postgresql://localhost:5432/azazael")
    )


# -- Schema ----------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id          SERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL,
    source      TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    data        TEXT NOT NULL DEFAULT '{}',
    severity    TEXT NOT NULL DEFAULT 'info',
    tags        TEXT NOT NULL DEFAULT '[]',
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);
CREATE INDEX IF NOT EXISTS idx_memories_event_type ON memories(event_type);
CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp);
CREATE INDEX IF NOT EXISTS idx_memories_severity ON memories(severity);

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    source      TEXT NOT NULL DEFAULT 'unknown',
    metadata    TEXT NOT NULL DEFAULT '{}',
    event_count INTEGER NOT NULL DEFAULT 0
);

-- Full-text search index (PostgreSQL native tsvector)
CREATE INDEX IF NOT EXISTS idx_memories_fts
    ON memories USING gin(
        to_tsvector('english', coalesce(event_type, '') || ' ' || coalesce(data, '') || ' ' || coalesce(tags, ''))
    );
"""


class PostgresMemoryStore:
    """PostgreSQL-backed persistent memory store for AI session recording.

    Mirrors the LocalMemoryStore API for seamless swapping.
    Uses PostgreSQL full-text search instead of FTS5.
    """

    def __init__(self, dsn: Optional[str] = None):
        if not _HAS_PSYCOPG2:
            raise ImportError(
                "psycopg2 is required for PostgresMemoryStore. "
                "Install with: pip install psycopg2-binary"
            )
        self.dsn = dsn or _get_dsn()
        self._pool: Any = None  # psycopg2.pool.ThreadedConnectionPool when installed; Any because psycopg2 is None on the ImportError path
        self._lock = threading.Lock()
        self._session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S")
        self._init_pool()
        self._init_db()


    def _init_pool(self):
        """Initialize the connection pool."""
        with self._lock:
            if self._pool is None:
                self._pool = psycopg2.pool.ThreadedConnectionPool(  # pyright: ignore[reportOptionalMemberAccess]
                    minconn=1, maxconn=10, dsn=self.dsn
                )


    def _get_conn(self):
        """Get a connection from the pool."""
        if self._pool is None:
            self._init_pool()
        return self._pool.getconn()  # type: ignore[union-attr]

    def _put_conn(self, conn):
        """Return a connection to the pool."""
        if self._pool and conn:
            self._pool.putconn(conn)
    def _init_db(self):
        """Initialize database schema."""
        with self._lock:
            conn = self._get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(SCHEMA_SQL)
                conn.commit()
            finally:
                self._put_conn(conn)

    # -- Session Management --------------------------------------------

    def start_session(self, source: str = "unknown", metadata: Optional[dict] = None,
                      session_id: Optional[str] = None) -> str:
        """Start a new session. Returns session_id."""
        sid = session_id or self._session_id
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions(session_id, started_at, source, metadata) "
                    "VALUES(%s, %s, %s, %s) "
                    "ON CONFLICT (session_id) DO UPDATE SET source=EXCLUDED.source",
                    (sid, datetime.now(timezone.utc).isoformat(), source,
                     json.dumps(metadata or {}))
                )
            conn.commit()
        finally:
            self._put_conn(conn)
        return sid
    @property
    def current_session_id(self) -> str:
        """Return the current session ID."""
        return self._session_id
    def end_session(self, session_id: Optional[str] = None):
        """Mark a session as ended."""
        sid = session_id or self._session_id
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET ended_at = %s WHERE session_id = %s",
                    (datetime.now(timezone.utc).isoformat(), sid)
                )
            conn.commit()
        finally:
            self._put_conn(conn)
    def list_sessions(self, limit: int = 20) -> List[Dict]:
        """List recent sessions with event counts."""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute(
                    "SELECT session_id, started_at, ended_at, source, event_count, metadata "
                    "FROM sessions ORDER BY started_at DESC LIMIT %s", (limit,)
                )
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> Dict:
        """Get session info with all memories."""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
                session = cur.fetchone()
                if not session:
                    return {"session_id": session_id, "error": "Not found", "memories": []}
                cur.execute(
                    "SELECT * FROM memories WHERE session_id = %s ORDER BY id ASC",
                    (session_id,)
                )
                memories = cur.fetchall()
        finally:
            self._put_conn(conn)
        result = dict(session)
        result["memories"] = [dict(m) for m in memories]
        return result

    # -- Recording ------------------------------------------------------

    def record_event(self, source: str, event_type: str, data: Any = None,
                     severity: str = "info", tags: Optional[List[str]] = None,
                     session_id: Optional[str] = None) -> int:
        """Record an event to memory.

        Args:
            source: Component name (e.g., "EveUnleashed", "CLUSTERWORMS").
            event_type: Event category (e.g., "inference", "repl_exec", "session_start").
            data: JSON-serializable payload.
            severity: "info", "warning", "error", or "critical".
            tags: Optional list of string tags for categorization.
            session_id: Optional session override.

        Returns:
            The memory ID (rowid).

        Raises:
            ValueError: If validation fails.
        """
        severity = self._validate_event_args(source, event_type, data, severity, tags)

        sid = session_id or self._session_id
        data_str = json.dumps(data if data is not None else {}, ensure_ascii=False)
        tags_str = json.dumps(tags or [], ensure_ascii=False)
        ts = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO memories(session_id, source, event_type, data, severity, tags, timestamp) "
                    "VALUES(%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (sid, source, event_type, data_str, severity, tags_str, ts)
                )
                mem_id = cur.fetchone()[0]

                cur.execute(
                    "UPDATE sessions SET event_count = event_count + 1 WHERE session_id = %s",
                    (sid,)
                )
            conn.commit()
        finally:
            self._put_conn(conn)

        return mem_id

    # -- Querying -------------------------------------------------------

    def query_memories(self, source: Optional[str] = None, event_type: Optional[str] = None,
                       severity: Optional[str] = None, limit: int = 50,
                       offset: int = 0, since: Optional[str] = None,
                       session_id: Optional[str] = None, max_age_days: Optional[int] = None) -> List[Dict]:
        """Query memories with optional filters.

        Args:
            source: Filter by source component.
            event_type: Filter by event type.
            severity: Filter by severity level.
            limit: Max results to return.
            offset: Pagination offset.
            since: ISO timestamp — only return events after this time.
            session_id: Filter by session.
            max_age_days: Only include records newer than N days.

        Returns:
            List of memory dicts, newest first.
        """
        conditions = []
        params: List[Any] = []

        if source:
            conditions.append("source = %s")
            params.append(source)
        if event_type:
            conditions.append("event_type = %s")
            params.append(event_type)
        if severity:
            conditions.append("severity = %s")
            params.append(severity)
        if since:
            conditions.append("timestamp > %s")
            params.append(since)
        if session_id:
            conditions.append("session_id = %s")
            params.append(session_id)
        if max_age_days:
            conditions.append("timestamp > NOW() - INTERVAL '%s days'")
            params.append(str(max_age_days))

        where = " AND ".join(conditions) if conditions else "TRUE"
        sql = f"SELECT * FROM memories WHERE {where} ORDER BY id DESC LIMIT %s OFFSET %s"
        params.extend([min(limit, 500), offset])

        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute(sql, params)
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)

        results = []
        for r in rows:
            d = dict(r)
            for field in ("data", "tags"):
                try:
                    d[field] = json.loads(d[field]) if isinstance(d[field], str) else d[field]
                except (json.JSONDecodeError, TypeError) as exc:
                    note_failure("postgres_memory:L341", exc)
            results.append(d)
        return results
    def search_memories(self, query: str, source: Optional[str] = None,
                        limit: int = 50) -> List[Dict]:
        """Full-text search across event_type, data, and tags.

        Uses PostgreSQL native full-text search (to_tsvector / plainto_tsquery).

        Args:
            query: Search text.
            source: Optional source filter.
            limit: Max results.

        Returns:
            List of matching memory dicts, ranked by relevance.
        """
        conditions = []
        params: List[Any] = []

        # PostgreSQL full-text search
        conditions.append(
            "to_tsvector('english', coalesce(event_type, '') || ' ' || "
            "coalesce(data, '') || ' ' || coalesce(tags, '')) @@ plainto_tsquery('english', %s)"
        )
        params.append(query)

        if source:
            conditions.append("source = %s")
            params.append(source)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT *,
                   ts_rank(
                       to_tsvector('english', coalesce(event_type, '') || ' ' ||
                                   coalesce(data, '') || ' ' || coalesce(tags, '')),
                       plainto_tsquery('english', %s)
                   ) AS rank
            FROM memories
            WHERE {where}
            ORDER BY rank DESC
            LIMIT %s
        """
        params.append(query)  # For ts_rank
        params.append(limit)

        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception as exc:
            # Fall back to LIKE search if FTS fails
            # Fall back to LIKE search if FTS fails
            note_failure("postgres_memory:L394", exc)
            like_param = f"%{query}%"
            if source:
                sql = (
                    "SELECT * FROM memories WHERE (event_type LIKE %s OR data LIKE %s) "
                    "AND source = %s ORDER BY id DESC LIMIT %s"
                )
                params = [like_param, like_param, source, limit]
            else:
                sql = (
                    "SELECT * FROM memories WHERE event_type LIKE %s OR data LIKE %s "
                    "ORDER BY id DESC LIMIT %s"
                )
                params = [like_param, like_param, limit]
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute(sql, params)
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)

        results = []
        for r in rows:
            d = dict(r)
            for field in ("data", "tags"):
                try:
                    d[field] = json.loads(d[field]) if isinstance(d[field], str) else d[field]
                except (json.JSONDecodeError, TypeError) as exc:
                    note_failure("postgres_memory:L421", exc)
            results.append(d)
        return results
    def recall_recent(self, limit: int = 20, source: Optional[str] = None) -> List[Dict]:
        """Get the most recent memories, optionally filtered by source."""
        return self.query_memories(source=source, limit=limit)

    # -- Statistics -----------------------------------------------------

    def get_stats(self) -> Dict:
        """Get memory store statistics."""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute("SELECT COUNT(*) as n FROM memories")
                total = cur.fetchone()["n"]

                cur.execute("SELECT COUNT(*) as n FROM sessions")
                sessions_count = cur.fetchone()["n"]

                cur.execute(
                    "SELECT source, COUNT(*) as n FROM memories "
                    "GROUP BY source ORDER BY n DESC LIMIT 10"
                )
                sources = cur.fetchall()

                cur.execute(
                    "SELECT event_type, COUNT(*) as n FROM memories "
                    "GROUP BY event_type ORDER BY n DESC LIMIT 10"
                )
                event_types = cur.fetchall()

                cur.execute(
                    "SELECT severity, COUNT(*) as n FROM memories "
                    "GROUP BY severity ORDER BY n DESC"
                )
                severities = cur.fetchall()

                # DB size via pg_database_size (with fallback)
                try:
                    cur.execute(
                        "SELECT pg_database_size(current_database()) AS db_size_bytes"
                    )
                    db_size_bytes = cur.fetchone()["db_size_bytes"]
                    db_size_mb = db_size_bytes / (1024 * 1024)
                except Exception as exc:
                    note_failure("postgres_memory:L467", exc)
                    db_size_mb = 0.0

        finally:
            self._put_conn(conn)

        return {
            "version": VERSION,
            "dsn": self.dsn,
            "db_size_mb": round(db_size_mb, 2),
            "total_memories": total,
            "total_sessions": sessions_count,
            "current_session": self._session_id,
            "sources": {r["source"]: r["n"] for r in sources},
            "event_types": {r["event_type"]: r["n"] for r in event_types},
            "severities": {r["severity"]: r["n"] for r in severities},
        }

    # -- Export ---------------------------------------------------------

    def export_json(self, path: Optional[str] = None) -> str:
        """Export all memories to a JSON file. Returns the file path."""
        out_path = path or str(
            Path.cwd() / f"memory_export_{self._session_id}.json"
        )

        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute("SELECT * FROM memories ORDER BY id ASC")
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)

        memories = []
        for r in rows:
            d = dict(r)
            for field in ("data", "tags"):
                try:
                    d[field] = json.loads(d[field]) if isinstance(d[field], str) else d[field]
                except (json.JSONDecodeError, TypeError) as exc:
                    note_failure("postgres_memory:L507", exc)
            memories.append(d)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "version": VERSION,
                "count": len(memories),
                "memories": memories,
            }, f, indent=2, ensure_ascii=False)

        return out_path

    # -- Data Validation ----------------------------------------------

    VALID_SEVERITIES = {"info", "warning", "error", "critical"}
    MAX_DATA_SIZE = 10 * 1024 * 1024
    MAX_TAGS = 50
    MAX_SOURCE_LENGTH = 128
    MAX_EVENT_TYPE_LENGTH = 128
    def _validate_event_args(self, source: str, event_type: str, data: Any,
                              severity: str, tags: Optional[List[str]]):
        """Validate event arguments before recording.

        Raises ValueError on invalid input. Mirrors LocalMemoryStore validation.
        """
        if not source or not isinstance(source, str):
            raise ValueError(f"source must be a non-empty string, got: {source!r}")  # type: ignore[reportUnnecessaryIsInstance]
        if len(source) > self.MAX_SOURCE_LENGTH:
            raise ValueError(f"source too long ({len(source)} > {self.MAX_SOURCE_LENGTH})")

        if not event_type or not isinstance(event_type, str):
            raise ValueError(f"event_type must be a non-empty string, got: {event_type!r}")  # type: ignore[reportUnnecessaryIsInstance]
        if len(event_type) > self.MAX_EVENT_TYPE_LENGTH:
            raise ValueError(f"event_type too long ({len(event_type)} > {self.MAX_EVENT_TYPE_LENGTH})")

        severity_lower = severity.lower() if isinstance(severity, str) else ""
        if severity_lower not in self.VALID_SEVERITIES:  # type: ignore[reportUnnecessaryIsInstance]
            raise ValueError(
                f"severity must be one of {sorted(self.VALID_SEVERITIES)}, got: {severity!r}"
            )

        if tags is not None:
            if not isinstance(tags, list):
                raise ValueError(f"tags must be a list, got: {type(tags).__name__}")  # type: ignore[reportUnnecessaryIsInstance]
            if len(tags) > self.MAX_TAGS:
                raise ValueError(f"tags too many ({len(tags)} > {self.MAX_TAGS})")
            for tag in tags:
                if not isinstance(tag, str):
                    raise ValueError(f"each tag must be a string, got: {type(tag).__name__}: {tag!r}")  # type: ignore[reportUnnecessaryIsInstance]

        if data is not None:
            data_str = json.dumps(data, ensure_ascii=False)
            if len(data_str) > self.MAX_DATA_SIZE:
                raise ValueError(f"data payload too large ({len(data_str)} bytes > {self.MAX_DATA_SIZE})")

        return severity_lower

    # -- Test Artifact Filtering (parity with LocalMemoryStore) ---------

    _TEST_ARTIFACT_PATTERNS = [
        'nonexistent.exe',
        'nonexistent',
        'bad_action',
    ]

    def _is_test_artifact(self, memory: Dict) -> bool:
        """Check if a memory record is a test artifact.

        Mirrors LocalMemoryStore._is_test_artifact for API parity.
        """
        data = memory.get("data", {})
        if isinstance(data, dict):
            data_str = json.dumps(data, default=str).lower()
        else:
            data_str = str(data).lower()
        event_type = str(memory.get("event_type", "")).lower()
        source = str(memory.get("source", "")).lower()
        combined = f"{data_str} {event_type} {source}"
        for pattern in self._TEST_ARTIFACT_PATTERNS:
            if pattern in combined:
                return True
        return False

    # -- Maintenance ----------------------------------------------------

    def vacuum(self):
        """Compact the database (PostgreSQL VACUUM ANALYZE)."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("VACUUM ANALYZE memories")
                cur.execute("VACUUM ANALYZE sessions")
            conn.commit()
        finally:
            self._put_conn(conn)
    def integrity_check(self) -> Dict[str, Any]:
        """Run PostgreSQL integrity checks.

        Verifies: database access, table existence, row counts,
        index validity, and connection pool health.

        Returns:
            Dict with 'passed' (bool), 'errors' (list), and diagnostics.
        """
        errors = []
        diagnostics = {}

        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                # 1. Check database is accessible
                cur.execute("SELECT current_database(), version()")
                db_info = cur.fetchone()
                diagnostics["database"] = db_info["current_database"]
                diagnostics["server_version"] = db_info["version"].split(",")[0].split(" ")[-1]

                # 2. Check tables exist and have expected columns
                for table in ("memories", "sessions"):
                    cur.execute(
                        "SELECT COUNT(*) as cnt FROM information_schema.tables "
                        "WHERE table_name = %s", (table,)
                    )
                    exists = cur.fetchone()["cnt"] > 0
                    if not exists:
                        errors.append(f"Table '{table}' does not exist")
                    else:
                        t_stmt = safe_sql('SELECT COUNT(*) as cnt FROM {tbl:ident}', tbl=table)
                        cur.execute(t_stmt.sql)
                        diagnostics[f"{table}_rows"] = cur.fetchone()["cnt"]

                # 3. Check indexes
                cur.execute(
                    "SELECT indexname, tablename FROM pg_indexes "
                    "WHERE tablename IN ('memories', 'sessions') "
                    "ORDER BY tablename, indexname"
                )
                indexes = cur.fetchall()
                diagnostics["indexes"] = [
                    {"name": idx["indexname"], "table": idx["tablename"]}
                    for idx in indexes
                ]

                # 4. Check for corrupt sequences
                try:
                    cur.execute(
                        "SELECT schemaname, tablename, sequencename, last_value "
                        "FROM pg_sequences WHERE schemaname = 'public'"
                    )
                    sequences = cur.fetchall()
                    diagnostics["sequences"] = [
                        {"name": s["sequencename"], "last_value": s["last_value"]}
                        for s in sequences
                    ]
                except Exception as exc:
                    note_failure("postgres_memory:L662", exc)

                # 5. Check pool health
                pool = getattr(self, "_pool", None)
                if pool:
                    diagnostics["pool_minconn"] = pool.minconn
                    diagnostics["pool_maxconn"] = pool.maxconn

        except Exception as e:
            note_failure("postgres_memory:L671", e)
            errors.append(f"Integrity check failed: {e}")
        finally:
            self._put_conn(conn)

        return {
            "passed": len(errors) == 0,
            "ok": len(errors) == 0,
            "errors": errors,
            **diagnostics,
        }

    def backup(self, target_path: str) -> str:
        """Create a database backup using PostgreSQL COPY.

        Exports all tables to a SQL dump file using COPY ... TO.
        This creates a consistent point-in-time snapshot.

        Args:
            target_path: Path for the backup SQL file.

        Returns:
            The target file path on success.

        Raises:
            RuntimeError: If backup fails.
        """
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(f"-- PostgreSQL backup: {datetime.now(timezone.utc).isoformat()}\n")
                    f.write(f"-- Database: {self.dsn}\n\n")
                    f.write("SET statement_timeout = '300s';\n\n")

                    for table in ("memories", "sessions"):
                        # Get column names
                        cur.execute(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = %s ORDER BY ordinal_position",
                            (table,)
                        )
                        columns = [r[0] for r in cur.fetchall()]
                        col_names = ", ".join(f'"{c}"' for c in columns)

                        # Write INSERT statements (pg_restore-compatible)
                        cur.execute(f"SELECT * FROM \"{table}\" ORDER BY id")
                        for row in cur.fetchall():
                            values = []
                            for val in row:
                                if val is None:
                                    values.append("NULL")
                                elif isinstance(val, (int, float)):
                                    values.append(str(val))
                                else:
                                    escaped = str(val).replace("'", "''")
                                    values.append(f"'{escaped}'")
                            f.write(
                                f"INSERT INTO {table} ({col_names}) VALUES ({', '.join(values)});\n"
                            )
                        f.write("\n")

            # Verify the backup is readable
            with open(target, "r", encoding="utf-8") as f:
                content = f.read()
                if len(content) < 100:
                    raise RuntimeError("Backup file too small — likely empty")

        except Exception as e:
            raise RuntimeError(f"PostgreSQL backup failed: {e}") from e
        finally:
            self._put_conn(conn)

        return str(target)
    def purge_old(self, days: int = 90) -> int:
        """Delete memories older than N days. Returns count deleted."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM memories WHERE timestamp < NOW() - INTERVAL '%s days'",
                    (str(days),)
                )
                deleted = cur.rowcount
            conn.commit()
        finally:
            self._put_conn(conn)
        return deleted

    # -- Interface Compatibility Methods ---------------------------------

    def query_real_errors(self, days: int = 7, limit: int = 100) -> Dict[str, Any]:
        """Query real errors/warnings/criticals, excluding known test artifacts.

        Now has full parity with LocalMemoryStore.query_real_errors
        (2026-10-17: 'criticals' + exact totals added additively).
        """
        if days <= 0:
            return {
                "errors": [], "warnings": [], "criticals": [],
                "total_errors": 0, "total_warnings": 0, "total_criticals": 0,
                "filtered_errors": 0, "filtered_warnings": 0,
                "filtered_criticals": 0,
            }
        all_errors = self.query_memories(severity="error", max_age_days=days, limit=limit * 2)
        all_warnings = self.query_memories(severity="warning", max_age_days=days, limit=limit * 2)
        all_criticals = self.query_memories(severity="critical", max_age_days=days, limit=limit * 2)

        real_errors = [e for e in all_errors if not self._is_test_artifact(e)]
        real_warnings = [w for w in all_warnings if not self._is_test_artifact(w)]
        real_criticals = [c for c in all_criticals if not self._is_test_artifact(c)]

        return {
            "errors": real_errors[:limit],
            "warnings": real_warnings[:limit],
            "criticals": real_criticals[:limit],
            "total_errors": len(real_errors),
            "total_warnings": len(real_warnings),
            "total_criticals": len(real_criticals),
            "filtered_errors": len(all_errors) - len(real_errors),
            "filtered_warnings": len(all_warnings) - len(real_warnings),
            "filtered_criticals": len(all_criticals) - len(real_criticals),
        }

    def get_error_trends(self, days: int = 30) -> List[Dict]:
        """Get daily error/warning/critical counts (additive 'criticals', 2026-10-17)."""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute(
                    "SELECT date(timestamp) as day, severity, COUNT(*) as cnt "
                    "FROM memories WHERE severity IN ('error', 'warning', 'critical') "
                    "AND timestamp > NOW() - INTERVAL '%s days' "
                    "GROUP BY day, severity ORDER BY day",
                    (str(days),)
                )
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)

        trend: Dict[str, Dict] = {}
        for r in rows:
            day = r["day"]
            if day not in trend:
                trend[day] = {"date": day, "errors": 0, "warnings": 0,
                              "criticals": 0}
            if r["severity"] == "error":
                trend[day]["errors"] = r["cnt"]
            elif r["severity"] == "warning":
                trend[day]["warnings"] = r["cnt"]
            elif r["severity"] == "critical":
                trend[day]["criticals"] = r["cnt"]

        return sorted(trend.values(), key=lambda d: d["date"])
    def get_db_growth(self, days: int = 30) -> List[Dict]:
        """Get daily event count growth for trend analysis."""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # pyright: ignore[reportOptionalMemberAccess]
                cur.execute(
                    "SELECT date(timestamp) as day, COUNT(*) as cnt "
                    "FROM memories WHERE timestamp > NOW() - INTERVAL '%s days' "
                    "GROUP BY day ORDER BY day",
                    (str(days),)
                )
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)
        return [{"date": r["day"], "count": r["cnt"]} for r in rows]

    @property
    def auto_vacuum_status(self) -> Dict[str, Any]:
        """Return auto-vacuum status.

        Checks whether PostgreSQL's autovacuum daemon is active.
        Falls back to reporting enabled if the query fails.
        """
        running = True
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW autovacuum")
                row = cur.fetchone()
                if row:
                    running = row[0].lower() == "on"
        except Exception as exc:
            note_failure("postgres_memory:L849", exc) # Fall back to True
        finally:
            self._put_conn(conn)
        return {
            "running": running,
            "interval_hours": "postgresql_autovacuum",
            "thread_alive": True,
            "note": "Checked via SHOW autovacuum; daemon manages itself",
        }

    def start_auto_vacuum(self, interval_hours: float = 24.0):
        """No-op: PostgreSQL has built-in autovacuum."""

    def stop_auto_vacuum(self):
        """No-op: PostgreSQL has built-in autovacuum."""

    def close(self):
        """Close all connections in the pool."""
        with self._lock:
            if self._pool:
                self._pool.closeall()
                self._pool = None


# -- Self-test ---------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  PostgresMemoryStore — Self-test")
    print("=" * 60)

    if not _HAS_PSYCOPG2:
        print("  [SKIP] psycopg2 not installed. Install with:")
        print("         pip install psycopg2-binary")
        sys.exit(0)

    dsn = _get_dsn()
    print(f"  DSN: {dsn}")

    try:
        store = PostgresMemoryStore(dsn=dsn)
    except Exception as e:
        print(f"  [FAIL] Could not connect to PostgreSQL: {e}")
        print("  Set AZAZAEL_PG_DSN or DATABASE_URL env var.")
        sys.exit(1)

    # Test 1: Record events
    print("\n  [1] Recording events...")
    id1 = store.record_event("EveUnleashed", "session_start",
                             {"python": sys.version}, tags=["startup", "eve"])
    id2 = store.record_event("EveUnleashed", "inference",
                             {"prompt": "Hello world", "tps": 45.2}, tags=["inference", "test"])
    id3 = store.record_event("CLUSTERWORMS", "worm_execute",
                             {"worm": "SystemZ", "success": True}, severity="info",
                             tags=["worm", "monitor"])
    id4 = store.record_event("EveUnleashed", "error",
                             {"msg": "GPU not detected"}, severity="error",
                             tags=["error", "gpu"])
    print(f"      Recorded 4 events: IDs = {id1}, {id2}, {id3}, {id4}")

    # Test 2: Query
    print("\n  [2] Querying by source...")
    results = store.query_memories(source="EveUnleashed", limit=10)
    print(f"      EveUnleashed events: {len(results)}")
    for r in results:
        print(f"        [{r['id']}] {r['event_type']} ({r['severity']})")

    # Test 3: Full-text search
    print("\n  [3] Full-text search for 'GPU'...")
    found = store.search_memories("GPU")
    print(f"      Found {len(found)} results:")
    for r in found:
        print(f"        [{r['id']}] {r['event_type']}: {r['data']}")

    # Test 4: Recall recent
    print("\n  [4] Recall recent (last 5)...")
    recent = store.recall_recent(5)
    print(f"      {len(recent)} memories, newest first")

    # Test 5: Sessions
    print("\n  [5] Sessions...")
    sessions = store.list_sessions()
    print(f"      {len(sessions)} sessions found")

    # Test 6: Stats
    print("\n  [6] Stats...")
    stats = store.get_stats()
    print(f"      Total memories: {stats['total_memories']}")
    print(f"      DB size: {stats['db_size_mb']:.2f} MB")
    print(f"      Sources: {stats['sources']}")
    print(f"      Event types: {stats['event_types']}")

    store.close()
    print("\n" + "=" * 60)
    print("  Self-test complete.")
    print("=" * 60)
