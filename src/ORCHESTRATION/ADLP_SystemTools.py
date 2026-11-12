#!/usr/bin/env python3
"""
Assistant Dynamic Logging Protocol (ADLP) - System Tools Edition
================================================================
Centralized logging with full system tool access and execution.
Version: 3.0.0
"""

import os
import json
import datetime
import uuid
import subprocess
import time
from pathlib import Path
from enum import Enum
from typing import Any, List
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

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
    CMD_EXECUTION = "cmd_execution"
    DISK = "disk"
    PROCESS = "process"
    REGISTRY = "registry"


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


class SystemToolResult:
    def __init__(
        self,
        tool_name: str,
        success: bool,
        output: str,
        error: str,
        return_code: int,
        duration_ms: float,
    ):
        self.tool_name = tool_name
        self.success = success
        self.output = output
        self.error = error
        self.return_code = return_code
        self.duration_ms = duration_ms
        self.timestamp = datetime.datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "tool": self.tool_name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "return_code": self.return_code,
            "duration_ms": round(self.duration_ms, 2),
            "timestamp": self.timestamp,
        }


class SystemToolExecutor:
    SYSTEM_TOOLS = {
        # Process Management
        "ps": "list running processes",
        "tasklist": "list all running processes",
        "taskkill": "Terminate a process",
        "get-process": "Get process info (PowerShell)",
        "stop-process": "Stop a process (PowerShell)",
        # Disk & Storage
        "dir": "list directory contents",
        "tree": "Display directory tree",
        "chkdsk": "Check disk for errors",
        "defrag": "Defragment disk",
        "diskpart": "Disk partitioning tool",
        "fsutil": "File system utility",
        "mountvol": "Mount volume management",
        # Network
        "ipconfig": "Network configuration",
        "ping": "Test network connectivity",
        "tracert": "Trace route to host",
        "pathping": "Path ping utility",
        "netstat": "Network statistics",
        "arp": "ARP table",
        "nslookup": "DNS lookup",
        "netsh": "Network shell",
        "ip": "IP information (if available)",
        # System Information
        "systeminfo": "System information",
        "hostname": "Display hostname",
        "whoami": "Current user info",
        "ver": "OS version",
        "driverquery": "list installed drivers",
        "sfc": "System file checker",
        "dxdiag": "DirectX diagnostic",
        # Services
        "net": "Network/service management",
        "sc": "Service control manager",
        "tasklist /svc": "list services by process",
        # Environment
        "set": "Display environment variables",
        "echo %PATH%": "Display PATH variable",
        "where": "Locate executables",
        # Windows Specific
        "reg": "Registry editor",
        "powercfg": "Power configuration",
        "wevtutil": "Windows event utility",
        "eventcreate": "Create custom event",
        # Performance
        "perfmon": "Performance monitor",
        "resmon": "Resource monitor",
        "typeperf": "Performance counter tool",
    }

    POWERSHELL_TOOLS = {
        "Get-Process": "Get process information",
        "Get-Service": "Get service status",
        "Get-EventLog": "Read event logs",
        "Get-WmiObject": "WMI queries",
        "Get-CimInstance": "CIM queries",
        "Test-Connection": "Ping with PowerShell",
        "Resolve-DnsName": "DNS resolution",
        "Get-NetIPAddress": "IP address info",
        "Get-Disk": "Disk information",
        "Get-Volume": "Volume information",
        "Get-ItemProperty": "Registry/file properties",
        "Invoke-Expression": "Run commands",
        "Invoke-Command": "Remote/local execution",
    }

    def __init__(self, logger: "AssistantLogProtocol"):
        self.logger = logger
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sys_tool")

    def run_command(
        self,
        command: str,
        timeout: int = 30,
        shell: bool = True,
        capture_output: bool = True,
        workdir: (str | None) = None,
    ) -> SystemToolResult:
        start_time = time.time()
        try:
            result = subprocess.run(
                command,
                shell=shell,
                capture_output=capture_output,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
                cwd=workdir,
            )
            success = result.returncode == 0
            output = result.stdout
            error = result.stderr
            return_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            note_failure("ADLP_SystemTools:L191", exc)
            success = False
            output = ""
            error = "Command timed out"
            return_code = -1
        except Exception as e:
            note_failure("ADLP_SystemTools:L196", e)
            success = False
            output = ""
            error = str(e)
            return_code = -2

        duration_ms = (time.time() - start_time) * 1000

        self.logger.log(
            category=LogCategory.CMD_EXECUTION,
            level=LogLevel.INFO if success else LogLevel.ERROR,
            source="SystemToolExecutor",
            message=f"Executed: {command[:800]}",
            data={
                "command": command,
                "success": success,
                "return_code": return_code,
                "duration_ms": round(duration_ms, 2),
                "output_length": len(output),
                "error_length": len(error),
            },
            tags=["system_tool", "cmd", command.split()[0] if command else "unknown"],
        )

        return SystemToolResult(
            tool_name=command.split()[0] if command else "unknown",
            success=success,
            output=output,
            error=error,
            return_code=return_code,
            duration_ms=duration_ms,
        )

    def run_powershell(
        self,
        script: str,
        timeout: int = 30,
        execution_policy: str = "Bypass",
    ) -> SystemToolResult:
        full_cmd = f'powershell -ExecutionPolicy {execution_policy} -NoProfile -Command "{script}"'
        return self.run_command(full_cmd, timeout=timeout)

    def get_processes(self) -> SystemToolResult:
        return self.run_command("tasklist /FO CSV /NH", timeout=15)

    def get_process_by_name(self, name: str) -> SystemToolResult:
        return self.run_command(f'tasklist /FI "IMAGENAME eq {name}"', timeout=15)

    def kill_process(self, pid: int) -> SystemToolResult:
        return self.run_command(f"taskkill /PID {pid} /F", timeout=10)

    def kill_process_by_name(self, name: str) -> SystemToolResult:
        return self.run_command(f"taskkill /IM {name} /F", timeout=10)

    def get_disk_info(self) -> SystemToolResult:
        return self.run_command(
            "wmic diskdrive get model,size,status /FORMAT:CSV", timeout=30
        )

    def get_volume_info(self) -> SystemToolResult:
        return self.run_command(
            "wmic volume get driveletter,label, freespace, capacity /FORMAT:CSV",
            timeout=30,
        )

    def get_network_info(self) -> SystemToolResult:
        return self.run_command("ipconfig /all", timeout=30)

    def get_netstat(self, proto: str = "TCP") -> SystemToolResult:
        return self.run_command(f"netstat -ano -p {proto}", timeout=30)

    def get_system_info(self) -> SystemToolResult:
        return self.run_command("systeminfo", timeout=60)

    def get_drivers(self) -> SystemToolResult:
        return self.run_command("driverquery /FO CSV /NH", timeout=30)

    def get_services(self) -> SystemToolResult:
        return self.run_command("sc query state= all", timeout=30)

    def get_environment(self) -> SystemToolResult:
        return self.run_command("set", timeout=15)

    def get_path_entries(self) -> SystemToolResult:
        return self.run_command("echo %PATH%", timeout=10)

    def find_executable(self, name: str) -> SystemToolResult:
        return self.run_command(f"where {name}", timeout=15)

    def list_directory(self, path: str, recursive: bool = False) -> SystemToolResult:
        cmd = f'dir {" /s " if recursive else " "} "{path}"'
        return self.run_command(cmd, timeout=60)

    def get_registry_value(self, key: str, value: str) -> SystemToolResult:
        return self.run_command(f'reg query "{key}" /v {value}', timeout=15)

    def get_event_logs(
        self, logtype: str = "System", count: int = 50
    ) -> SystemToolResult:
        return self.run_command(
            f"wevtutil qe {logtype} /c:{count} /rd:true /f:text", timeout=30
        )

    def ping_host(self, host: str, count: int = 4) -> SystemToolResult:
        return self.run_command(f"ping -n {count} {host}", timeout=30)

    def trace_route(self, host: str) -> SystemToolResult:
        return self.run_command(f"tracert -d -h 30 {host}", timeout=60)

    def dns_lookup(self, host: str) -> SystemToolResult:
        return self.run_command(f"nslookup {host}", timeout=15)

    def get_python_info(self) -> dict:
        result = self.run_command("where python")
        result3 = self.run_command("where python3")
        result_py = self.run_command("where py")

        versions = []
        for r in [result, result3]:
            for line in r.output.split("\n"):
                if line.strip() and os.path.exists(line.strip()):
                    v_result = self.run_command(f'"{line.strip()}" --version')
                    if v_result.success:
                        versions.append(
                            {"path": line.strip(), "version": v_result.output.strip()}
                        )

        return {
            "python_paths": result.output.split("\n") if result.output else [],
            "py_exe": result_py.output.strip() if result_py.output else None,
            "installations": versions,
        }

    def get_all_python_info(self) -> SystemToolResult:
        info = self.get_python_info()
        output = json.dumps(info, indent=2)
        return SystemToolResult(
            tool_name="get_all_python_info",
            success=True,
            output=output,
            error="",
            return_code=0,
            duration_ms=0,
        )

    def system_diagnostic(self) -> dict:
        diagnostics = {
            "timestamp": datetime.datetime.now().isoformat(),
            "processes": self.get_processes().to_dict(),
            "disk": self.get_volume_info().to_dict(),
            "network": self.get_network_info().to_dict(),
            "system": self.get_system_info().to_dict(),
            "python": self.get_python_info(),
        }

        self.logger.log(
            category=LogCategory.SYSTEM,
            level=LogLevel.INFO,
            source="SystemToolExecutor",
            message="Full system diagnostic completed",
            data=diagnostics,
            tags=["diagnostic", "system", "full_scan"],
        )

        return diagnostics

    def shutdown(self):
        self.executor.shutdown(wait=False)


