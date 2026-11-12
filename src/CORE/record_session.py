# coding: utf-8
"""
record_session.py — DEPRECATED. Use DirectMemory instead.

This module is maintained for backward compatibility only.
All new code should use: from CORE.direct_memory import DirectMemory

Zero CORE imports. Zero worm registry discovery. Always works in <2s.

Writes directly to the same SQLite database that LocalMemoryStore uses,
so all events are queryable via store.search_memories(), store.recall_recent(), etc.

Usage (from within project with azazael/src on sys.path):
    from CORE.record_session import SessionRecorder

    rec = SessionRecorder()
    rec.record("skill_loaded", {"skill": "azazael-startup"})
    rec.record("file_read", {"files": ["MEMORIES.md", "ERROR_LOG.md"]})
    rec.milestone("startup complete")
    rec.end({"tasks": 5, "summary": "completed"})

Or use the module-level convenience functions:
    from CORE.record_session import record, end
    record("session_start", {"focus": "..."})
    end({"summary": "done"})
"""

import sqlite3
import json
import os
import threading
import warnings
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Deprecation ────────────────────────────────────────────────────
warnings.warn(
    "record_session.SessionRecorder is DEPRECATED. "
    "Use from CORE.direct_memory import DirectMemory instead.",
    FutureWarning, stacklevel=2
)

__version__ = "1.1.0"

# -- Database path (same as LocalMemoryStore) --
def _get_db_path() -> Path:
    env_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    if env_dir:
        base = Path(env_dir)
    else:
        base = Path.home() / ".azazael"
    base.mkdir(parents=True, exist_ok=True)
    return base / "memory_store.db"

DB_PATH = _get_db_path()

