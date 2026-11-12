# coding: utf-8
"""
direct_memory.py — DirectMemory persistent session recording.

Stdlib-only. Zero CORE imports. Always works in <0.1s.
Writes directly to the same SQLite database as LocalMemoryStore
for cross-session queryability.

This is the CANONICAL memory system for all Codebuff/Azazael sessions.
Replaces 660-line CodebuffTracker with a lean, focused API.

Usage (from any Codebuff session):
    import sys; from pathlib import Path
    sys.path.insert(0, str(Path("azazael/src").resolve()))
    from CORE.direct_memory import DirectMemory

    dm = DirectMemory()
    dm.file_read(["MEMORIES.md", "ERROR_LOG.md"])
    dm.skill_loaded("azazael-startup")
    dm.file_edit("file.py", "Fix bug", lines_changed=3)
    dm.milestone("Phase complete", {"count": 5})
    dm.end({"tasks_complete": 5, "summary": "All done"})

Or use the module-level convenience:
    from CORE.direct_memory import record, end
    record("event_type", {"key": "value"})
    end({"summary": "ok"})

Command line (read-only unless the flag says otherwise):
    python direct_memory.py --stats           # counts, DB size, event-type mix
    python direct_memory.py --context [N]     # the N most recent events
    python direct_memory.py --integrity       # DM<->audit ledger cross-check
    python direct_memory.py --search --event-type file_edit --since 2026-10-01
    python direct_memory.py --banner          # one-line summary
    python direct_memory.py --gc              # close sessions left open (WRITES)
    python direct_memory.py --self-test       # recorder self-test, THROWAWAY db

Add --json for machine-readable output, --db PATH to target another store.
Read commands never construct DirectMemory(): __init__ starts a session.
"""

import argparse
import shutil
import sqlite3
import sys
import tempfile
import json
import os
import signal
import hashlib
import fnmatch
import threading
import atexit
import uuid
import time
import itertools
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        _SWALLOWED.append("%s:no-recorder" % where)

#: Fail-open swallows, recorded LOCALLY on purpose: this module's documented
#: contract is "stdlib-only, zero CORE imports, always works in <0.1s" (see the
#: module docstring), so it must not depend on the shared runtime recorder. An
#: unrecorded swallow is indistinguishable from success, so the tally exists
#: anyway -- a reader can inspect it instead of guessing whether the handler ran.
#: (The fallback ``note_failure`` above records into this same tally when the
#: failure ledger is unavailable -- it is a stdlib-only leaf, so that fallback
#: should never fire; if it does, the ledger is missing.)
_SWALLOWED: List[str] = []


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow. Never raises; imports nothing (by design)."""
    _SWALLOWED.append("%s:%s" % (
        where, type(exc).__name__ if exc is not None else "unknown"))

# Stdlib-only UUID7 generator (RFC 9562) — inlined to maintain zero-CORE-imports design.
# Produces time-sortable UUIDs for event_uuid and correlation_id columns.
_counter_uuid7 = itertools.count(0)
def _uuid7() -> str:
    ts = int(time.time() * 1000)
    rand = bytearray(os.urandom(10))
    buf = bytearray(16)
    buf[0] = (ts >> 40) & 0xFF; buf[1] = (ts >> 32) & 0xFF
    buf[2] = (ts >> 24) & 0xFF; buf[3] = (ts >> 16) & 0xFF
    buf[4] = (ts >> 8) & 0xFF;  buf[5] = ts & 0xFF
    c = next(_counter_uuid7) & 0xFFF
    buf[6] = 0x70 | ((c >> 8) & 0x0F); buf[7] = c & 0xFF
    buf[8] = 0x80 | (rand[0] & 0x3F)
    buf[9] = rand[1]; buf[10] = rand[2]; buf[11] = rand[3]
    buf[12] = rand[4]; buf[13] = rand[5]; buf[14] = rand[6]; buf[15] = rand[7]
    return uuid.UUID(bytes=bytes(buf)).hex

__version__ = "1.1.0"  # 2026-09-07: recorder gates (dedupe 30m, milestone cap, alert throttle)

# -- Database path (same as LocalMemoryStore) ----------------------
def _get_db_path() -> Path:
    env_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    if env_dir:
        base = Path(env_dir)
    else:
        base = Path.home() / ".azazael"
    base.mkdir(parents=True, exist_ok=True)
    return base / "memory_store.db"

DB_PATH = _get_db_path()

# -- Telemetry database (2026-09-28 split) ---------------------------
# otel_spans + memorybus_queue lived in memory_store.db, so every telemetry
# burst shared the ONE writer lock with training and maintenance -- the
# "database is locked" class. They move to their own file (own lock, own
# WAL). Nothing with FTS triggers or memories JOINs moves: memories stays.
def _get_telemetry_db_path() -> Path:
    env_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    if env_dir:
        base = Path(env_dir)
    else:
        base = Path.home() / ".azazael"
    base.mkdir(parents=True, exist_ok=True)
    return base / "telemetry_store.db"

TELEMETRY_DB_PATH = _get_telemetry_db_path()

# -- Schema (same as LocalMemoryStore) -----------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    source      TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    data        TEXT NOT NULL DEFAULT '{}',
    severity    TEXT NOT NULL DEFAULT 'info',
    tags        TEXT NOT NULL DEFAULT '[]',
    timestamp   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    -- CD1 schema migration (June 11 2026): Cross-domain correlation fields
    -- See azazael/research/CROSS_DOMAIN.md §CD1 for unified event schema
    event_uuid      TEXT,
    trace_id        TEXT,
    correlation_id  TEXT,
    audit_hash      TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    source      TEXT NOT NULL DEFAULT 'unknown',
    metadata    TEXT NOT NULL DEFAULT '{}',
    event_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_mappings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dm_session_id    TEXT NOT NULL,
    audit_filepath   TEXT NOT NULL,
    audit_session_id TEXT,
    correlation_id   TEXT,
    linked_at        TEXT NOT NULL DEFAULT (datetime('now')),
    mapping_type     TEXT NOT NULL DEFAULT 'auto',
    confidence       REAL NOT NULL DEFAULT 0.0,
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_mappings_dm ON session_mappings(dm_session_id);
CREATE INDEX IF NOT EXISTS idx_mappings_aj ON session_mappings(audit_filepath);
CREATE INDEX IF NOT EXISTS idx_mappings_corr ON session_mappings(correlation_id);
"""


# Schema version - bump when _SCHEMA or _migrate_schema changes.
# Persisted via PRAGMA user_version; when the stored value matches,
# _init_db skips DDL + migration entirely (keeps init <100ms on the
# ~900MB production database).
SCHEMA_VERSION = 2


# -- Event vocabulary (2026-09-21) -----------------------------------
# `CORE/vocab_migration.py:28` and `CORE/__init__.py:54` BOTH import `EVENT_VOCAB`
# from `CORE.audit_trail`, and that name does not exist anywhere in the tree, so
# both fall back to `{}`. Measured consequence: `get_non_vocab_types()` compares
# every stored `event_type` against an empty canonical set, so it reports 100% of
# the store as a vocabulary violation and has never been able to report anything
# else. A report that cannot fail is not a check.
#
# The vocabulary is GROUNDED, not invented:
#   EVENT_TYPES            -- every event type this module's own writers emit
#                             (grepped from `record("<type>"` here) plus the
#                             AJ-compatible types the methods below write.
#   LEGACY_EVENT_TYPES     -- the legacy list `vocab_migration.py:69` already
#                             names in its own output as "legacy Worm types --
#                             not in CD2 vocab". `None` means NO replacement is
#                             declared anywhere in the tree; it does not mean the
#                             type was removed, and `CLUSTERWORMS.py:882` still
#                             emits `cluster_created` today.
#
# `validate_event_type()` is ADVISORY: it is deliberately NOT wired into
# `record()`. A vocabulary that could reject telemetry would drop the evidence it
# exists to describe, so this is a report a caller can consult, never a gate.
EVENT_TYPES = frozenset({
    # emitted by this module
    "session_start", "session_end", "message_start", "message_end",
    "file_read", "file_edit", "file_create", "file_delete",
    "skill_loaded", "code_review", "test_run", "milestone_reached",
    "tooling_verify", "error_occurrence",
    # AJ-compatible emitters defined on DirectMemory below
    "inference_spawn", "gate_passed", "sentinel_run", "scan_anti_pattern",
    "sync_skills", "expect_action",
    "thought_process",
})

#: The chain-of-thought corpus event type. Its consumers hardcode this string
#: (train_thought_reward.py, run_training_orchestrator.py, live_monitor.py,
#: TERENCE, evolution_cycle) and select rows by equality, so a typo at a call
#: site drops a trace from training with no error raised anywhere. Callers
#: should use this constant and `record_thought()` rather than a bare literal.
THOUGHT_EVENT = "thought_process"

#: Cap on `reasoning` text in a thought row, matching scripts/record_thought.py.
#: Long traces are a storage and prompt-size cost, not a fidelity one.
THOUGHT_REASONING_MAX = 4000

LEGACY_EVENT_TYPES: Dict[str, Optional[str]] = {
    "cluster_created": None,
    "training_complete": None,
    "model_registered": None,
    "nature_cluster_discovery": None,
    "cross_category_route": None,
    "category_cluster_executed": None,
    "cluster_execution_started": None,
    "cluster_execution_completed": None,
}


# -- Connection policy (2026-09-21) ----------------------------------
# One place that decides how a connection to the store is opened. The static
# methods used to call `sqlite3.connect(..., timeout=2)` directly with no PRAGMAs
# at all while instance methods went through `_get_conn`, so read paths and write
# paths disagreed about both the journal mode and how long to wait for a lock.
def _connect(db_path: Optional[Any] = None, timeout: float = 15.0) -> sqlite3.Connection:
    """Open the memory store with this module's canonical PRAGMAs applied."""
    conn = sqlite3.connect(str(db_path or DB_PATH), check_same_thread=False,
                           timeout=timeout)
    conn.execute("PRAGMA busy_timeout=%d" % int(timeout * 1000))
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    _ensure_wal(conn)
    return conn


_WAL_LOCK = threading.Lock()
_WAL_CONFIRMED: set = set()