class AssistantLogProtocol:
    VERSION = "3.0.0"

    def __init__(self, assistant_name: str = "default"):
        self.assistant_name = assistant_name
        self.log_file = LOG_DIR / "assistant_log.json"
        self.snapshot_file = LOG_DIR / "system_snapshot.json"
        self.context_file = LOG_DIR / "conversation_contexts.json"
        self.index_file = LOG_DIR / "log_index.json"

        self.session_id = str(uuid.uuid4())[:8]
        self._lock = __import__("threading").Lock()

        self._init_files()
        self.system_tools = SystemToolExecutor(self)

    def _init_files(self):
        for f in [
            self.log_file,
            self.snapshot_file,
            self.context_file,
            self.index_file,
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
        except Exception as exc:
            note_failure("ADLP_SystemTools:L403", exc)
            return {
                "version": self.VERSION,
                "entries": [],
                "created": datetime.datetime.now().isoformat(),
            }

    def _write_json(self, path: Path, data: Any):
        pass

        with self._lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

    def log(
        self,
        category: LogCategory,
        level: LogLevel,
        source: str,
        message: str,
        data: (dict | None) = None,
        tags: (list[str] | None) = None,
        conversation_id: (str | None) = None,
    ) -> str:
        entry = {
            "id": str(uuid.uuid4())[:12],
            "timestamp": datetime.datetime.now().isoformat(),
            "category": category.value,
            "level": level.name,
            "source": source,
            "message": message,
            "data": data or {},
            "tags": tags or [],
            "conversation_id": conversation_id,
        }

        log_data = self._read_json(self.log_file)
        log_data["entries"].append(entry)
        log_data["last_update"] = datetime.datetime.now().isoformat()
        log_data["total_entries"] = len(log_data["entries"])
        self._write_json(self.log_file, log_data)

        self._update_index(entry)

        return entry["id"]

    def _update_index(self, entry: dict):
        index = self._read_json(self.index_file)

        for key in ["by_category", "by_level", "by_source", "tags"]:
            if key not in index:
                index[key] = {}

        cat = entry.get("category", "")
        lvl = entry.get("level", "")
        src = entry.get("source", "")

        if cat not in index["by_category"]:
            index["by_category"][cat] = []
        index["by_category"][cat].append(entry["id"])

        if lvl not in index["by_level"]:
            index["by_level"][lvl] = []
        index["by_level"][lvl].append(entry["id"])

        if src not in index["by_source"]:
            index["by_source"][src] = []
        index["by_source"][src].append(entry["id"])

        for tag in entry.get("tags", []):
            if tag not in index["tags"]:
                index["tags"][tag] = []
            index["tags"][tag].append(entry["id"])

        self._write_json(self.index_file, index)

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
            if category and e.get("category") != category:
                continue
            if level and e.get("level") != level:
                continue
            if source and e.get("source") != source:
                continue
            if since and e.get("timestamp") < since:
                continue
            if tags and not any(t in e.get("tags", []) for t in tags):
                continue
            filtered.append(e)
            if len(filtered) >= limit:
                break

        return filtered

    def get_system_summary(self) -> dict:
        index = self._read_json(self.index_file)
        return {
            "session_id": self.session_id,
            "timestamp": datetime.datetime.now().isoformat(),
            "total_entries": self._read_json(self.log_file).get("total_entries", 0),
            "categories": list(index.get("by_category", {}).keys()),
            "sources": list(index.get("by_source", {}).keys()),
        }

    def run_tool(self, tool_name: str, **kwargs) -> SystemToolResult:
        if hasattr(self.system_tools, tool_name):
            method = getattr(self.system_tools, tool_name)
            if callable(method):
                return method(**kwargs)  # type: ignore[reportReturnType]

        result = self.system_tools.run_command(tool_name, **kwargs)
        return result

    def get_python_status(self) -> dict:
        info = self.system_tools.get_python_info()
        return {"python_info": info}

    def take_snapshot(self):
        snapshot = {
            "timestamp": datetime.datetime.now().isoformat(),
            "hostname": os.environ.get("COMPUTERNAME", "unknown"),
            "username": os.environ.get("USERNAME", "unknown"),
            "system_info": self.system_tools.get_system_info().to_dict(),
            "processes": self.system_tools.get_processes().to_dict(),
            "python": self.system_tools.get_python_info(),
        }
        self._write_json(self.snapshot_file, snapshot)
        self.log(LogCategory.SYSTEM, LogLevel.INFO, "snapshot", "System snapshot taken")
        return snapshot


def get_logger(assistant_name: str = "default") -> AssistantLogProtocol:
    return AssistantLogProtocol(assistant_name)


if __name__ == "__main__":
    logger = get_logger("system_tools_test")

    print("=" * 60)
    print("ADLP SYSTEM TOOLS EDITION v3.0")
    print("=" * 60)

    print("\n[1] Running process list...")
    result = logger.run_tool("get_processes")
    print(f"   Success: {result.success}")
    print(f"   Processes listed: {len(result.output.split(chr(10)))}")

    print("\n[2] Running network info...")
    result = logger.run_tool("get_network_info")
    print(f"   Success: {result.success}")

    print("\n[3] Getting Python info...")
    result = logger.run_tool("get_all_python_info")
    print(f"   Output: {result.output[:200]}...")

    print("\n[4] Running system diagnostic...")
    diag = logger.system_tools.system_diagnostic()
    print(f"   Diagnostic complete: {len(diag)} categories")

    print("\n[5] Taking full snapshot...")
    snapshot = logger.take_snapshot()
    print("   Snapshot saved")

    print("\n[6] Querying recent logs...")
    logs = logger.query(limit=5)
    print(f"   Found {len(logs)} recent entries")

    print("\n" + "=" * 60)
    print("Available System Tools:")
    print("-" * 60)
    tools = [
        "get_processes",
        "get_process_by_name",
        "kill_process",
        "get_disk_info",
        "get_volume_info",
        "get_network_info",
        "get_netstat",
        "get_system_info",
        "get_drivers",
        "get_services",
        "get_environment",
        "find_executable",
        "list_directory",
        "get_registry_value",
        "ping_host",
        "trace_route",
        "dns_lookup",
        "get_all_python_info",
        "system_diagnostic",
    ]
    for tool in tools:
        print(f"   - {tool}")
    print("=" * 60)