# -- Schema (same as LocalMemoryStore) --
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
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    source      TEXT NOT NULL DEFAULT 'unknown',
    metadata    TEXT NOT NULL DEFAULT '{}',
    event_count INTEGER NOT NULL DEFAULT 0
);
"""


class SessionRecorder:
    """Stdlib-only session recorder. No CORE imports, no worm registry discovery.

    Writes to the same SQLite database as LocalMemoryStore for
    cross-session queryability. Use this for session recording from
    Codebuff where CORE imports would trigger 38-45s worm registry discovery.

    API mirrors CodebuffTracker for drop-in replacement:

        rec = SessionRecorder()
        rec.file_edit("x.py", "Fix bug", lines_changed=3)
        rec.code_review("Phase 19 fixes", issues_found=2, issues_fixed=2)
        rec.test_run("tests", passed=10, failed=0)
        rec.milestone("Phase complete")
        rec.end({"tasks_complete": 5})
    """

    def __init__(self, session_id: str | None = None):
        self._db_path = str(DB_PATH)
        self._lock = threading.Lock()
        self._session_id = session_id or datetime.now().strftime("codebuff_%Y%m%d_%H%M%S")
        self._event_count = 0
        self._init_db()
        self._start_session()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=2)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=2000")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)
            conn.commit()
            conn.close()

    def _start_session(self):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO sessions(session_id, started_at, source, metadata) "
                "VALUES(?, ?, ?, ?)",
                (self._session_id, now, "Codebuff",
                 json.dumps({"recorder": f"record_session.py v{__version__}"}))
            )
            conn.commit()
            conn.close()

    @property
    def session_id(self) -> str:
        return self._session_id

    # -- Core recording --

    def record(self, event_type: str, data: dict[str, Any] | None = None,
               severity: str = "info", tags: list[str] | None = None,
               source: str = "Codebuff") -> int:
        """Record a single event.

        Args:
            event_type: snake_case event type (e.g., "file_edit", "code_review").
            data: JSON-serializable dict with event details.
            severity: "info", "warning", "error", or "critical".
            tags: list of string tags (auto-adds "session-recording").
            source: Source label (default "Codebuff").

        Returns:
            The row ID of the inserted record.
        """
        now = datetime.now(timezone.utc).isoformat()
        data_str = json.dumps(data if data is not None else {}, ensure_ascii=False)
        all_tags = list(tags or [])
        if "session-recording" not in all_tags:
            all_tags.append("session-recording")
        tags_str = json.dumps(all_tags, ensure_ascii=False)

        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "INSERT INTO memories(session_id, source, event_type, data, severity, tags, timestamp) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (self._session_id, source, event_type, data_str, severity, tags_str, now)
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

    # -- Convenience methods (mirroring CodebuffTracker API) --

    def file_edit(self, filepath: str, description: str, lines_changed: int = 0):
        """Record a file edit."""
        return self.record("file_edit", {
            "action": f"edit: {description}",
            "filepath": filepath,
            "filename": Path(filepath).name if filepath else "unknown",
            "description": description,
            "lines_changed": lines_changed,
            "tool": "str_replace",
        }, tags=["file", "file-edit", "code"])

    def file_create(self, filepath: str, description: str, file_size_bytes: int = 0):
        """Record a file creation."""
        return self.record("file_create", {
            "action": f"create: {description}",
            "filepath": filepath,
            "filename": Path(filepath).name if filepath else "unknown",
            "description": description,
            "size_bytes": file_size_bytes,
            "tool": "write_file",
        }, tags=["file", "file-create", "code"])

    def file_delete(self, filepath: str, description: str):
        """Record a file deletion."""
        return self.record("file_delete", {
            "action": f"delete: {description}",
            "filepath": filepath,
            "filename": Path(filepath).name if filepath else "unknown",
            "description": description,
        }, severity="warning", tags=["file", "file-delete", "code"])

    def file_read(self, filepaths: list, description: str = ""):
        """Record a file read operation."""
        return self.record("file_read", {
            "action": f"read: {description or ', '.join(Path(p).name for p in filepaths[:5])}",
            "files": filepaths,
            "count": len(filepaths),
        }, tags=["file", "file-read"])

    def skill_loaded(self, skill_name: str):
        """Record a skill being loaded."""
        return self.record("skill_loaded", {
            "action": f"skill: {skill_name}",
            "skill": skill_name,
        }, tags=["skill", "startup"])

    def code_review(self, subject: str, issues_found: int = 0, issues_fixed: int = 0,
                    summary: str = ""):
        """Record a code review."""
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
        """Record a test run."""
        severity = "error" if failed > 0 else "info"
        extra_tags = ["test-failure"] if failed > 0 else ["test-pass"]
        return self.record("test_run", {
            "action": f"test: {test_suite} ({passed}p/{failed}f/{skipped}s)",
            "test_suite": test_suite,
            "passed": passed, "failed": failed, "skipped": skipped,
            "duration_ms": round(duration_ms, 1),
        }, severity=severity, tags=["test", "test-run", "ci", "quality"] + extra_tags)

    def milestone(self, title: str, details: dict | None = None):
        """Record a milestone."""
        return self.record("milestone_reached", {
            "action": f"milestone: {title}",
            "title": title,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, tags=["milestone", "progress"])

    def tooling_verify(self, tools_found: int, tools_missing: int,
                       details: dict | None = None):
        """Record tooling verification results."""
        severity = "warning" if tools_missing > 0 else "info"
        return self.record("tooling_verify", {
            "action": f"tooling: {tools_found} found, {tools_missing} missing",
            "tools_found": tools_found,
            "tools_missing": tools_missing,
            "details": details or {},
        }, severity=severity, tags=["tooling", "startup", "environment"])

    # -- Session lifecycle --

    def end(self, summary: dict | None = None):
        """End the session with summary data."""
        summary = summary or {}
        summary.update({"event_count": self._event_count})
        self.record("session_end", {
            "action": "session_end",
            "summary": summary,
        }, tags=["session", "session-lifecycle", "shutdown"])

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                (now, self._session_id)
            )
            conn.commit()
            conn.close()

    # -- Query helpers --

    def count(self) -> int:
        """Return event count for this session."""
        conn = self._get_conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE session_id = ?",
            (self._session_id,)
        ).fetchone()[0]
        conn.close()
        return n

    def list_events(self, limit: int = 50) -> list[dict]:
        """list all events in this session."""
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
                    note_failure("record_session:L321", exc)
            results.append(d)
        return results


# -- Module-level convenience (for quick one-liners) --

_default_recorder: SessionRecorder | None = None
_recorder_lock = threading.Lock()


def get_recorder(session_id: str | None = None) -> SessionRecorder:
    """Get or create the default SessionRecorder for this process."""
    global _default_recorder
    if _default_recorder is None:
        with _recorder_lock:
            if _default_recorder is None:
                _default_recorder = SessionRecorder(session_id=session_id)
    return _default_recorder


def record(event_type: str, data: dict | None = None,
           severity: str = "info", tags: list[str] | None = None) -> int:
    """Quick record using the default recorder."""
    return get_recorder().record(event_type, data, severity=severity, tags=tags)


def end(summary: dict | None = None):
    """End the default session."""
    get_recorder().end(summary)


# -- Self-test --
if __name__ == "__main__":
    print(f"SessionRecorder v{__version__} — Self-test")
    print(f"  DB: {DB_PATH}")

    rec = SessionRecorder()
    print(f"  Session: {rec.session_id}")

    rec.record("session_start", {"action": "self-test", "version": __version__})
    rec.skill_loaded("azazael-startup")
    rec.file_read(["MEMORIES.md", "ERROR_LOG.md"], "canonical files")
    rec.milestone("self-test complete", {"events": 3})
    rec.end({"status": "ok"})

    count = rec.count()
    print(f"  Events recorded: {count}")
    assert count >= 4, f"Expected >=4 events, got {count}"

    events = rec.list_events()
    for e in events:
        print(f"    [{e['id']}] {e['event_type']}: {e['data'].get('action', '?')}")

    print("  PASS")
