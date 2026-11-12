# coding: utf-8
"""
PamsRecorder — Universal PAMS memory recording for all Azazael components.

CANONICAL SOURCE — import from here project-wide:
    from azazael.src.CORE.PamsRecorder import PamsRecorder
    rec = PamsRecorder()
    rec.record_event("event_type", {"key": "value"})

Now with LocalMemoryStore fallback for offline recording + recall.
When PAMS server is unreachable, automatically uses SQLite-backed
local memory with full query/recall/search capability.

Durable event recorder: keeps every event in the local SQLite store that
trainers and recall actually read. (2026-10-10: the PAMS HTTP post it used
to try first targeted a route the server never had; it now records locally
and returns True for kept, False for buffer-only. Curated promotion to PAMS
long-term memory belongs to MemoryBus._post_to_pams.)

Original location (deprecated): azazael/src/UTILITIES/PamsRecorder.py
"""

import os
import sys
import json
import threading
from CORE.uuid7 import uuid7
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Root resolver (2026-10-11). This module used to fall back to
#: `Path.home() / "azazael"` -- a DIFFERENT, stale checkout from this repo -- and
#: `__init__` then MKDIRs `<that>/data/` for the JSON fallback buffer, so with
#: AZAZAEL_ROOT unset the buffer was written into the wrong tree. The resolver
#: lives in CORE.paths so the decision is made once instead of per module:
#: AZAZAEL_ROOT first, never raises, falls back to the tree it ships in.
try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # never break the host
    note_failure("PamsRecorder:core-paths-import", exc)

    def _get_project_root_safe():                 # CORE unreachable: own tree
        return Path(__file__).resolve().parents[2]

_AZAZAEL_ROOT = os.environ.get(
    "AZAZAEL_ROOT",
    os.environ.get("Azazael_ROOT", str(_get_project_root_safe())),
)

# Try sqlite3 for direct DB fallback
try:
    import sqlite3
    SQLITE_OK = True
except ImportError as exc:
    note_failure("PamsRecorder:L44", exc)
    SQLITE_OK = False

# F05 fix (June 7 2026): Import LocalMemoryStore at module level to avoid
# double-checked locking deadlock under Python 3.14t free-threading.
# Previously this was lazy-loaded inside _ensure_local_store(), which
# created a circular import chain deadlock window.
try:
    from CORE.local_memory import LocalMemoryStore
    LOCAL_MEMORY_OK = True
except ImportError as exc:
    note_failure("PamsRecorder:L54", exc)
    LOCAL_MEMORY_OK = False
    LocalMemoryStore = None  # type: ignore

PAMS_URL = os.environ.get("PAMS_URL", "http://127.0.0.1:21434")
#: M-2 (2026-10-10): the default used to resolve INSIDE the installed
#: interpreter's tree and was pinned to Python314 --
#: `~/AppData/Local/Programs/Python/Python314/Lib/site-packages/memory_data`.
#: That is stale twice over: it moves with the interpreter version, and it is a
#: package directory rather than a store. $PAMS_DB_DIR still wins when set; the
#: fallback is now the live store under the user's home.
#: NOTE: nothing in this module reads these two any more -- the conversations.db
#: layer that used them was removed by the F10 fix (see record_event, which now
#: writes LocalMemoryStore first and this module's JSON buffer last). They are
#: kept for external importers, and now at least point somewhere that exists.
PAMS_DB_DIR = os.environ.get(
    "PAMS_DB_DIR",
    str(Path.home() / ".pams" / "memory_data"),
)
RECORDINGS_DB = os.path.join(PAMS_DB_DIR, "conversations.db")

# -- Memory-dir-aware DB path (conftest rebind target) --------------------
# PamsRecorder instantiates LocalMemoryStore() lazily; without its own cached
# path derived from AZAZAEL_MEMORY_DIR it cannot be rebound by the test
# harness, so writes leak to the production store. Mirror the shape used by
# CORE.direct_memory / CORE.local_memory so conftest can restore it.
def _get_db_path() -> "Path":
    env_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    if env_dir:
        base = Path(env_dir)
    else:
        base = Path.home() / ".azazael"
    base.mkdir(parents=True, exist_ok=True)
    return base / "memory_store.db"


