# coding: utf-8
"""
MemoryBus — PAMS-backed inter-agent pub/sub communication bus.

Provides a publish/subscribe messaging layer for the AZAZAEL multi-agent
ecosystem, backed by the PAMS memory server (port 21434 by default).

Features:
- publish(topic, message, metadata) -> stores to PAMS as a classified memory
- subscribe(topic, callback, pattern) -> polls PAMS for new topic messages
- poll(topic, since_id) -> query PAMS for messages since last check
- Wildcard topic matching (e.g., "agent.*.error")
- Thread-safe subscription registry
- Graceful degradation if PAMS is unreachable
- Optional local fallback queue for offline operation

CANONICAL SOURCE — import from here project-wide:
    from azazael.src.CORE.MemoryBus import MemoryBus

Usage:
    bus = MemoryBus()
    bus.publish("agent.status", {"agent_id": "agent-1", "status": "running"})
    bus.subscribe("agent.*", lambda msg: print(f"Got: {msg}"))
    messages = bus.poll("agent.status", since_id=0)
"""

import sys
import os
import threading
import json
import re
import sqlite3
import itertools
import time
from CORE.uuid7 import uuid7
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict, List, Tuple
from collections import defaultdict
from tools.defaults_profile import read_default

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Import CORE utilities ----------------------------------------------------
_azazael_src = str(Path(__file__).resolve().parent.parent)
if _azazael_src not in sys.path:
    sys.path.insert(0, _azazael_src)

try:
    from CORE.freethreading import is_free_threaded, ThreadSafeDict, ThreadSafeList
    HAS_FREETHREADING = True
except ImportError as exc:
    note_failure("MemoryBus:L49", exc)
    HAS_FREETHREADING = False
    ThreadSafeDict: Any = dict  # type: ignore[no-redef]
    ThreadSafeList: Any = list  # type: ignore[no-redef]
    def is_free_threaded() -> bool:
        return hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()

IS_FREE_THREADED = is_free_threaded()

# -- HTTP client for PAMS (requests preferred, urllib fallback) ---------------
try:
    import requests
    HAS_REQUESTS = True
except ImportError as exc:
    note_failure("MemoryBus:L62", exc)
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error

PAMS_URL = os.environ.get("PAMS_URL", "http://127.0.0.1:21434")
PAMS_TIMEOUT = float(os.environ.get("PAMS_TIMEOUT", "2.0"))
#: Promote does server-side classification – seconds, not milliseconds.
#: The 2s check timeout never fit it: every post timed out client-side
#: (server may still have stored it → retry duplicates). 2026-09-28.
PAMS_POST_TIMEOUT = float(os.environ.get("PAMS_POST_TIMEOUT", "30.0"))
#: Minimum seconds between re-checks of a DOWN verdict. `_pams_available` is a
#: startup verdict and `_check_pams()` is the only thing that can revise it, so a
#: bus built while PAMS was down stayed convinced it was down for the life of the
#: bus. The throttle keeps a re-check from becoming a probe storm on a hot publish
#: loop. 2026-10-11 (M-2 residual).
PAMS_RECHECK_SEC = float(os.environ.get("PAMS_RECHECK_SEC", "15.0"))
#: Bound per flush_local_queue call so a 5k backlog drains progressively
#: across nightly runs instead of hanging one task for hours.
FLUSH_MAX_ROWS = int(os.environ.get("PAMS_FLUSH_MAX_ROWS", "250"))
#: Cap on the posted envelope: queued rows average ~41KB (max 31MB) and
#: PAMS embeds/classifies every promote, so full payloads take minutes or
#: time out. PAMS keeps the digest; the full row stays in the local queue
#: table. 2026-09-28.
PAMS_POST_MAX_CHARS = int(os.environ.get("PAMS_POST_MAX_CHARS", "8000"))
#: Flushed rows are IN PAMS already -- delete them after this many days so
#: the queue table cannot grow forever (was 214MB, zero rows ever flushed).
FLUSHED_TTL_DAYS = int(os.environ.get("PAMS_FLUSHED_TTL_DAYS", "7"))

# Read memory config from defaults
MEMORY_PRIMARY = read_default("memory.primary", "pmb")
MEMORY_PMB_WORKSPACE = read_default("memory.pmb_workspace", "personal")
MEMORY_ENGRAM_PROJECT = read_default("memory.engram_project", "azazael")
MEMORY_ENGRAM_ALL_PROJECTS = read_default("memory.engram_all_projects", True)
MEMORY_PAMS_ENABLED = read_default("memory.pams_enabled", True)
MEMORY_PAMS_USER_ID = read_default("memory.pams_user_id", "clint")
MEMORY_PAMS_MODEL_ID = read_default("memory.pams_model_id", "mcp-client")

#: PAMS serves its HTTP API under /api (mcp_server.py: /api/health,
#: /api/memories/promote, ...). The bare paths (/health, /memories) 404,
#: which silently forced every publish into the local fallback queue.
#: 2026-09-28: all client paths below use this prefix.
PAMS_API_PREFIX = "/api"

