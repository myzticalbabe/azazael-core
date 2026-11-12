#!/usr/bin/env python3
"""
Assistant Dynamic Logging Protocol (ADLP) - Enhanced Edition
============================================================
Centralized logging system for instant AI assistant access.
Features: Log rotation, async writes, full-text search, time queries, context tracking.

Version: 2.0.0
"""

import os
import json
import threading
import datetime
import uuid
import subprocess
import re
import time
from pathlib import Path
from enum import Enum
from typing import Any, List
from dataclasses import dataclass, field, asdict
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import shutil

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LOG_DIR = Path(os.environ.get("APPDATA", "~/.appdata")).expanduser() / "AssistantLogs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class LogCategory(Enum):
    SYSTEM = "system"
    PYTHON = "python"
    ENVIRONMENT = "environment"
    NETWORK = "network"
    SECURITY = "security"
    PERFORMANCE = "performance"
    USER = "user"
    APP = "app"
    ASSISTANT = "assistant"
    CONVERSATION = "conversation"
    TOOL_CALL = "tool_call"
    DECISION = "decision"
    ERROR = "error"


class LogLevel(Enum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


@dataclass
class LogEntry:
    id: str
    timestamp: str
    category: str
    level: str
    source: str
    message: str
    data: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    conversation_id: (str | None) = None
    parent_entry_id: (str | None) = None

    def to_searchable(self) -> str:
        return f"{self.timestamp} {self.category} {self.source} {self.message} {' '.join(self.tags)}"

    def get(self, key: str, default: Any = None) -> Any:
        """dict-style read access (lets index code treat entries uniformly)."""
        return getattr(self, key, default)


@dataclass
class ConversationContext:
    conversation_id: str
    assistant_id: str
    start_time: str
    goal: str
    entries: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    tools_used: list[dict] = field(default_factory=list)
    outcome: (str | None) = None


class AsyncLogWriter:
    def __init__(self, flush_interval: float = 1.0, max_queue_size: int = 1000):
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="log_writer"
        )
        self._flush_interval = flush_interval
        self._max_queue_size = max_queue_size
        self._running = True
        self._executor.submit(self._background_writer)

    def queue_entry(self, entry: LogEntry):
        with self._lock:
            if len(self._queue) < self._max_queue_size:
                self._queue.append(entry)

    def _background_writer(self):
        while self._running:
            time.sleep(self._flush_interval)
            self._flush()

    def _flush(self):
        with self._lock:
            entries_to_write = list(self._queue)
            self._queue.clear()

        if entries_to_write:
            self._write_batch(entries_to_write)

    def _write_batch(self, entries: list[LogEntry]):
        pass  # Override in main class

    def shutdown(self):
        self._running = False
        self._flush()