DB_PATH = _get_db_path()


class PamsRecorder:
    """
    Universal event recorder for PAMS memory.
    
    Records agent actions, worm executions, morph events, debug diagnostics,
    fleet status changes, and any other Azazael events.
    
    Uses LocalMemoryStore (SQLite) as the durable event store.
    Provides full query/recall/search capability.
    """
    
    VERSION = "2.0.1"
    _instance: "PamsRecorder | None" = None
    _instance_lock = threading.Lock()
    _lock = threading.Lock()
    # NOTE (2026-09-21): the constructor is deliberately NOT a singleton.
    #
    # `get_recorder()` is the singleton accessor and has used double-checked
    # locking on `_instance`/`_instance_lock` since the June 11 2026 P0 fix.
    # Making `__new__` cache as well was tried and reverted, because the test suite
    # states BOTH contracts and they cannot both hold: `test_same_instance` asserts
    # `PamsRecorder() is PamsRecorder()`, while four lazy-init tests assert that a
    # freshly constructed recorder has `_local_store_init_attempted is False` --
    # which is only true for a new object. Four tests to one, and the code ships a
    # dedicated accessor for the singleton, so the accessor is the contract and
    # `tests/test_pams_recorder.py::test_same_instance` was corrected to test it.
    # The production call sites that
    # do `_pams = PamsRecorder()` at import now use `get_recorder()` (S-8 pass);
    # the migration is behaviour-preserving: same module, one shared instance.

    def __init__(self):
        self.session_id = uuid7().hex[:8]
        self.event_count = 0
        # P0 fix (June 11 2026): Dedicated lock for event_count increments.
        self._count_lock = threading.Lock()
        # CD1 correlation (June 11 2026): Cross-domain correlation ID
        # Links PamsRecorder events to DirectMemory/MemoryBus events
        # across the unified event schema. Uses UUID7 (RFC 9562) for
        # time-sortable, index-friendly identifiers.
        self._correlation_id = uuid7().hex
        self.fallback_path = os.path.join(_AZAZAEL_ROOT, "data", "pams_fallback_records.json")
        os.makedirs(os.path.dirname(self.fallback_path), exist_ok=True)
        self._fallback_buffer: List[Dict] = self._load_fallback()

        # -- Local SQLite memory store (F05 fix: module-level import) ---
        # LocalMemoryStore is now imported at module level to avoid the
        # double-checked locking + lazy import deadlock under 3.14t.
        # If the module-level import failed, we still fall through gracefully.
        self._local_store: Any = None
        self._local_ok = False
        self._local_store_init_attempted = False
    
    def _load_fallback(self) -> List[Dict]:
        """Load existing fallback records."""
        if os.path.exists(self.fallback_path):
            try:
                with open(self.fallback_path, "r", encoding="utf-8") as f:
                    return json.load(f)[-1000:]  # Keep last 1000
            except Exception as exc:
                note_failure("PamsRecorder:L124", exc)
        return []
    
    def _save_fallback(self):
        """Save fallback records to disk."""
        try:
            with open(self.fallback_path, "w", encoding="utf-8") as f:
                json.dump(self._fallback_buffer, f, indent=2, default=str)
        except Exception as exc:
            note_failure("PamsRecorder:L133", exc)
    
    def _migrate_fallback_to_local(self):
        """Migrate old JSON fallback buffer to local SQLite store."""
        if not self._local_ok or not self._fallback_buffer:
            return
        with self._lock:
            buffer_copy = list(self._fallback_buffer)
            self._fallback_buffer = []
            # P2 fix (Jun 2026): Immediately persist empty state to disk
            self._save_fallback()
        for event in buffer_copy:
            try:
                self._local_store.record_event(
                    source=event.get("source", "unknown"),
                    event_type=event.get("event_type", "migrated"),
                    data=event.get("data", {}),
                    severity=event.get("severity", "info"),
                    session_id=event.get("session_id", self.session_id),
                )
            except Exception as exc:
                note_failure("PamsRecorder:L154", exc)
        self._save_fallback()
    
    def record_event(self, event_type: str, data: Optional[Dict[str, Any]] = None,
                     source: str = "unknown", severity: str = "info",
                     tags: Optional[List[str]] = None) -> bool:
        """
        Record an event to PAMS memory.
        
        Args:
            event_type: Type of event (agent_action, worm_execution, morph_event, etc.)
            data: Arbitrary event data
            source: Component that generated the event
            severity: info, warning, error, critical
            tags: Optional list of string tags
        
        Returns:
            True if recorded to the local store, False if buffer-only
        """
        if data is None:
            data = {}
        
        event = {
            "event_type": event_type,
            "source": source,
            "severity": severity,
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }
        
        # NOTE (2026-10-10): the PAMS HTTP layer is removed. It posted every
        # event to POST {PAMS_URL}/api/memory — a route the server never had
        # (real routes: GET /api/health, POST /api/memories/promote for curated
        # promotion with embedding + API key). Every call failed into silent
        # local fallback while reporting PAMS success. Curated promotion belongs
        # to MemoryBus._post_to_pams; hot event telemetry belongs here, in the
        # local store that trainers and recall actually read.
        # Memory unity: this recorder is a FEEDER into the canonical store
        # (LocalMemoryStore). Join key for cross-system reads: correlation_id
        # (UUID7) on every row; query by source/event_type to consume.

        # -- Primary store: Local SQLite memory store (lazy init) ----
        if not self._local_ok and not self._local_store_init_attempted:
            self._ensure_local_store()
        if self._local_ok:
            try:
                self._local_store.record_event(
                    source=source, event_type=event_type,
                    data=data, severity=severity, tags=tags,
                    session_id=self.session_id,
                    correlation_id=self._correlation_id,
                )
                with self._count_lock:
                    self.event_count += 1
                return True
            except Exception as exc:
                note_failure("PamsRecorder:L225", exc)
        
        # -- F10 FIX (June 7 2026): Removed fallback layer 3 (conversations.db)
        # and fixed layer 4 (JSON buffer with file locking).
        # Layer 3 used a hardcoded path that doesn't exist on this system.
        # Layer 4 wrote without file locking — concurrent writes corrupt data.
        # Now: layer 2 (LocalMemoryStore) is the only fallback. If it fails,
        # we write to JSON buffer WITH lock protection.

        # -- Last resort: local JSON file buffer (with lock) -------------
        with self._lock:
            self._fallback_buffer.append(event)
            if len(self._fallback_buffer) % 10 == 0:
                self._save_fallback()
        with self._count_lock:
            self.event_count += 1
        return False  # Recorded to buffer only
    
    def record_batch(self, events: List[Dict]) -> int:
        """Record multiple events at once."""
        count = 0
        for evt in events:
            if self.record_event(**evt):
                count += 1
        return count
    
    # -- Local Store Initialization (F05 fix: no more lazy import) ---

    def _ensure_local_store(self):
        """Initialize LocalMemoryStore on first use.

        F05 FIX (June 7 2026): The import is now at module level (above).
        This eliminates the double-checked locking + lazy import pattern
        that caused deadlocks under Python 3.14t free-threading.

        The instantiation is still lazy (deferred to first record_event call),
        but the import itself is no longer in a critical section.
        """
        if self._local_store_init_attempted:
            return
        with self._lock:
            if self._local_store_init_attempted:
                return
            self._local_store_init_attempted = True

        if LOCAL_MEMORY_OK and LocalMemoryStore is not None:
            try:
                self._local_store = LocalMemoryStore(db_path=str(DB_PATH))
                self._local_ok = True
                # Flush any old fallback buffer into local store
                self._migrate_fallback_to_local()
            except Exception as exc:
                note_failure("PamsRecorder:L277", exc)

    # -- Query/Recall API (new in v2.0) ------------------------------------
    
    def query_memories(self, source: Optional[str] = None, event_type: Optional[str] = None,
                       severity: Optional[str] = None, limit: int = 50,
                       since: Optional[str] = None) -> List[Dict]:
        """
        Query recorded memories with optional filters.
        Delegates to LocalMemoryStore if available.
        """
        self._ensure_local_store()
        if self._local_ok:
            try:
                return self._local_store.query_memories(
                    source=source, event_type=event_type,
                    severity=severity, limit=limit, since=since,
                )
            except Exception as exc:
                note_failure("PamsRecorder:L296", exc)
        # Fallback: search buffer
        results = []
        for evt in reversed(self._fallback_buffer):
            if source and evt.get("source") != source:
                continue
            if event_type and evt.get("event_type") != event_type:
                continue
            if severity and evt.get("severity") != severity:
                continue
            results.append(evt)
            if len(results) >= limit:
                break
        return results
    
    def search_memories(self, query: str, source: Optional[str] = None,
                        limit: int = 50) -> List[Dict]:
        """
        Full-text search across recorded memories.
        Delegates to LocalMemoryStore if available.
        """
        self._ensure_local_store()
        if self._local_ok:
            try:
                return self._local_store.search_memories(
                    query=query, source=source, limit=limit,
                )
            except Exception as exc:
                note_failure("PamsRecorder:L324", exc)
        # Fallback: linear scan buffer
        q = query.lower()
        results = []
        for evt in reversed(self._fallback_buffer):
            if source and evt.get("source") != source:
                continue
            evt_str = json.dumps(evt).lower()
            if q in evt_str:
                results.append(evt)
                if len(results) >= limit:
                    break
        return results
    
    def recall_recent(self, limit: int = 20, source: Optional[str] = None) -> List[Dict]:
        """Get the most recent memories, optionally filtered by source."""
        return self.query_memories(source=source, limit=limit)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get recorder statistics."""
        self._ensure_local_store()
        stats = {
            "version": self.VERSION,
            "session_id": self.session_id,
            "total_events": self.event_count,
            "fallback_buffer_size": len(self._fallback_buffer),
            "pams_url": PAMS_URL,
            "fallback_path": self.fallback_path,
            "local_store_available": self._local_ok,
        }
        if self._local_ok:
            try:
                stats["local_store"] = self._local_store.get_stats()
            except Exception as exc:
                note_failure("PamsRecorder:L358", exc)
                stats["local_store"] = {"error": "unavailable"}
        return stats


    @classmethod
    def reset_for_testing(cls):
        """Reset singleton state for test isolation. Only use in tests."""
        with cls._lock:
            cls._instance = None


# Singleton access (P0 fix June 11 2026: double-checked locking)
def get_recorder() -> PamsRecorder:
    """Get the global PamsRecorder singleton.

    P0 fix (June 11 2026): Previously created a new PamsRecorder() on every
    call, making get_recorder() NOT a singleton. Under 3.14t free-threading,
    concurrent calls created multiple instances writing to the same JSON
    buffer file without coordination. Now uses double-checked locking.
    """
    if PamsRecorder._instance is None:
        with PamsRecorder._instance_lock:
            if PamsRecorder._instance is None:
                PamsRecorder._instance = PamsRecorder()
    return PamsRecorder._instance


if __name__ == "__main__":
    # Test
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    rec = PamsRecorder()
    rec.record_event("test", {"message": "PamsRecorder functional test"}, source="PamsRecorder")
    stats = rec.get_stats()
    print(f"PamsRecorder v{stats['version']}")
    print(f"Session: {stats['session_id']}")
    print(f"Events: {stats['total_events']}")
    print(f"Fallback buffer: {stats['fallback_buffer_size']}")