def _ensure_wal(conn: sqlite3.Connection) -> None:
    """Put the store in WAL mode, once per process per database.

    `PRAGMA journal_mode=WAL` is PERSISTENT in the file header, so it needs
    setting once -- but it was being issued by every single connection, and
    switching journal mode is the one PRAGMA that can return SQLITE_BUSY
    immediately even with a busy_timeout set, because it needs exclusive access.
    Measured: `tests/test_concurrency_200.py` (200 threads, each constructing a
    DirectMemory) failed at init with

        Failed to initialize database at .../memory_store.db: database is locked

    i.e. 200 threads racing to switch a brand-new file into WAL. Reading the
    current mode is lock-free, and the process-global lock means at most one
    thread ever attempts the switch.
    """
    try:
        key = str(conn.execute("PRAGMA database_list").fetchone()[2])
    except sqlite3.Error as exc:
        _note_failure("direct_memory:L271", exc)
        key = ""
    if key and key in _WAL_CONFIRMED:
        return
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()
        if mode and str(mode[0]).lower() == "wal":
            if key:
                _WAL_CONFIRMED.add(key)
            return
    except sqlite3.Error as exc:
        _note_failure("direct_memory:L281", exc)
        return
    with _WAL_LOCK:
        if key and key in _WAL_CONFIRMED:
            return
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            if key:
                _WAL_CONFIRMED.add(key)
        except sqlite3.Error as exc:
            # Another process holds the file; the store stays in whatever mode it
            # already is. Correct either way -- only slower under contention -- so
            # this must not fail the caller's operation.
            _note_failure("direct_memory:L290", exc)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the tables if this database has never been initialized.

    Needed by the static write path: `import_events` is called on a fresh machine
    before any DirectMemory instance exists, and `executemany` into a missing table
    raises `no such table: memories` -- which the fail-soft handler turns into a
    return of 0, i.e. a bulk import that reports "nothing to do" while its caller
    handed it 100 rows. Measured: `test_batch_import_threshold` asserted 100 and
    got 0. Read-first, the same fast path `_init_db` uses.
    """
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except sqlite3.Error as exc:
        _note_failure("direct_memory:L314", exc)


# -- Process-local TTL cache -----------------------------------------
# Used by the dashboard/CLI read paths to avoid re-running aggregate queries on
# every refresh. Keyed by string; `_cache_invalidate` accepts a glob so a writer
# can drop a family of keys (`"stats:*"`) rather than guessing each one.
_CACHE: Dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


class _TTLCache:
    """Tiny stdlib TTL cache. `_TTLCache` is exposed on DirectMemory."""

    @staticmethod
    def set(key: str, value: Any, ttl: float = 30.0) -> None:
        with _CACHE_LOCK:
            _CACHE[str(key)] = (value, time.monotonic() + float(ttl))

    @staticmethod
    def get(key: str) -> Any:
        with _CACHE_LOCK:
            entry = _CACHE.get(str(key))
            if entry is None:
                return None
            value, expires = entry
            if time.monotonic() >= expires:
                _CACHE.pop(str(key), None)
                return None
            return value

    @staticmethod
    def invalidate(pattern: str = "*") -> int:
        """Drop keys matching a glob. Returns how many entries were removed."""
        with _CACHE_LOCK:
            doomed = [k for k in _CACHE if fnmatch.fnmatch(k, pattern)]
            for k in doomed:
                _CACHE.pop(k, None)
            return len(doomed)

    @staticmethod
    def clear() -> None:
        with _CACHE_LOCK:
            _CACHE.clear()


class DirectMemory:
    """Persistent memory that auto-initializes every session. Stdlib-only, <0.1s.

    MANDATORY: Every Codebuff session MUST initialize DirectMemory at startup
    and call .end() at session completion. The atexit handler provides safety
    for unexpected exits but explicit .end() is preferred for summary data.

    This is the canonical memory system for Azazael. CodebuffTracker is
    deprecated — use DirectMemory instead.
    """

    #: Named sessions instantiated by long-lived services. These are reusable
    #: operational state, not abandoned per-message sessions, so age-only GC
    #: must preserve them. The list is derived from production constructors in
    #: cd2_verifier, CLUSTERWORMS, PARALLEL_INFERENCE_WORM and session_state.
    PERSISTENT_SESSION_IDS = frozenset({
        "cd2_verifier",
        "clusterwormz",
        "parallel_inference_worm",
        "session_state_manager",
    })

    def __init__(self, session_id: Optional[str] = None,
                 merged_session_id: Optional[str] = None,
                 db_path: Optional[str] = None):
        """Initialize DirectMemory.

        Args:
            session_id: Custom session ID. Auto-generated if not provided.
            merged_session_id: If provided, reuse this existing session instead
                of creating a new one. Events will be recorded under this session.
                Use this to group multiple Codebuff messages into one session.
            db_path: Explicit database path. Required under pytest to avoid
                writing to the production store.
        """
        if db_path is None and os.environ.get("PYTEST_CURRENT_TEST"):
            if not os.environ.get("AZAZAEL_MEMORY_DIR"):
                raise RuntimeError(
                    "DirectMemory() with no db_path is forbidden under pytest; "
                    "pass an explicit db_path to avoid writing to the production store."
                )
        self._db_path = str(db_path) if db_path is not None else str(DB_PATH)
        self._lock = threading.Lock()
        self._session_id = session_id or (
            datetime.now().strftime("codebuff_%Y%m%d_%H%M%S") + f"_{os.getpid()}")
        self._explicit_session = bool(session_id)  # True if user provided session_id
        self._merged_session_id = merged_session_id
        self._event_count = 0
        self._files_changed: List[str] = []
        self._ended = False
        # CD1 correlation (June 11 2026): Per-instance correlation ID for
        # cross-ledger linking. Uses inlined UUID7 for zero-CORE-imports.
        self._correlation_id = _uuid7()
        # Validate session_id before proceeding
        if not self._session_id or not isinstance(self._session_id, str):
            self._session_id = (datetime.now().strftime("codebuff_%Y%m%d_%H%M%S")
                                + f"_{os.getpid()}")
        if merged_session_id:
            if not merged_session_id or not isinstance(merged_session_id, str):
                merged_session_id = None
        self._init_db()

        if merged_session_id:
            # Reuse existing session — don't create a new one
            self._session_id = merged_session_id
            self._explicit_session = True
        elif not self._explicit_session:
            # Phase-2 reconnect merge (fails open to a fresh session id).
            recent = self._find_recent_session()
            if recent:
                self._session_id = recent
                self._merged_session_id = recent
                self._explicit_session = True
        self._start_session()
        atexit.register(self._atexit_handler)
        # F02 fix (June 7 2026): Register signal handlers for hard kills.
        # atexit alone does not fire on SIGTERM, SIGINT, or SIGBREAK.
        # These handlers ensure ended_at is set even on forced shutdown.
        self._register_signal_handlers()
        # Fire-and-forget worm registry prewarm (Error #57 permanent fix).
        self._prewarm_registry()

    def _register_signal_handlers(self):
        """Register signal handlers to catch hard-kill signals.

        F02 fix: atexit handlers don't fire on SIGTERM/SIGINT/SIGBREAK.
        These handlers call .end() before the process terminates,
        preventing orphaned sessions with ended_at=NULL.

        Uses a separate flag (_signal_ending) and try/finally to avoid
        deadlock if the signal arrives while self._lock is held.
        """
        self._signal_ending = False

        def _handle_signal(signum, frame):
            if self._ended or self._signal_ending:
                return
            self._signal_ending = True
            try:
                sig_name = signal.Signals(signum).name
            except (ValueError, AttributeError) as exc:
                _note_failure("direct_memory:L452", exc)
                sig_name = f"signal_{signum}"
            try:
                # Direct DB write to avoid lock contention in signal context
                now = datetime.now(timezone.utc).isoformat()
                conn = sqlite3.connect(self._db_path, timeout=1)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "UPDATE sessions SET ended_at=? WHERE session_id=? AND ended_at IS NULL",
                    (now, self._session_id)
                )
                # CD1: signal handler includes event_uuid and correlation_id
                sig_ev_uuid = _uuid7()
                conn.execute(
                    "INSERT INTO memories(session_id,source,event_type,data,severity,tags,timestamp,"
                    "event_uuid,trace_id,correlation_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (self._session_id, "Codebuff", "session_end",
                     json.dumps({"auto_recorded_by": f"signal_handler_{sig_name}",
                                "note": f"Session ended via {sig_name}",
                                "signal_number": signum}, ensure_ascii=False),
                     "warning", json.dumps(["session","session-lifecycle","shutdown","signal"]),
                     now, sig_ev_uuid, self._correlation_id, self._correlation_id)
                )
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                conn.commit()
                conn.close()
            except Exception as exc:
                _note_failure("direct_memory:L479", exc)
            finally:
                self._ended = True
            os._exit(128 + signum)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError) as exc:
                _note_failure("direct_memory:L488", exc)
        if hasattr(signal, 'SIGBREAK'):
            try:
                signal.signal(signal.SIGBREAK, _handle_signal)
            except (ValueError, OSError) as exc:
                _note_failure("direct_memory:L493", exc)

    def _atexit_handler(self):
        """Auto-end session on unexpected process exit."""
        if not self._ended:
            try:
                self.end({"auto_recorded_by": "atexit_handler",
                          "note": "Session ended via atexit (unexpected exit)"})
            except Exception as exc:
                _note_failure("direct_memory:L502", exc)

    def _prewarm_registry(self):
        """Fire-and-forget worm registry prewarm (Error #57 permanent fix).

        Called exactly once from __init__ after session setup. Imports
        CORE.startup_warm lazily and kicks off non-blocking cache warming.
        Must never raise and never block session recording: a broken or
        missing startup_warm module must not take down memory recording.
        """
        try:
            from CORE.startup_warm import ensure_registry_warm
            ensure_registry_warm()
        except Exception as exc:
            # Bare except: startup must not block on a broken warm module.
            _note_failure("direct_memory:L516", exc)

    def _get_conn(self) -> sqlite3.Connection:
        # Delegates to the module-level policy so instance writes and the static
        # read paths below cannot drift apart on journal mode or lock timeout.
        return _connect(self._db_path, timeout=15.0)

    def _init_db(self):
        """Initialize the database schema and run migrations.

        Creates tables if they don't exist. Handles concurrent access
        safely by wrapping in the instance lock.

        Fast path (PRAGMA user_version >= SCHEMA_VERSION): the database was
        already created and migrated by a previous init, so skip
        executescript + column migration. Running the CD1 migration on
        every init full-scans 700k+ memories rows (~270ms on the
        production DB), breaking the <100ms init contract
        (tests/test_startup_warm_wiring.py::TestDirectMemoryInitTiming).
        """
        with self._lock:
            try:
                conn = self._get_conn()
                already_initialized = (
                    conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION
                )
                if not already_initialized:
                    conn.executescript(_SCHEMA)
                    conn.commit()
                    # CD1 schema migration (June 11 2026): Add cross-domain columns
                    self._migrate_schema(conn)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    conn.commit()
                conn.close()
            except sqlite3.Error as e:
                raise RuntimeError(f"Failed to initialize database at {self._db_path}: {e}") from e

    def _migrate_schema(self, conn: sqlite3.Connection):
        """Run additive schema migrations for existing databases.

        CD1 (June 11 2026): Adds event_uuid, correlation_id, audit_hash,
        and schema_version columns to the memories table if they don't exist.
        """
        existing_cols = set(
            r[1] for r in conn.execute("PRAGMA table_info('memories')").fetchall()
        )
        migrations = [
            ("event_uuid", "TEXT"),
            ("trace_id", "TEXT"),
            ("correlation_id", "TEXT"),
            ("audit_hash", "TEXT"),
            ("schema_version", "INTEGER NOT NULL DEFAULT 2"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing_cols:
                conn.execute(
                    f"ALTER TABLE memories ADD COLUMN {col_name} {col_type}"
                )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_event_uuid "
            "ON memories(event_uuid)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_correlation "
            "ON memories(correlation_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_trace_id "
            "ON memories(trace_id)"
        )
        # CD1 Phase 2 (June 11 2026): Backfill existing NULL rows with UUID7.
        # Batched in chunks of 1000 to avoid large transactions under 3.14t.
        # Schema_version bumped to 2 for all existing rows.
        conn.execute("UPDATE memories SET schema_version = 2 WHERE schema_version = 1")
        conn.commit()
        while True:
            batch = conn.execute(
                "SELECT id FROM memories WHERE event_uuid IS NULL LIMIT 1000"
            ).fetchall()
            if not batch:
                break
            for (row_id,) in batch:
                conn.execute(
                    "UPDATE memories SET event_uuid = ? WHERE id = ?",
                    (_uuid7(), row_id)
                )
            conn.commit()

    def _find_recent_session(self, minutes: int = 30) -> Optional[str]:
        """Return an open session id started within minutes, else None.

        Reconnect-merge lookup for Phase 2. Never raises: fails open (None),
        leaving the caller with a fresh session id.
        2026-09-07: window 10->30m to dedupe session-start storms (129K rows/day).
        """
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT session_id FROM sessions WHERE ended_at IS NULL "
                "AND datetime(started_at) > datetime('now', ?) "
                "ORDER BY started_at DESC LIMIT 1",
                (f"-{minutes} minutes",)).fetchone()
            conn.close()
            if row and row[0] != self._session_id:
                return row[0]
        except Exception as exc:
            _note_failure("direct_memory:L623", exc)
        return None

    def _start_session(self):
        """Insert or verify session record.

        For merged sessions, preserves the original started_at.
        For new sessions, inserts with current timestamp.

        Thread-safe: uses INSERT OR IGNORE + separate SELECT to avoid
        the SELECT-then-INSERT race on 3.14t free-threading.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                conn = self._get_conn()
                # Atomic INSERT OR IGNORE — avoids SELECT/INSERT race
                conn.execute(
                    "INSERT OR IGNORE INTO sessions(session_id, started_at, source, metadata) "
                    "VALUES(?, ?, ?, ?)",
                    (self._session_id, now, "Codebuff",
                     json.dumps({"recorder": f"DirectMemory v{__version__}"}))
                )
                conn.commit()
                conn.close()
            except sqlite3.Error as exc:
                _note_failure("direct_memory:L649", exc)

        if not self._merged_session_id:
            self.record("session_start", {
                "action": "session_start",
                "direct_memory_version": __version__,
            }, tags=["session", "session-lifecycle", "startup"])
        else:
            self.record("message_start", {
                "action": "message_start (merged session)",
                "merged_into": self._merged_session_id,
            }, tags=["session", "session-lifecycle", "message"])

    @property
    def session_id(self) -> str:
        return self._session_id

    # -- Validation ------------------------------------------------

    VALID_SEVERITIES = {"info", "warning", "error", "critical"}
    MAX_SOURCE_LENGTH = 128
    MAX_EVENT_TYPE_LENGTH = 128

    def _validate(self, event_type: str, severity: str, source: str):
        """Lightweight validation — raises ValueError on invalid input."""
        if not source or not isinstance(source, str):
            raise ValueError(f"source must be a non-empty string, got: {source!r}")
        if len(source) > self.MAX_SOURCE_LENGTH:
            raise ValueError(f"source too long ({len(source)} > {self.MAX_SOURCE_LENGTH})")
        if not event_type or not isinstance(event_type, str):
            raise ValueError(f"event_type must be a non-empty string, got: {event_type!r}")
        if len(event_type) > self.MAX_EVENT_TYPE_LENGTH:
            raise ValueError(f"event_type too long ({len(event_type)} > {self.MAX_EVENT_TYPE_LENGTH})")
        sev = severity.lower() if isinstance(severity, str) else ""
        if sev not in self.VALID_SEVERITIES:
            raise ValueError(
                f"severity must be one of {sorted(self.VALID_SEVERITIES)}, got: {severity!r}"
            )
        return sev

    # -- Core recording ---------------------------------------------

    def record(self, event_type: str, data: Optional[Dict[str, Any]] = None,
               severity: str = "info", tags: Optional[List[str]] = None,
               source: str = "Codebuff") -> int:
        """Record a single event. Returns the row ID."""
        severity = self._validate(event_type, severity, source)

        # Recorder guard (2026-10-31): this is the SECOND INSERT path into
        # `memories`, and the guard living only on LocalMemoryStore.record_event
        # would leave every DirectMemory caller — including the RL trainers —
        # unthrottled. Same bucket, same exemptions (errors/criticals pass, so
        # does evolution_result and the rest of the audited types). Fail open.
        try:
            from CORE.recorder_guard import allow as _guard_allow
            if not _guard_allow(event_type, severity):
                return 0
        except Exception as exc:                  # noqa: BLE001 -- guard is advisory
            _note_failure("direct_memory:recorder-guard", exc)

        now = datetime.now(timezone.utc).isoformat()
        data_str = json.dumps(data if data is not None else {}, ensure_ascii=False, default=str)
        all_tags = list(tags or [])
        if "session-recording" not in all_tags:
            all_tags.append("session-recording")
        tags_str = json.dumps(all_tags, ensure_ascii=False, default=str)

        # Noise gate (lossless): skip identical auto events re-recorded
        # within 30 min (reconnect/startup storms). Distinct payloads pass.
        # Fails open: any error proceeds to record. Returns 0 when skipped.
        if event_type in ("session_start", "milestone_reached", "skill_loaded"):
            try:
                action = data.get("action", "") if isinstance(data, dict) else ""
                cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
                conn0 = self._get_conn()
                # Per-SESSION scope. This gate dedups a reconnect/startup storm
                # WITHIN one session, so it must never suppress a DIFFERENT
                # session's first event. Without `session_id` in the predicate a
                # `session_start` from any session in the last 30 min silently
                # dropped every new session's own `session_start`, leaving that
                # session with no start row at all (the symptom: a fresh
                # DirectMemory whose list_events() has no "session_start").
                rows = conn0.execute(
                    "SELECT timestamp FROM memories WHERE source = ? AND event_type = ? "
                    "AND session_id = ? "
                    "AND json_extract(data, '$.action') = ? "
                    "AND id > (SELECT COALESCE(MAX(id), 0) - 5000 FROM memories)",
                    (source, event_type, self._session_id, action)).fetchall()
                conn0.close()
                for (ts,) in rows:
                    if "T" in ts and ts > cutoff:
                        return 0
            except Exception as exc:
                _note_failure("direct_memory:L729", exc)

        # 2026-09-07 recorder gates (approved): throttle alert/error loops.
        # Per-session scope (fixes self-test: different sessions must not
        # suppress each other). Same action within 5 min -> skip.
        # >50 same-type in this session last hour -> skip.
        # Fails open. Returns 0 when throttled. Zero history touched.
        if event_type in ("error_occurrence", "alert", "session_end",
                          "message_end", "tooling_verify"):
            try:
                action = data.get("action", "") if isinstance(data, dict) else ""
                now_utc = datetime.now(timezone.utc)
                cutoff5 = (now_utc - timedelta(minutes=5)).isoformat()
                cutoff60 = (now_utc - timedelta(minutes=60)).isoformat()
                conn1 = self._get_conn()
                dup = conn1.execute(
                    "SELECT 1 FROM memories WHERE session_id = ? "
                    "AND event_type = ? "
                    "AND json_extract(data, '$.action') = ? "
                    "AND timestamp > ? LIMIT 1",
                    (self._session_id, event_type, action, cutoff5)).fetchone()
                if dup:
                    conn1.close()
                    return 0
                cnt = conn1.execute(
                    "SELECT COUNT(*) FROM memories WHERE session_id = ? "
                    "AND event_type = ? AND timestamp > ?",
                    (self._session_id, event_type, cutoff60)).fetchone()
                conn1.close()
                if cnt and cnt[0] > 50:
                    return 0
            except Exception as exc:
                _note_failure("direct_memory:L761", exc)

        # CD1 correlation (June 11 2026): Generate UUID7 for cross-ledger linking
        ev_uuid = _uuid7()
        corr_id = self._correlation_id  # Use instance correlation_id

        # BLAKE2b-256 audit hash of full event for cross-referencing
        # Covers source + event_type + timestamp + data + severity per CD1 spec
        canonical = f"{source}|{event_type}|{now}|{data_str}|{severity}"
        audit_val = hashlib.blake2b(canonical.encode(), digest_size=32).hexdigest()

        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "INSERT INTO memories(session_id, source, event_type, data, severity, tags, timestamp, "
                "event_uuid, trace_id, correlation_id, audit_hash) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self._session_id, source, event_type, data_str, severity, tags_str, now,
                 ev_uuid, corr_id, corr_id, audit_val)
            )
            mem_id = cursor.lastrowid or 0
            conn.execute(
                "UPDATE sessions SET event_count = event_count + 1 WHERE session_id = ?",
                (self._session_id,)
            )
            conn.commit()
            conn.close()
            self._event_count += 1
        return mem_id

    # -- Convenience methods ----------------------------------------

    def file_read(self, filepaths: List[str], description: str = ""):
        return self.record("file_read", {
            "action": f"read: {description or ', '.join(Path(p).name for p in filepaths[:5])}",
            "files": filepaths,
            "count": len(filepaths),
        }, tags=["file", "file-read"])

    def file_edit(self, filepath: str, description: str, lines_changed: int = 0):
        with self._lock:
            if filepath and filepath not in self._files_changed:
                self._files_changed.append(filepath)
        return self.record("file_edit", {
            "action": f"edit: {description}",
            "filepath": filepath,
            "filename": Path(filepath).name if filepath else "unknown",
            "description": description,
            "lines_changed": lines_changed,
        }, tags=["file", "file-edit", "code"])

    def file_create(self, filepath: str, description: str, file_size_bytes: int = 0):
        with self._lock:
            if filepath and filepath not in self._files_changed:
                self._files_changed.append(filepath)
        return self.record("file_create", {
            "action": f"create: {description}",
            "filepath": filepath,
            "filename": Path(filepath).name if filepath else "unknown",
            "description": description,
            "size_bytes": file_size_bytes,
        }, tags=["file", "file-create", "code"])

    def file_delete(self, filepath: str, description: str):
        return self.record("file_delete", {
            "action": f"delete: {description}",
            "filepath": filepath,
            "filename": Path(filepath).name if filepath else "unknown",
            "description": description,
        }, severity="warning", tags=["file", "file-delete", "code"])

    def skill_loaded(self, skill_name: str):
        return self.record("skill_loaded", {
            "action": f"skill: {skill_name}",
            "skill": skill_name,
        }, tags=["skill", "startup"])

    def code_review(self, subject: str, issues_found: int = 0, issues_fixed: int = 0,
                    summary: str = ""):
        severity = "warning" if issues_found > 0 else "info"
        return self.record("code_review", {
            "action": f"review: {subject}",
            "subject": subject,
            "issues_found": issues_found,
            "issues_fixed": issues_fixed,
            "summary": summary[:500] if summary else "",
        }, severity=severity, tags=["review", "code-review", "quality"])

    def test_run(self, test_suite: str, passed: int, failed: int = 0,
                 skipped: int = 0, duration_ms: float = 0):
        severity = "error" if failed > 0 else "info"
        extra_tags = ["test-failure"] if failed > 0 else ["test-pass"]
        return self.record("test_run", {
            "action": f"test: {test_suite} ({passed}p/{failed}f/{skipped}s)",
            "test_suite": test_suite,
            "passed": passed, "failed": failed, "skipped": skipped,
            "duration_ms": round(duration_ms, 1),
        }, severity=severity, tags=["test", "test-run", "ci", "quality"] + extra_tags)

    def record_thought(self, task: str, reasoning: str,
                       actions: Optional[List[str]] = None,
                       success: bool = True,
                       *, source: str = "ReasoningTrace",
                       tags: Optional[List[str]] = None,
                       session_id: Optional[str] = None) -> int:
        """Record a chain-of-thought trace as training data.

        The typed front door to the `thought_process` corpus. Before this
        existed every caller hand-built the same four-key payload against a
        stringly-typed event type, and a misspelling produced a row that the
        reward trainer, the DPO pairing and the monitor all silently ignored —
        a trace that looks recorded and is not in the loop.

        The payload shape is the contract the consumers already read
        (`task`, `reasoning`, `actions`, `success`, `session_id`); changing it
        would silently change what training learns, so it is fixed here rather
        than by each call site. `reasoning` is truncated to
        THOUGHT_REASONING_MAX; severity is derived from `success` so a failed
        trace is queryable as such without each caller remembering.

        Returns the new memory row id (0 if the store rejected it).
        """
        text = (reasoning or "").strip()
        if not text:
            text = "(no reasoning recorded)"
        text = text[:THOUGHT_REASONING_MAX]
        extra = list(tags) if tags else []
        return self.record(
            THOUGHT_EVENT,
            {
                "task": task,
                "reasoning": text,
                "actions": list(actions or []),
                "success": bool(success),
                "session_id": session_id or self._session_id,
            },
            severity="info" if success else "warning",
            tags=["thought", "chain-of-thought"] + extra,
            source=source,
        )

    def fetch_thoughts(self, limit: int = 2000) -> List[Dict]:
        """Thought rows newest-first, paged past LocalMemoryStore's clamp.

        The corpus outgrew `query_memories`' MAX_QUERY_LIMIT, which caps a
        single call at 500 and drops the oldest — minority-family — rows. Use
        this for every corpus read rather than passing a large `limit`.
        """
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        rows = store.fetch_all_memories(limit=limit,
                                        event_type=THOUGHT_EVENT)
        out: List[Dict] = []
        for r in rows:
            d = r.get("data")
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except (json.JSONDecodeError, TypeError) as exc:
                    _note_failure("direct_memory:L920", exc)
                    continue
            if not isinstance(d, dict):
                continue
            r = dict(r)
            r["data"] = d
            out.append(r)
        return out

    def milestone(self, title: str, details: Optional[Dict] = None):
        # 2026-09-07 gate (approved): block auto-milestones unless opted in,
        # cap 20 milestones/session-hour, dedupe same title 120 min.
        # Env AZAZAEL_ALLOW_AUTO_MILESTONE=1 to allow auto_* titles.
        # Fails open (records) on any gate error. Zero history touched.
        try:
            if "auto" in (title or "").lower() and os.environ.get(
                    "AZAZAEL_ALLOW_AUTO_MILESTONE", "1") == "0":
                return 0
            now_utc = datetime.now(timezone.utc)
            cutoff120 = (now_utc - timedelta(minutes=120)).isoformat()
            cutoff60 = (now_utc - timedelta(minutes=60)).isoformat()
            connm = self._get_conn()
            dup = connm.execute(
                "SELECT 1 FROM memories WHERE session_id = ? "
                "AND event_type = 'milestone_reached' "
                "AND json_extract(data, '$.title') = ? "
                "AND timestamp > ? LIMIT 1",
                (self._session_id, title, cutoff120)).fetchone()
            if dup:
                connm.close()
                return 0
            cnt = connm.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id = ? "
                "AND event_type = 'milestone_reached' AND timestamp > ?",
                (self._session_id, cutoff60)).fetchone()
            connm.close()
            if cnt and cnt[0] >= 20:
                return 0
        except Exception as exc:
            _note_failure("direct_memory:L958", exc)
        return self.record("milestone_reached", {
            "action": f"milestone: {title}",
            "title": title,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, tags=["milestone", "progress"])

    def tooling_verify(self, tools_found: int, tools_missing: int,
                       details: Optional[Dict] = None):
        severity = "warning" if tools_missing > 0 else "info"
        return self.record("tooling_verify", {
            "action": f"tooling: {tools_found} found, {tools_missing} missing",
            "tools_found": tools_found,
            "tools_missing": tools_missing,
            "details": details or {},
        }, severity=severity, tags=["tooling", "startup", "environment"])

    def error_occurrence(self, error_id: int, title: str, file_path: str,
                         severity: str = "error", details: Optional[Dict] = None):
        return self.record("error_occurrence", {
            "action": f"error #{error_id}: {title}",
            "error_id": error_id,
            "title": title,
            "file_path": file_path,
            "error_log_ref": f"ERROR_LOG.md#error-{error_id}",
            "details": details or {},
        }, severity=severity, tags=["error", "error-log", "error-occurrence"])

    # -- Session lifecycle ------------------------------------------

    def end(self, summary: Optional[Dict] = None, close_explicit: bool = False):
        """End the session with summary data. MANDATORY at session end.

        Explicit and merged sessions normally remain open for reuse. A caller
        that owns the complete session lifecycle (for example, an automated
        start/close wrapper) can opt into closing the SQLite session row with
        ``close_explicit=True``. The default preserves the existing reuse
        behavior.
        """
        if self._ended:
            return
        self._ended = True
        try:
            atexit.unregister(self._atexit_handler)
        except Exception as exc:
            _note_failure("direct_memory:L1004", exc)

        summary = summary or {}
        summary.update({
            "event_count": self._event_count,
            "files_changed_count": len(self._files_changed),
            "files_changed": self._files_changed[:50],
        })

        self.record("session_end", {
            "action": "session_end",
            "summary": summary,
        }, tags=["session", "session-lifecycle", "shutdown"])

        if self._explicit_session and not close_explicit:
            # Explicitly named session - don't close by default (may be reused).
            if self._merged_session_id:
                self.record("message_end", {
                    "action": "message_end (merged session)",
                    "summary": summary,
                }, tags=["session", "session-lifecycle", "message"])
            else:
                self.record("session_end", {
                    "action": "session_end (explicit session, stays open)",
                    "summary": summary,
                }, tags=["session", "session-lifecycle", "shutdown"])
            return

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            # WAL checkpoint before closing
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                (now, self._session_id)
            )
            conn.commit()
            conn.close()

    # -- Session maintenance ----------------------------------------

    @staticmethod
    def stale_session_inventory(
        max_age_hours: int = 1,
        protected_session_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Classify every open session without mutating the store.

        Invalid timestamps fail closed into ``protected`` rather than becoming
        deletion candidates. Reports the latest event and audit mappings so an
        operator can distinguish an orphan from a quiet reusable service.
        """
        age_hours = max(0.0, float(max_age_hours))
        protected = set(
            DirectMemory.PERSISTENT_SESSION_IDS
            if protected_session_ids is None
            else protected_session_ids
        )
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=age_hours)
        conn = sqlite3.connect(str(DB_PATH), timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT s.session_id, s.started_at, s.event_count, "
                "MAX(m.timestamp) AS latest_event, "
                "(SELECT event_type FROM memories x WHERE x.session_id=s.session_id "
                " ORDER BY x.id DESC LIMIT 1) AS latest_type, "
                "(SELECT COUNT(*) FROM memories x WHERE x.session_id=s.session_id "
                " AND x.event_type='session_end') AS end_events, "
                "(SELECT GROUP_CONCAT(audit_filepath, '|') FROM session_mappings sm "
                " WHERE sm.dm_session_id=s.session_id) AS mappings "
                "FROM sessions s LEFT JOIN memories m ON m.session_id=s.session_id "
                "WHERE s.ended_at IS NULL GROUP BY s.session_id "
                "ORDER BY s.started_at"
            ).fetchall()
        finally:
            conn.close()

        candidates: List[Dict[str, Any]] = []
        protected_rows: List[Dict[str, Any]] = []
        active: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                started = datetime.fromisoformat(str(item["started_at"]))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                item["age_hours"] = round(max(0.0, (now - started).total_seconds() / 3600.0), 3)
                item["started_at"] = started.isoformat()
            except (TypeError, ValueError) as exc:
                _note_failure("direct_memory:L1096", exc)
                item["age_hours"] = None
            sid = str(item["session_id"])
            if sid in protected:
                item["reason"] = "protected_operational_session"
                protected_rows.append(item)
            elif item["age_hours"] is None:
                item["reason"] = "invalid_started_at"
                protected_rows.append(item)
            elif item["age_hours"] >= age_hours:
                item["reason"] = "stale_unclosed_session"
                candidates.append(item)
            else:
                item["reason"] = "within_age_window"
                active.append(item)
        return {
            "schema_version": 1,
            "generated_at": now.isoformat(),
            "database": str(DB_PATH),
            "max_age_hours": age_hours,
            "cutoff": cutoff.isoformat(),
            "dry_run": True,
            "protected_session_ids": sorted(protected),
            "open_count": len(rows),
            "candidate_count": len(candidates),
            "protected_count": len(protected_rows),
            "active_count": len(active),
            "candidates": candidates,
            "protected": protected_rows,
            "active": active,
        }

    @staticmethod
    def cleanup_stale_sessions(
        max_age_hours: int = 1,
        dry_run: bool = False,
        protected_session_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Inventory and optionally close only stale, unprotected sessions."""
        before = DirectMemory.stale_session_inventory(
            max_age_hours=max_age_hours,
            protected_session_ids=protected_session_ids,
        )
        candidate_ids = [str(row["session_id"]) for row in before["candidates"]]
        closed = 0
        if not dry_run and candidate_ids:
            ended_at = datetime.now(timezone.utc).isoformat()
            conn = sqlite3.connect(str(DB_PATH), timeout=15, check_same_thread=False)
            try:
                conn.execute("BEGIN IMMEDIATE")
                placeholders = ",".join("?" for _ in candidate_ids)
                cursor = conn.execute(
                    "UPDATE sessions SET ended_at=? WHERE ended_at IS NULL "
                    f"AND session_id IN ({placeholders})",
                    (ended_at, *candidate_ids),
                )
                closed = int(cursor.rowcount or 0)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        after = DirectMemory.stale_session_inventory(
            max_age_hours=max_age_hours,
            protected_session_ids=protected_session_ids,
        )
        return {
            "schema_version": 1,
            "operation": "dry_run" if dry_run else "close_stale_sessions",
            "dry_run": bool(dry_run),
            "database": str(DB_PATH),
            "max_age_hours": float(max_age_hours),
            "closed_count": closed,
            "candidate_count": len(candidate_ids),
            "before": before,
            "after": after,
            "remaining_candidates": [
                row["session_id"] for row in after["candidates"]
            ],
        }

    @staticmethod
    def close_stale_sessions(max_age_hours: int = 1) -> int:
        """Compatibility wrapper: close stale per-message sessions, return count.

        Named operational sessions are protected by default. New callers that
        need an audit trail or a dry run should use ``cleanup_stale_sessions``.
        """
        return int(DirectMemory.cleanup_stale_sessions(
            max_age_hours=max_age_hours,
            dry_run=False,
        )["closed_count"])

    @staticmethod
    def get_open_sessions_count() -> int:
        """Return the number of currently open (un-ended) sessions."""
        conn = sqlite3.connect(str(DB_PATH), timeout=2, check_same_thread=False)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        return n

    @staticmethod
    def summary_stats() -> Dict[str, Any]:
        """Return lightweight summary statistics for the memory database.

        Used by doctor.py, azazael_ctl.py (cmd_stats), and dashboards to
        report total event counts, session counts, and event-type breakdown.

        Returns:
            dict with:
                total_events: int   — total rows in `memories`
                sessions: int       — total session rows
                open_sessions: int  — sessions with ended_at IS NULL
                by_type: dict       — {event_type: count}, sorted desc
                db_size_mb: float   — size of the DB file on disk
        """
        conn = sqlite3.connect(str(DB_PATH), timeout=2, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            total_events = conn.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0]
            sessions = conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            open_sessions = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
            ).fetchone()[0]
            by_type = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT event_type, COUNT(*) FROM memories GROUP BY event_type "
                    "ORDER BY COUNT(*) DESC"
                )
            }
        finally:
            conn.close()
        return {
            "total_events": total_events,
            "sessions": sessions,
            "open_sessions": open_sessions,
            "by_type": by_type,
            "db_size_mb": DirectMemory.db_size().get("total_mb", 0.0),
        }

    @staticmethod
    def db_size() -> Dict[str, Any]:
        """Return the size of the memory database file (and WAL/SHM if present).

        Used by azazael_ctl.py (cmd_stats), metrics.py (health endpoint), and
        dashboards to report disk usage.

        Returns:
            dict with:
                db_mb: float     — main DB file size in MB
                total_mb: float  — DB + WAL + SHM size in MB
                path: str        — absolute path to the DB file
        """
        db_path = Path(str(DB_PATH))
        wal = db_path.with_suffix(".db-wal")
        shm = db_path.with_suffix(".db-shm")
        db_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0.0
        total = db_mb
        for extra in (wal, shm):
            if extra.exists():
                total += extra.stat().st_size / (1024 * 1024)
        return {
            "db_mb": round(db_mb, 2),
            "total_mb": round(total, 2),
            "path": str(db_path),
        }

    # -- Cross-reference (CD2 session_mappings) -----------------------
    # Links DM sessions to audit JSONL files. The mappings live in the
    # `session_mappings` table (created in _SCHEMA). Consumers: azazael_ctl.py
    # (status), cli_crossref.py, xref_dashboard.py.

    @staticmethod
    def get_mapping_summary() -> Dict[str, Any]:
        """Summarize the DM<->AJ cross-reference mapping state.

        Returns:
            dict with:
                total_mappings: int       — rows in session_mappings
                unique_dm_sessions: int   — distinct DM sessions linked
                unique_aj_sessions: int   — distinct audit files linked
        """
        conn = sqlite3.connect(str(DB_PATH), timeout=5, check_same_thread=False)
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM session_mappings"
            ).fetchone()[0]
            dm = conn.execute(
                "SELECT COUNT(DISTINCT dm_session_id) FROM session_mappings"
            ).fetchone()[0]
            aj = conn.execute(
                "SELECT COUNT(DISTINCT audit_filepath) FROM session_mappings"
            ).fetchone()[0]
            # `by_type` added 2026-09-21: `tests/test_session_mappings.py` and
            # `tests/test_property_based.py` both read it, and a bare total cannot
            # distinguish a store full of hand-made `manual` links from one full of
            # `auto` links produced by the reconnect path -- which is the difference
            # between a cross-reference that was asserted and one that was guessed.
            by_type = {
                r[0]: r[1] for r in conn.execute(
                    "SELECT mapping_type, COUNT(*) FROM session_mappings "
                    "GROUP BY mapping_type ORDER BY COUNT(*) DESC")
            }
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1309", exc)
            total, dm, aj, by_type = 0, 0, 0, {}
        finally:
            conn.close()
        return {
            "total_mappings": total,
            "unique_dm_sessions": dm,
            "unique_aj_sessions": aj,
            "by_type": by_type,
        }

    @staticmethod
    def query_cross_reference(
        dm_session_id: Optional[str] = None,
        audit_filepath: Optional[str] = None,
        correlation_id: Optional[str] = None,
        limit: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return cross-reference rows, optionally filtered.

        Args:
            dm_session_id: Filter to one DM session.
            audit_filepath: Filter to one audit file.
            correlation_id: Filter by correlation ID.
            limit: Max rows (0 = no limit).

        Returns:
            List of rows with keys dm_session_id, audit_filepath,
            audit_session_id, confidence, mapping_type, correlation_id,
            linked_at, notes.
        """
        clauses = []
        params: List[Any] = []
        if dm_session_id:
            clauses.append("dm_session_id = ?")
            params.append(dm_session_id)
        if audit_filepath:
            clauses.append("audit_filepath = ?")
            params.append(audit_filepath)
        if correlation_id:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        sql = ("SELECT id, dm_session_id, audit_filepath, audit_session_id, "
               "correlation_id, linked_at, mapping_type, confidence, notes "
               "FROM session_mappings")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id ASC"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        conn = sqlite3.connect(str(DB_PATH), timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1364", exc)
            return []
        finally:
            conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def get_mapping_by_correlation_id(corr_id: str) -> List[Dict[str, Any]]:
        """Return mappings for a given correlation ID."""
        return DirectMemory.query_cross_reference(correlation_id=corr_id)

    @staticmethod
    def get_unlinked_report() -> Dict[str, Any]:
        """Report DM sessions and audit files that have no mapping.

        Returns:
            dict with:
                unlinked_dm_count: int                    — DM sessions without a mapping
                unlinked_aj_count: int                    — audit files without a mapping
                unlinked_dm: list                         — of {session_id, started_at}
                unlinked_aj: list of dict                 — of {filepath, ts}
        """
        try:
            from CORE.audit_trail import AUDIT_DIR
        except Exception as exc:
            _note_failure("direct_memory:L1388", exc)
            AUDIT_DIR = None
        mapped_dm = set(
            r["dm_session_id"]
            for r in DirectMemory.query_cross_reference()
        )
        unlinked_dm = []
        conn = sqlite3.connect(str(DB_PATH), timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT session_id, started_at FROM sessions "
                "ORDER BY started_at DESC LIMIT 500"
            ).fetchall()
            mapped_aj = {
                r["audit_filepath"]
                for r in DirectMemory.query_cross_reference()
            }
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1406", exc)
            rows, mapped_aj = [], set()
        finally:
            conn.close()
        for r in rows:
            if r["session_id"] not in mapped_dm:
                unlinked_dm.append({
                    "session_id": r["session_id"],
                    "started_at": r["started_at"],
                })

        unlinked_aj = []
        if AUDIT_DIR is not None and AUDIT_DIR.exists():
            for fp in sorted(AUDIT_DIR.glob("session_*.jsonl")):
                key = str(fp)
                if key in mapped_aj:
                    continue
                mtime = None
                try:
                    mtime = fp.stat().st_mtime
                except OSError as exc:
                    _note_failure("direct_memory:L1426", exc)
                unlinked_aj.append({"filepath": key, "ts": mtime})

        return {
            "unlinked_dm_count": len(unlinked_dm),
            "unlinked_aj_count": len(unlinked_aj),
            "unlinked_dm": unlinked_dm,
            "unlinked_aj": unlinked_aj,
        }

    @staticmethod
    def cross_reference_ledgers() -> Dict[str, Any]:
        """Auto-link DM sessions to audit files and report new links.

        Strategy (non-destructive): for each DM session not yet mapped,
        find the newest audit file that is not yet mapped and link them.
        Reuses existing mappings (never duplicates).

        Returns:
            dict with:
                new_links: int           — mappings created this call
                total_mappings: int      — total mappings after linking
        """
        mapped_dm = set()
        mapped_aj = set()
        for r in DirectMemory.query_cross_reference():
            mapped_dm.add(r["dm_session_id"])
            mapped_aj.add(r["audit_filepath"])

        conn = sqlite3.connect(str(DB_PATH), timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            dm_sessions = conn.execute(
                "SELECT session_id FROM sessions "
                "WHERE session_id NOT IN "
                "(SELECT dm_session_id FROM session_mappings) "
                "ORDER BY started_at ASC"
            ).fetchall()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1465", exc)
            dm_sessions = []
        finally:
            conn.close()

        audit_files = []
        try:
            from CORE.audit_trail import AUDIT_DIR
        except Exception as exc:
            _note_failure("direct_memory:L1473", exc)
            AUDIT_DIR = None
        if AUDIT_DIR is not None and AUDIT_DIR.exists():
            audit_files = sorted(
                AUDIT_DIR.glob("session_*.jsonl"),
                key=lambda fp: fp.stat().st_mtime,
            )

        new_links = 0
        for drow in dm_sessions:
            sid = drow["session_id"]
            # Next unlinked audit file (oldest-first pairing).
            target = None
            for fp in audit_files:
                key = str(fp)
                if key not in mapped_aj:
                    target = (key, fp)
                    break
            if target is None:
                break
            key, fp = target
            # Derive the short audit session id from the filename stem,
            # e.g. session_20260611_080124_86c9 -> 86c9.
            audit_sid = fp.stem.rsplit("_", 1)[-1] if "_" in fp.stem else fp.stem
            try:
                fp.stat()
            except OSError as exc:
                _note_failure("direct_memory:L1499", exc)
                audit_sid = ""
            conn = sqlite3.connect(str(DB_PATH), timeout=5, check_same_thread=False)
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO session_mappings("
                    "dm_session_id, audit_filepath, audit_session_id, "
                    "mapping_type, confidence, correlation_id, notes) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (sid, key, audit_sid, "auto", 1.0, None,
                     "linked via timestamp proximity")
                )
                conn.commit()
            except sqlite3.Error as exc:
                _note_failure("direct_memory:L1512", exc)
            finally:
                conn.close()
            mapped_aj.add(key)
            new_links += 1

        return {
            "new_links": new_links,
            "total_mappings": DirectMemory.get_mapping_summary()["total_mappings"],
        }

    # -- Query helpers ----------------------------------------------

    # -- Session mappings: the WRITE side (2026-09-21) -------------------
    # The read side (`get_mapping_summary`, `query_cross_reference`) has existed
    # since CD2, but nothing in the tree could CREATE a mapping: `grep -rn
    # "INSERT INTO session_mappings" src/` matched nothing, so the table was
    # created by the schema, read by two methods, and never written. Every summary
    # reported zeros and `query_cross_reference` could only ever return the empty
    # list. These are the missing write path.

    def link_audit_session(self, audit_filepath: str,
                           audit_session_id: Optional[str] = None,
                           mapping_type: str = "auto",
                           confidence: float = 1.0,
                           notes: Optional[str] = None,
                           correlation_id: Optional[str] = None) -> bool:
        """Link this DM session to an audit JSONL file. False if it could not be.

        Idempotent per (dm_session_id, audit_filepath): a second identical link
        returns False rather than raising, because the caller is a startup path and
        a duplicate is not an error. An empty path is rejected with False for the
        same reason -- a blank path is not a link, and raising there would turn a
        data-quality problem into a crash of the session that is being recorded.
        """
        path = str(audit_filepath or "").strip()
        if not path:
            return False
        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    duplicate = conn.execute(
                        "SELECT id FROM session_mappings "
                        "WHERE dm_session_id = ? AND audit_filepath = ?",
                        (self._session_id, path),
                    ).fetchone()
                    if duplicate is not None:
                        return False
                    conn.execute(
                        "INSERT INTO session_mappings(dm_session_id, "
                        "audit_filepath, audit_session_id, correlation_id, "
                        "mapping_type, confidence, notes) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (self._session_id, path, audit_session_id,
                         correlation_id or self._correlation_id,
                         mapping_type, float(confidence), notes),
                    )
                    conn.commit()
                finally:
                    conn.close()
            return True
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1575", exc)
            return False

    def list_mappings(self, limit: int = 0) -> List[Dict[str, Any]]:
        """Return this DM session's audit links, newest first.

        Session-scoped on purpose: the cross-reference tools want "which audit
        files describe THIS session", and a global listing would make the
        per-session answer a filter the caller has to remember to apply.
        """
        conn = _connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            sql = ("SELECT dm_session_id, audit_filepath, audit_session_id, "
                   "correlation_id, linked_at, mapping_type, confidence, notes "
                   "FROM session_mappings WHERE dm_session_id = ? "
                   "ORDER BY id DESC")
            params: List[Any] = [self._session_id]
            if limit:
                sql += " LIMIT ?"
                params.append(int(limit))
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1597", exc)
            return []
        finally:
            conn.close()

    # -- Session lifecycle (static) -------------------------------------
    # Named by `tests/test_validation.py`, `tests/test_property_based.py` and
    # `tests/test_concurrency_200.py` and absent from the class until 2026-09-21.
    # All of them are read/repair paths, so all of them are fail-soft: a repair
    # tool that raises on a missing session cannot be used to find out whether the
    # session exists.

    @staticmethod
    def _decode_rows(rows) -> List[Dict[str, Any]]:
        """Decode `data`/`tags` JSON on rows of the `memories` table."""
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            for field in ("data", "tags"):
                val = d.get(field)
                if isinstance(val, str):
                    try:
                        d[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError) as exc:
                        _note_failure("direct_memory:L1620", exc)
            out.append(d)
        return out

    @staticmethod
    def replay_session(session_id: str, limit: int = 0) -> List[Dict[str, Any]]:
        """Return every event of a session in record order.

        Ordered by `id ASC`, not by timestamp: ordering is the point of a replay,
        and `id` is the only monotonic column when several events share a
        timestamp (which they routinely do -- the whole point of a batch import).
        """
        conn = _connect()
        try:
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM memories WHERE session_id = ? ORDER BY id ASC"
            params: List[Any] = [str(session_id)]
            if limit:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1642", exc)
            return []
        finally:
            conn.close()
        return DirectMemory._decode_rows(rows)

    @staticmethod
    def purge_session(session_id: str) -> int:
        """Delete a session and its events. Returns the number of events deleted.

        Also removes the session's rows from `session_mappings`, because a mapping
        whose DM session no longer exists is exactly the dangling cross-reference
        `get_unlinked_report()` exists to complain about.
        """
        sid = str(session_id)
        conn = _connect()
        try:
            deleted = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id = ?", (sid,)
            ).fetchone()[0]
            conn.execute("DELETE FROM memories WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM session_mappings WHERE dm_session_id = ?", (sid,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
            conn.commit()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1666", exc)
            return 0
        finally:
            conn.close()
        return int(deleted or 0)

    @staticmethod
    def merge_sessions(source_session_id: str,
                       target_session_id: str) -> int:
        """Move every event of one session onto another. Returns rows moved.

        Used by the reconnect path, where a message that died without calling
        `.end()` left its events under a session id nothing will ever ask for.
        """
        src, dst = str(source_session_id), str(target_session_id)
        if src == dst:
            return 0
        conn = _connect()
        try:
            cur = conn.execute(
                "UPDATE memories SET session_id = ? WHERE session_id = ?", (dst, src)
            )
            moved = cur.rowcount or 0
            conn.execute(
                "UPDATE session_mappings SET dm_session_id = ? "
                "WHERE dm_session_id = ?", (dst, src))
            row = conn.execute(
                "SELECT event_count, started_at FROM sessions WHERE session_id = ?",
                (src,)).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE sessions SET event_count = event_count + ? "
                    "WHERE session_id = ?", (int(row[0] or 0), dst))
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (src,))
            conn.commit()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1701", exc)
            return 0
        finally:
            conn.close()
        return int(moved)

    @staticmethod
    def session_duration(session_id: str) -> Dict[str, Any]:
        """Return `{duration_s, events, started_at, ended_at}` for a session.

        An unknown session is reported as a zero-length session rather than as an
        error, so "how long was this session" has an answer for every id.
        """
        sid = str(session_id)
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT started_at, ended_at FROM sessions WHERE session_id = ?",
                (sid,)).fetchone()
            events = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id = ?", (sid,)
            ).fetchone()[0]
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1723", exc)
            return {"duration_s": 0, "events": 0,
                    "started_at": None, "ended_at": None}
        finally:
            conn.close()
        if row is None:
            return {"duration_s": 0, "events": 0,
                    "started_at": None, "ended_at": None}
        started, ended = row[0], row[1]
        try:
            t0 = datetime.fromisoformat(str(started))
            t1 = (datetime.fromisoformat(str(ended)) if ended
                  else datetime.now(timezone.utc))
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=timezone.utc)
            if t1.tzinfo is None:
                t1 = t1.replace(tzinfo=timezone.utc)
            duration = max(0.0, (t1 - t0).total_seconds())
        except (TypeError, ValueError) as exc:
            _note_failure("direct_memory:L1741", exc)
            duration = 0.0
        return {"duration_s": round(duration, 3), "events": int(events or 0),
                "started_at": started, "ended_at": ended}

    @staticmethod
    def session_gc(max_age_hours: int = 1) -> int:
        """Close sessions left open longer than `max_age_hours`. Returns how many.

        The concurrency-safe entry point: `tests/test_concurrency_200.py` runs 3
        threads calling this while 10 threads write, so it must never raise. SQLite
        reports lock contention as an exception, and a background collector that
        dies on contention is a collector that silently stops collecting -- hence
        the catch. Waits via `busy_timeout` first (see `_connect`), so this returns
        0 only when the store is genuinely held longer than 15s.
        """
        conn = _connect()
        try:
            cur = conn.execute(
                "UPDATE sessions SET ended_at = datetime('now') "
                "WHERE ended_at IS NULL AND started_at < datetime('now', ?)",
                (f"-{int(max_age_hours)} hours",))
            conn.commit()
            return int(cur.rowcount or 0)
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1765", exc)
            return 0
        finally:
            conn.close()

    # -- Query / analytics (static) -------------------------------------

    @staticmethod
    def filtered_query(limit: int = 10,
                       session_id: Optional[str] = None,
                       event_type: Optional[str] = None,
                       severity: Optional[str] = None,
                       source: Optional[str] = None,
                       tags: Optional[List[str]] = None,
                       since: Optional[str] = None,
                       until: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return recent events matching all supplied filters, newest first."""
        clauses: List[str] = []
        params: List[Any] = []
        for column, value in (("session_id", session_id),
                              ("event_type", event_type),
                              ("severity", severity),
                              ("source", source)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        for tag in (tags or []):
            # `tags` is a JSON array in one TEXT column, so a LIKE is the only
            # filter available without a second table. Quoted so "bug" does not
            # match "debug".
            clauses.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        sql = "SELECT * FROM memories"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit) if limit else -1)
        conn = _connect()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1812", exc)
            return []
        finally:
            conn.close()
        return DirectMemory._decode_rows(rows)

    @staticmethod
    def time_series(granularity: str = "day", limit: int = 7) -> List[Dict[str, Any]]:
        """Event counts bucketed by time. Oldest bucket first, at most `limit`."""
        fmt = {"hour": "%Y-%m-%dT%H:00",
               "day": "%Y-%m-%d",
               "week": "%Y-W%W",
               "month": "%Y-%m"}.get(str(granularity), "%Y-%m-%d")
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT strftime(?, timestamp) AS bucket, COUNT(*) AS n "
                "FROM memories GROUP BY bucket ORDER BY bucket DESC LIMIT ?",
                (fmt, int(limit) if limit else -1)).fetchall()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1831", exc)
            return []
        finally:
            conn.close()
        return [{"bucket": r[0], "count": int(r[1] or 0)} for r in reversed(rows)]

    @staticmethod
    def import_events(events: List[Dict[str, Any]],
                      session_id: Optional[str] = None) -> int:
        """Insert many events in ONE transaction. Returns the number inserted.

        Deliberately bypasses `record()`'s per-event gates (dedupe window, milestone
        cap, alert throttle): those exist to keep a live session's stream readable,
        and applying them to a backfill would silently drop the rows the caller is
        importing. One bad row is normalised rather than rejected, so it cannot
        abort the other 99 -- `severity` falls back to "info" and a missing
        `event_type` is skipped and counted out of the total.
        """
        if not events:
            return 0
        sid = str(session_id or "bulk_import")
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            etype = ev.get("event_type")
            if not etype or not isinstance(etype, str):
                continue
            sev = str(ev.get("severity") or "info").lower()
            if sev not in DirectMemory.VALID_SEVERITIES:
                sev = "info"
            rows.append((
                sid, str(ev.get("source") or "bulk_import"), etype,
                json.dumps(ev.get("data") or {}, ensure_ascii=False, default=str),
                sev,
                json.dumps(ev.get("tags") or [], ensure_ascii=False),
                str(ev.get("timestamp") or now),
                _uuid7(),
            ))
        if not rows:
            return 0
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.executemany(
                "INSERT INTO memories(session_id, source, event_type, data, "
                "severity, tags, timestamp, event_uuid) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)", rows)
            conn.commit()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1881", exc)
            return 0
        finally:
            conn.close()
        return len(rows)

    @staticmethod
    def validate_event_type(event_type: Any) -> bool:
        """True if `event_type` is in this store's declared vocabulary.

        ADVISORY ONLY -- deliberately not called by `record()`. See the note on
        `EVENT_TYPES` above: a vocabulary that can reject telemetry would delete the
        evidence it exists to describe.
        """
        return isinstance(event_type, str) and event_type in EVENT_TYPES

    @staticmethod
    def deprecation_warnings() -> List[Dict[str, Any]]:
        """Event types in the store that this module marks as legacy.

        Reports only types that are actually PRESENT, with their counts, so the
        answer is about this database rather than about the constant. `new` is None
        when no replacement is declared anywhere in the tree (see
        `LEGACY_EVENT_TYPES`); None is not a claim that the type was removed.
        """
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) FROM memories GROUP BY event_type"
            ).fetchall()
        except sqlite3.Error as exc:
            _note_failure("direct_memory:L1911", exc)
            return []
        finally:
            conn.close()
        out = []
        for etype, count in rows:
            if etype in LEGACY_EVENT_TYPES:
                out.append({"old": etype, "new": LEGACY_EVENT_TYPES[etype],
                            "count": int(count or 0)})
        out.sort(key=lambda w: w["count"], reverse=True)
        return out

    # -- Deep data analytics (CHANGELOG v1.0.0 "8 new static methods") ----
    # The CHANGELOG names these as shipped; they were not. Implemented here
    # against the REAL schema (memories + sessions, JSON tags column, no FTS5
    # in this store — full-text lives in LocalMemoryStore, a different file).
    # Every method is read-only and never raises: analytics must not take down
    # the recorder that feeds them.

    @staticmethod
    def explain_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """EXPLAIN QUERY PLAN rows for an arbitrary SELECT against the store.

        wrappers the raw plan in a dict per step (id/parent/detail) so callers
        can render it. Read-only guard: the statement must start with SELECT
        or WITH; anything else returns an error dict rather than executing.
        """
        stripped = (sql or "").lstrip().lower()
        if not (stripped.startswith("select") or stripped.startswith("with")):
            return [{"error": "explain_query only accepts SELECT/WITH statements"}]
        conn = _connect()
        try:
            rows = conn.execute(
                "EXPLAIN QUERY PLAN " + sql, tuple(params or [])).fetchall()
        except sqlite3.Error as e:
            _note_failure("direct_memory:explain_query", e)
            return [{"error": str(e)}]
        finally:
            conn.close()
        return [{"id": r[0], "parent": r[1], "detail": r[3]} for r in rows]

    @staticmethod
    def schema_diff() -> Dict[str, Any]:
        """Compare the live schema against what _ensure_schema() declares.

        Reports tables/columns present vs expected on both sides. Drift here
        is exactly how a migration gets silently half-applied.
        """
        conn = _connect()
        try:
            live: Dict[str, set] = {}
            for (tname,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'").fetchall():
                live[tname] = {c[1] for c in conn.execute(
                    "PRAGMA table_info(%s)" % tname)}
        except sqlite3.Error as e:
            _note_failure("direct_memory:schema_diff", e)
            return {"error": str(e)}
        finally:
            conn.close()
        # Producer of truth = _ensure_schema/_migrate_schema. Core columns are
        # the MINIMUM a reader may assume; the live DB legitimately carries
        # MORE (created_at, audit_hash, FTS shadow tables, agent_patterns…).
        # Only MISSING columns are drift; extras are recorded, not failed.
        _core = {
            "memories": {"id", "session_id", "source", "event_type", "data",
                         "severity", "tags", "timestamp", "event_uuid"},
            "sessions": {"session_id", "started_at", "ended_at"},
            "memories_fts": {"event_type", "data", "tags"},
            "session_mappings": {"id", "dm_session_id", "audit_filepath",
                                 "correlation_id"},
        }
        out: Dict[str, Any] = {"live_tables": sorted(live), "tables": {}}
        total_drift = 0
        for table, want in _core.items():
            got = live.get(table, set())
            missing = sorted(want - got)
            extra = sorted(got - want)
            out["tables"][table] = {"missing": missing, "extra": extra,
                                    "present": table in live}
            total_drift += len(missing)
        out["in_sync"] = total_drift == 0 and set(_core) <= set(live)
        return out

    @staticmethod
    def event_correlation(window_seconds: int = 60,
                          limit: int = 20) -> List[Dict[str, Any]]:
        """Event-type pairs that co-occur within `window_seconds`.

        Self-join on session + bounded time delta; only pairs where type_a
        < type_b alphabetically are kept so (a,b) and (b,a) collapse to one.
        """
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT a.event_type AS a, b.event_type AS b, COUNT(*) AS n "
                "FROM memories a JOIN memories b "
                "ON a.session_id = b.session_id AND a.id < b.id "
                "WHERE julianday(b.timestamp) - julianday(a.timestamp) <= ?/86400.0 "
                "AND a.event_type < b.event_type "
                "GROUP BY a.event_type, b.event_type "
                "ORDER BY n DESC LIMIT ?",
                (int(window_seconds), int(limit))).fetchall()
        except sqlite3.Error as e:
            _note_failure("direct_memory:event_correlation", e)
            return []
        finally:
            conn.close()
        return [{"type_a": r[0], "type_b": r[1], "count": int(r[2] or 0)}
                for r in rows]

    @staticmethod
    def session_clustering(min_similarity: float = 0.5,
                           limit: int = 50) -> List[Dict[str, Any]]:
        """Group recent sessions by Jaccard similarity of their event-type sets.

        Greedy clustering: pick an unclustered seed, absorb sessions whose
        event-type-set Jaccard vs the seed >= min_similarity, repeat. O(n²)
        over recent sessions only, bounded by `limit`, so this stays cheap.
        """
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT session_id, GROUP_CONCAT(DISTINCT event_type) "
                "FROM memories GROUP BY session_id ORDER BY session_id DESC "
                "LIMIT ?", (int(limit),)).fetchall()
        except sqlite3.Error as e:
            _note_failure("direct_memory:session_clustering", e)
            return []
        finally:
            conn.close()
        sets = [(r[0], set((r[1] or "").split(","))) for r in rows if r[0]]

        def jac(a: set, b: set) -> float:
            if not a and not b:
                return 1.0
            if not a or not b:
                return 0.0
            return len(a & b) / len(a | b)

        clusters: List[Dict[str, Any]] = []
        unassigned = list(sets)
        while unassigned:
            seed_id, seed_set = unassigned.pop(0)
            members = [seed_id]
            keep = []
            for sid, s in unassigned:
                if jac(seed_set, s) >= min_similarity:
                    members.append(sid)
                else:
                    keep.append((sid, s))
            unassigned = keep
            clusters.append({"seed": seed_id, "size": len(members),
                             "members": members})
        return clusters

    @staticmethod
    def anomaly_detection(days: int = 7) -> List[Dict[str, Any]]:
        """Sessions whose shape deviates >3σ from the recent mean.

        Compares each session's event count against the mean+stddev over the
        last `days` worth of sessions. Small-sample safe: <5 sessions returns
        an explicit not_enough_data rather than a fabricated z-score.
        """
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT s.session_id, COUNT(m.id) AS n "
                "FROM sessions s LEFT JOIN memories m ON m.session_id = s.session_id "
                "WHERE s.started_at >= datetime('now', ?) "
                "GROUP BY s.session_id", (f"-{int(days)} days",)).fetchall()
        except sqlite3.Error as e:
            _note_failure("direct_memory:anomaly_detection", e)
            return []
        finally:
            conn.close()
        if len(rows) < 5:
            return [{"status": "not_enough_data", "sessions": len(rows),
                     "minimum": 5}]
        counts = [int(r[1] or 0) for r in rows]
        mean = sum(counts) / len(counts)
        var = sum((c - mean) ** 2 for c in counts) / len(counts)
        std = var ** 0.5
        out = []
        for (sid, n) in rows:
            c = int(n or 0)
            if std == 0:
                continue
            z = (c - mean) / std
            if abs(z) > 3.0:
                out.append({"session_id": sid, "events": c,
                            "mean": round(mean, 2), "z": round(z, 2),
                            "kind": "high_volume" if z > 0 else "low_volume"})
        return out

    @staticmethod
    def trend_analysis(days: int = 30) -> Dict[str, Any]:
        """Event-rate trends: recent-window vs previous-window, per type.

        Splits the last `days` into two equal halves and reports per-type
        deltas, plus an overall daily-rate comparison. Answers "is telemetry
        rising or dying" — a dying recorder looks like success otherwise.
        """
        half = max(1, int(days) // 2)
        conn = _connect()
        try:
            recent = dict(conn.execute(
                "SELECT event_type, COUNT(*) FROM memories "
                "WHERE timestamp >= datetime('now', ?) GROUP BY event_type",
                (f"-{half} days",)).fetchall())
            prior = dict(conn.execute(
                "SELECT event_type, COUNT(*) FROM memories "
                "WHERE timestamp < datetime('now', ?) "
                "AND timestamp >= datetime('now', ?) GROUP BY event_type",
                (f"-{half} days", f"-{int(days)} days")).fetchall())
        except sqlite3.Error as e:
            _note_failure("direct_memory:trend_analysis", e)
            return {"error": str(e)}
        finally:
            conn.close()
        types = sorted(set(recent) | set(prior))
        per_type = {}
        for t in types:
            r, p = recent.get(t, 0), prior.get(t, 0)
            per_type[t] = {"recent_half": r, "prior_half": p,
                           "delta": r - p,
                           "direction": ("up" if r > p else
                                         "down" if r < p else "flat")}
        overall_recent = sum(recent.values())
        overall_prior = sum(prior.values())
        return {
            "window_days": int(days),
            "per_type": per_type,
            "overall": {"recent_half": overall_recent,
                        "prior_half": overall_prior,
                        "daily_rate_recent": round(overall_recent / half, 2),
                        "daily_rate_prior": round(overall_prior / half, 2),
                        "direction": ("up" if overall_recent > overall_prior
                                      else "down" if overall_recent < overall_prior
                                      else "flat")},
        }

    @staticmethod
    def session_search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Full-text search across event data + tags via the store's FTS5 index.

        FTS5 IS present here (memories_fts shadows memories) — the CHANGELOG
        was right; an earlier implementation comment of this method wrongly
        disclaimed it. Quotes the query so FTS operators in user input are
        literals, and falls back to a LIKE scan if the index is missing.
        """
        if not query:
            return []
        conn = _connect()
        try:
            conn.row_factory = sqlite3.Row
            fts_row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='memories_fts'").fetchone()
            if fts_row:
                # Quote each whitespace token so user punctuation stays literal.
                safe = " ".join('"%s"' % tok.replace('"', '""')
                                for tok in query.split())
                rows = conn.execute(
                    "SELECT m.id, m.session_id, m.event_type, m.severity, "
                    "m.timestamp, m.data FROM memories_fts f "
                    "JOIN memories m ON m.rowid = f.rowid "
                    "WHERE memories_fts MATCH ? ORDER BY m.id DESC LIMIT ?",
                    (safe, int(limit))).fetchall()
            else:
                escaped = (query.replace("\\", "\\\\")
                           .replace("%", "\\%").replace("_", "\\_"))
                like = f"%{escaped}%"
                rows = conn.execute(
                    "SELECT id, session_id, event_type, severity, timestamp, data "
                    "FROM memories WHERE data LIKE ? ESCAPE '\\\\' "
                    "ORDER BY id DESC LIMIT ?",
                    (like, int(limit))).fetchall()
        except sqlite3.Error as e:
            _note_failure("direct_memory:session_search", e)
            return []
        finally:
            conn.close()
        return DirectMemory._decode_rows(rows)

    @staticmethod
    def data_quality_report() -> Dict[str, Any]:
        """Null-orphan/deprecation/shape census over the live store.

        Checks: events with unknown event_type vocabulary (advisory), NULL
        severity, sessions with zero events (orphans), deprecated types
        (via deprecation_warnings). Shapes, not judgments — the caller decides.
        """
        conn = _connect()
        checks: Dict[str, Any] = {}
        try:
            checks["null_severity"] = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE severity IS NULL").fetchone()[0]
            checks["null_session_id"] = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id IS NULL").fetchone()[0]
            checks["orphan_sessions"] = conn.execute(
                "SELECT COUNT(*) FROM sessions s "
                "WHERE NOT EXISTS (SELECT 1 FROM memories m "
                "WHERE m.session_id = s.session_id)").fetchone()[0]
            checks["unknown_types"] = conn.execute(
                "SELECT COUNT(DISTINCT event_type) FROM memories "
                "WHERE event_type NOT IN (" + ",".join("?" * len(EVENT_TYPES)) + ")",
                tuple(EVENT_TYPES)).fetchone()[0]
        except sqlite3.Error as e:
            _note_failure("direct_memory:data_quality_report", e)
            return {"error": str(e)}
        finally:
            conn.close()
        return {"checks": checks,
                "deprecated": DirectMemory.deprecation_warnings(),
                "schema": DirectMemory.schema_diff()}

    # -- TTL cache (static) ---------------------------------------------
    # `tests/test_property_based.py::test_cache_invalidation` names these; the
    # cache itself lives at module level so that a fresh DirectMemory instance does
    # not start with an empty view of data a previous instance just read.

    @staticmethod
    def _cache_set(key: str, value: Any, ttl: float = 30.0) -> None:
        _TTLCache.set(key, value, ttl)

    @staticmethod
    def _cache_get(key: str) -> Any:
        return _TTLCache.get(key)

    @staticmethod
    def _cache_invalidate(pattern: str = "*") -> int:
        return _TTLCache.invalidate(pattern)

    # -- AJ-compatible emitters -----------------------------------------
    # The six below were named by `tests/test_session_mappings.py` (class
    # `TestAJCompatibleMethods`) and were absent. They exist so sessions recorded
    # through DirectMemory carry the same event types the audit journal (`AJ`) uses;
    # without them the two ledgers could not be joined on event type at all. Each
    # returns the row id, so a caller can join the event it just wrote.

    def inference_spawn(self, agent: str, model: Optional[str] = None,
                        **extra: Any) -> int:
        """Record that an inference provider was spawned for an agent."""
        payload: Dict[str, Any] = {"agent": agent}
        if model:
            payload["model"] = model
        payload.update(extra)
        return self.record("inference_spawn", payload, tags=["inference"])

    def gate_passed(self, passed: int, failed: int = 0) -> int:
        """Record a gate evaluation and its pass/fail counts."""
        total = int(passed) + int(failed)
        return self.record(
            "gate_passed",
            {"passed": int(passed), "failed": int(failed), "total": total,
             "rate": (float(passed) / total) if total else 0.0},
            severity="error" if int(failed) else "info",
            tags=["gate"],
        )

    def sentinel_run(self, result: str, **extra: Any) -> int:
        """Record a compliance-sentinel outcome (e.g. "PASS")."""
        verdict = str(result).upper()
        payload: Dict[str, Any] = {"result": verdict}
        payload.update(extra)
        return self.record(
            "sentinel_run", payload,
            severity="error" if verdict not in ("PASS", "OK") else "info",
            tags=["compliance"],
        )

    def record_verified_negative(self, subject: str, claim: str,
                                 evidence: str = "",
                                 checked_by: str = "") -> int:
        """Record a verified negative — proof that something did NOT occur.

        P0 slice 1 (ATP negatives): mirrors sentinel_run; the raw payload is
        stored for audit exactness while recall layers must reconstruct from
        it, never inject verbatim (MemHarness rule).
        """
        return self.record(
            "verified_negative",
            {"subject": subject, "claim": claim, "evidence": evidence,
             "checked_by": checked_by},
            tags=["verification", "negative"],
        )

    def reconstruct_context(self, current_task: str,
                            limit: int = 20) -> Dict[str, Any]:
        """Audited reconstructive recall for the Codebuff path (P0 slices 2+3).

        recall_recent -> shared MAFIA audit + MemHarness reconstruction from
        CORE.local_memory (lazy import: breaks the local_memory ->
        verification_gate -> CORE-package -> direct_memory top-level
        reachability path; a top-level import here would re-enter this
        partially-initialized module. The laziness is load-bearing.)
        Raw payloads never enter the returned brief.
        """
        from CORE.local_memory import (  # noqa: PLC0415
            audit_result_set, LocalMemoryStore)
        events = self.recall_recent(limit=limit)
        audit = audit_result_set(events, current_task)
        self.record("retrieval_audit", audit,
                    severity="warning" if audit["n_quarantined"] else "info",
                    tags=["verification", "mafia-audit"])
        kept = LocalMemoryStore.filter_quarantined(events, audit)
        out = LocalMemoryStore.reconstruct_events(kept, current_task)
        out["audit"] = audit
        return out

    def scan_anti_pattern(self, filepath: str, method: str, checked: int,
                          issues: int = 0) -> int:
        """Record an anti-pattern scan of one file."""
        return self.record(
            "scan_anti_pattern",
            {"filepath": filepath, "method": method, "checked": int(checked),
             "issues": int(issues)},
            severity="warning" if int(issues) else "info",
            tags=["scan"],
        )

    def sync_skills(self, synced: int, missing: int = 0, failed: int = 0) -> int:
        """Record a skill-sync pass across environments."""
        return self.record(
            "sync_skills",
            {"synced": int(synced), "missing": int(missing),
             "failed": int(failed)},
            severity="warning" if int(failed) else "info",
            tags=["sync"],
        )

    def expect_action(self, action: str, expectation: str) -> int:
        """Record what the next action is expected to do, so it can be checked."""
        return self.record(
            "expect_action",
            {"action": action, "expectation": expectation},
            tags=["expectation"],
        )

    def count(self) -> int:
        conn = self._get_conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE session_id = ?",
            (self._session_id,)
        ).fetchone()[0]
        conn.close()
        return n

    def list_events(self, limit: int = 50) -> List[Dict]:
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM memories WHERE session_id = ? ORDER BY id ASC LIMIT ?",
            (self._session_id, limit)
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            for field in ("data", "tags"):
                try:
                    d[field] = json.loads(d[field]) if isinstance(d[field], str) else d[field]
                except (json.JSONDecodeError, TypeError) as exc:
                    _note_failure("direct_memory:L2066", exc)
            results.append(d)
        return results

    def recall_recent(self, limit: int = 20) -> List[Dict]:
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM memories WHERE source='Codebuff' ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
            conn.close()
        results = []
        for r in rows:
            d = dict(r)
            for field in ("data", "tags"):
                try:
                    d[field] = json.loads(d[field]) if isinstance(d[field], str) else d[field]
                except (json.JSONDecodeError, TypeError) as exc:
                    _note_failure("direct_memory:L2086", exc)
            results.append(d)
        # xMemory decouple-aggregate tags (additive metadata, fail-open).
        # Inline classifier (no CORE.WORMS.expansion import: that package
        # __init__ is heavy and this is a low-level module — import risk).
        # Hint source of truth duplicated from codebuff_tracker._decouple_rows.
        for d in results:
            try:
                et = str(d.get("event_type", "")).lower()
                d["_xkind"] = ("fact" if ("defin" in et or "claim" in et
                                          or "fact" in et or "decis" in et)
                                 else "update" if ("updat" in et or "edit" in et
                                                   or "fix" in et)
                                 else "detail")
                d["_bank"] = "short_term"
            except Exception as exc:  # never break recall
                _note_failure("direct_memory:recall-decouple", exc)
        return results


# -- Module-level convenience ---------------------------------------

_default_dm: Optional[DirectMemory] = None
_dm_lock = threading.Lock()


def get_dm(session_id: Optional[str] = None,
           merged_session_id: Optional[str] = None) -> DirectMemory:
    """Get or create the default DirectMemory singleton for this process.

    Args:
        session_id: Custom session ID (auto-generated if None).
        merged_session_id: Reuse an existing session for multi-message grouping.
    """
    global _default_dm
    if _default_dm is None:
        with _dm_lock:
            if _default_dm is None:
                _default_dm = DirectMemory(
                    session_id=session_id,
                    merged_session_id=merged_session_id,
                )
    return _default_dm


def record(event_type: str, data: Optional[Dict] = None,
           severity: str = "info", tags: Optional[List[str]] = None) -> int:
    """Quick record using the default DirectMemory instance."""
    return get_dm().record(event_type, data, severity=severity, tags=tags)


def end(summary: Optional[Dict] = None):
    """End the default session."""
    get_dm().end(summary)


# -- Self-test ------------------------------------------------------
def _self_test_body() -> int:
    """Run the recorder self-test. The caller supplies a THROWAWAY database."""
    print(f"DirectMemory v{__version__} — Self-test")
    print(f"  DB: {DB_PATH}")

    dm = DirectMemory()
    print(f"  Session: {dm.session_id}")

    dm.file_read(["/fake/MEMORIES.md", "/fake/ERROR_LOG.md"], "canonical files")
    dm.skill_loaded("azazael-startup")
    dm.skill_loaded("azazael-error-prevention")
    dm.file_create("/fake/new_module.py", "Test creation")
    dm.file_edit("/fake/module.py", "Fix bug", lines_changed=5)
    dm.code_review("Test review", issues_found=1, issues_fixed=1)
    dm.test_run("test_suite", passed=5, failed=0)
    dm.milestone("Self-test complete", {"events": 7})
    dm.end({"status": "ok"})

    count = dm.count()
    print(f"  Events recorded: {count}")
    assert count >= 9, f"Expected >=9 events, got {count}"

    events = dm.list_events()
    for e in events:
        et = e['event_type']
        data = e.get('data', {})
        action = data.get('action', '?') if isinstance(data, dict) else '?'
        print(f"    [{e['id']}] {et}: {action}")

    # Verify cross-session recall
    recent = dm.recall_recent(3)
    print(f"  Recent Codebuff events: {len(recent)}")
    for r in recent:
        print(f"    [{r['id']}] {r['event_type']}")

    print("  DirectMemory self-test PASSED")
    return 0


# -- Command line ----------------------------------------------------
# The documented query surface (MEMORIES.md, the azazael-audit-infrastructure
# skill). Every read command below is built ONLY from the module's
# @staticmethod helpers: DirectMemory.__init__ starts a session and records
# `session_start`, so a read routed through an instance would WRITE. `--db`
# rebinds the module globals those helpers resolve at call time.
#
# `python direct_memory.py` used to run the recorder self-test -- ten synthetic
# events into the PRODUCTION store -- while the documented flags did nothing,
# because they were never parsed. The self-test now needs an explicit
# --self-test and gets a throwaway database; no flag is read-only by default
# only because it happens to change nothing.


def _apply_db_override(path: Optional[str]) -> int:
    """Point the module's DB globals at `path`. 0 on success, else an exit code.

    The target must already exist. sqlite3.connect() would otherwise materialise
    an empty file, so a typo would read as "no data" instead of "wrong path" --
    and a read command would have created a database as a side effect.
    """
    global DB_PATH, TELEMETRY_DB_PATH
    if not path:
        return 0
    target = Path(path).expanduser()
    if not target.exists():
        print(f"error: database not found: {target}", file=sys.stderr)
        return 2
    DB_PATH = target
    TELEMETRY_DB_PATH = target.with_name("telemetry_store.db")
    return 0


def _print_events(events: List[Dict[str, Any]]) -> None:
    """One line per event: id, timestamp, type, severity, action."""
    if not events:
        print("(no matching events)")
        return
    for ev in events:
        data = ev.get("data")
        data = data if isinstance(data, dict) else {}
        action = data.get("action") or data.get("summary") or ""
        print(f"[{ev.get('id')}] {str(ev.get('timestamp') or '')[:19]} "
              f"{ev.get('event_type')} ({ev.get('severity')}) {str(action)[:90]}")


def _cmd_stats(as_json: bool) -> int:
    """Counts, DB size, mapping totals, and the event-type breakdown.

    `summary_stats()`'s totals replace the instance `count()`, which counts only
    the calling session's rows and can only be reached by constructing a
    DirectMemory -- i.e. by writing a session_start to answer a read.
    """
    stats = DirectMemory.summary_stats()
    size = DirectMemory.db_size()
    mappings = DirectMemory.get_mapping_summary()
    if as_json:
        print(json.dumps({"stats": stats, "size": size, "mappings": mappings},
                         indent=2, default=str))
        return 0
    print(f"DB       : {size.get('path')}")
    print(f"Size     : {size.get('db_mb')} MB (with WAL/SHM {size.get('total_mb')} MB)")
    print(f"Events   : {stats.get('total_events')}")
    print(f"Sessions : {stats.get('sessions')} ({stats.get('open_sessions')} open)")
    print(f"Mappings : {mappings.get('total_mappings')}")
    by_type = stats.get("by_type") or {}
    if by_type:
        print("By type  :")
        for name, n in by_type.items():
            print(f"  {int(n):>9}  {name}")
    return 0


def _cmd_context(limit: int, as_json: bool) -> int:
    """The most recent events across every session, newest first.

    Uses the static `filtered_query()` rather than the instance `recall_recent()`
    for the write-avoidance reason in the section note above; the rows returned
    are the same cross-session view, minus a session that answering would create.
    """
    events = DirectMemory.filtered_query(limit=limit)
    if as_json:
        print(json.dumps(events, indent=2, default=str))
        return 0
    print(f"Most recent {len(events)} event(s) across all sessions:")
    _print_events(events)
    return 0


def _cmd_integrity(as_json: bool) -> int:
    """Read-only ledger cross-check. Exit 1 when the ledgers are divergent.

    Deliberately does NOT call cross_reference_ledgers(): that helper LINKS
    unmatched sessions, so it writes rows and would make an integrity check a
    mutation. get_unlinked_report() answers the same question without mutating
    and get_mapping_summary() reports what is already linked; the linker stays a
    separate, explicit call.
    """
    mappings = DirectMemory.get_mapping_summary()
    unlinked = DirectMemory.get_unlinked_report()
    legacy = DirectMemory.deprecation_warnings()
    dm_gap = int(unlinked.get("unlinked_dm_count") or 0)
    aj_gap = int(unlinked.get("unlinked_aj_count") or 0)
    consistent = dm_gap == 0 and aj_gap == 0
    if as_json:
        print(json.dumps({"consistent": consistent,
                          "mappings": mappings,
                          "unlinked": {"dm": dm_gap, "aj": aj_gap},
                          "legacy_event_types": legacy},
                         indent=2, default=str))
        return 0 if consistent else 1
    print(f"Consistent   : {consistent}")
    print(f"Mappings     : {mappings.get('total_mappings')} "
          f"({mappings.get('unique_dm_sessions')} DM / "
          f"{mappings.get('unique_aj_sessions')} audit)")
    print(f"Unlinked     : {dm_gap} DM session(s), {aj_gap} audit file(s)")
    if legacy:
        print("Legacy types :")
        for row in legacy:
            print(f"  {row.get('count')}x {row.get('old')} (use {row.get('new')})")
    if not consistent:
        print("Reconcile with DirectMemory.cross_reference_ledgers() "
              "(that call links the rows; this check does not).")
    return 0 if consistent else 1


def _cmd_gc(max_age_hours: int, as_json: bool) -> int:
    """Close sessions left open. The one command that changes the store."""
    closed = DirectMemory.close_stale_sessions(max_age_hours)
    gc_closed = DirectMemory.session_gc(max_age_hours)
    payload = {"closed_per_message_sessions": closed,
               "closed_sessions": gc_closed,
               "max_age_hours": max_age_hours}
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"Closed {closed} stale per-message session(s) and {gc_closed} "
              f"session(s) open longer than {max_age_hours}h")
    return 0


def _cmd_search(args: argparse.Namespace, as_json: bool) -> int:
    """Structured filter query over recorded events.

    `filtered_query()` has NO free-text parameter: the only textual column with
    a match is `tags`, filtered by LIKE against a quoted element. So this reports
    the filters it actually applied instead of accepting a query string it would
    silently ignore.
    """
    events = DirectMemory.filtered_query(
        limit=args.limit,
        session_id=args.session_id,
        event_type=args.event_type,
        severity=args.severity,
        source=args.source,
        tags=args.tag or None,
        since=args.since,
        until=args.until,
    )
    if as_json:
        print(json.dumps(events, indent=2, default=str))
        return 0
    applied = {k: v for k, v in (
        ("session_id", args.session_id), ("event_type", args.event_type),
        ("severity", args.severity), ("source", args.source),
        ("tags", args.tag), ("since", args.since), ("until", args.until),
    ) if v}
    print(f"{len(events)} event(s)" + (f" matching {applied}" if applied
                                       else " (no filters; newest first)"))
    _print_events(events)
    return 0


def _cmd_banner(as_json: bool) -> int:
    """One-screen summary: volume, open sessions, size, busiest event types."""
    stats = DirectMemory.summary_stats()
    size = DirectMemory.db_size()
    open_sessions = DirectMemory.get_open_sessions_count()
    if as_json:
        print(json.dumps({"events": stats.get("total_events"),
                          "sessions": stats.get("sessions"),
                          "open_sessions": open_sessions,
                          "db_mb": size.get("db_mb"),
                          "top_types": list(stats.get("by_type") or {})[:5]},
                         indent=2, default=str))
        return 0
    top = list(stats.get("by_type") or {})[:3]
    print(f"DirectMemory v{__version__} | {stats.get('total_events')} events | "
          f"{stats.get('sessions')} sessions ({open_sessions} open) | "
          f"{size.get('db_mb')} MB | top: {', '.join(top) or 'n/a'}")
    return 0


def _run_self_test() -> int:
    """Run the recorder self-test against a THROWAWAY database.

    Never the production store. The self-test writes ten synthetic events, and
    pointing it at ~/.azazael/memory_store.db is what made the bare
    `python direct_memory.py` invocation destructive while looking like a report.
    DB_PATH is resolved at import, so the globals are swapped for the duration
    and restored afterwards; the temp directory is removed either way, and the
    instance's atexit handler cannot re-create it because the self-test ends its
    session explicitly (see `_atexit_handler`'s `_ended` guard).
    """
    global DB_PATH, TELEMETRY_DB_PATH
    tmp = Path(tempfile.mkdtemp(prefix="direct_memory_selftest_"))
    saved = (DB_PATH, TELEMETRY_DB_PATH)
    DB_PATH = tmp / "memory_store.db"
    TELEMETRY_DB_PATH = tmp / "telemetry_store.db"
    try:
        return _self_test_body()
    finally:
        DB_PATH, TELEMETRY_DB_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)


# Residue of the old `python direct_memory.py` self-test. The self-test is now
# temp-DB only (`_run_self_test`), so what it wrote into whichever store it was
# pointed at is residue, not history: one `file_create` of `/fake/new_module.py`
# and one `file_edit` of `/fake/module.py` per polluted session. Matching demands
# BOTH the fixture action string AND a `/fake/` path inside `data`, so a real
# file event cannot satisfy it.
_TEST_FIXTURE_SQL = (
    "(event_type = 'file_create' AND data LIKE ? AND data LIKE ?) "
    "OR (event_type = 'file_edit' AND data LIKE ? AND data LIKE ?)"
)
_TEST_FIXTURE_ARGS = (
    "%create: Test creation%", "%/fake/%",
    "%edit: Fix bug%", "%/fake/%",
)


def _cmd_purge_test_fixtures(apply_change: bool, as_json: bool) -> int:
    """List, and optionally delete, rows left by the old self-test.

    Dry-run by default: `--apply` is required to delete, and `--db` selects the
    store. The listing opens the file read-only and never constructs a
    DirectMemory, so it writes nothing. The DELETE names `memories` only --
    `memories_fts` is an external-content table kept current by the
    `memories_ad` AFTER DELETE trigger (see local_memory.py's schema), so the two
    counts stay in sync without a manual FTS delete; the post-purge line prints
    both to make that visible.
    """
    if not DB_PATH.exists():
        print(f"error: database not found: {DB_PATH}", file=sys.stderr)
        return 2
    mode = "rw" if apply_change else "ro"
    conn = sqlite3.connect(f"file:{DB_PATH}?mode={mode}", uri=True, timeout=30)
    try:
        rows = conn.execute(
            "SELECT id, session_id, event_type, timestamp FROM memories "
            f"WHERE {_TEST_FIXTURE_SQL} ORDER BY timestamp",
            _TEST_FIXTURE_ARGS,
        ).fetchall()
        if as_json:
            print(json.dumps(
                {"database": str(DB_PATH), "matched": len(rows),
                 "applied": bool(apply_change),
                 "rows": [dict(zip(("id", "session_id", "event_type",
                                    "timestamp"), r)) for r in rows]},
                indent=2, default=str))
        else:
            for r in rows:
                print(f"[{r[0]}] {str(r[3])[:19]} {r[2]} session={r[1]}")
            if apply_change:
                print(f"Deleting {len(rows)} self-test fixture row(s) from {DB_PATH}")
            else:
                print(f"Would delete {len(rows)} self-test fixture row(s) in "
                      f"{DB_PATH} (dry run; pass --apply to delete)")
        if apply_change and rows:
            conn.executemany("DELETE FROM memories WHERE id = ?",
                             [(r[0],) for r in rows])
            conn.commit()
            if not as_json:
                try:
                    m, f = conn.execute(
                        "SELECT (SELECT COUNT(*) FROM memories), "
                        "(SELECT COUNT(*) FROM memories_fts)").fetchone()
                    print(f"Post-purge: memories={m} memories_fts={f}")
                except sqlite3.Error as exc:      # noqa: BLE001 — count print is informational
                    _note_failure("direct_memory:purge-fts-count", exc)
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    """The CLI parser. Exposed so callers can introspect the documented flags."""
    parser = argparse.ArgumentParser(
        prog="direct_memory.py",
        description="DirectMemory query and maintenance CLI. Read-only unless "
                    "--gc or --self-test or --purge-test-fixtures --apply is given.",
        epilog="Examples: direct_memory.py --stats | direct_memory.py "
               "--context 20 | direct_memory.py --search --event-type file_edit",
    )
    parser.add_argument("--stats", action="store_true",
                        help="counts, DB size, and event-type breakdown")
    parser.add_argument("--context", nargs="?", const=10, type=int, default=None,
                        metavar="N",
                        help="the N most recent events across all sessions (default 10)")
    parser.add_argument("--integrity", action="store_true",
                        help="read-only DM<->audit ledger cross-check; "
                             "exit 1 if sessions are unlinked")
    parser.add_argument("--gc", action="store_true",
                        help="close sessions left open (WRITES)")
    parser.add_argument("--search", action="store_true",
                        help="structured event query (see the filter options)")
    parser.add_argument("--banner", action="store_true",
                        help="one-line summary")
    parser.add_argument("--self-test", dest="self_test", action="store_true",
                        help="run the recorder self-test on a THROWAWAY database")
    parser.add_argument("--purge-test-fixtures", dest="purge_test_fixtures",
                        action="store_true",
                        help="list rows left by the old self-test; with --apply, delete them")
    parser.add_argument("--apply", dest="apply", action="store_true",
                        help="--purge-test-fixtures: actually delete (default is a dry run)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output in every mode")
    parser.add_argument("--db", metavar="PATH",
                        help="target another database file (must already exist)")
    parser.add_argument("--max-age-hours", type=int, default=1, metavar="H",
                        help="--gc: close sessions open longer than H hours (default 1)")
    parser.add_argument("--limit", type=int, default=20, metavar="N",
                        help="--search: maximum rows to return (default 20)")
    parser.add_argument("--session-id", metavar="ID", help="--search: exact session id")
    parser.add_argument("--event-type", metavar="TYPE", help="--search: exact event_type")
    parser.add_argument("--severity", metavar="LEVEL", help="--search: exact severity")
    parser.add_argument("--source", metavar="SRC", help="--search: exact source")
    parser.add_argument("--tag", action="append", metavar="TAG",
                        help="--search: repeatable; matches one JSON tag element")
    parser.add_argument("--since", metavar="ISO", help="--search: timestamp >= ISO")
    parser.add_argument("--until", metavar="ISO", help="--search: timestamp <= ISO")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point. Returns the process exit code; never raises for user input."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.self_test:
        # --self-test deliberately ignores --db: it must never touch a real store.
        return _run_self_test()

    if not any((args.stats, args.context is not None, args.integrity,
                args.gc, args.search, args.banner, args.purge_test_fixtures)):
        # No command: print help and change nothing. This used to run the
        # self-test, so `direct_memory.py --typo` wrote to the live store.
        parser.print_help()
        return 0

    code = _apply_db_override(args.db)
    if code:
        return code

    if args.stats:
        return _cmd_stats(args.json)
    if args.context is not None:
        limit = args.context if args.context > 0 else 10
        return _cmd_context(limit, args.json)
    if args.integrity:
        return _cmd_integrity(args.json)
    if args.gc:
        return _cmd_gc(args.max_age_hours, args.json)
    if args.purge_test_fixtures:
        return _cmd_purge_test_fixtures(args.apply, args.json)
    if args.search:
        return _cmd_search(args, args.json)
    if args.banner:
        return _cmd_banner(args.json)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