class AssistantLogProtocol:
    VERSION = "2.0.0"

    SETTINGS = {
        "max_log_size_mb": 10,
        "max_log_files": 5,
        "retention_days": 30,
        "async_write": True,
        "flush_interval": 0.5,
        "enable_fulltext_search": True,
        "index_ttl_seconds": 300,
    }

    def __init__(self, assistant_name: str = "default"):
        self.assistant_name = assistant_name
        self.log_file = LOG_DIR / "assistant_log.json"
        self.archive_dir = LOG_DIR / "archive"
        self.archive_dir.mkdir(exist_ok=True)
        self.snapshot_file = LOG_DIR / "system_snapshot.json"
        self.context_file = LOG_DIR / "conversation_contexts.json"
        self.index_file = LOG_DIR / "log_index.json"
        self.search_index_file = LOG_DIR / "search_index.json"
        self.session_id = str(uuid.uuid4())[:8]

        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._init_files()

        self._async_writer = None
        if self.SETTINGS["async_write"]:
            self._async_writer = AsyncLogWriter(
                flush_interval=self.SETTINGS["flush_interval"], max_queue_size=1000
            )
            self._async_writer._write_batch = self._process_batch_write

    def _init_files(self):
        for f in [
            self.log_file,
            self.snapshot_file,
            self.context_file,
            self.search_index_file,
        ]:
            if not f.exists():
                self._write_json(
                    f,
                    {
                        "version": self.VERSION,
                        "entries": [],
                        "created": datetime.datetime.now().isoformat(),
                    },
                )

    def _read_json(self, path: Path) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            note_failure("AssistantLogProtocol_Enhanced:L177", exc)
            return {
                "version": self.VERSION,
                "entries": [],
                "created": datetime.datetime.now().isoformat(),
            }

    def _write_json(self, path: Path, data: Any):
        with self._write_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

    def _check_rotation(self):
        if (
            self.log_file.exists()
            and self.log_file.stat().st_size
            > self.SETTINGS["max_log_size_mb"] * 1024 * 1024
        ):
            self._rotate_logs()

    def _rotate_logs(self):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"assistant_log_{timestamp}.json"
        archive_path = self.archive_dir / archive_name

        log_data = self._read_json(self.log_file)
        self._write_json(archive_path, log_data)

        archives = sorted(
            self.archive_dir.glob("assistant_log_*.json"),
            key=lambda x: x.stat().st_mtime,
        )
        while len(archives) > self.SETTINGS["max_log_files"]:
            archives[0].unlink()
            archives.pop(0)

        self._write_json(
            self.log_file,
            {
                "version": self.VERSION,
                "entries": [],
                "created": datetime.datetime.now().isoformat(),
            },
        )

    def _process_batch_write(self, entries: list[LogEntry]):
        log_data = self._read_json(self.log_file)
        for entry in entries:
            log_data["entries"].append(asdict(entry))
            self._update_search_index(entry)

        log_data["last_update"] = datetime.datetime.now().isoformat()
        log_data["total_entries"] = len(log_data["entries"])
        self._write_json(self.log_file, log_data)
        self._update_index_fast(entries)

    def _update_search_index(self, entry: LogEntry):
        searchable = entry.to_searchable().lower()
        index = self._read_json(self.search_index_file)

        if "tokens" not in index:
            index["tokens"] = defaultdict(list)

        tokens = set(re.findall(r"\b\w+\b", searchable))
        for token in tokens:
            if len(token) > 2:
                index["tokens"][token].append(entry.id)

        index["last_update"] = datetime.datetime.now().isoformat()
        self._write_json(self.search_index_file, dict(index))

    def log(
        self,
        category: LogCategory,
        level: LogLevel,
        source: str,
        message: str,
        data: (dict | None) = None,
        tags: (list[str] | None) = None,
        conversation_id: (str | None) = None,
        parent_entry_id: (str | None) = None,
    ) -> str:
        entry = LogEntry(
            id=str(uuid.uuid4())[:12],
            timestamp=datetime.datetime.now().isoformat(),
            category=category.value,
            level=level.name,
            source=source,
            message=message,
            data=data or {},
            tags=tags or [],
            conversation_id=conversation_id,
            parent_entry_id=parent_entry_id,
        )

        if self._async_writer:
            self._async_writer.queue_entry(entry)
            return entry.id

        log_data = self._read_json(self.log_file)
        log_data["entries"].append(asdict(entry))
        log_data["last_update"] = datetime.datetime.now().isoformat()
        log_data["total_entries"] = len(log_data["entries"])
        self._write_json(self.log_file, log_data)
        self._update_index_fast([entry])
        self._update_search_index(entry)

        return entry.id

    def _update_index_fast(self, entries: list[LogEntry]):
        index = self._read_json(self.index_file)

        if "by_category" not in index:
            index["by_category"] = {}
            index["by_level"] = {}
            index["by_source"] = {}
            index["tags"] = {}
            index["recent"] = []
            index["conversations"] = {}

        for entry in entries:
            cat = (
                entry.category
                if hasattr(entry, "category")
                else entry.get("category", "")
            )
            lvl = entry.level if hasattr(entry, "level") else entry.get("level", "")
            src = entry.source if hasattr(entry, "source") else entry.get("source", "")
            eid = entry.id if hasattr(entry, "id") else entry.get("id", "")
            tgs = entry.tags if hasattr(entry, "tags") else entry.get("tags", [])

            if cat not in index["by_category"]:
                index["by_category"][cat] = []
            index["by_category"][cat].append(eid)

            if lvl not in index["by_level"]:
                index["by_level"][lvl] = []
            index["by_level"][lvl].append(eid)

            if src not in index["by_source"]:
                index["by_source"][src] = []
            index["by_source"][src].append(eid)

            for tag in tgs:
                if tag not in index["tags"]:
                    index["tags"][tag] = []
                index["tags"][tag].append(eid)

            if entry.conversation_id:
                if entry.conversation_id not in index["conversations"]:
                    index["conversations"][entry.conversation_id] = []
                index["conversations"][entry.conversation_id].append(eid)

        index["recent"] = [
            eid if hasattr(eid, "id") else eid
            for eid in (
                [e.id if hasattr(e, "id") else e.get("id", "") for e in entries]
                + index["recent"]
            )
        ][:800]
        index["last_update"] = datetime.datetime.now().isoformat()

        self._write_json(self.index_file, dict(index))

    def query(
        self,
        category: (str | None) = None,
        level: (str | None) = None,
        source: (str | None) = None,
        tags: (list[str] | None) = None,
        limit: int = 100,
        since: (str | None) = None,
        until: (str | None) = None,
        conversation_id: (str | None) = None,
        search_text: (str | None) = None,
    ) -> list[dict]:
        entries = self._read_json(self.log_file).get("entries", [])

        if search_text and self.SETTINGS["enable_fulltext_search"]:
            search_index = self._read_json(self.search_index_file)
            tokens = search_text.lower().split()
            matching_ids = None
            for token in tokens:
                if token in search_index.get("tokens", {}):
                    token_ids = set(search_index["tokens"][token])
                    if matching_ids is None:
                        matching_ids = token_ids
                    else:
                        matching_ids &= token_ids
            if matching_ids:
                entries = [e for e in entries if e["id"] in matching_ids]

        filtered = []
        for e in reversed(entries):
            if category and e.get("category") != category:
                continue
            if level and e.get("level") != level:
                continue
            if source and e.get("source") != source:
                continue
            if since and e.get("timestamp") < since:
                continue
            if until and e.get("timestamp") > until:
                continue
            if conversation_id and e.get("conversation_id") != conversation_id:
                continue
            if tags and not any(t in e.get("tags", []) for t in tags):
                continue
            filtered.append(e)
            if len(filtered) >= limit:
                break

        return filtered

    def query_timerange(self, start: str, end: str, limit: int = 100) -> list[dict]:
        return self.query(since=start, until=end, limit=limit)

    def query_recent(self, limit: int = 20) -> list[dict]:
        return self.query(limit=limit)

    def query_by_conversation(self, conversation_id: str) -> list[dict]:
        return self.query(conversation_id=conversation_id, limit=1000)

    def get_python_status(self) -> dict:
        entries = self.query(category="python", limit=50)
        return {
            "last_python_log": entries[0] if entries else None,
            "python_entries_count": len(entries),
            "latest_versions": list(
                set(
                    e.get("data", {}).get("version")
                    for e in entries
                    if e.get("data", {}).get("version")
                )
            ),
        }

    def get_system_summary(self) -> dict:
        index = self._read_json(self.index_file)
        return {
            "session_id": self.session_id,
            "timestamp": datetime.datetime.now().isoformat(),
            "total_entries": self._read_json(self.log_file).get("total_entries", 0),
            "categories": list(index.get("by_category", {}).keys()),
            "sources": list(index.get("by_source", {}).keys()),
            "active_conversations": len(index.get("conversations", {})),
            "last_activity": self._read_json(self.log_file).get("last_update"),
            "log_size_mb": round(self.log_file.stat().st_size / (1024 * 1024), 2)
            if self.log_file.exists()
            else 0,
        }

    def take_snapshot(self):
        snapshot = {
            "timestamp": datetime.datetime.now().isoformat(),
            "hostname": os.environ.get("COMPUTERNAME", "unknown"),
            "username": os.environ.get("USERNAME", "unknown"),
            "os_version": os.environ.get("OS", "unknown"),
            "python_installations": self._get_python_info(),
            "environment_variables": {
                k: v
                for k, v in os.environ.items()
                if any(x in k.lower() for x in ["path", "python", "conda", "home"])
            },
            "disk_usage": self._get_disk_info(),
            "running_processes": self._get_running_python(),
            "conversation_contexts": self._get_all_conversations(),
        }
        self._write_json(self.snapshot_file, snapshot)
        self.log(LogCategory.SYSTEM, LogLevel.INFO, "snapshot", "System snapshot taken")
        return snapshot

    def _get_python_info(self) -> list[dict]:
        info = []
        # Production 3.15t leads. 3.14t was retired 2026-10-19; the 3.14m
        # venv is the only sanctioned 3.14 runtime.
        known = [
            r"C:\Azazael\.venvs\py315t\Scripts\python.exe",
            r"C:\Azazael\.venvs\py315\Scripts\python.exe",
            r"C:\Azazael\.venvs\py314m\Scripts\python.exe",
            r"C:\Program Files\Python311\python.exe",
            r"C:\Users\clint\anaconda3\python.exe",
        ]
        for path in known:
            if os.path.exists(path):
                result = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=1800)
                info.append(
                    {
                        "path": path,
                        "exists": True,
                        "version": result.stdout.strip()
                        if result.returncode == 0
                        else "unknown",
                    }
                )
        return info

    def _get_disk_info(self) -> dict:

        d = shutil.disk_usage("/")
        return {
            "total_gb": d.total // (1024**3),
            "free_gb": d.free // (1024**3),
            "used_percent": round((d.used / d.total) * 100, 1),
        }

    def _get_running_python(self) -> list[str]:
        result = subprocess.run(
            'tasklist /FO CSV /FI "IMAGENAME eq python.exe"',
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        timeout=1800)
        return [l for l in result.stdout.split("\n") if "python" in l.lower()]

    def log_python_scan(self, installations: list[dict]):
        self.log(
            category=LogCategory.PYTHON,
            level=LogLevel.INFO,
            source="PythonMapper",
            message=f"Found {len(installations)} Python installations",
            data={"installations": installations},
            tags=["python", "scan", "inventory"],
        )

    def log_tool_call(
        self,
        tool_name: str,
        args: dict,
        result: Any,
        conversation_id: (str | None) = None,
    ):
        self.log(
            category=LogCategory.TOOL_CALL,
            level=LogLevel.INFO,
            source="tool_dispatcher",
            message=f"Tool call: {tool_name}",
            data={"tool": tool_name, "arguments": args, "result": str(result)[:1000]},
            tags=["tool", tool_name],
            conversation_id=conversation_id,
        )

    def log_decision(
        self,
        decision: str,
        reasoning: str,
        alternatives: list[str],
        conversation_id: (str | None) = None,
    ):
        self.log(
            category=LogCategory.DECISION,
            level=LogLevel.INFO,
            source="assistant_reasoning",
            message=f"Decision: {decision}",
            data={
                "decision": decision,
                "reasoning": reasoning,
                "alternatives": alternatives,
            },
            tags=["decision", "reasoning"],
            conversation_id=conversation_id,
        )

    def start_conversation(self, assistant_id: str, goal: str) -> str:
        conversation_id = str(uuid.uuid4())[:12]
        context = {
            "conversation_id": conversation_id,
            "assistant_id": assistant_id,
            "start_time": datetime.datetime.now().isoformat(),
            "goal": goal,
            "entries": [],
            "decisions": [],
            "tools_used": [],
        }
        contexts = self._read_json(self.context_file)
        if "conversations" not in contexts:
            contexts["conversations"] = {}
        contexts["conversations"][conversation_id] = context
        contexts["last_update"] = datetime.datetime.now().isoformat()
        self._write_json(self.context_file, contexts)
        return conversation_id

    def end_conversation(self, conversation_id: str, outcome: str):
        contexts = self._read_json(self.context_file)
        if conversation_id in contexts.get("conversations", {}):
            contexts["conversations"][conversation_id]["outcome"] = outcome
            contexts["conversations"][conversation_id]["end_time"] = (
                datetime.datetime.now().isoformat()
            )
            self._write_json(self.context_file, contexts)

    def _get_all_conversations(self) -> dict:
        return self._read_json(self.context_file).get("conversations", {})

    def get_conversation(self, conversation_id: str) -> (dict | None):
        contexts = self._read_json(self.context_file)
        return contexts.get("conversations", {}).get(conversation_id)

    def get_assistant_context(self) -> dict:
        index = self._read_json(self.index_file)
        return {
            "protocol_version": self.VERSION,
            "session_id": self.session_id,
            "system_summary": self.get_system_summary(),
            "recent_errors": self.query(category="error", level="ERROR", limit=5),
            "python_status": self.get_python_status(),
            "active_conversations": list(index.get("conversations", {}).keys()),
            "log_file_location": str(self.log_file),
            "quick_query_examples": {
                "all_python_logs": '{"category": "python"}',
                "all_errors": '{"category": "error"}',
                "last_10_entries": '{"limit": 10}',
                "by_source": '{"source": "assistant_name"}',
                "timerange": '{"since": "2026-02-13T00:00:00", "until": "2026-02-13T23:59:59"}',
                "search_text": '{"search_text": "python installation"}',
                "conversation": '{"conversation_id": "abc123"}',
            },
        }

    def flush(self):
        if self._async_writer:
            self._async_writer._flush()

    def shutdown(self):
        self.flush()
        if self._async_writer:
            self._async_writer.shutdown()


