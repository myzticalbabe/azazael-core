# coding: utf-8
"""
OS CONGRUENCY MANAGER - Windows System Integration

Deep Windows OS integration for congruent system management.

Features:
- Windows Services management
- Registry monitoring/editing
- Event log analysis
- Startup program management
- Windows Update monitoring
- Defender status
- Deep disk/partition management
- Network adapter control
- User session management
- Windows Task Scheduler integration
- System resource deep monitoring
- Driver management

Usage:
    >>> from os_congruency import OSCongruency
    >>> os_congruency = OSCongruency()
    >>> os_congruency.get_system_status()
"""

import os
import json
import threading
import subprocess
import winreg
from datetime import datetime
from typing import Any, List
from dataclasses import dataclass, field
import tkinter as tk
from tkinter import ttk, scrolledtext
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class SystemStatus:
    """Complete Windows system status."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    cpu_percent: float = 0.0
    cpu_count: int = 0
    cpu_freq_mhz: float = 0.0
    memory_percent: float = 0.0
    memory_used_gb: float = 0.0
    memory_total_gb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    network_adapters: int = 0
    network_bytes_sent_mb: float = 0.0
    network_bytes_recv_mb: float = 0.0
    process_count: int = 0
    thread_count: int = 0
    handle_count: int = 0
    services_running: int = 0
    services_stopped: int = 0
    uptime_seconds: float = 0.0


@dataclass
class WindowsService:
    """Windows service information."""

    name: str
    display_name: str
    status: str
    start_type: str
    username: str
    binary_path: str
    description: str


@dataclass
class StartupItem:
    """Startup program information."""

    name: str
    command: str
    location: str
    enabled: bool
    registry_key: str = ""


@dataclass
class DiskPartition:
    """Disk partition information."""

    drive_letter: str
    volume_name: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float
    filesystem: str


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("os_congruency:L103", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="monitor", priority=6, cooldown_seconds=30.0, description="Windows OS congruency manager with deep system integration")
class OSCongruency:

    def __init__(self):
        try:
            import psutil
            self.boot_time = datetime.fromtimestamp(psutil.boot_time())
        except Exception as exc:  # noqa: BLE001 — psutil unavailable; fall back to now
            note_failure("os_congruency:L114", exc)
            self.boot_time = datetime.now()
        self.lock = threading.Lock()

    def _run_powershell(self, command: str) -> str:
        """Run PowerShell command."""
        try:
            result = subprocess.run(
                ["powershell", "-Command", command],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=30,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as e:
            note_failure("os_congruency:L128", e)
            return f"Error: {e}"

    def _get_cpu_info(self) -> dict[str, Any]:
        """Get detailed CPU information."""
        try:
            import psutil
            cpu_freq = psutil.cpu_freq()
            return {
                "percent": psutil.cpu_percent(interval=1),
                "count": psutil.cpu_count(),
                "freq_mhz": cpu_freq.current if cpu_freq else 0,
                "freq_max_mhz": cpu_freq.max if cpu_freq else 0,
                "percent_per_core": psutil.cpu_percent(interval=None, percpu=True),
            }
        except Exception as exc:  # noqa: BLE001 — psutil read failed; return neutral defaults
            note_failure("os_congruency:L143", exc)
            return {"percent": 0, "count": 4, "freq_mhz": 0}

    def _get_memory_info(self) -> dict[str, Any]:
        """Get detailed memory information."""
        try:
            import psutil
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return {
                "percent": mem.percent,
                "used_gb": round(mem.used / (1024**3), 2),
                "total_gb": round(mem.total / (1024**3), 2),
                "available_gb": round(mem.available / (1024**3), 2),
                "swap_used_gb": round(swap.used / (1024**3), 2),
                "swap_total_gb": round(swap.total / (1024**3), 2),
            }
        except Exception as exc:  # noqa: BLE001 — psutil memory read failed; neutral defaults
            note_failure("os_congruency:L160", exc)
            return {"percent": 0, "used_gb": 0, "total_gb": 0}

    def _get_disk_info(self) -> dict[str, Any]:
        """Get detailed disk information."""
        try:
            import psutil
            disk = psutil.disk_usage("/")
            partitions = []
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    partitions.append(
                        {
                            "drive": part.device,
                            "mountpoint": part.mountpoint,
                            "filesystem": part.fstype,
                            "total_gb": round(usage.total / (1024**3), 2),
                            "used_gb": round(usage.used / (1024**3), 2),
                            "free_gb": round(usage.free / (1024**3), 2),
                            "percent": usage.percent,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 — per-partition probe best-effort
                    note_failure("os_congruency:L183", exc)

            io = psutil.disk_io_counters()
            io_read_mb = round(io.read_bytes / (1024**2), 2) if io is not None else 0
            io_write_mb = round(io.write_bytes / (1024**2), 2) if io is not None else 0
            return {
                "root_percent": disk.percent,
                "root_used_gb": round(disk.used / (1024**3), 2),
                "root_total_gb": round(disk.total / (1024**3), 2),
                "partitions": partitions,
                "io_read_mb": io_read_mb,
                "io_write_mb": io_write_mb,
            }
        except Exception as exc:  # noqa: BLE001 — psutil disk read failed; neutral defaults
            note_failure("os_congruency:L195", exc)
            return {"root_percent": 0, "partitions": []}

    def _get_network_info(self) -> dict[str, Any]:
        """Get network information."""
        try:
            import psutil
            counters = psutil.net_io_counters()
            interfaces = psutil.net_if_addrs()
            connections = len(psutil.net_connections())
            stats = psutil.net_if_stats()

            return {
                "bytes_sent_mb": round(counters.bytes_sent / (1024**2), 2),
                "bytes_recv_mb": round(counters.bytes_recv / (1024**2), 2),
                "packets_sent": counters.packets_sent,
                "packets_recv": counters.packets_recv,
                "interfaces": len(interfaces),
                "connections": connections,
                "interface_details": [
                    {"name": name, "up": stat.isup, "speed_mbps": stat.speed}
                    for name, stat in stats.items()
                ],
            }
        except Exception as exc:  # noqa: BLE001 — psutil network read failed; neutral defaults
            note_failure("os_congruency:L219", exc)
            return {"bytes_sent_mb": 0, "bytes_recv_mb": 0, "interfaces": 0}

    def _get_process_info(self) -> dict[str, Any]:
        """Get process information."""
        try:
            import psutil
            processes = list(
                psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"])
            )
            total_threads = 0

            for proc in processes[:20]:
                try:
                    proc_info = proc.info
                    total_threads += proc_info.get("num_threads", 0)
                except Exception as exc:  # noqa: BLE001 — per-process probe best-effort
                    note_failure("os_congruency:L235", exc)

            return {
                "count": len(processes),
                "threads": total_threads,
                "running": len(
                    [p for p in processes if p.info.get("status") == "running"]
                ),
            }
        except Exception as exc:  # noqa: BLE001 — psutil process read failed; neutral defaults
            note_failure("os_congruency:L245", exc)
            return {"count": 0, "threads": 0}

    def get_system_status(self) -> SystemStatus:
        """Get complete system status."""
        cpu = self._get_cpu_info()
        mem = self._get_memory_info()
        disk = self._get_disk_info()
        net = self._get_network_info()
        proc = self._get_process_info()
        services = self._get_services_status()

        uptime = (datetime.now() - self.boot_time).total_seconds()

        return SystemStatus(
            cpu_percent=cpu["percent"],
            cpu_count=cpu["count"],
            cpu_freq_mhz=cpu.get("freq_mhz", 0),
            memory_percent=mem["percent"],
            memory_used_gb=mem["used_gb"],
            memory_total_gb=mem["total_gb"],
            disk_percent=disk["root_percent"],
            disk_used_gb=disk["root_used_gb"],
            disk_total_gb=disk["root_total_gb"],
            network_adapters=net["interfaces"],
            network_bytes_sent_mb=net["bytes_sent_mb"],
            network_bytes_recv_mb=net["bytes_recv_mb"],
            process_count=proc["count"],
            thread_count=proc["threads"],
            services_running=services["running"],
            services_stopped=services["stopped"],
            uptime_seconds=uptime,
        )

    def _get_services_status(self) -> dict[str, int]:
        """Get services status counts."""
        try:
            result = self._run_powershell(
                '(Get-Service | Measure-Object).Count; (Get-Service | Where-Object {$_.Status -eq "Running"}).Count; (Get-Service | Where-Object {$_.Status -eq "Stopped"}).Count'
            )
            parts = result.split("\n")
            if len(parts) >= 3:
                try:
                    return {
                        "total": int(parts[0]),
                        "running": int(parts[1]),
                        "stopped": int(parts[2]),
                    }
                except ValueError as exc:
                    note_failure("os_congruency:L293", exc)
        except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001 — PS unavailable / no services
            note_failure("os_congruency:L295", exc)
        return {"total": 0, "running": 0, "stopped": 0}

    def get_services(self, running_only: bool = False) -> list[WindowsService]:
        """Get Windows services."""
        services = []
        try:
            filter_param = (
                "Where-Object {$_.Status -eq 'Running'}" if running_only else ""
            )
            cmd = f"Get-Service {filter_param} | Select-Object Name,DisplayName,Status,StartType,UserName,BinaryPath,Description | ConvertTo-Json -Depth 3"

            result = self._run_powershell(cmd)

            if result and "Error" not in result:
                import json as json_module

                try:
                    data = json_module.loads(result)
                    if isinstance(data, list):
                        for s in data:
                            services.append(
                                WindowsService(
                                    name=s.get("Name", ""),
                                    display_name=s.get("DisplayName", ""),
                                    status=s.get("Status", ""),
                                    start_type=s.get("StartType", ""),
                                    username=s.get("UserName", ""),
                                    binary_path=s.get("BinaryPath", ""),
                                    description=s.get("Description", ""),
                                )
                            )
                except json_module.JSONDecodeError as exc:
                    note_failure("os_congruency:L328", exc)
        except Exception as exc:  # noqa: BLE001 — service query best-effort; return what we have
            note_failure("os_congruency:L330", exc)
        return services

    def start_service(self, service_name: str) -> bool:
        """Start a Windows service."""
        try:
            result = self._run_powershell(f"Start-Service -Name '{service_name}'")
            return "Error" not in result
        except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001 — service operation failure
            note_failure("os_congruency:L339", exc)
            return False

    def stop_service(self, service_name: str) -> bool:
        """Stop a Windows service."""
        try:
            result = self._run_powershell(f"Stop-Service -Name '{service_name}'")
            return "Error" not in result
        except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001 — service operation failure
            note_failure("os_congruency:L347", exc)
            return False

    def get_startup_programs(self) -> list[StartupItem]:
        """Get startup programs from registry."""
        startup_items = []

        startup_paths = [
            (
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ),
            (
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run",
            ),
        ]

        for hive, key_path in startup_paths:
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        try:
                            name, value, value_type = winreg.EnumValue(key, i)
                            startup_items.append(
                                StartupItem(
                                    name=name,
                                    command=value,
                                    location=key_path,
                                    enabled=True,
                                    registry_key=f"{hive}:{key_path}",
                                )
                            )
                        except OSError as exc:  # individual registry value unusable — skip
                            note_failure("os_congruency:L392", exc)
            except OSError as exc:  # registry key missing/inaccessible — skip this hive
                note_failure("os_congruency:L394", exc)

        startup_folders = [
            os.path.expandvars(
                r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
            ),
            r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp",
        ]

        for folder in startup_folders:
            try:
                if os.path.exists(folder):
                    for item in os.listdir(folder):
                        if item.endswith((".lnk", ".url")):
                            startup_items.append(
                                StartupItem(
                                    name=item,
                                    command=os.path.join(folder, item),
                                    location=folder,
                                    enabled=True,
                                    registry_key="StartupFolder",
                                )
                            )
            except Exception as exc:  # noqa: BLE001 — startup folder unreadable; best-effort
                note_failure("os_congruency:L418", exc)

        return startup_items

    def get_event_logs(
        self, log_name: str = "System", max_events: int = 50
    ) -> list[dict]:
        """Get Windows event logs."""
        events = []
        try:
            result = self._run_powershell(
                f"Get-EventLog -LogName {log_name} -Newest {max_events} | Select-Object TimeGenerated,EntryType,EventID,Source,Message | ConvertTo-Json -Depth 2"
            )
            if result and "Error" not in result:
                import json as json_module

                try:
                    data = json_module.loads(result)
                    if isinstance(data, list):
                        for e in data:
                            events.append(
                                {
                                    "time": e.get("TimeGenerated", ""),
                                    "type": e.get("EntryType", ""),
                                    "event_id": e.get("EventID", 0),
                                    "source": e.get("Source", ""),
                                    "message": str(e.get("Message", ""))[:200],
                                }
                            )
                except json_module.JSONDecodeError as exc:
                    note_failure("os_congruency:L448", exc)
        except Exception as exc:  # noqa: BLE001 — event log query failed; return what we have
            note_failure("os_congruency:L450", exc)
        return events

    def get_windows_updates(self) -> dict[str, Any]:
        """Get Windows Update status."""
        try:
            result = self._run_powershell(
                "Get-WindowsUpdate -UpdatesOnly | ConvertTo-Json -Depth 2; (Get-Wulog -Newest 10 | Select-Object Time,Status,Message | ConvertTo-Json -Depth 2)"
            )
            return {"status": "checked", "output": result[:500]}
        except Exception as exc:  # noqa: BLE001 — update check failed; report error state
            note_failure("os_congruency:L461", exc)
            return {"status": "error", "output": ""}

    def get_users(self) -> list[dict[str, Any]]:
        """Get user sessions."""
        users = []
        try:
            result = self._run_powershell(
                "Get-CimInstance -ClassName Win32_LoggedOnUser | Select-Object Antecedent -Unique | ForEach-Object { $_.Antecedent.Name } | Select-Object -First 10"
            )
            for line in result.split("\n"):
                if line.strip():
                    users.append({"username": line.strip()})
        except Exception as exc:  # noqa: BLE001 — user enumeration best-effort
            note_failure("os_congruency:L474", exc)

        try:
            result = self._run_powershell("query user 2>nul")
            for line in result.split("\n"):
                if "ACTIVE" in line.upper() or "DISCONNECTED" in line.upper():
                    users.append({"session": line.strip()})
        except Exception as exc:  # noqa: BLE001 — session query best-effort
            note_failure("os_congruency:L482", exc)

        return users

    def get_disk_partitions(self) -> list[DiskPartition]:
        """Get detailed disk partition info."""
        import psutil
        partitions = []
        try:
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    partitions.append(
                        DiskPartition(
                            drive_letter=part.device,
                            volume_name=part.mountpoint,
                            total_gb=round(usage.total / (1024**3), 2),
                            used_gb=round(usage.used / (1024**3), 2),
                            free_gb=round(usage.free / (1024**3), 2),
                            percent=usage.percent,
                            filesystem=part.fstype,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — per-partition probe best-effort
                    note_failure("os_congruency:L506", exc)
        except Exception as exc:  # noqa: BLE001 — partition enumeration best-effort
            note_failure("os_congruency:L508", exc)
        return partitions

    def get_driver_list(self) -> list[dict[str, Any]]:
        """Get installed drivers."""
        drivers = []
        try:
            result = self._run_powershell(
                "Get-WindowsDriver -Online | Select-Object Driver,ProviderName,Version,Date,ClassDescription | ConvertTo-Json -Depth 2"
            )
            if result and "Error" not in result:
                import json as json_module

                try:
                    data = json_module.loads(result)
                    if isinstance(data, list):
                        for d in data:
                            drivers.append(
                                {
                                    "driver": str(d.get("Driver", ""))[:50],
                                    "provider": str(d.get("ProviderName", ""))[:30],
                                    "version": str(d.get("Version", ""))[:20],
                                    "class": str(d.get("ClassDescription", ""))[:30],
                                }
                            )
                except json_module.JSONDecodeError as exc:
                    note_failure("os_congruency:L534", exc)
        except Exception as exc:  # noqa: BLE001 — driver query best-effort
            note_failure("os_congruency:L536", exc)
        return drivers

    def get_summary(self) -> dict[str, Any]:
        """Get summary status — standard method expected by ClusterWORMZ."""
        result = self.get_comprehensive_status()
        return {"status": "ok", "worm": "OSCongruency", "version": "1.0.0", "data": result}

    def execute(self) -> dict[str, Any]:
        """Execute OSCongruency — standard method expected by ClusterWORMZ.
        
        Returns the comprehensive OS status.
        """
        try:
            result = self.get_comprehensive_status()
            return {"success": True, "data": result}
        except Exception as e:  # noqa: BLE001 — worm boundary must report, never raise
            note_failure("os_congruency:L553", e)
            return {"success": False, "error": str(e)}

    def get_comprehensive_status(self) -> dict[str, Any]:
        """Get comprehensive OS status."""
        status = self.get_system_status()
        services = self._get_services_status()
        startup = self.get_startup_programs()
        partitions = self.get_disk_partitions()

        return {
            "timestamp": status.timestamp,
            "os": {
                "version": "Windows",
                "uptime_hours": round(status.uptime_seconds / 3600, 2),
            },
            "cpu": {
                "percent": status.cpu_percent,
                "cores": status.cpu_count,
                "freq_mhz": status.cpu_freq_mhz,
            },
            "memory": {
                "percent": status.memory_percent,
                "used_gb": status.memory_used_gb,
                "total_gb": status.memory_total_gb,
            },
            "disk": {
                "percent": status.disk_percent,
                "used_gb": status.disk_used_gb,
                "total_gb": status.disk_total_gb,
                "partitions": len(partitions),
            },
            "network": {
                "adapters": status.network_adapters,
                "bytes_sent_mb": status.network_bytes_sent_mb,
                "bytes_recv_mb": status.network_bytes_recv_mb,
            },
            "processes": {
                "count": status.process_count,
                "threads": status.thread_count,
            },
            "services": {
                "running": services.get("running", 0),
                "stopped": services.get("stopped", 0),
            },
            "startup_items": len(startup),
        }


def run_popup():
    """Run OS Congruency with popup interface."""
    root = tk.Tk()
    root.title("OS CONGRUENCY - Windows System Integration")
    root.geometry("1100x800")
    root.configure(bg="#1a1a2e")

    os_congruency = OSCongruency()

    def log(msg, level="INFO"):
        colors = {
            "OK": "#00ff88",
            "FAIL": "#ff4444",
            "INFO": "#00aaff",
            "WARN": "#ffaa00",
        }
        timestamp = datetime.now().strftime("%H:%M:%S")
        text_area.insert("end", f"[{timestamp}] [{level}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    header = tk.Frame(root, bg="#16213e", pady=15)
    header.pack(fill="x")

    tk.Label(
        header,
        text="OS CONGRUENCY MANAGER",
        font=("Consolas", 28, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    tk.Label(
        header,
        text="Windows System Integration - Comprehensive Management",
        font=("Consolas", 12),
        bg="#16213e",
        fg="#7c3aed",
    ).pack()

    status_label = tk.Label(
        root,
        text="Ready",
        font=("Consolas", 14),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    status_label.pack(pady=5)

    progress_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    progress_frame.pack(fill="x", padx=20)

    progress = ttk.Progressbar(progress_frame, mode="determinate", length=1050)
    progress.pack(fill="x")

    progress_label = tk.Label(
        progress_frame,
        text="Ready",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    progress_label.pack()

    stats_frame = tk.Frame(root, bg="#1a1a2e", pady=5)
    stats_frame.pack(fill="x", padx=20)

    stats_label = tk.Label(
        stats_frame,
        text="System: CPU: 0% | Memory: 0% | Disk: 0%",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00d4ff",
    )
    stats_label.pack()

    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=38
    )
    text_area.pack(fill="both", expand=True, padx=20, pady=10)

    button_frame = tk.Frame(root, bg="#1a1a2e", pady=15)
    button_frame.pack(fill="x", padx=20)

    def run_full_scan():
        status_label.config(text="Running Full Scan...")
        progress["value"] = 0
        text_area.delete("1.0", "end")

        log("=" * 60, "INFO")
        log("OS CONGRUENCY - Full System Scan", "INFO")
        log("=" * 60, "INFO")

        progress["value"] = 20
        progress_label.config(text="Gathering CPU info...")
        root.update()

        cpu = os_congruency._get_cpu_info()
        log(
            f"CPU: {cpu['percent']}% | Cores: {cpu['count']} | Freq: {cpu.get('freq_mhz', 0)} MHz"
        )

        progress["value"] = 40
        progress_label.config(text="Gathering Memory info...")
        root.update()

        mem = os_congruency._get_memory_info()
        log(f"Memory: {mem['percent']}% | {mem['used_gb']}GB / {mem['total_gb']}GB")

        progress["value"] = 60
        progress_label.config(text="Gathering Disk info...")
        root.update()

        disk = os_congruency._get_disk_info()
        log(
            f"Disk: {disk['root_percent']}% | {disk['root_used_gb']}GB / {disk['root_total_gb']}GB"
        )
        log(f"Partitions: {len(disk['partitions'])}")

        progress["value"] = 80
        progress_label.config(text="Gathering Network info...")
        root.update()

        net = os_congruency._get_network_info()
        log(
            f"Network: {net['interfaces']} adapters | Sent: {net['bytes_sent_mb']}MB | Recv: {net['bytes_recv_mb']}MB"
        )

        services = os_congruency._get_services_status()
        log(
            f"Services: {services.get('running', 0)} running | {services.get('stopped', 0)} stopped"
        )

        startup = os_congruency.get_startup_programs()
        log(f"Startup Items: {len(startup)}")

        progress["value"] = 100
        progress_label.config(text="Scan Complete")

        status_label.config(text="Full Scan Complete!")
        stats_label.config(
            text=f"CPU: {cpu['percent']}% | Memory: {mem['percent']}% | Disk: {disk['root_percent']}%"
        )

    def show_services():
        text_area.delete("1.0", "end")
        log("=" * 60, "INFO")
        log("Windows Services", "INFO")
        log("=" * 60, "INFO")

        running = os_congruency.get_services(running_only=False)
        for svc in running[:20]:
            log(f"{svc.status}: {svc.display_name} ({svc.name})")

        log(f"\nTotal Services: {len(running)}")

    def show_startup():
        text_area.delete("1.0", "end")
        log("=" * 60, "INFO")
        log("Startup Programs", "INFO")
        log("=" * 60, "INFO")

        startup = os_congruency.get_startup_programs()
        for item in startup:
            log(f"{item.name}: {item.command[:60]}...")
        log(f"\nTotal Startup Items: {len(startup)}")

    def show_partitions():
        text_area.delete("1.0", "end")
        log("=" * 60, "INFO")
        log("Disk Partitions", "INFO")
        log("=" * 60, "INFO")

        parts = os_congruency.get_disk_partitions()
        for p in parts:
            log(
                f"{p.drive_letter}: {p.volume_name} | {p.percent}% | {p.used_gb}GB / {p.total_gb}GB | {p.filesystem}"
            )

    def show_comprehensive():
        status_label.config(text="Gathering Comprehensive Status...")
        progress["value"] = 0
        text_area.delete("1.0", "end")

        log("=" * 60, "INFO")
        log("COMPREHENSIVE SYSTEM STATUS", "INFO")
        log("=" * 60, "INFO")

        status = os_congruency.get_comprehensive_status()

        log("\n[OS]", "INFO")
        log(f"  Version: {status['os']['version']}", "INFO")
        log(f"  Uptime: {status['os']['uptime_hours']} hours", "INFO")

        log("\n[CPU]", "INFO")
        log(f"  Usage: {status['cpu']['percent']}%", "INFO")
        log(f"  Cores: {status['cpu']['cores']}", "INFO")
        log(f"  Frequency: {status['cpu']['freq_mhz']} MHz", "INFO")

        log("\n[Memory]", "INFO")
        log(f"  Usage: {status['memory']['percent']}%", "INFO")
        log(
            f"  Used: {status['memory']['used_gb']}GB / {status['memory']['total_gb']}GB",
            "INFO",
        )

        log("\n[Disk]", "INFO")
        log(f"  Usage: {status['disk']['percent']}%", "INFO")
        log(
            f"  Used: {status['disk']['used_gb']}GB / {status['disk']['total_gb']}GB",
            "INFO",
        )
        log(f"  Partitions: {status['disk']['partitions']}", "INFO")

        log("\n[Network]", "INFO")
        log(f"  Adapters: {status['network']['adapters']}", "INFO")
        log(f"  Sent: {status['network']['bytes_sent_mb']}MB", "INFO")
        log(f"  Received: {status['network']['bytes_recv_mb']}MB", "INFO")

        log("\n[Processes]", "INFO")
        log(f"  Count: {status['processes']['count']}", "INFO")
        log(f"  Threads: {status['processes']['threads']}", "INFO")

        log("\n[Services]", "INFO")
        log(f"  Running: {status['services']['running']}", "INFO")
        log(f"  Stopped: {status['services']['stopped']}", "INFO")

        log("\n[Startup]", "INFO")
        log(f"  Items: {status['startup_items']}", "INFO")

        progress["value"] = 100
        status_label.config(text="Comprehensive Status Complete!")

    def on_full_scan():
        for btn in [btn_scan, btn_services, btn_startup, btn_parts, btn_comp]:
            btn.config(state="disabled")
        root.update()
        try:
            run_full_scan()
        finally:
            for btn in [btn_scan, btn_services, btn_startup, btn_parts, btn_comp]:
                btn.config(state="normal")

    btn_scan = tk.Button(
        button_frame,
        text="FULL SCAN",
        command=on_full_scan,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#00ff88",
        width=12,
    )
    btn_scan.pack(side="left", padx=8)

    btn_services = tk.Button(
        button_frame,
        text="SERVICES",
        command=show_services,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#00d4ff",
        width=10,
    )
    btn_services.pack(side="left", padx=8)

    btn_startup = tk.Button(
        button_frame,
        text="STARTUP",
        command=show_startup,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#ffaa00",
        width=10,
    )
    btn_startup.pack(side="left", padx=8)

    btn_parts = tk.Button(
        button_frame,
        text="PARTITIONS",
        command=show_partitions,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#ff00ff",
        width=12,
    )
    btn_parts.pack(side="left", padx=8)

    btn_comp = tk.Button(
        button_frame,
        text="COMPREHENSIVE",
        command=show_comprehensive,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#7c3aed",
        width=14,
    )
    btn_comp.pack(side="left", padx=8)

    close_btn = tk.Button(
        button_frame,
        text="CLOSE",
        command=root.destroy,
        font=("Consolas", 13, "bold"),
        bg="#ff4444",
        fg="#ffffff",
        width=10,
    )
    close_btn.pack(side="right", padx=8)

    log("OS CONGRUENCY Ready", "INFO")
    log("Click FULL SCAN for comprehensive status", "INFO")

    root.mainloop()


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(
        description="OS CONGRUENCY - Windows System Integration"
    )
    parser.add_argument("--status", action="store_true", help="Get system status")
    parser.add_argument("--services", action="store_true", help="list services")
    parser.add_argument("--startup", action="store_true", help="list startup programs")
    parser.add_argument(
        "--partitions", action="store_true", help="list disk partitions"
    )
    parser.add_argument(
        "--comprehensive", action="store_true", help="Comprehensive status"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--popup", action="store_true", help="Show popup window")

    args = parser.parse_args()

    os_congruency = OSCongruency()

    if args.popup:
        run_popup()
    elif args.json:
        if args.status:
            print(json.dumps(os_congruency.get_system_status().__dict__, indent=2))
        elif args.comprehensive:
            print(json.dumps(os_congruency.get_comprehensive_status(), indent=2))
        elif args.services:
            svcs = os_congruency.get_services()
            print(json.dumps([s.__dict__ for s in svcs], indent=2, default=str))
        elif args.startup:
            items = os_congruency.get_startup_programs()
            print(json.dumps([i.__dict__ for i in items], indent=2, default=str))
        elif args.partitions:
            parts = os_congruency.get_disk_partitions()
            print(json.dumps([p.__dict__ for p in parts], indent=2, default=str))
        else:
            print(json.dumps(os_congruency.get_comprehensive_status(), indent=2))
    else:
        if args.comprehensive:
            status = os_congruency.get_comprehensive_status()
            print(
                f"CPU: {status['cpu']['percent']}% | Memory: {status['memory']['percent']}% | Disk: {status['disk']['percent']}%"
            )
            print(
                f"Services: {status['services']['running']} running | {status['services']['stopped']} stopped"
            )
        elif args.status:
            status = os_congruency.get_system_status()
            print(
                f"CPU: {status.cpu_percent}% | Memory: {status.memory_percent}% | Disk: {status.disk_percent}%"
            )
            print(
                f"Processes: {status.process_count} | Services: {status.services_running} running"
            )
        elif args.services:
            for svc in os_congruency.get_services()[:20]:
                print(f"{svc.status}: {svc.display_name}")
        elif args.startup:
            for item in os_congruency.get_startup_programs():
                print(f"{item.name}: {item.command[:60]}...")
        elif args.partitions:
            for p in os_congruency.get_disk_partitions():
                print(
                    f"{p.drive_letter}: {p.percent}% | {p.used_gb}GB / {p.total_gb}GB"
                )
        else:
            print("OS CONGRUENCY - Windows System Integration")
            print()
            print("Usage:")
            print("  python OS_CONGRUENCY.py --status        Get system status")
            print("  python OS_CONGRUENCY.py --services     list services")
            print("  python OS_CONGRUENCY.py --startup      list startup programs")
            print("  python OS_CONGRUENCY.py --partitions   list disk partitions")
            print("  python OS_CONGRUENCY.py --comprehensive Full status")
            print("  python OS_CONGRUENCY.py --popup        Show GUI")


if __name__ == "__main__":
    main()