#: Port discovery: pams-server picks a free port (21434, else 21435, ...)
#: and records it. A hardcoded URL rots the moment it restarts elsewhere.
def _port_file_candidates() -> Tuple[Path, ...]:
    """Every place a running pams-server may record its port, best-first.

    2026-10-10 (M-2): this was a single `G:\\...` path. On a machine without
    the G: archive drive there was NO candidate at all, so discovery fell
    straight through to the hardcoded PAMS_URL and rotted the moment the server
    moved port. The live store is under the user's home; $PAMS_PORT_FILE and
    $PAMS_DB_DIR are the project's own knobs for this directory and win when set.
    The G: path is kept LAST so an archive copy cannot outrank a live one.
    """
    candidates: List[Path] = []
    explicit = (os.environ.get("PAMS_PORT_FILE") or "").strip()
    if explicit:
        candidates.append(Path(explicit))
    db_dir = (os.environ.get("PAMS_DB_DIR") or "").strip()
    if db_dir:
        candidates.append(Path(db_dir) / "mcp_server_port.json")
    candidates.append(Path.home() / ".pams" / "mcp_server_port.json")
    candidates.append(Path.home() / "AppData" / "Local" / "persistent-ai-memory"
                      / "memory_data" / "mcp_server_port.json")
    candidates.append(Path(
        r"G:\source-of-truth\persistent-ai-memory\memory_data\mcp_server_port.json"))
    return tuple(candidates)


_PAMS_PORT_FILES = _port_file_candidates()


def _resolve_pams_url() -> str:
    """Best-effort PAMS base URL: explicit env wins, then the server's own
    port file, then the default. Never raises.

    2026-10-10 (M-2): among several port files the FRESHEST wins, instead of the
    first one in list order / the first one that parses. A stale copy (an
    archive drive left behind by an earlier run) used to pin the bus to a
    long-dead port, which is indistinguishable from PAMS being down.
    """
    env = (os.environ.get("PAMS_URL") or "").strip()
    if env:
        return env.rstrip("/")
    best_mtime: float = -1.0
    best_url = ""
    for pf in _PAMS_PORT_FILES:
        try:
            if not pf.is_file():
                continue
            data = json.loads(pf.read_text(encoding="utf-8"))
            port = int(data.get("active_port") or 0)
            if port > 0:
                candidate = f"http://127.0.0.1:{port}"
            else:
                candidate = str(data.get("http_url") or "").strip().rstrip("/")
            if not candidate:
                continue
            mtime = pf.stat().st_mtime
            if mtime > best_mtime:
                best_mtime, best_url = mtime, candidate
        except Exception as exc:
            note_failure("MemoryBus:L115", exc)
            continue
    if best_url:
        return best_url
    return PAMS_URL.rstrip("/")


_PAMS_API_KEY: str | None = None
_PAMS_API_KEY_LOADED = False


def _pams_api_key() -> str | None:
    """X-API-Key for PAMS write endpoints. Env PAMS_API_KEY wins, else the
    server's own key file (same path mcp_server.py loads). Cached. The
    promote endpoint 403s without it, which used to fail every flush."""
    global _PAMS_API_KEY, _PAMS_API_KEY_LOADED
    if _PAMS_API_KEY_LOADED:
        return _PAMS_API_KEY
    _PAMS_API_KEY_LOADED = True
    env = (os.environ.get("PAMS_API_KEY") or "").strip()
    if env:
        _PAMS_API_KEY = env
        return _PAMS_API_KEY
    try:
        import persistent_ai_memory as _pam
        kf = Path(str(_pam.__file__)).resolve().parent / "keys" / "mcpo_api_key.txt"
        if kf.is_file():
            _PAMS_API_KEY = kf.read_text(encoding="utf-8").strip() or None
    except Exception as exc:
        note_failure("MemoryBus:L141", exc)
    return _PAMS_API_KEY


def _pams_headers() -> dict:
    key = _pams_api_key()
    return {"Content-Type": "application/json",
            **({"X-API-Key": key} if key else {})}


def _truncate_post(text: str, limit: int = 0) -> str:
    """Cap a posted envelope (PAMS embeds every promote). Full payload
    stays in the local queue row; the marker records the original size."""
    lim = limit or PAMS_POST_MAX_CHARS
    if len(text) <= lim:
        return text
    return text[:lim] + '"...[truncated %d->%d for PAMS; full row kept locally]"' % (len(text), lim)


def _promote_id(data: Any) -> Any:
    """Extract the stored-memory id from a promote response. The server
    nests it (memory_id holds the response object again in some builds),
    so unwrap defensively; None when absent (caller treats as unflushed)."""
    if not isinstance(data, dict):
        return data
    mid = data.get("memory_id")
    if isinstance(mid, dict):
        inner = mid.get("memory_id")
        return inner if isinstance(inner, str) else mid
    return mid

# -- F07 fix (June 7 2026): Persistent SQLite fallback queue --------
# When PAMS is unreachable, messages are persisted to memory_store.db
# instead of an ephemeral in-memory list. This prevents absolute data
# loss on process exit during PAMS downtime.
def _get_memorybus_db_path() -> Path:
    """Get the telemetry DB path for the persistent fallback queue.

    2026-09-28 split: the queue lived in memory_store.db, sharing its ONE
    writer lock with training and maintenance. It now lives in
    telemetry_store.db (own lock, own WAL). Single source of truth is
    CORE.direct_memory.TELEMETRY_DB_PATH; this resolver delegates so the
    conftest path-caching contract (module, resolver, global) is unchanged.
    """
    try:
        from CORE.direct_memory import _get_telemetry_db_path
        return _get_telemetry_db_path()
    except Exception as exc:
        note_failure("MemoryBus:L189", exc)
        env_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
        if env_dir:
            base = Path(env_dir)
        else:
            base = Path.home() / ".azazael"
        base.mkdir(parents=True, exist_ok=True)
        return base / "telemetry_store.db"

_MEMORYBUS_DB_PATH = _get_memorybus_db_path()

