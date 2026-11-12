#!/usr/bin/env python3
"""
System Manager - Full System Management Suite
=============================================
Comprehensive system management using all crawlers and tools.
Version: 1.0.0
"""

import os
import json
import datetime
import shlex
import subprocess
import shutil
import re
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LOG_DIR = Path(os.environ.get("APPDATA", "~/.appdata")).expanduser() / "SystemManager"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class HealthStatus(Enum):
    GOOD = "good"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class SystemComponent:
    name: str
    status: str
    details: dict = field(default_factory=dict)
    last_checked: str = ""
    recommendations: list[str] = field(default_factory=list)


class SystemCrawler:
    """Base class for all system crawlers."""

    def __init__(self, name: str):
        self.name = name
        self.results = []

    def crawl(self) -> list[dict]:
        raise NotImplementedError


class PythonCrawler(SystemCrawler):
    """Crawler for Python installations."""

    def __init__(self):
        super().__init__("Python")
        self.installations = []
        self.environments = []

    def crawl(self) -> list[dict]:
        results = []
        found = {}

        # where command
        for cmd in ["where python", "where python3", "where py"]:
            out, _, rc = self._run_cmd(cmd)
            if rc == 0:
                for line in out.strip().split("\n"):
                    line = line.strip()
                    if line and line not in found:
                        ver = self._get_version(line)
                        found[line] = {"path": line, "version": ver, "source": cmd}
                        self.installations.append(found[line])

        # Known paths. Production 3.15t leads; 3.14t was retired 2026-10-19.
        known_paths = [
            r"C:\Azazael\.venvs\py315t\Scripts\python.exe",
            r"C:\Azazael\.venvs\py314m\Scripts\python.exe",
            r"C:\Program Files\Python311\python.exe",
            r"C:\Users\clint\anaconda3\python.exe",
            r"C:\Users\clint\.local\bin\python3.11.exe",
        ]
        for p in known_paths:
            if os.path.exists(p) and p not in found:
                ver = self._get_version(p)
                found[p] = {"path": p, "version": ver, "source": "known_path"}
                self.installations.append(found[p])

        # Downloads (installers)
        dl = os.path.expanduser("~/Downloads")
        if os.path.exists(dl):
            for f in os.listdir(dl):
                if re.match(r"python-[\d.]+-amd64\.exe", f, re.I):
                    results.append(
                        {
                            "type": "installer",
                            "file": f,
                            "path": os.path.join(dl, f),
                            "size_mb": round(
                                os.path.getsize(os.path.join(dl, f)) / (1024 * 1024), 2
                            ),
                        }
                    )

        # Embedded Pythons
        embedded_paths = [
            r"C:\Program Files\DeepFaceLab\_internal\python-3.6.8\python.exe",
            r"C:\Users\clint\.lmstudio\extensions\backends\vendor\_amphibian\cpython3.11-win-x86@6\python.exe",
            r"C:\Users\clint\Desktop\Azazael_UNIFIED\.venv\Scripts\python.exe",
        ]
        for p in embedded_paths:
            if os.path.exists(p):
                results.append(
                    {
                        "type": "embedded",
                        "path": p,
                        "version": self._get_version(p),
                        "source": "embedded",
                    }
                )

        results.extend(self.installations)

        # Conda environments
        out, _, rc = self._run_cmd("conda info --envs")
        if rc == 0:
            for line in out.split("\n"):
                if line.strip():
                    parts = line.split()
                    self.environments.append(
                        {
                            "name": parts[0],
                            "path": parts[-1] if len(parts) > 1 else None,
                            "active": "*" in line,
                        }
                    )
            results.append({"type": "conda_environments", "envs": self.environments})

        return results

    def _get_version(self, path: str) -> str:
        out, _, rc = self._run_cmd(f'"{path}" --version')
        if rc == 0:
            match = re.search(r"Python\s+([\d.]+)", out)
            return match.group(1) if match else "unknown"
        return "unknown"

    def _run_cmd(self, cmd: str) -> tuple:
        try:
            result = subprocess.run(
                shlex.split(cmd, posix=False), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
            )
            return result.stdout, result.stderr, result.returncode
        except Exception as e:
            note_failure("SystemManager:L154", e)
            return "", str(e), 1


