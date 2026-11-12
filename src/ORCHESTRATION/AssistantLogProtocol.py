#!/usr/bin/env python3
"""
Assistant Dynamic Logging Protocol (ADLP)
==========================================
Centralized logging system for instant AI assistant access.
Version: 1.0.0
"""

import os
import json
import threading
import datetime
import hashlib
import uuid
import subprocess
from pathlib import Path
from enum import Enum
from typing import Any, List
from dataclasses import dataclass, field, asdict
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
    checksum: str = ""

    def __post_init__(self):
        self.checksum = self._compute_checksum()

    def _compute_checksum(self) -> str:
        content = f"{self.timestamp}{self.category}{self.source}{self.message}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def validate(self) -> bool:
        return self.checksum == self._compute_checksum()


@dataclass
class SystemSnapshot:
    timestamp: str
    hostname: str
    username: str
    os_version: str
    python_installations: list[dict]
    environment_variables: dict
    network_info: dict
    disk_usage: dict
    running_processes: list[str]
    active_connections: list[str]


class AssistantLogProtocol:
    VERSION = "1.0.0"

    def __init__(self, assistant_name: str = "default"):
        self.assistant_name = assistant_name
        self.log_file = LOG_DIR / "assistant_log.json"
        self.snapshot_file = LOG_DIR / "system_snapshot.json"
        self.index_file = LOG_DIR / "log_index.json"
        self.session_id = str(uuid.uuid4())[:8]
        self._lock = threading.Lock()

        self._init_files()

    def _init_files(self):
        for f in [self.log_file, self.snapshot_file, self.index_file]:
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
        except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError) as exc:
            note_failure("AssistantLogProtocol:L112", exc)
            return {
                "version": self.VERSION,
                "entries": [],
                "created": datetime.datetime.now().isoformat(),
            }

    def _write_json(self, path: Path, data: Any):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except (PermissionError, OSError) as exc:
            note_failure("AssistantLogProtocol:L131", exc)

    def log(
        self,
        category: LogCategory,
        level: LogLevel,
        source: str,
        message: str,
        data: (dict | None) = None,
        tags: (list[str] | None) = None,
    ):
        entry = LogEntry(
            id=str(uuid.uuid4())[:12],
            timestamp=datetime.datetime.now().isoformat(),
            category=category.value,
            level=level.name,
            source=source,
            message=message,
            data=data or {},
            tags=tags or [],
        )

        with self._lock:
            log_data = self._read_json(self.log_file)
            log_data["entries"].append(asdict(entry))
            log_data["last_update"] = datetime.datetime.now().isoformat()
            log_data["total_entries"] = len(log_data["entries"])
            self._write_json(self.log_file, log_data)
            self._update_index(entry)

        return entry.id

    def _update_index(self, entry: LogEntry):
        index = self._read_json(self.index_file)

        if "by_category" not in index:
            index["by_category"] = {}
            index["by_level"] = {}
            index["by_source"] = {}
            index["tags"] = {}
            index["recent"] = []

        cat_key = f"{entry.category}"
        if cat_key not in index["by_category"]:
            index["by_category"][cat_key] = []
        index["by_category"][cat_key].append(entry.id)

        if entry.level not in index["by_level"]:
            index["by_level"][entry.level] = []
        index["by_level"][entry.level].append(entry.id)

        if entry.source not in index["by_source"]:
            index["by_source"][entry.source] = []
        index["by_source"][entry.source].append(entry.id)

        for tag in entry.tags:
            if tag not in index["tags"]:
                index["tags"][tag] = []
            index["tags"][tag].append(entry.id)

        index["recent"] = [entry.id] + index["recent"][:99]
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
    ) -> list[dict]:
        entries = self._read_json(self.log_file).get("entries", [])

        filtered = []
        for e in reversed(entries):
            if category and e["category"] != category:
                continue
            if level and e["level"] != level:
                continue
            if source and e["source"] != source:
                continue
            if since and e["timestamp"] < since:
                continue
            if tags and not any(t in e["tags"] for t in tags):
                continue
            filtered.append(e)
            if len(filtered) >= limit:
                break

        return filtered

    def get_python_status(self) -> dict:
        """Get current Python environment status."""
        entries = self.query(category="python", limit=50)
        return {
            "last_python_log": entries[0] if entries else None,
            "python_entries_count": len(entries),
        }

    def get_system_summary(self) -> dict:
        """Get quick system summary for assistants."""
        return {
            "session_id": self.session_id,
            "timestamp": datetime.datetime.now().isoformat(),
            "total_entries": self._read_json(self.log_file).get("total_entries", 0),
            "categories": list(
                self._read_json(self.index_file).get("by_category", {}).keys()
            ),
            "last_activity": self._read_json(self.log_file).get("last_update"),
        }

    def take_snapshot(self):
        """Capture full system snapshot."""

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
            "running_python": self._get_running_python(),
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
                ver = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=1800)
                info.append(
                    {
                        "path": path,
                        "exists": True,
                        "version": ver.stdout.strip()
                        if ver.returncode == 0
                        else "unknown",
                    }
                )
        return info

    def _get_disk_info(self) -> dict:

        d = shutil.disk_usage("/")
        return {"total_gb": d.total // (1024**3), "free_gb": d.free // (1024**3)}

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

    def log_error(
        self, source: str, message: str, error_details: (dict | None) = None
    ):
        self.log(
            category=LogCategory.ERROR,
            level=LogLevel.ERROR,
            source=source,
            message=message,
            data=error_details or {},
        )

    def get_assistant_context(self) -> dict:
        """Generate context summary for new assistants."""
        return {
            "protocol_version": self.VERSION,
            "session_id": self.session_id,
            "system_summary": self.get_system_summary(),
            "recent_errors": self.query(category="error", level="ERROR", limit=5),
            "python_status": self.get_python_status(),
            "log_file_location": str(self.log_file),
            "quick_query_examples": {
                "all_python_logs": '{"category": "python"}',
                "all_errors": '{"category": "error"}',
                "last_10_entries": '{"limit": 10}',
                "by_source_assistant": '{"source": "assistant_name"}',
            },
        }


def get_logger(assistant_name: str = "default") -> AssistantLogProtocol:
    return AssistantLogProtocol(assistant_name)


def quick_log(category: str, level: str, source: str, message: str, **kwargs):
    logger = get_logger("quick")
    logger.log(
        LogCategory(category) if isinstance(category, str) else category,
        LogLevel[level.upper()] if isinstance(level, str) else level,
        source,
        message,
        **kwargs,
    )


if __name__ == "__main__":
    logger = get_logger("system_init")

    logger.log_python_scan(
        [
            {"path": "C:\\Python314\\python.exe", "version": "3.14.3"},  # scanner:ignore deliberate: __main__ sample scan payload
            {"path": "C:\\Program Files\\Python311\\python.exe", "version": "3.11.9"},  # scanner:ignore deliberate: __main__ sample scan payload
            {"path": "C:\\Users\\clint\\anaconda3\\python.exe", "version": "3.13.9"},  # scanner:ignore deliberate: __main__ sample scan payload
        ]
    )

    snapshot = logger.take_snapshot()

    print("=" * 50)
    print("ASSISTANT DYNAMIC LOGGING PROTOCOL")
    print("=" * 50)
    print(f"Log file: {logger.log_file}")
    print(f"Snapshot: {logger.snapshot_file}")
    print(f"Session: {logger.session_id}")
    print("\nSystem Summary:")
    summary = logger.get_system_summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\nContext for new assistants:")
    ctx = logger.get_assistant_context()
    print(json.dumps(ctx, indent=2, default=str))
