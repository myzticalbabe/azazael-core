# coding: utf-8
"""
codebuff_tracker.py — DEPRECATED. Use DirectMemory instead.

This module is maintained for backward compatibility only.
All new code should use: from CORE.direct_memory import DirectMemory

Wraps LocalMemoryStore with convenience methods that auto-record:
- file edits (str_replace / write_file / edit)
- file creates (write_file)
- code reviews (code-reviewer-deepseek results)
- test results
- session lifecycle (start, end, milestone)
- generic actions with quality-enforced data structure
- tooling verification
- error occurrences

MANDATORY: Every Codebuff session MUST call record_session_start() at
init and record_session_end() at completion. See azazael-sop skill Rule 5.

An atexit handler auto-records session_end if the session wasn't explicitly
ended, ensuring no events are lost on unexpected termination.

Data Quality: Use validate_event_quality() to check that recorded events
follow the quality guidelines (action field, proper tags, severity, etc.).

Usage from any Buffy/Codebuff session:
    from CORE.codebuff_tracker import CodebuffTracker, get_tracker
    tracker = get_tracker()
    tracker.record_file_edit("path/to/file.py", "Fix circuit breaker", lines_changed=3)
    tracker.record_file_create("path/to/new.py", "New memory store")
    tracker.record_code_review("Phase 19 fixes", issues_found=2, issues_fixed=2)
    tracker.record_session_start()
    tracker.record_milestone("Phase 20 complete", details={"wins": 5, "files": 10})

The tracker auto-manages session IDs and tags for easy recall via /recall.
"""

import sys
import sqlite3
import json
import os
import threading
import atexit
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

try:
    from CORE.WORMS.expansion.xmemory_decouple_aggregate import (
        MemoryBank as _XMemoryBank,
        XMemoryDecoupler as _XMemoryDecoupler,
    )
    _XMEMORY = True
except ImportError:                               # never break the host
    _XMEMORY = False
    _XMemoryBank = None
    _XMemoryDecoupler = None

_FACT_HINTS = ("defin", "claim", "fact", "decis", "conclud")
_UPDATE_HINTS = ("updat", "edit", "fix", "chang", "modif", "revis")


def _decouple_rows(rows):
    """xMemory decouple-aggregate on recall (additive metadata, fail-open caller).

    Isolates facts/updates/details BEFORE organizing: classifies each recalled
    row by event_type and bank-tags it. Never drops or reorders rows.
    """
    dec = _XMemoryDecoupler() if (_XMEMORY and _XMemoryDecoupler is not None) else None
    out = []
    for r in rows:
        et = str(r.get("event_type", "")).lower()
        kind = ("fact" if any(h in et for h in _FACT_HINTS)
                else "update" if any(h in et for h in _UPDATE_HINTS)
                else "detail")
        if dec is not None and _XMemoryBank is not None:
            dec.write(_XMemoryBank.SHORT_TERM, "recall:%s" % r.get("id"),
                      kind, worm="recall_recent")
        r = dict(r)
        r["_xkind"] = kind
        r["_bank"] = "short_term"
        out.append(r)
    return out

# ── Deprecation ────────────────────────────────────────────────────
warnings.warn(
    "codebuff_tracker.CodebuffTracker is DEPRECATED. "
    "Use from CORE.direct_memory import DirectMemory instead.",
    FutureWarning, stacklevel=2
)

# -- Ensure azazael/src is in path --
_this_dir = Path(__file__).resolve().parent
_src_dir = _this_dir.parent if _this_dir.name == "CORE" else _this_dir
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

VERSION = "2.2.0"

# -- Database path (same as LocalMemoryStore, but stdlib-only resolution) --
def _get_db_path() -> Path:
    """Resolve memory store DB path without importing any CORE modules."""
    env_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    if env_dir:
        base = Path(env_dir)
    else:
        base = Path.home() / ".azazael"
    base.mkdir(parents=True, exist_ok=True)
    return base / "memory_store.db"

# -- DirectStore: stdlib-only backend (zero CORE imports, always works) --