_MEMORYBUS_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memorybus_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic       TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    source      TEXT NOT NULL DEFAULT 'memorybus',
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    flushed     INTEGER NOT NULL DEFAULT 0,
    -- CD1 schema migration (June 11 2026): Cross-domain correlation fields
    -- See azazael/research/CROSS_DOMAIN.md §CD1 for unified event schema
    event_uuid      TEXT,
    trace_id        TEXT,
    correlation_id  TEXT,
    audit_hash      TEXT,
    schema_version  INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_mbqueue_flushed ON memorybus_queue(flushed, id);
-- idx_mbqueue_correlation + idx_mbqueue_audit created in _migrate_queue_schema()
-- after ALTER TABLE
"""


@dataclass
class BusMessage:
    """A message published on the MemoryBus."""
    message_id: int = 0
    topic: str = ""
    content: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    session_id: str = ""
    source: str = "memorybus"

    def to_dict(self) -> dict:
        return {
            'message_id': self.message_id,
            'topic': self.topic,
            'content': self.content,
            'metadata': self.metadata,
            'timestamp': self.timestamp,
            'session_id': self.session_id,
            'source': self.source,
        }


class HiddenStateCarrier:
    """Shared hidden-state carrier: many reasoners, one store (EP442).

    The store exists (LocalMemoryStore / the bus itself); the carrier did
    not. A carrier holds one named state vector per reasoner plus a shared
    read view (mean). Pure in-memory, thread-safe, no I/O. Wiring reasoners
    to query through it (rather than each holding private state) is the
    consumer step — this is the carrier they query.
    """

    def __init__(self):
        import threading as _th
        self._states: dict = {}
        self._lock = _th.Lock()

    def put(self, reasoner: str, state: dict) -> bool:
        """Store one reasoner's hidden state. Returns True if stored."""
        if not str(reasoner or "").strip() or not isinstance(state, dict):
            return False
        with self._lock:
            self._states[str(reasoner)] = dict(state)
        return True

    def shared_view(self) -> dict:
        """Mean view over all held states (numeric fields only)."""
        with self._lock:
            states = list(self._states.values())
        if not states:
            return {"reasoners": 0, "mean": {}}
        keys = {k for s in states for k, v in s.items()
                if isinstance(v, (int, float))}
        mean = {}
        for k in sorted(keys):
            vals = [s[k] for s in states if isinstance(s.get(k), (int, float))]
            mean[k] = round(sum(vals) / len(vals), 6) if vals else 0.0
        return {"reasoners": len(states), "mean": mean}