class ProcessCrawler(SystemCrawler):
    """Crawler for running processes."""

    def __init__(self):
        super().__init__("Processes")

    def crawl(self) -> list[dict]:
        results = []
        out, _, _ = self._run_cmd("tasklist /FO CSV /NH")

        for line in out.split("\n"):
            if line.strip():
                parts = line.split(",")
                if len(parts) >= 2:
                    results.append(
                        {
                            "name": parts[0].strip('"'),
                            "pid": parts[1].strip('"'),
                            "session_name": parts[2].strip('"')
                            if len(parts) > 2
                            else "N/A",
                            "memory": parts[3].strip('"') if len(parts) > 3 else "N/A",
                        }
                    )

        # Add Python processes specifically
        python_procs = [p for p in results if "python" in p["name"].lower()]
        results.append(
            {
                "type": "python_specific",
                "count": len(python_procs),
                "processes": python_procs,
            }
        )

        return results

    def _run_cmd(self, cmd: str) -> tuple:
        result = subprocess.run(
            shlex.split(cmd, posix=False), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        return result.stdout, result.stderr, result.returncode


class DiskCrawler(SystemCrawler):
    """Crawler for disk and storage."""

    def __init__(self):
        super().__init__("Disk")

    def crawl(self) -> list[dict]:
        results = []

        # Disk usage
        total, used, free = shutil.disk_usage("/")
        results.append(
            {
                "type": "usage",
                "total_gb": total // (1024**3),
                "used_gb": used // (1024**3),
                "free_gb": free // (1024**3),
                "used_percent": round((used / total) * 100, 1),
            }
        )

        # Volume info
        out, _, _ = self._run_cmd(
            "wmic volume get DriveLetter,Label,Freespace,Capacity /FORMAT:CSV"
        )
        for line in out.split("\n"):
            if line.strip() and "," in line:
                parts = line.split(",")
                if len(parts) >= 4:
                    results.append(
                        {
                            "type": "volume",
                            "drive": parts[0].strip(),
                            "label": parts[1].strip(),
                            "freespace": parts[2].strip(),
                            "capacity": parts[3].strip(),
                        }
                    )

        return results

    def _run_cmd(self, cmd: str) -> tuple:
        result = subprocess.run(
            shlex.split(cmd, posix=False), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        return result.stdout, result.stderr, result.returncode


class NetworkCrawler(SystemCrawler):
    """Crawler for network information."""

    def __init__(self):
        super().__init__("Network")

    def crawl(self) -> list[dict]:
        results = []

        # IP config
        out, _, _ = self._run_cmd("ipconfig /all")
        results.append({"type": "ipconfig", "output": out[:5000]})

        # Netstat
        out, _, _ = self._run_cmd("netstat -ano -p TCP")
        connections = []
        for line in out.split("\n")[4:]:
            if line.strip():
                parts = line.split()
                if len(parts) >= 4:
                    connections.append(
                        {
                            "proto": parts[0],
                            "local": parts[1],
                            "remote": parts[2],
                            "state": parts[3] if len(parts) > 3 else "N/A",
                        }
                    )
        results.append(
            {
                "type": "connections",
                "count": len(connections),
                "connections": connections[:800],
            }
        )

        return results

    def _run_cmd(self, cmd: str) -> tuple:
        result = subprocess.run(
            shlex.split(cmd, posix=False), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        return result.stdout, result.stderr, result.returncode


class EnvironmentCrawler(SystemCrawler):
    """Crawler for environment variables."""

    def __init__(self):
        super().__init__("Environment")

    def crawl(self) -> list[dict]:
        results = []

        # All env vars
        results.append(
            {"type": "all", "variables": {k: v[:200] for k, v in os.environ.items()}}
        )

        # PATH entries
        path = os.environ.get("PATH", "")
        path_entries = []
        for entry in path.split(";"):
            if entry.strip():
                path_entries.append(
                    {
                        "path": entry,
                        "exists": os.path.exists(entry),
                    }
                )
        results.append({"type": "path", "entries": path_entries})

        # Python-related
        python_vars = {
            k: os.environ.get(k, "")
            for k in os.environ
            if "PYTHON" in k.upper() or "CONDA" in k.upper()
        }
        results.append({"type": "python_vars", "variables": python_vars})

        return results


class ServiceCrawler(SystemCrawler):
    """Crawler for Windows services."""

    def __init__(self):
        super().__init__("Services")

    def crawl(self) -> list[dict]:
        results = []
        out, _, _ = self._run_cmd("sc query state= all")

        current_service = {}
        for line in out.split("\n"):
            if "SERVICE_NAME:" in line:
                if current_service:
                    results.append(current_service)
                current_service = {"name": line.split(":")[1].strip()}
            elif "STATE" in line:
                current_service["state"] = line.split(":")[1].strip()
            elif "DISPLAY_NAME" in line:
                current_service["display_name"] = line.split(":")[1].strip()

        if current_service:
            results.append(current_service)

        return results

    def _run_cmd(self, cmd: str) -> tuple:
        result = subprocess.run(
            shlex.split(cmd, posix=False), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        return result.stdout, result.stderr, result.returncode


class SystemManager:
    """Main system management class."""

    VERSION = "1.0.0"

    def __init__(self):
        self.crawlers = {
            "python": PythonCrawler(),
            "process": ProcessCrawler(),
            "disk": DiskCrawler(),
            "network": NetworkCrawler(),
            "environment": EnvironmentCrawler(),
            "service": ServiceCrawler(),
        }
        self.components = {}
        self.log_file = LOG_DIR / "system_manager_log.json"
        self.report_file = LOG_DIR / "system_report.json"
        self._lock = threading.Lock()

    def run_all_crawlers(self, parallel: bool = True) -> dict[str, list[dict]]:
        print("=" * 60)
        print("SYSTEM MANAGER - FULL SCAN")
        print("=" * 60)
        print(f"\nRunning {len(self.crawlers)} crawlers...")

        results = {}

        if parallel:
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = {
                    name: executor.submit(c.crawl) for name, c in self.crawlers.items()
                }
                for name, future in futures.items():
                    try:
                        results[name] = future.result()
                        print(f"  [OK] {name}: {len(results[name])} items")
                    except Exception as e:
                        print(f"  [XX] {name}: Error - {e}")
                        results[name] = [{"error": str(e)}]
        else:
            for name, crawler in self.crawlers.items():
                try:
                    results[name] = crawler.crawl()
                    print(f"  [OK] {name}: {len(results[name])} items")
                except Exception as e:
                    print(f"  [XX] {name}: Error - {e}")
                    results[name] = [{"error": str(e)}]

        self._save_report(results)
        self._analyze_health(results)

        return results

    def _analyze_health(self, results: dict):
        self.components = {}

        # Python health
        python_data = results.get("python", [])
        python_count = len(
            [
                p
                for p in python_data
                if isinstance(p, dict) and p.get("type") in ["standalone", "embedded"]
            ]
        )
        installers = len(
            [
                p
                for p in python_data
                if isinstance(p, dict) and p.get("type") == "installer"
            ]
        )
        self.components["python"] = SystemComponent(
            name="Python",
            status=HealthStatus.GOOD.value
            if python_count <= 5
            else HealthStatus.WARNING.value,
            details={"installations": python_count, "installers": installers},
            recommendations=["Remove old installers from Downloads"]
            if installers > 0
            else [],
        )

        # Disk health
        disk_data = results.get("disk", [])
        for d in disk_data:
            if isinstance(d, dict) and d.get("type") == "usage":
                if d.get("used_percent", 0) > 90:
                    self.components["disk"] = SystemComponent(
                        name="Disk",
                        status=HealthStatus.CRITICAL.value,
                        details=d,
                        recommendations=[
                            "Clean up temporary files",
                            "Remove unused programs",
                        ],
                    )
                elif d.get("used_percent", 0) > 80:
                    self.components["disk"] = SystemComponent(
                        name="Disk",
                        status=HealthStatus.WARNING.value,
                        details=d,
                        recommendations=["Consider freeing up disk space"],
                    )
                else:
                    self.components["disk"] = SystemComponent(
                        name="Disk", status=HealthStatus.GOOD.value, details=d
                    )

        # Process health
        proc_data = results.get("process", [])
        python_procs = [
            p
            for p in proc_data
            if isinstance(p, dict) and p.get("type") == "python_specific"
        ]
        if python_procs and python_procs[0].get("count", 0) > 20:
            self.components["process"] = SystemComponent(
                name="Processes",
                status=HealthStatus.WARNING.value,
                details={"python_count": python_procs[0].get("count", 0)},
                recommendations=["Check for runaway Python processes"],
            )

    def get_health_summary(self) -> dict:
        summary: dict[str, object] = {"timestamp": datetime.datetime.now().isoformat()}
        for name, comp in self.components.items():
            summary[name] = {
                "status": comp.status,
                "details": comp.details,
                "recommendations": comp.recommendations,
            }
        return summary

    def _save_report(self, results: dict):
        report = {
            "timestamp": datetime.datetime.now().isoformat(),
            "version": self.VERSION,
            "crawlers": {name: len(data) for name, data in results.items()},
            "results": results,
            "health": self.get_health_summary(),
        }
        with open(self.report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

    def cleanup_python_installers(self) -> dict:
        """Remove old Python installers from Downloads."""
        dl = os.path.expanduser("~/Downloads")
        removed = []
        if os.path.exists(dl):
            for f in os.listdir(dl):
                if re.match(r"python-[\d.]+-amd64\.exe", f, re.I):
                    path = os.path.join(dl, f)
                    size_mb = round(os.path.getsize(path) / (1024 * 1024), 2)
                    os.remove(path)
                    removed.append({"file": f, "size_mb": size_mb})

        return {"removed": removed, "count": len(removed)}

    def cleanup_temp_files(self) -> dict:
        """Clean temporary files."""
        paths = [
            os.path.expandvars(r"%TEMP%"),
            os.path.expandvars(r"%TMP%"),
        ]
        cleaned = {"paths": [], "files_removed": 0, "space_freed_mb": 0}

        for path in paths:
            if os.path.exists(path):
                count = 0
                freed = 0
                for root, dirs, files in os.walk(path):
                    for f in files:
                        try:
                            fp = os.path.join(root, f)
                            size = os.path.getsize(fp)
                            os.remove(fp)
                            count += 1
                            freed += size
                        except Exception as exc:
                            note_failure("SystemManager:L545", exc)
                cleaned["paths"].append(path)
                cleaned["files_removed"] += count
                cleaned["space_freed_mb"] += round(freed / (1024 * 1024), 2)

        return cleaned

    def kill_python_processes(self) -> dict:
        """Kill all Python processes."""
        # REAL-BUG FIX (slice C): subprocess.run returns a CompletedProcess, not a
        # 3-tuple -- the old unpack raised TypeError on every call, so this
        # command could never have executed. Take .stdout directly.
        result = subprocess.run(
            "tasklist /FO CSV /NH", capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1800)
        out = result.stdout or ""
        killed = []
        for line in out.split("\n"):
            if "python.exe" in line.lower():
                parts = line.split(",")
                if len(parts) >= 2:
                    pid = parts[1].strip('"')
                    subprocess.run(f"taskkill /PID {pid} /F", capture_output=True, timeout=1800)
                    killed.append(pid)
        return {"killed": killed, "count": len(killed)}

    def get_python_status(self) -> dict:
        """Get comprehensive Python status."""
        return self.crawlers["python"].crawl()

    def optimize_system(self) -> dict:
        """Run full system optimization."""
        print("\n" + "=" * 60)
        print("SYSTEM OPTIMIZATION")
        print("=" * 60)

        actions = []

        print("\n[1] Cleaning temp files...")
        temp_result = self.cleanup_temp_files()
        actions.append({"action": "cleanup_temp", "result": temp_result})
        print(
            f"      Removed {temp_result['files_removed']} files, {temp_result['space_freed_mb']} MB freed"
        )

        print("\n[2] Cleaning Python installers...")
        installer_result = self.cleanup_python_installers()
        actions.append({"action": "cleanup_installers", "result": installer_result})
        print(f"      Removed {installer_result['count']} installers")

        print("\n[3] Checking disk health...")
        disk_result = self.crawlers["disk"].crawl()
        print(f"      Disk usage: {disk_result[0].get('used_percent', 'N/A')}%")

        return {"actions": actions, "timestamp": datetime.datetime.now().isoformat()}


def main():
    manager = SystemManager()

    while True:
        print("\n" + "=" * 60)
        print("SYSTEM MANAGER v1.0")
        print("=" * 60)
        print("\n[1] Full System Scan")
        print("[2] Health Summary")
        print("[3] Python Status")
        print("[4] Kill Python Processes")
        print("[5] Clean Temp Files")
        print("[6] Clean Python Installers")
        print("[7] Optimize System")
        print("[8] Run All Crawlers")
        print("[q] Quit")
        print("\nSelect option: ", end="")

        choice = input().strip().lower()

        if choice == "1" or choice == "8":
            results = manager.run_all_crawlers(parallel=(choice == "8"))
            print(f"\n✓ Scan complete. Report saved to: {manager.report_file}")

        elif choice == "2":
            health = manager.get_health_summary()
            print("\n--- HEALTH SUMMARY ---")
            for comp, data in health.items():
                if comp != "timestamp":
                    status = data.get("status", "unknown").upper()
                    print(f"  {comp}: [{status}]")
                    if data.get("recommendations"):
                        for rec in data["recommendations"]:
                            print(f"    → {rec}")

        elif choice == "3":
            print("\n--- PYTHON STATUS ---")
            py_status = manager.get_python_status()
            for item in py_status:
                if isinstance(item, dict):
                    if item.get("type") == "standalone":
                        print(
                            f"  ✓ {item.get('path', 'Unknown')} ({item.get('version', '?')})"
                        )
                    elif item.get("type") == "embedded":
                        print(
                            f"  • {item.get('path', 'Unknown')} ({item.get('version', '?')}) [Embedded]"
                        )
                    elif item.get("type") == "installer":
                        print(
                            f"  • {item.get('file', 'Unknown')} ({item.get('size_mb', '?')} MB)"
                        )

        elif choice == "4":
            result = manager.kill_python_processes()
            print(f"\nKilled {result['count']} Python processes: {result['killed']}")

        elif choice == "5":
            result = manager.cleanup_temp_files()
            print(
                f"\nCleaned {result['files_removed']} temp files, freed {result['space_freed_mb']} MB"
            )

        elif choice == "6":
            result = manager.cleanup_python_installers()
            print(f"\nRemoved {result['count']} Python installers")

        elif choice == "7":
            result = manager.optimize_system()
            print("\n✓ Optimization complete")

        elif choice == "q":
            print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