class _DirectStore:
    """Direct SQLite backend that mirrors the LocalMemoryStore API used by CodebuffTracker.

    Zero CORE imports. No worm registry discovery. Completes in <2s.
    Writes to the SAME database as LocalMemoryStore for cross-session queryability.
    """

    def __init__(self):
        self._db_path = str(_get_db_path())
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=2)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, source TEXT NOT NULL,
                    event_type TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
                    severity TEXT NOT NULL DEFAULT 'info',
                    tags TEXT NOT NULL DEFAULT '[]',
                    timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                    ended_at TEXT, source TEXT NOT NULL DEFAULT 'unknown',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    event_count INTEGER NOT NULL DEFAULT 0
                );
            """)
            conn.commit()
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=2000")
        return conn

    def record_event(self, source: str, event_type: str, data: Any = None,
                     severity: str = "info", tags: list[str] | None = None,
                     session_id: str | None = None) -> int:
        """Record an event. Mirrors LocalMemoryStore.record_event()."""
        data_str = json.dumps(data if data is not None else {}, ensure_ascii=False)
        tags_str = json.dumps(tags or [], ensure_ascii=False)
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "INSERT INTO memories(session_id,source,event_type,data,severity,tags,timestamp) VALUES(?,?,?,?,?,?,?)",
                (session_id or "unknown", source, event_type, data_str, severity, tags_str, ts)
            )
            mem_id = cursor.lastrowid or 0
            conn.execute("UPDATE sessions SET event_count=event_count+1 WHERE session_id=?", (session_id or "unknown",))
            conn.commit()
            conn.close()
        return mem_id

    def start_session(self, source: str = "unknown", metadata: dict | None = None,
                      session_id: str | None = None) -> str:
        sid = session_id or datetime.now().strftime("codebuff_%Y%m%d_%H%M%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO sessions(session_id,started_at,source,metadata) VALUES(?,?,?,?)",
                (sid, datetime.now(timezone.utc).isoformat(), source, json.dumps(metadata or {}))
            )
            conn.commit()
            conn.close()
        return sid

    def end_session(self, session_id: str):
        with self._lock:
            conn = self._get_conn()
            conn.execute("UPDATE sessions SET ended_at=? WHERE session_id=?",
                         (datetime.now(timezone.utc).isoformat(), session_id))
            conn.commit()
            conn.close()

    def recall_recent(self, limit: int = 20, source: str | None = None) -> list[dict]:
        conn = self._get_conn()
        if source:
            rows = conn.execute("SELECT * FROM memories WHERE source=? ORDER BY id DESC LIMIT ?",
                               (source, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memories ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        conn.close()
        rows = [_parse_row(r) for r in rows]
        try:
            rows = _decouple_rows(rows)
        except Exception as exc:                      # never break recall
            note_failure("codebuff_tracker:recall-decouple", exc)
        return rows

    def search_memories(self, query: str, source: str | None = None,
                        limit: int = 30) -> list[dict]:
        like = f"%{query}%"
        conn = self._get_conn()
        if source:
            rows = conn.execute(
                "SELECT * FROM memories WHERE source=? AND (event_type LIKE ? OR data LIKE ?) ORDER BY id DESC LIMIT ?",
                (source, like, like, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories WHERE event_type LIKE ? OR data LIKE ? ORDER BY id DESC LIMIT ?",
                (like, like, limit)
            ).fetchall()
        conn.close()
        return [_parse_row(r) for r in rows]

    def get_session(self, session_id: str) -> dict:
        conn = self._get_conn()
        session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if not session:
            conn.close()
            return {"session_id": session_id, "error": "Not found", "memories": []}
        memories = conn.execute(
            "SELECT * FROM memories WHERE session_id=? ORDER BY id ASC", (session_id,)
        ).fetchall()
        conn.close()
        result = dict(session)
        result["memories"] = [_parse_row(m) for m in memories]
        return result

    def list_sessions(self, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT session_id,started_at,ended_at,source,event_count,metadata FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def query_memories(self, source: str | None = None, event_type: str | None = None,
                       severity: str | None = None, limit: int = 50,
                       offset: int = 0, since: str | None = None,
                       session_id: str | None = None, max_age_days: int | None = None) -> list[dict]:
        conditions = []
        params: list[Any] = []
        if source:
            conditions.append("source=?"); params.append(source)
        if event_type:
            conditions.append("event_type=?"); params.append(event_type)
        if severity:
            conditions.append("severity=?"); params.append(severity)
        if since:
            conditions.append("timestamp>?"); params.append(since)
        if session_id:
            conditions.append("session_id=?"); params.append(session_id)
        if max_age_days:
            # Python UTC T-cutoff (2026-09-09 fix): SQLite datetime('now')
            # renders space-separated and sorts below every stored
            # T-separated row, making intraday windows match everything.
            from datetime import timedelta as _td
            conditions.append("timestamp>?")
            params.append((datetime.now(timezone.utc) - _td(days=max_age_days)).isoformat())
        where = " AND ".join(conditions) if conditions else "1=1"
        conn = self._get_conn()
        rows = conn.execute(
            f"SELECT * FROM memories WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [min(limit, 500), offset]
        ).fetchall()
        conn.close()
        return [_parse_row(r) for r in rows]


def _parse_row(row: sqlite3.Row) -> dict:
    """Parse a sqlite3.Row into a dict with deserialized JSON fields."""
    d = dict(row)
    for field in ("data", "tags"):
        try:
            d[field] = json.loads(d[field]) if isinstance(d[field], str) else d[field]
        except (json.JSONDecodeError, TypeError) as exc:
            note_failure("codebuff_tracker:L251", exc)
    return d

# -- Quality Guidelines for event data --
# Every event recorded should follow these conventions for high-quality DB knowledge:
#
# source:     Always "Codebuff" (this tracker records Codebuff session actions)
# event_type: snake_case, specific: "file_edit", "code_review", "tooling_verify", etc.
# data:       Dict with at minimum { "action": "<short description>", "target": "<what>" }
# severity:   "info" (success), "warning" (issues found), "error" (failure)
# tags:       At least 2 tags: [<category>, <specific>]. Always include "session-recording"
#
# Naming conventions:
# - event_type uses snake_case
# - tags use kebab-case: "file-edit", "code-review", "session-lifecycle"
# - data keys use snake_case

# -- Quality validation thresholds --
MIN_TAGS_PER_EVENT = 2
REQUIRED_TAG = "session-recording"
MAX_ACTION_LENGTH = 300
VALID_SEVERITIES = {"info", "warning", "error", "critical"}
RECOMMENDED_DATA_FIELDS = {"action", "target"}


class CodebuffTracker:
    """Auto-records Codebuff session events to the memory store.

    Uses _DirectStore (stdlib-only SQLite) by default — zero CORE imports,
    no worm registry discovery, always completes in <2s.

    For LocalMemoryStore features (FTS5, composite indexes, config-driven
    maintenance), pass store=LocalMemoryStore() explicitly. This is only
    recommended inside long-running processes where CORE is already loaded.

    Provides high-quality structured event recording for every session action.
    Used by the azazael-startup skill and mandated by azazael-sop Rule 5.

    Registers an atexit handler that auto-calls record_session_end() if the
    session was started but not explicitly ended before process exit.
    """

    def __init__(self, store = None):
        # Default to _DirectStore (stdlib-only, no CORE imports, always works).
        # Only use the provided store if explicitly passed in (e.g., LocalMemoryStore
        # from a long-running process where CORE is already loaded).
        self.store = store if store is not None else _DirectStore()
        self._session_id = (datetime.now().strftime("codebuff_%Y%m%d_%H%M%S")
                              + f"_{os.getpid()}")
        self._lock = threading.Lock()
        self._event_count = 0
        self._session_started = False
        self._session_ended = False
        self._files_changed: list[str] = []
        self._event_types_used: dict[str, int] = {}
        # Register atexit handler for safe shutdown
        atexit.register(self._atexit_handler)

    def _atexit_handler(self):
        """Auto-record session_end on process exit if session wasn't ended.

        This ensures no events are lost when Codebuff shuts down unexpectedly.
        Only fires once and only if the session was started but not ended.
        """
        if self._session_started and not self._session_ended:
            try:
                self.record_session_end({
                    "auto_recorded_by": "atexit_handler",
                    "note": "Session ended via atexit (process exit). "
                            "Explicit record_session_end() should be called "
                            "for better summary data.",
                })
            except Exception as exc:
                note_failure("codebuff_tracker:L324", exc) # Silently handle atexit errors

    @property
    def session_id(self) -> str:
        """Return the current session ID."""
        return self._session_id

    # -- Data Quality Validation --

    def validate_event_quality(self, event_type: str, data: dict,
                                severity: str, tags: list) -> dict[str, Any]:
        """Validate that an event follows the quality guidelines.

        Checks:
        - event_type is non-empty snake_case
        - data contains 'action' field (recommended)
        - data contains 'target' field (recommended)
        - severity is valid
        - tags include 'session-recording'
        - minimum number of tags

        Args:
            event_type: The event type string.
            data: The event data dict.
            severity: The severity level.
            tags: The list of tags.

        Returns:
            dict with 'passed' (bool), 'warnings' (list), and optional 'score' (0-100).
        """
        warnings = []
        score = 100

        # Check event_type
        if not event_type or not isinstance(event_type, str):
            warnings.append("event_type must be a non-empty string")
            score -= 20
        elif not event_type.islower() or "_" not in event_type:
            warnings.append(f"event_type '{event_type}' should use snake_case")
            score -= 5

        # Check data has recommended fields
        if not isinstance(data, dict):
            warnings.append("data should be a dict")
            score -= 10
        else:
            if "action" not in data:
                warnings.append("data should include 'action' field for searchability")
                score -= 10
            if "target" not in data:
                warnings.append("data should include 'target' field describing what was acted upon")
                score -= 5

        # Check severity
        if severity not in VALID_SEVERITIES:
            warnings.append(f"severity '{severity}' not in {sorted(VALID_SEVERITIES)}")
            score -= 10

        # Check tags
        if not tags or not isinstance(tags, list):
            warnings.append("tags should be a non-empty list")
            score -= 10
        else:
            if len(tags) < MIN_TAGS_PER_EVENT:
                warnings.append(f"at least {MIN_TAGS_PER_EVENT} tags recommended, got {len(tags)}")
                score -= 5
            if REQUIRED_TAG not in tags:
                warnings.append(f"tags should include '{REQUIRED_TAG}' for queryability")
                score -= 10

        return {
            "passed": len(warnings) == 0,
            "warnings": warnings,
            "score": max(0, score),
        }

    def _record_with_quality(self, event_type: str, data: dict,
                              severity: str, tags: list):
        """Record an event and log quality warnings.

        Internal method that applies quality validation before recording.
        """
        quality = self.validate_event_quality(event_type, data, severity, tags)
        if not quality["passed"]:
            # Log quality warnings but still record
            warning_msg = "; ".join(quality["warnings"])
            self.store.record_event(
                source="Codebuff",
                event_type="quality_warning",
                data={
                    "action": f"quality: {warning_msg}",
                    "target": event_type,
                    "warnings": quality["warnings"],
                    "score": quality["score"],
                },
                severity="warning",
                tags=["quality", "validation", "session-recording"],
                session_id=self._session_id,
            )
        self._event_count += 1
        self._event_types_used[event_type] = self._event_types_used.get(event_type, 0) + 1

    # -- Session Management --

    def record_session_start(self, metadata: dict | None = None) -> str:
        """Record session start. Call once per Codebuff session.

        Enforces singleton: subsequent calls are no-ops.

        Args:
            metadata: Optional dict with initial context (project, focus, etc.)

        Returns:
            The session ID string.
        """
        if self._session_started:
            return self._session_id
        self._session_started = True
        sid = self._session_id
        meta = metadata or {}
        meta.update({
            "python_version": sys.version.split()[0],
            "tracker_version": VERSION,
            "source": "codebuff",
        })
        self.store.start_session(source="Codebuff", metadata=meta, session_id=sid)
        self.store.record_event(
            source="Codebuff",
            event_type="session_start",
            data=meta,
            severity="info",
            tags=["session", "session-lifecycle", "startup", "session-recording"],
            session_id=sid,
        )
        self._event_count += 1
        self._event_types_used["session_start"] = self._event_types_used.get("session_start", 0) + 1
        return sid

    def record_session_end(self, summary: dict | None = None):
        """Record session end with summary stats.

        Mandatory: MUST be called at the end of every Codebuff session.
        If not called, the atexit handler will auto-record on process exit.

        Args:
            summary: Optional dict with final context (tasks_complete, outcomes, etc.)
        """
        if self._session_ended:
            return
        self._session_ended = True
        # Unregister atexit handler to prevent double-recording
        try:
            atexit.unregister(self._atexit_handler)
        except Exception as exc:
            note_failure("codebuff_tracker:L478", exc)

        summary = summary or {}
        summary.update({
            "event_count": self._event_count,
            "files_changed_count": len(self._files_changed),
            "files_changed": self._files_changed[:800],  # Cap at 100 filenames
            "event_type_breakdown": dict(sorted(self._event_types_used.items())),
        })
        self.store.record_event(
            source="Codebuff",
            event_type="session_end",
            data=summary,
            severity="info",
            tags=["session", "session-lifecycle", "shutdown", "session-recording"],
            session_id=self._session_id,
        )
        self.store.end_session(self._session_id)

    def record_milestone(self, title: str, details: dict | None = None):
        """Record a named milestone (e.g., 'Phase 20 complete', 'All tests passing').

        Args:
            title: Short descriptive title (< 100 chars).
            details: Optional structured data about the milestone.
        """
        data = {
            "action": f"milestone: {title}",
            "title": title,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.store.record_event(
            source="Codebuff",
            event_type="milestone_reached",
            data=data,
            severity="info",
            tags=["milestone", "progress", "session-recording"],
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used["milestone_reached"] = self._event_types_used.get("milestone_reached", 0) + 1

    # -- Generic Action Recording (high quality) --

    def record_action(self, action_type: str, description: str,
                      target: str = "", details: dict | None = None,
                      severity: str = "info", extra_tags: list | None = None):
        """Record any session action with quality-enforced data structure.

        This is the generic recorder for actions that don't have a dedicated method.
        Use the dedicated methods (record_file_edit, record_code_review, etc.)
        when available for better-structured data.

        Args:
            action_type: Snake_case event type (e.g., "research_performed", "config_updated").
            description: Short human-readable action description (< 200 chars).
            target: What the action was performed on (file path, component name, etc.).
            details: Optional dict with additional structured fields.
            severity: "info", "warning", or "error".
            extra_tags: Optional additional tags (auto-appended to action_type + "session-recording").
        """
        data: dict[str, Any] = {
            "action": description[:MAX_ACTION_LENGTH],
            "target": target,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if details:
            data["details"] = details

        tags = [action_type, REQUIRED_TAG]
        if extra_tags:
            tags.extend(extra_tags)

        self.store.record_event(
            source="Codebuff",
            event_type=action_type,
            data=data,
            severity=severity,
            tags=tags,
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used[action_type] = self._event_types_used.get(action_type, 0) + 1

    # -- File Operations --

    def record_file_edit(self, filepath: str, description: str,
                         lines_changed: int = 0, tool: str = "str_replace",
                         extra: dict | None = None):
        """Record a file edit (str_replace / write_file overwrite).

        Args:
            filepath: Path to the edited file.
            description: What was changed (e.g., "Fix bug in auth logic").
            lines_changed: Estimated number of lines changed.
            tool: Tool used for the edit ("str_replace", "write_file", etc.).
            extra: Optional additional fields.
        """
        if filepath and filepath not in self._files_changed:
            self._files_changed.append(filepath)
        data = {
            "action": f"edit: {description}",
            "filepath": filepath,
            "filename": Path(filepath).name if filepath else "unknown",
            "description": description,
            "lines_changed": lines_changed,
            "tool": tool,
        }
        if extra:
            data.update(extra)
        self.store.record_event(
            source="Codebuff",
            event_type="file_edit",
            data=data,
            severity="info",
            tags=["file", "file-edit", "code", REQUIRED_TAG],
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used["file_edit"] = self._event_types_used.get("file_edit", 0) + 1

    def record_file_create(self, filepath: str, description: str,
                           file_size_bytes: int = 0, extra: dict | None = None):
        """Record a new file creation (write_file).

        Args:
            filepath: Path to the created file.
            description: Brief description of the file's purpose.
            file_size_bytes: Approximate file size.
            extra: Optional additional fields.
        """
        if filepath and filepath not in self._files_changed:
            self._files_changed.append(filepath)
        data = {
            "action": f"create: {description}",
            "filepath": filepath,
            "filename": Path(filepath).name if filepath else "unknown",
            "description": description,
            "size_bytes": file_size_bytes,
            "tool": "write_file",
        }
        if extra:
            data.update(extra)
        self.store.record_event(
            source="Codebuff",
            event_type="file_create",
            data=data,
            severity="info",
            tags=["file", "file-create", "code", REQUIRED_TAG],
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used["file_create"] = self._event_types_used.get("file_create", 0) + 1

    def record_file_delete(self, filepath: str, description: str):
        """Record a file deletion.

        Args:
            filepath: Path to the deleted file.
            description: Why the file was deleted.
        """
        self.store.record_event(
            source="Codebuff",
            event_type="file_delete",
            data={
                "action": f"delete: {description}",
                "filepath": filepath,
                "filename": Path(filepath).name if filepath else "unknown",
                "description": description,
            },
            severity="warning",
            tags=["file", "file-delete", "code", REQUIRED_TAG],
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used["file_delete"] = self._event_types_used.get("file_delete", 0) + 1

    # -- Code Review --

    def record_code_review(self, subject: str, issues_found: int = 0,
                           issues_fixed: int = 0, summary: str = "",
                           extra: dict | None = None):
        """Record a code review operation.

        Args:
            subject: What was reviewed (e.g., "Phase 19 worm changes").
            issues_found: Number of issues identified.
            issues_fixed: Number of issues resolved.
            summary: Optional brief summary of findings.
            extra: Optional additional fields.
        """
        data = {
            "action": f"review: {subject}",
            "subject": subject,
            "issues_found": issues_found,
            "issues_fixed": issues_fixed,
            "summary": summary[:500] if summary else "",
        }
        if extra:
            data.update(extra)
        severity = "warning" if issues_found > 0 else "info"
        self.store.record_event(
            source="Codebuff",
            event_type="code_review",
            data=data,
            severity=severity,
            tags=["review", "code-review", "quality", REQUIRED_TAG],
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used["code_review"] = self._event_types_used.get("code_review", 0) + 1

    # -- Test Results --

    def record_test_run(self, test_suite: str, passed: int, failed: int = 0,
                        skipped: int = 0, duration_ms: float = 0,
                        failures: list | None = None, extra: dict | None = None):
        """Record test suite execution results.

        Args:
            test_suite: Name of the test suite or file.
            passed: Number of tests passed.
            failed: Number of tests failed.
            skipped: Number of tests skipped.
            duration_ms: Test execution time in milliseconds.
            failures: Optional list of individual failure descriptions.
            extra: Optional additional fields.
        """
        data = {
            "action": f"test: {test_suite} ({passed}p/{failed}f/{skipped}s)",
            "test_suite": test_suite,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration_ms": round(duration_ms, 1),
            "failures": failures or [],
        }
        if extra:
            data.update(extra)
        severity = "error" if failed > 0 else "info"
        tags = ["test", "test-run", "ci", "quality", REQUIRED_TAG]
        if failed > 0:
            tags.append("test-failure")
        else:
            tags.append("test-pass")
        self.store.record_event(
            source="Codebuff",
            event_type="test_run",
            data=data,
            severity=severity,
            tags=tags,
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used["test_run"] = self._event_types_used.get("test_run", 0) + 1

    # -- Tooling Verification --

    def record_tooling_verify(self, tools_found: int, tools_missing: int,
                              details: dict | None = None):
        """Record a tooling verification result (from check_tooling.py).

        Args:
            tools_found: Number of CLI tools available.
            tools_missing: Number of CLI tools not found.
            details: dict with tool-by-tool status breakdown.
        """
        data = {
            "action": f"tooling verify: {tools_found} found, {tools_missing} missing",
            "tools_found": tools_found,
            "tools_missing": tools_missing,
        }
        if details:
            data["details"] = details
        severity = "warning" if tools_missing > 0 else "info"
        self.store.record_event(
            source="Codebuff",
            event_type="tooling_verify",
            data=data,
            severity=severity,
            tags=["tooling", "startup", "environment", REQUIRED_TAG],
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used["tooling_verify"] = self._event_types_used.get("tooling_verify", 0) + 1

    # -- Error Recording --

    def record_error_occurrence(self, error_id: int, title: str, file_path: str,
                                severity: str = "error", details: dict | None = None):
        """Record an error occurrence from ERROR_LOG.md.

        Args:
            error_id: The Error #NN sequential ID.
            title: Short descriptive title.
            file_path: The file where the error occurred.
            severity: "error" or "warning".
            details: Optional root cause, fix, prevention info.
        """
        data = {
            "action": f"error #{error_id}: {title}",
            "error_id": error_id,
            "title": title,
            "file_path": file_path,
            "error_log_ref": f"ERROR_LOG.md#error-{error_id}",
        }
        if details:
            data["details"] = details
        self.store.record_event(
            source="Codebuff",
            event_type="error_occurrence",
            data=data,
            severity=severity,
            tags=["error", "error-log", "error-occurrence", REQUIRED_TAG],
            session_id=self._session_id,
        )
        self._event_count += 1
        self._event_types_used["error_occurrence"] = self._event_types_used.get("error_occurrence", 0) + 1

    # -- Query Helpers --

    def recall_recent(self, limit: int = 20) -> list[dict]:
        """Get most recent Codebuff events from current session."""
        return self.store.recall_recent(limit=limit, source="Codebuff")

    def search(self, query: str, limit: int = 30) -> list[dict]:
        """Full-text search Codebuff session events."""
        return self.store.search_memories(query=query, source="Codebuff", limit=limit)

    def get_session_summary(self) -> dict:
        """Get summary of current session.

        Falls back to the most recent Codebuff session if the current
        per-process session_id has no events yet (since Codebuff spawns
        a new process for each message, creating a new session_id each time).
        """
        session = self.store.get_session(self._session_id)
        memories = session.get("memories", [])

        if len(memories) == 0:
            sessions = self.store.list_sessions(limit=5)
            for s in sessions:
                sid = s.get("session_id", "")
                source = s.get("source", "")
                if sid and sid != self._session_id and source == "Codebuff":
                    fallback = self.store.get_session(sid)
                    fallback_memories = fallback.get("memories", [])
                    if len(fallback_memories) > 0:
                        event_types: dict[str, int] = {}
                        for m in fallback_memories:
                            et = m.get("event_type", "unknown")
                            event_types[et] = event_types.get(et, 0) + 1
                        return {
                            "session_id": sid,
                            "event_count": len(fallback_memories),
                            "event_types": event_types,
                            "started_at": fallback.get("started_at", ""),
                            "source": fallback.get("source", ""),
                            "note": "Fell back to most recent Codebuff session",
                        }

            return {
                "session_id": self._session_id,
                "event_count": 0,
                "event_types": {},
                "started_at": "",
                "source": "",
                "note": "No Codebuff session events found in the memory store",
            }

        event_types = {}
        for m in memories:
            et = m.get("event_type", "unknown")
            event_types[et] = event_types.get(et, 0) + 1
        return {
            "session_id": self._session_id,
            "event_count": len(memories),
            "event_types": event_types,
            "started_at": session.get("started_at", ""),
            "source": session.get("source", ""),
        }

    def recall_by_type(self, event_type: str, limit: int = 20) -> list[dict]:
        """Recall events by type (e.g., 'file_edit', 'code_review')."""
        return self.store.query_memories(
            source="Codebuff", event_type=event_type, limit=limit,
            session_id=self._session_id,
        )


# -- Global singleton --
_global_tracker: CodebuffTracker | None = None
_tracker_lock = threading.Lock()


def get_tracker(store=None) -> CodebuffTracker:
    """Get or create the global CodebuffTracker singleton.

    Auto-starts session on first call. Registers atexit handler for safe shutdown.
    Subsequent calls return the same tracker with the same session_id.

    Uses lazy import of LocalMemoryStore; falls back to stdlib-only _DirectStore
    if CORE modules are not available or import times out.
    """
    global _global_tracker
    if _global_tracker is None:
        with _tracker_lock:
            if _global_tracker is None:
                _global_tracker = CodebuffTracker(store=store)
                _global_tracker.record_session_start({
                    "auto_init": True,
                    "init_source": "codebuff_tracker.get_tracker()",
                })
    return _global_tracker


# -- Self-test --
if __name__ == "__main__":
    print("=" * 60)
    print("  CodebuffTracker v2.2 -- Self-test")
    print("=" * 60)

    tracker = CodebuffTracker()
    tracker.record_session_start({"test": True, "focus": "data-quality"})

    # Test data quality validation
    print("\n  [1] Data Quality Validation:")
    valid_quality = tracker.validate_event_quality(
        "file_edit", {"action": "edit: fixed bug", "target": "file.py"},
        "info", ["file", "session-recording"]
    )
    print(f"      Valid event: passed={valid_quality['passed']}, "
          f"score={valid_quality['score']}")
    assert valid_quality["passed"], "Valid event should pass quality check"

    bad_quality = tracker.validate_event_quality(
        "bad", {}, "invalid", []
    )
    print(f"      Bad event: passed={bad_quality['passed']}, "
          f"score={bad_quality['score']}, "
          f"warnings={len(bad_quality['warnings'])}")
    assert not bad_quality["passed"], "Bad event should fail quality check"
    assert len(bad_quality["warnings"]) >= 4, "Bad event should have multiple warnings"

    print("      Quality validation: PASS")

    # Test atexit handler registration
    print("\n  [2] Atexit Handler:")
    assert hasattr(tracker, '_atexit_handler'), "Missing atexit handler"
    print("      Atexit handler registered: PASS")

    # Test session lifecycle
    tracker2 = CodebuffTracker()
    tracker2.record_session_start({"test": True})
    tracker2.record_session_end({"tasks": 3, "outcome": "test_complete"})
    assert tracker2._session_ended, "Session should be marked as ended"
    print("      Session lifecycle: PASS")

    # Test recording basic events
    tracker.record_file_create("test.py", "Test file", file_size_bytes=100)
    tracker.record_file_edit("test.py", "Fix bug", lines_changed=5)
    tracker.record_code_review("Test review", issues_found=1, issues_fixed=1)
    tracker.record_test_run("tests", passed=10, failed=0)
    tracker.record_tooling_verify(5, 1, details={"python": "3.14t"})
    tracker.record_error_occurrence(50, "Test error", "file.py")
    tracker.record_milestone("Test milestone", {"done": True})

    print(f"\n  [3] Events recorded: {tracker._event_count}")
    print(f"      Types: {tracker._event_types_used}")

    print("\n" + "=" * 60)
    print("  Self-test complete.")
    print("=" * 60)
