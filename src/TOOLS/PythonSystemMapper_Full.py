#!/usr/bin/env python3
"""
Python System Mapper - Full System Scan
Complete Python installation mapper with relationships and cleanup recommendations.
"""

import os
import sys
import json
import shlex
import subprocess
import re
from datetime import datetime
from collections import defaultdict
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Platform guard ──────────────────────────────────────────────
# This tool uses Windows-specific commands (where, dir /s /b, registry).
if sys.platform != "win32":
    print("[!] FullSystemPythonMapper is Windows-only (uses 'where', 'dir', and registry).")
    sys.exit(0)
# ────────────────────────────────────────────────────────────────


class FullSystemPythonMapper:
    def __init__(self):
        self.data = {
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "hostname": os.environ.get("COMPUTERNAME", "UNKNOWN"),
                "user": os.environ.get("USERNAME", "UNKNOWN"),
                "os": os.environ.get("OS", "Unknown"),
                "system_drive": os.environ.get("SystemDrive", "C:"),
            },
            "executables": [],
            "installations": {},
            "environments": {
                "conda": [],
                "virtualenv": [],
                "venv": [],
                "pipenv": [],
                "poetry": [],
            },
            "registry": {},
            "path_variables": {},
            "file_associations": {},
            "embedded_systems": [],
            "downloads": [],
            "relationships": {},
            "cleanup_recommendations": [],
            "system_map": {},
        }
        self.exe_patterns = ["python.exe", "pythonw.exe", "py.exe", "python3.exe"]

    def run_cmd(self, cmd, timeout=15):
        try:
            result = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
            )
            return result.stdout, result.stderr, result.returncode
        except Exception as e:
            note_failure("PythonSystemMapper_Full:L60", e)
            return "", str(e), 1

    def get_version(self, path):
        if not os.path.exists(path):
            return None
        out, err, rc = self.run_cmd(f'"{path}" --version 2>&1')
        if rc == 0:
            match = re.search(r"Python\s+([\d._]+)", out)
            return match.group(1) if match else "unknown"
        return None

    def scan_where(self):
        print("[1/10] Scanning PATH with 'where' command...")
        for cmd in ["where python", "where python3", "where py"]:
            out, _, rc = self.run_cmd(cmd)
            if rc == 0 and out.strip():
                for line in out.strip().split("\n"):
                    line = line.strip()
                    if line and os.path.exists(line):
                        info = {
                            "path": line,
                            "type": "executable",
                            "discovered_via": cmd,
                            "version": self.get_version(line),
                        }
                        self.data["executables"].append(info)

    def scan_filesystem(self):
        print("[2/10] Scanning filesystem for Python installations...")
        search_roots = [
            os.environ.get("SystemDrive", "C:") + "\\",
            os.path.expandvars("%ProgramFiles%"),
            os.path.expandvars("%ProgramFiles(x86)%"),
            os.path.expandvars("%LocalAppData%"),
            os.path.expandvars("%AppData%"),
            os.path.expanduser("~"),
        ]

        patterns = [
            "*Python*",
            "*Anaconda*",
            "*Miniconda*",
            "*DeepFaceLab*",
            "*venv*",
            "*envs*",
            "*python*",
            "*conda*",
        ]

        for root in search_roots:
            if not os.path.exists(root):
                continue
            for pattern in patterns:
                out, _, _ = self.run_cmd(f'dir /s /b "{root}{pattern}" 2>nul')
                if out:
                    for line in out.strip().split("\n"):
                        line = line.strip()
                        if any(exe in line for exe in self.exe_patterns):
                            if os.path.exists(line):
                                self.data["system_map"][line] = {
                                    "type": "filesystem",
                                    "parent_dir": os.path.dirname(line),
                                    "size_bytes": os.path.getsize(line)
                                    if os.path.isfile(line)
                                    else 0,
                                    "modified": datetime.fromtimestamp(
                                        os.path.getmtime(line)
                                    ).isoformat()
                                    if os.path.exists(line)
                                    else None,
                                }

    def scan_embedded(self):
        print("[3/10] Scanning for embedded/bundled Pythons...")
        embedded_sources = [
            ("DeepFaceLab", r"C:\Program Files\DeepFaceLab\_internal\python-*"),
            (
                "DeepFaceLab_Downloads",
                r"C:\Users\*\Downloads\DeepFaceLab\*\_internal\python-*",
            ),
            ("LMStudio", r"C:\Users\*\.lmstudio\extensions\backends\vendor\*\python*"),
            ("Azazael", r"C:\Users\*\Desktop\Azazael*\.venv"),
            ("User_Venv", r"C:\Users\*\.virtualenvs\*"),
            ("Project_Venv", r"C:\Users\*\Desktop\*\.venv"),
        ]

        for source_name, pattern in embedded_sources:
            out, _, _ = self.run_cmd(f'dir /s /b "{pattern}" 2>nul')
            if out:
                for line in out.strip().split("\n"):
                    if "python.exe" in line.lower() and os.path.exists(line):
                        self.data["embedded_systems"].append(
                            {
                                "path": line,
                                "source": source_name,
                                "version": self.get_version(line),
                                "embedded": True,
                            }
                        )

    def scan_conda(self):
        print("[4/10] Scanning for Conda installations...")
        conda_paths = [
            os.path.expandvars("%UserProfile%\\Anaconda3"),
            os.path.expandvars("%UserProfile%\\Miniconda3"),
            os.path.expandvars("%ProgramData%\\Anaconda3"),
            os.path.expandvars("%ProgramData%\\Miniconda3"),
        ]

        for path in conda_paths:
            if os.path.exists(path):
                self.data["installations"]["conda"] = {
                    "path": path,
                    "version": self.get_version(os.path.join(path, "python.exe")),
                    "type": "conda",
                }

        out, _, rc = self.run_cmd("conda info --envs")
        if rc == 0:
            for line in out.split("\n"):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        self.data["environments"]["conda"].append(
                            {
                                "name": parts[0],
                                "path": parts[-1] if len(parts) > 1 else None,
                                "active": "*" in line,
                            }
                        )

    def scan_virtualenvs(self):
        print("[5/10] Scanning for virtual environments...")
        search_patterns = [
            r"%UserProfile%\.virtualenvs\*",
            r"%UserProfile%\.venv\*",
            r"%UserProfile%\Desktop\*\.venv",
            r"%ProgramFiles%\Python*\Lib\venv",
        ]

        for pattern in search_patterns:
            expanded = os.path.expandvars(pattern)
            out, _, _ = self.run_cmd(
                f'dir /s /b "{expanded}" 2>nul | findstr python.exe'
            )
            if out:
                for line in out.strip().split("\n"):
                    if os.path.exists(line.strip()):
                        self.data["environments"]["venv"].append(
                            {
                                "path": line.strip(),
                                "version": self.get_version(line.strip()),
                            }
                        )

    def scan_registry(self):
        print("[6/10] Scanning Windows registry...")
        reg_paths = [
            "HKLM\\SOFTWARE\\Python\\PythonCore",
            "HKLM\\SOFTWARE\\WOW6432Node\\Python\\PythonCore",
            "HKCU\\SOFTWARE\\Python\\PythonCore",
        ]

        for reg_path in reg_paths:
            out, _, rc = self.run_cmd(f'reg query "{reg_path}" /s 2>nul')
            if rc == 0 and out:
                self.data["registry"][reg_path] = []
                for line in out.split("\n"):
                    if (
                        "InstallPath" in line
                        or "DisplayName" in line
                        or "Version" in line
                    ):
                        self.data["registry"][reg_path].append(line.strip())

    def scan_path_env(self):
        print("[7/10] Analyzing PATH environment variable...")
        path_env = os.environ.get("PATH", "")
        for entry in path_env.split(";"):
            entry = entry.strip()
            if entry and ("python" in entry.lower() or "conda" in entry.lower()):
                self.data["path_variables"][entry] = {
                    "exists": os.path.exists(entry),
                    "is_directory": os.path.isdir(entry)
                    if os.path.exists(entry)
                    else False,
                    "files": os.listdir(entry) if os.path.isdir(entry) else [],
                }

    def scan_downloads(self):
        print("[8/10] Scanning Downloads folder for installers...")
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.exists(downloads):
            for f in os.listdir(downloads):
                if re.match(r"python-[\d.]+-amd64\.exe", f, re.I):
                    fpath = os.path.join(downloads, f)
                    self.data["downloads"].append(
                        {
                            "file": f,
                            "path": fpath,
                            "size_mb": round(os.path.getsize(fpath) / (1024 * 1024), 2),
                            "detected_version": (
                                (lambda x: x.group(1) if x else None)(
                                    re.search(r"python-([\d.]+)", f, re.I)
                                )
                            ),
                            "age_days": (
                                datetime.now()
                                - datetime.fromtimestamp(os.path.getmtime(fpath))
                            ).days,
                        }
                    )

    def analyze_relationships(self):
        print("[9/10] Building relationship map...")

        def categorize(path):
            path_lower = path.lower()
            if "anaconda" in path_lower:
                return "conda"
            elif "deepfacelab" in path_lower:
                return "embedded"
            elif "venv" in path_lower or ".venv" in path_lower:
                return "virtualenv"
            elif "python31" in path_lower and "program files" in path_lower:
                return "standalone"
            elif "lmstudio" in path_lower:
                return "app_bundled"
            else:
                return "other"

        self.data["relationships"] = {
            "dependency_graph": {},
            "version_distribution": defaultdict(list),
            "category_distribution": defaultdict(int),
        }

        all_pythons = self.data["executables"].copy()
        all_pythons.extend(self.data["embedded_systems"])

        for py in all_pythons:
            path = py.get("path", "")
            cat = categorize(path)
            ver = py.get("version", "unknown")
            self.data["relationships"]["dependency_graph"][path] = {
                "category": cat,
                "version": ver,
                # py315t is the production venv; py314m is the only sanctioned
                # 3.14 runtime. Python311/Python314 remain census entries.
                "in_path": any(
                    p in path
                    for p in ["py315t", "py314m", "Python311", "Python314"]
                ),
                "accessibility": "easy"
                if any(
                    p in path
                    for p in ["py315t", "py314m", "Python311", "Python314", "Anaconda3"]
                )
                else "requires_app",
            }
            self.data["relationships"]["version_distribution"][ver].append(path)
            self.data["relationships"]["category_distribution"][cat] += 1

    def generate_cleanup_recommendations(self):
        print("[10/10] Generating cleanup recommendations...")

        recommendations = []

        installers = self.data.get("downloads", [])
        for inst in installers:
            if inst["age_days"] > 30:
                recommendations.append(
                    {
                        "action": "delete_installer",
                        "item": inst["file"],
                        "path": inst["path"],
                        "reason": f"Outdated installer ({inst['age_days']} days old)",
                        "impact": "low",
                        "size_mb": inst["size_mb"],
                    }
                )

        embedded = self.data.get("embedded_systems", [])
        for emb in embedded:
            if "DeepFaceLab" in emb.get("source", ""):
                recommendations.append(
                    {
                        "action": "keep_embedded",
                        "item": emb["source"],
                        "path": emb["path"],
                        "reason": "Required by DeepFaceLab application",
                        "impact": "critical",
                        "can_remove": False,
                    }
                )

        for inst in self.data["installations"].values():
            if inst.get("type") == "conda":
                recommendations.append(
                    {
                        "action": "assess_conda",
                        "item": "Anaconda3",
                        "path": inst["path"],
                        "reason": "Large installation, assess if actively used",
                        "impact": "medium",
                        "size_gb": round(
                            sum(
                                os.path.getsize(os.path.join(inst["path"], f))
                                for f in os.listdir(inst["path"])
                                if os.path.isfile(os.path.join(inst["path"], f))
                            )
                            / (1024**3),
                            2,
                        )
                        if os.path.exists(inst["path"])
                        else 0,
                    }
                )

        self.data["cleanup_recommendations"] = recommendations

    def generate_summary(self):
        return {
            "total_executables": len(self.data["executables"]),
            "standalone_installations": len(
                [
                    p
                    for p in self.data["executables"]
                    if "Program Files" in p["path"] and "venv" not in p["path"]
                ]
            ),
            "embedded_systems": len(self.data["embedded_systems"]),
            "virtual_environments": sum(
                len(v) for v in self.data["environments"].values()
            ),
            "installer_files": len(self.data["downloads"]),
            "total_space_used_gb": round(
                sum(d.get("size_mb", 0) for d in self.data["downloads"]) / 1024, 2
            ),
            "unique_versions": list(
                set(p.get("version") for p in self.data["executables"])
            ),
            "conda_envs_count": len(self.data["environments"]["conda"]),
            "venv_count": len(self.data["environments"]["venv"]),
        }

    def run_full_scan(self):
        print("=" * 70)
        print("PYTHON SYSTEM MAPPER - FULL SYSTEM SCAN v2.0")
        print("=" * 70)

        self.scan_where()
        self.scan_filesystem()
        self.scan_embedded()
        self.scan_conda()
        self.scan_virtualenvs()
        self.scan_registry()
        self.scan_path_env()
        self.scan_downloads()
        self.analyze_relationships()
        self.generate_cleanup_recommendations()

        self.data["summary"] = self.generate_summary()

        return self.data

    def print_report(self):
        print("\n" + "=" * 70)
        print("SYSTEM MAPPING COMPLETE")
        print("=" * 70)

        s = self.data["summary"]
        print(f"\n{'TOTAL EXECUTABLES:':<30} {s['total_executables']}")
        print(f"{'STANDALONE INSTALLATIONS:':<30} {s['standalone_installations']}")
        print(f"{'EMBEDDED SYSTEMS:':<30} {s['embedded_systems']}")
        print(f"{'VIRTUAL ENVIRONMENTS:':<30} {s['virtual_environments']}")
        print(f"{'INSTALLER FILES:':<30} {s['installer_files']}")
        print(
            f"{'UNIQUE VERSIONS:':<30} {', '.join(str(v) for v in s['unique_versions'])}"
        )
        print(f"{'CONDA ENVS:':<30} {s['conda_envs_count']}")
        print(f"{'VENV COUNT:':<30} {s['venv_count']}")

        print("\n--- STANDALONE INSTALLATIONS ---")
        for i, py in enumerate(self.data["executables"], 1):
            if "Program Files" in py.get("path", "") and "venv" not in py.get(
                "path", ""
            ):
                print(f"{i}. {py['path']}")
                print(f"   Version: {py.get('version', 'Unknown')}")

        print("\n--- EMBEDDED PYTHONS ---")
        for emb in self.data["embedded_systems"]:
            print(f"  - {emb['path']} ({emb.get('source', 'Unknown')})")

        print("\n--- DOWNLOADS (Installers) ---")
        for inst in self.data["downloads"]:
            print(
                f"  - {inst['file']} ({inst['size_mb']} MB, {inst['age_days']} days old)"
            )

        print("\n--- CLEANUP RECOMMENDATIONS ---")
        for rec in self.data["cleanup_recommendations"][:5]:
            print(
                f"  [{rec['action'].upper()}] {rec.get('item', rec.get('file', 'Unknown'))}"
            )
            print(f"    Reason: {rec['reason']}")

        print("\n" + "=" * 70)

    def save_json(self, filename="python_full_system_map.json"):
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, default=str)
        print(f"\n[+] Full map saved to: {filepath}")
        return filepath


def main():
    mapper = FullSystemPythonMapper()
    data = mapper.run_full_scan()
    mapper.print_report()
    mapper.save_json()

    print(f"\n[+] Total system entries mapped: {len(data['system_map'])}")
    print(f"[+] Cleanup recommendations: {len(data['cleanup_recommendations'])}")

    return data


if __name__ == "__main__":
    main()