class MemoryBus:
    """
    PAMS-backed publish/subscribe bus for inter-agent communication.

    Uses the PAMS REST API to store published messages as classified memories
    (category="memorybus", tags=[topic]) and polls for new messages by topic.

    Subscriptions use glob-style wildcard patterns:
        "agent.*.error" matches "agent.runtime.error", "agent.network.error"
        "*.status" matches "agent.status", "pool.status"
        "agent.>" matches "agent.status", "agent.error.detail" (prefix wildcard)
    """

    VERSION = "1.1.0"

    def __init__(self, pams_url: Optional[str] = None, session_id: Optional[str] = None,
                 poll_interval_sec: float = 1.0, use_local_fallback: bool = True):
        self.pams_url = (pams_url or _resolve_pams_url()).rstrip('/')
        self.session_id = session_id or uuid7().hex[:8]
        self.poll_interval_sec = poll_interval_sec
        self.use_local_fallback = use_local_fallback

        # -- Subscription registry (thread-safe) --------------------------
        # {pattern: [(callback, compiled_regex), ...]}
        self._subscriptions: Dict[str, List[Tuple[Callable, re.Pattern]]] = defaultdict(list)
        self._sub_lock = threading.Lock()

        # -- Poll state (per-topic last seen ID) ---------------------------
        self._last_seen: Dict[str, int] = ThreadSafeDict()
        self._poll_lock = threading.Lock()

        # -- Local fallback queue (when PAMS is down) ----------------------
        # F07 fix (June 7 2026): In-memory queue is now backed by persistent
        # SQLite storage. Messages are written to memory_store.db when PAMS
        # is unreachable, preventing absolute data loss on process exit.
        self._local_queue: List[BusMessage] = []
        self._local_queue_lock = threading.Lock()
        self._persisted_ids: set = set()  # Track which messages were already persisted
        self._persistent_queue_initialized = False
        # P0 fix (June 11 2026): Atomic message_id counter using itertools.count
        self._message_counter = itertools.count(1)
        # CD1 correlation (June 11 2026): Cross-domain correlation ID
        self._correlation_id = uuid7().hex
        self._init_persistent_queue()

        # -- Stats (thread-safe: backing dict + dedicated lock) ----------
        self._stats_data: Dict[str, int] = {
            'published': 0,
            'delivered': 0,
            'polls': 0,
            'pams_errors': 0,
            'local_fallback_used': 0,
            'persistent_fallback_used': 0,
            'persistent_flushed': 0,
            'subscriptions': 0,
        }
        self._stats = ThreadSafeDict()  # Maintain backward compat for dynamic keys
        self._stats_lock = threading.Lock()

        # -- Check PAMS connectivity --------------------------------------
        self._pams_available = self._check_pams()
        #: Earliest monotonic time a DOWN verdict may be re-checked; see
        #: _ensure_pams_available(). 0.0 means "re-check allowed immediately".
        self._pams_next_recheck = 0.0

    def _init_persistent_queue(self):
        """Initialize the persistent SQLite queue for message durability."""
        if self._persistent_queue_initialized:
            return
        try:
            conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_MEMORYBUS_QUEUE_SCHEMA)
            conn.commit()
            # CD1 schema migration (June 11 2026): Add cross-domain columns
            self._migrate_queue_schema(conn)
            conn.close()
            self._persistent_queue_initialized = True
        except Exception as exc:
            note_failure("MemoryBus:L322", exc)

    def _migrate_queue_schema(self, conn: sqlite3.Connection):
        """Run additive schema migrations for existing queue tables.

        CD1 (June 11 2026): Adds event_uuid and correlation_id columns
        to memorybus_queue if they don't exist.
        """
        existing_cols = set(
            r[1] for r in conn.execute("PRAGMA table_info('memorybus_queue')").fetchall()
        )
        migrations = [
            ("event_uuid", "TEXT"),
            ("trace_id", "TEXT"),
            ("correlation_id", "TEXT"),
            # CD1 Phase 2 (Jun 2026): Integrity + version tracking (P1 fix)
            ("audit_hash", "TEXT"),
            ("schema_version", "INTEGER"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing_cols:
                conn.execute(
                    f"ALTER TABLE memorybus_queue ADD COLUMN {col_name} {col_type}"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mbqueue_correlation "
            "ON memorybus_queue(correlation_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mbqueue_trace_id "
            "ON memorybus_queue(trace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mbqueue_audit "
            "ON memorybus_queue(audit_hash)"
        )
        # CD1 Phase 2 (June 11 2026): Backfill existing NULL rows with UUID7
        # + audit_hash + schema_version. Batched in chunks of 1000.
        while True:
            batch = conn.execute(
                "SELECT id FROM memorybus_queue WHERE event_uuid IS NULL LIMIT 1000"
            ).fetchall()
            if not batch:
                break
            for (row_id,) in batch:
                ev_uuid = uuid7().hex
                conn.execute(
                    "UPDATE memorybus_queue SET event_uuid = ?, audit_hash = ?,"
                    " schema_version = 1 WHERE id = ?",
                    (ev_uuid, ev_uuid, row_id)
                )
            conn.commit()

    def _persist_to_queue(self, topic: str, content: Any, metadata: Dict,
                          source: str, event_uuid: str = "") -> int:
        """Persist a message to the SQLite fallback queue. Returns row ID."""
        if not self._persistent_queue_initialized:
            return 0
        try:
            conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
            conn.execute("PRAGMA journal_mode=WAL")
            corr_id = self._correlation_id
            cursor = conn.execute(
                "INSERT INTO memorybus_queue(topic,content,metadata,source,session_id,timestamp,"
                "event_uuid,trace_id,correlation_id) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (topic, json.dumps(content, default=str),
                 json.dumps(metadata, default=str), source,
                 self.session_id, datetime.now().isoformat(),
                 event_uuid or uuid7().hex, corr_id, corr_id)
            )
            row_id = cursor.lastrowid or 0
            conn.commit()
            conn.close()
            return row_id
        except Exception as exc:
            note_failure("MemoryBus:L398", exc)
            self._increment('pams_errors')
            return 0

    # -- Public API ------------------------------------------------------------

    def publish(self, topic: str, content: Any, metadata: Optional[Dict[str, Any]] = None,
                source: str = "memorybus") -> Optional[int]:
        """
        Publish a message to a topic.

        Args:
            topic: The topic string (e.g., "agent.status", "pool.task.complete").
            content: The message payload (dict, string, or any JSON-serializable).
            metadata: Optional additional metadata.
            source: Identifier for the publishing component.

        Returns:
            Message ID from PAMS if successful, None if fallback-only.
        """
        metadata = metadata or {}
        metadata['topic'] = topic
        metadata['source'] = source

        # Serialize content
        if isinstance(content, (dict, list, str, int, float, bool)):
            payload = content
        else:
            payload = str(content)

        self._increment('published')

        if self._ensure_pams_available():
            try:
                msg_id = self._post_to_pams(topic, payload, metadata, source)
                if msg_id is not None:
                    with self._poll_lock:
                        if isinstance(msg_id, int):
                            self._last_seen[topic] = max(
                                self._last_seen.get(topic, 0), msg_id
                            )
                    if self.use_local_fallback:
                        # Delivery copy: PAMS is long-term-memory only (no
                        # list/query endpoint), so cross-instance delivery
                        # rides the shared SQLite queue. Best-effort; the
                        # PAMS post above already succeeded.
                        self._persist_to_queue(topic, payload, metadata,
                                               source, uuid7().hex)
                    return msg_id
            except Exception as exc:
                note_failure("MemoryBus:L439", exc)
                self._increment('pams_errors')

        # -- Fallback: store locally + persist to SQLite -----------------
        if self.use_local_fallback:
            self._increment('local_fallback_used')
            # CD1 correlation (June 11 2026): UUID7 for cross-ledger linking
            ev_uuid = uuid7().hex
            msg = BusMessage(
                message_id=next(self._message_counter),
                topic=topic,
                content=payload,
                metadata=metadata,
                timestamp=datetime.now().isoformat(),
                session_id=self.session_id,
                source=source,
            )
            with self._local_queue_lock:
                self._local_queue.append(msg)
                if len(self._local_queue) > 1000:
                    self._local_queue = self._local_queue[-1000:]
            # F07 fix: Persist to SQLite for durability
            pid = self._persist_to_queue(topic, payload, metadata, source, ev_uuid)
            if pid:
                self._increment('persistent_fallback_used')
                self._persisted_ids.add(msg.message_id)
            return msg.message_id

        return None

    def subscribe(self, pattern: str, callback: Callable[[BusMessage], None]) -> int:
        """
        Subscribe to messages matching a topic pattern.

        Patterns use glob-style wildcards:
            "*" matches a single topic segment
            ">" matches one or more trailing segments (prefix wildcard)

        Examples:
            "agent.status" — exact match
            "agent.*.error" — single wildcard
            "pool.>" — prefix wildcard (matches pool.task, pool.status, etc.)

        Args:
            pattern: Topic pattern with wildcards.
            callback: Called with BusMessage when a matching message is published.

        Returns:
            Number of active subscriptions for this pattern.
        """
        compiled = self._compile_pattern(pattern)

        with self._sub_lock:
            self._subscriptions[pattern].append((callback, compiled))
            count = len(self._subscriptions[pattern])
            # Counted while _sub_lock is held: this previously summed the shared dict
            # AFTER releasing the lock, so a concurrent subscribe/unsubscribe could
            # mutate it mid-iteration (wrong total, or RuntimeError under free-threading).
            total = sum(len(subs) for subs in self._subscriptions.values())

        self._set_subscription_count(total)
        return count

    def _set_subscription_count(self, total: int) -> None:
        """Publish the live subscriber count into the dict that get_stats() reads.

        subscribe() and unsubscribe() used to write 'subscriptions' into self._stats,
        the backward-compat dict for dynamic keys, while get_stats() reads the
        canonical self._stats_data snapshot. Nothing ever wrote _stats_data, so
        get_stats()['subscriptions'] reported its initial 0 forever regardless of how
        many subscribers existed -- a silent false zero for every monitor or health
        check that reads it. Both dicts are updated here so external readers of the
        legacy ._stats attribute keep working.
        """
        with self._stats_lock:
            self._stats_data['subscriptions'] = total
        self._stats['subscriptions'] = total

    def unsubscribe(self, pattern: str, callback: Optional[Callable] = None) -> int:
        """
        Unsubscribe from a pattern.

        Args:
            pattern: The subscription pattern.
            callback: If provided, only remove this specific callback.
                      If None, remove all callbacks for this pattern.

        Returns:
            Number of remaining subscriptions for this pattern.
        """
        with self._sub_lock:
            if pattern not in self._subscriptions:
                return 0

            if callback is None:
                del self._subscriptions[pattern]
                count = 0
            else:
                self._subscriptions[pattern] = [
                    (cb, regex) for cb, regex in self._subscriptions[pattern]
                    if cb is not callback
                ]
                count = len(self._subscriptions[pattern])
                if not self._subscriptions.get(pattern):
                    del self._subscriptions[pattern]

            total = sum(len(subs) for subs in self._subscriptions.values())

        self._set_subscription_count(total)
        return count

    def poll(self, topic: Optional[str] = None, since_id: int = 0,
             limit: int = 20) -> List[BusMessage]:
        """
        Poll PAMS for messages since a given ID, optionally filtered by topic.

        Args:
            topic: Optional topic filter.
            since_id: Only return messages with ID > since_id.
            limit: Max messages to return.

        Returns:
            List of BusMessage objects.
        """
        self._increment('polls')

        messages = []

        # -- Check local fallback queue first -----------------------------
        if self.use_local_fallback:
            with self._local_queue_lock:
                local_msgs = [
                    m for m in self._local_queue
                    if m.message_id > since_id
                    and (topic is None or self._matches_topic(topic, m.topic))
                ]
                messages.extend(local_msgs)

        # -- Query PAMS ---------------------------------------------------
        if self._ensure_pams_available():
            try:
                pams_msgs = self._query_pams(topic=topic, since_id=since_id, limit=limit)
                if pams_msgs:
                    messages.extend(pams_msgs)
            except Exception as exc:
                note_failure("MemoryBus:L583", exc)
                self._increment('pams_errors')

        # Deduplicate by message_id
        seen = set()
        unique = []
        for m in sorted(messages, key=lambda x: x.message_id):
            if m.message_id not in seen:
                seen.add(m.message_id)
                unique.append(m)

        # Update last_seen
        if unique:
            with self._poll_lock:
                for m in unique:
                    self._last_seen[m.topic] = max(
                        self._last_seen.get(m.topic, 0), m.message_id
                    )

        return unique

    def poll_and_deliver(self, topic: Optional[str] = None) -> int:
        """
        Poll for new messages and deliver to matching subscribers.

        Args:
            topic: Optional topic filter (if None, polls all topics).

        Returns:
            Number of messages delivered to subscribers.
        """
        # Determine since_id per topic
        with self._poll_lock:
            if topic and topic in self._last_seen:
                since_id = self._last_seen[topic]
            else:
                since_id = 0

        messages = self.poll(topic=topic, since_id=since_id)

        delivered = 0
        for msg in messages:
            count = self._deliver_to_subscribers(msg)
            delivered += count

        return delivered

    def get_last_seen(self, topic: str) -> int:
        """Get the last seen message ID for a topic."""
        return self._last_seen.get(topic, 0)

    def get_stats(self) -> dict:
        """Get bus statistics. Thread-safe atomic snapshot."""
        pq_size = 0
        try:
            if self._persistent_queue_initialized:
                conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
                pq_size = conn.execute("SELECT COUNT(*) FROM memorybus_queue WHERE flushed=0").fetchone()[0]
                conn.close()
        except Exception as exc:
            note_failure("MemoryBus:L642", exc)

        with self._stats_lock:
            stats_snapshot = dict(self._stats_data)

        with self._local_queue_lock:
            lq_size = len(self._local_queue)

        with self._poll_lock:
            topics_count = len(self._last_seen)

        return {
            'version': self.VERSION,
            'session_id': self.session_id,
            'pams_url': self.pams_url,
            'pams_available': self._pams_available,
            'free_threaded': IS_FREE_THREADED,
            'subscriptions': stats_snapshot.get('subscriptions', 0),
            'published': stats_snapshot.get('published', 0),
            'delivered': stats_snapshot.get('delivered', 0),
            'polls': stats_snapshot.get('polls', 0),
            'pams_errors': stats_snapshot.get('pams_errors', 0),
            'local_fallback_used': stats_snapshot.get('local_fallback_used', 0),
            'persistent_fallback_used': stats_snapshot.get('persistent_fallback_used', 0),
            'persistent_flushed': stats_snapshot.get('persistent_flushed', 0),
            'local_queue_size': lq_size,
            'persistent_queue_size': pq_size,
            'topics_tracked': topics_count,
        }

    def flush_local_queue(self, max_rows: int = 0) -> int:
        """
        Attempt to flush the local + persistent fallback queues to PAMS.

        F07 fix: Also flushes persisted SQLite messages. 2026-09-28: bounded
        (FLUSH_MAX_ROWS, default 250) so a multi-thousand backlog drains
        progressively across runs instead of hanging one caller; pass
        max_rows=0 for the default, <0 for unbounded.
        """
        if not self._ensure_pams_available():
            return 0
        budget = FLUSH_MAX_ROWS if max_rows == 0 else max_rows

        flushed = 0
        # Flush in-memory queue first
        with self._local_queue_lock:
            remaining = []
            for msg in self._local_queue:
                if budget is not None and budget >= 0 and flushed >= budget:
                    remaining.append(msg)
                    continue
                try:
                    mid = self._post_to_pams(
                        msg.topic, msg.content, msg.metadata, msg.source
                    )
                    if mid is not None:
                        flushed += 1
                    else:
                        remaining.append(msg)
                except Exception as exc:
                    note_failure("MemoryBus:L702", exc)
                    remaining.append(msg)
            self._local_queue = remaining

        # Flush persistent SQLite queue (batched updates for efficiency)
        if self._persistent_queue_initialized:
            try:
                conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
                rows = conn.execute(
                    "SELECT id, topic, content, metadata, source FROM memorybus_queue WHERE flushed=0 ORDER BY id"
                ).fetchall()
                conn.close()
                flushed_ids = []
                for row in rows:
                    if budget is not None and budget >= 0 and flushed >= budget:
                        break
                    try:
                        row_content = json.loads(row[2]) if isinstance(row[2], str) else row[2]
                        row_metadata = json.loads(row[3]) if isinstance(row[3], str) else row[3]
                        mid = self._post_to_pams(row[1], row_content, row_metadata, row[4])
                        if mid is not None:
                            flushed_ids.append(row[0])
                            flushed += 1
                            self._increment('persistent_flushed')
                    except Exception as exc:
                        note_failure("MemoryBus:L726", exc)
                # Batch update all flushed messages in one query
                if flushed_ids:
                    conn2 = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
                    placeholders = ','.join('?' * len(flushed_ids))
                    conn2.execute(
                        f"UPDATE memorybus_queue SET flushed=1 WHERE id IN ({placeholders})",
                        flushed_ids
                    )
                    conn2.commit()
                    conn2.close()
            except Exception as exc:
                note_failure("MemoryBus:L738", exc)

        # Retire flushed rows: they live in PAMS now. Without this the queue
        # table grows forever (was 214MB, zero rows ever flushed). Best
        # effort, never fails the flush count above. Date-part compare:
        # queue timestamps are ISO with 'T', cutoff is space-separated --
        # comparing full strings mis-orders same-day rows.
        try:
            conn3 = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
            day_cut = (datetime.now(timezone.utc)
                       - timedelta(days=FLUSHED_TTL_DAYS)).strftime("%Y-%m-%d")
            conn3.execute(
                "DELETE FROM memorybus_queue WHERE flushed=1 AND "
                "substr(timestamp, 1, 10) < ?",
                (day_cut,))
            conn3.commit()
            conn3.close()
        except Exception as exc:
            note_failure("MemoryBus:L756", exc)

        return flushed

    # -- Internal --------------------------------------------------------------

    def refresh_pams_url(self) -> bool:
        """Re-discover the PAMS port; True when the resolved URL changed.

        2026-10-10 (M-2): the URL was resolved once in __init__ and cached for
        the life of the bus. The server picks a free port on every restart, so a
        long-lived bus stayed pointed at the dead port and every publish fell
        into the local queue -- silently, because fail-open is the design here.
        Never raises.
        """
        try:
            resolved = _resolve_pams_url()
        except Exception as exc:
            note_failure("MemoryBus:refresh-pams-url", exc)
            return False
        if resolved and resolved != self.pams_url:
            self.pams_url = resolved
            return True
        return False

    def _pams_probe(self) -> bool:
        """One reachability attempt against the CURRENT self.pams_url."""
        try:
            if HAS_REQUESTS:
                resp = requests.get(f"{self.pams_url}{PAMS_API_PREFIX}/health", timeout=PAMS_TIMEOUT)
                return resp.status_code == 200
            else:
                req = urllib.request.Request(f"{self.pams_url}{PAMS_API_PREFIX}/health")
                with urllib.request.urlopen(req, timeout=PAMS_TIMEOUT) as resp:
                    return resp.status == 200
        except Exception as exc:
            note_failure("MemoryBus:L773", exc)
            return False

    def _check_pams(self) -> bool:
        """Check if PAMS server is reachable (GET /api/health).

        2026-10-10 (M-2): a failed check re-resolves the port and retries ONCE.
        Bounded and one-shot by construction: the retry happens only when the
        resolved URL actually changed, so it cannot loop.
        """
        if self._pams_probe():
            return True
        if self.refresh_pams_url():
            return self._pams_probe()
        return False

    def _ensure_pams_available(self) -> bool:
        """Current PAMS reachability, re-checking a stale DOWN verdict.

        2026-10-11 (M-2 residual). `_pams_available` was assigned once in
        `__init__` (`self._pams_available = self._check_pams()`), and `__init__` was
        the ONLY caller of `_check_pams()` -- so `refresh_pams_url()`, which
        `_check_pams()` drives, could never fire after construction. The 2026-10-10
        M-2 repair built the refresh mechanism and left it UNREACHABLE: a fix that
        cannot fire, which is the defect class this sweep exists to remove.

        Two consequences were live on any bus constructed while PAMS was down:
        `publish()` and `query()` took the fallback path forever, and `flush()`
        returned 0 forever, so the local queue never drained -- silently, because
        fail-open is this module's design.

        Bounded by construction: re-checks only when the cached verdict is False, so
        the healthy path costs nothing; the throttle limits a hot publish loop to one
        probe per `PAMS_RECHECK_SEC`; and `_check_pams()` is itself bounded (one
        re-resolve plus one retry, and only when the resolved URL actually changed),
        so this cannot loop. Never raises.
        """
        if self._pams_available:
            return True
        now = time.monotonic()
        if now < self._pams_next_recheck:
            return False
        self._pams_next_recheck = now + PAMS_RECHECK_SEC
        try:
            self._pams_available = self._check_pams()
        except Exception as exc:                          # never break the host
            note_failure("MemoryBus:ensure-pams-available", exc)
            self._pams_available = False
        return self._pams_available

    def _post_to_pams(self, topic: str, content: Any,
                        metadata: Dict[str, Any], source: str) -> Optional[Any]:
        """Post a message to PAMS as a memory. Returns message ID or None.

        2026-09-28: the old target (POST /memories) never existed on the
        server (404 always) so every publish silently fell back to the local
        queue. The         server ingests via POST /api/memories/promote, which
        stores {content, tags, ...} to long-term memory and returns
        {status, memory_id, ...}. The bus envelope is embedded in content
        (as before) plus topic tags, so nothing downstream changes shape.

        Memory unity: this is the curated-promotion FEEDER into PAMS
        long-term memory. Hot event telemetry belongs to LocalMemoryStore
        (the canonical store trainers and recall read); see PamsRecorder.
        """
        body = {
            "content": _truncate_post(json.dumps({
                "topic": topic,
                "content": content,
                "metadata": metadata,
                "source": source,
                "timestamp": datetime.now().isoformat(),
            })),
            "tags": [topic] + self._topic_segments(topic),
            "memory_bank": "General",
        }

        if HAS_REQUESTS:
            _api_key = _pams_api_key()
            headers: dict[str, str] = {"X-API-Key": _api_key} if _api_key else {}
            resp = requests.post(
                f"{self.pams_url}{PAMS_API_PREFIX}/memories/promote",
                json=body,
                headers=headers or None,
                timeout=PAMS_POST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                return _promote_id(data)
        else:
            data_bytes = json.dumps(body).encode('utf-8')
            req = urllib.request.Request(
                f"{self.pams_url}{PAMS_API_PREFIX}/memories/promote",
                data=data_bytes,
                headers=_pams_headers(),
            )
            with urllib.request.urlopen(req, timeout=PAMS_POST_TIMEOUT) as resp:
                data = json.loads(resp.read())
                return _promote_id(data)

        return None

    def _query_pams(self, topic: Optional[str] = None, since_id: int = 0,
                    limit: int = 20) -> List[BusMessage]:
        """Query PAMS for memorybus memories. Returns list of BusMessage.

        2026-09-28: the server exposes no list/query endpoint (only POST
        /api/memories/promote, DELETE /api/memories/cleanup), so there is
        nothing to call -- returns []. Cross-process delivery therefore
        happens at flush time (persist) rather than poll time; the urllib
        branch is guarded so a 404 can never raise out of a poll loop.
        """
        return []

    def _deliver_to_subscribers(self, msg: BusMessage) -> int:
        """Deliver a message to all matching subscribers. Returns delivery count."""
        count = 0
        with self._sub_lock:
            # Snapshot subscriptions to avoid holding lock during callbacks
            all_subs = [
                (cb, regex) for subs in self._subscriptions.values()
                for cb, regex in subs
            ]

        for callback, regex in all_subs:
            try:
                if regex.search(msg.topic):
                    callback(msg)
                    count += 1
                    self._increment('delivered')
            except Exception as exc:
                # Don't let one bad callback crash the bus
                note_failure("MemoryBus:L851", exc)

        return count

    def close(self) -> None:
        """Flush local + persistent queues and clean up resources.

        F07 fix: In-memory messages are persisted to SQLite before clearing,
        preventing absolute data loss on process exit.
        """
        # Persist any remaining UNPERSISTED in-memory messages to SQLite
        if self._local_queue and self._persistent_queue_initialized:
            with self._local_queue_lock:
                for msg in self._local_queue:
                    if msg.message_id not in self._persisted_ids:
                        self._persist_to_queue(msg.topic, msg.content, msg.metadata, msg.source)

        if self._local_queue:
            self.flush_local_queue()
        self._local_queue.clear()
        self._subscriptions.clear()

        # WAL checkpoint on the persistent queue DB
        if self._persistent_queue_initialized:
            try:
                conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()
            except Exception as exc:
                note_failure("MemoryBus:L881", exc)

    @staticmethod
    def _matches_topic(pattern: str, topic: str) -> bool:
        """Check if a topic string matches a pattern."""
        regex = MemoryBus._compile_pattern(pattern)
        return bool(regex.search(topic))

    @staticmethod
    def _compile_pattern(pattern: str) -> re.Pattern:
        """
        Compile a glob-style topic pattern to a regex.

        Rules:
            * matches exactly one segment (between dots or at ends)
            > matches one or more trailing segments
            literal text matches exactly
        """
        # Escape regex special chars except * and >
        # Note: re.escape does NOT escape > (it's not a regex metachar),
        # so we must replace the literal '>' before it gets escaped.
        # Strategy: replace wildcards first, then escape the rest.
        
        # Replace > with a sentinel BEFORE escaping (since re.escape won't touch >)
        SENTINEL = '\x00WILDCARD_GT\x00'
        pattern = pattern.replace('>', SENTINEL)
        
        escaped = re.escape(pattern)
        
        # Restore sentinel to regex wildcard
        escaped = escaped.replace(SENTINEL, r'.+')
        
        # Replace escaped * (re.escape turns * into \*) with single-segment wildcard
        escaped = escaped.replace(r'\*', r'[^.]+')

        return re.compile(f'^{escaped}$')

    @staticmethod
    def _topic_segments(topic: str) -> List[str]:
        """Split a dotted topic into meaningful prefix segments as PAMS tags.
        
        Skips single-segment tags (too broad) and adds 'bus.' namespace prefix.
        Example: 'agent.status.error' -> ['bus.agent.status', 'bus.agent.status.error']
        """
        parts = topic.split('.')
        segments = []
        for i in range(2, len(parts) + 1):  # Start from 2+ segments to avoid single-segment
            segments.append('bus.' + '.'.join(parts[:i]))
        return segments

    def _increment(self, key: str, delta: int = 1) -> None:
        """Thread-safe stat increment.

        Uses dedicated _stats_lock to protect compound read-modify-write.
        Under 3.14t free-threading, ThreadSafeDict alone is insufficient
        because get() and __setitem__ are separate operations.
        """
        with self._stats_lock:
            current = self._stats_data.get(key, 0)
            self._stats_data[key] = current + delta

    def __repr__(self) -> str:
        stats = self.get_stats()
        return (f"MemoryBus(session={self.session_id}, "
                f"pams={'UP' if self._pams_available else 'DOWN'}, "
                f"subs={stats['subscriptions']}, "
                f"pub={stats['published']}, "
                f"del={stats['delivered']})")


# -- Self-test -----------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 65)
    print("  MemoryBus - Self-test")
    print("=" * 65)
    print(f"  Free-threaded: {IS_FREE_THREADED}")
    print(f"  PAMS URL: {PAMS_URL}")

    bus = MemoryBus()
    print(f"  PAMS available: {bus._pams_available}")

    # -- Test 1: Pattern compilation --------------------------------------
    print("\n  [1] Pattern matching:")
    patterns = {
        "agent.status": ["agent.status", "agent.status.detail", "pool.status"],
        "agent.*.error": ["agent.runtime.error", "agent.network.error", "agent.error"],
        "pool.>": ["pool.task", "pool.task.complete", "pool", "agent.task"],
        "*.status": ["agent.status", "pool.status", "agent.status.detail"],
    }
    for pattern, test_topics in patterns.items():
        matches = [t for t in test_topics if bus._matches_topic(pattern, t)]
        print(f"      '{pattern}' matches: {matches}")

    # -- Test 2: Publish (local fallback) ---------------------------------
    print("\n  [2] Publishing messages (local fallback)...")
    mid1 = bus.publish("agent.status", {"status": "running", "agent_id": "test-1"})
    mid2 = bus.publish("agent.error", {"error": "test error", "agent_id": "test-1"})
    mid3 = bus.publish("pool.task", {"task_id": "task-42", "action": "infer"})
    print(f"      Published 3 messages: IDs = {mid1}, {mid2}, {mid3}")

    # -- Test 3: Subscribe and deliver ------------------------------------
    print("\n  [3] Testing subscriptions...")
    received: Any = ThreadSafeList() if HAS_FREETHREADING else []

    def agent_handler(msg: BusMessage) -> None:
        if HAS_FREETHREADING:
            received.append(msg.to_dict())
        else:
            received.append(msg.to_dict())
        print(f"      [SUBSCRIBER] Received: {msg.topic} -> {str(msg.content)[:80]}")

    bus.subscribe("agent.*", agent_handler)
    bus.subscribe("pool.>", agent_handler)
    print(f"      Subscriptions: {bus.get_stats()['subscriptions']}")

    delivered = bus.poll_and_deliver()
    print(f"      Delivered: {delivered} messages to subscribers")

    # -- Test 4: Poll without delivery ------------------------------------
    print("\n  [4] Polling for agent messages...")
    msgs = bus.poll(topic="agent.status", since_id=0)
    print(f"      Found {len(msgs)} agent.status messages")
    for m in msgs[:3]:
        print(f"        [{m.message_id}] {m.topic}: {str(m.content)[:60]}")

    # -- Test 5: Stats ----------------------------------------------------
    print("\n  [5] Bus stats:")
    stats = bus.get_stats()
    for k, v in stats.items():
        print(f"      {k}: {v}")

    # -- Test 6: Unsubscribe ----------------------------------------------
    print("\n  [6] Unsubscribing...")
    remaining = bus.unsubscribe("agent.*", agent_handler)
    print(f"      agent.* remaining subs: {remaining}")
    stats = bus.get_stats()
    print(f"      Total subscriptions: {stats['subscriptions']}")

    print("\n" + "=" * 65)
    print("  MemoryBus self-test complete.")
    print("=" * 65)