def get_logger(assistant_name: str = "default") -> AssistantLogProtocol:
    return AssistantLogProtocol(assistant_name)


if __name__ == "__main__":
    logger = get_logger("system_init")

    print("=" * 60)
    print("ADLP ENHANCED v2.0")
    print("=" * 60)

    logger.log_python_scan(
        [
            {"path": "C:\\Python314\\python.exe", "version": "3.14.3"},  # scanner:ignore deliberate: __main__ sample scan payload
            {"path": "C:\\Program Files\\Python311\\python.exe", "version": "3.11.9"},  # scanner:ignore deliberate: __main__ sample scan payload
            {"path": "C:\\Users\\clint\\anaconda3\\python.exe", "version": "3.13.9"},  # scanner:ignore deliberate: __main__ sample scan payload
        ]
    )

    conv_id = logger.start_conversation("test_assistant", "Test conversation goal")
    logger.log_tool_call(
        "test_tool", {"arg1": "value1"}, "result_ok", conversation_id=conv_id
    )
    logger.log_decision(
        "Chose option A",
        "Option A is faster",
        ["Option A", "Option B"],
        conversation_id=conv_id,
    )
    logger.end_conversation(conv_id, "Completed successfully")

    snapshot = logger.take_snapshot()

    print(f"Log file: {logger.log_file}")
    print(f"Session: {logger.session_id}")
    print(f"Conversations: {logger.get_system_summary()['active_conversations']}")

    print("\n--- Test Full-Text Search ---")
    results = logger.query(search_text="python installations", limit=5)
    print(f"Found {len(results)} entries matching 'python installations'")

    print("\n--- Test Time Range Query ---")
    now = datetime.datetime.now()
    hour_ago = (now - datetime.timedelta(hours=1)).isoformat()
    recent = logger.query_timerange(hour_ago, now.isoformat(), limit=10)
    print(f"Found {len(recent)} entries in last hour")

    print("\n" + "=" * 60)
