#!/usr/bin/env python3
"""
Python System Mapper
Maps all Python installations and their relationships on the system.
Run with: python PythonSystemMapper.py
"""

import os
import sys
import json
import shlex
import subprocess
import re
from datetime import datetime
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
    print("[!] PythonSystemMapper is Windows-only (uses 'where', 'dir', and registry).")
    sys.exit(0)
# ────────────────────────────────────────────────────────────────


class PythonSystemMapper:
    def __init__(self):
        self.findings = {
            "timestamp": datetime.now().isoformat(),
            "hostname": os.environ.get("COMPUTERNAME", "UNKNOWN"),
            "user": os.environ.get("USERNAME", "UNKNOWN"),
            "python_installations": [],
            "conda_environments": [],
            "virtual_environments": [],
            "python_installers": [],
            "embedded_pythons": [],
            "path_entries": [],
            "registry_entries": [],
            "relationships": {},
            "summary": {},
        }

    def run_command(self, cmd, timeout=10):
        """Run a command and return output."""
        try:
            result = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except Exception as e:
            note_failure("PythonSystemMapper:L48", e)
            return "", str(e), 1

    def get_python_version(self, path):
        """Get version info from a Python executable."""
        if not os.path.exists(path):
            return None
        output, _, rc = self.run_command(f'"{path}" --version')
        if rc == 0:
            match = re.search(r"Python\s+([\d.]+)", output)
            if match:
                return match.group(1)
        return None

    def search_where(self):
        """Search using where command."""
        print("[*] Searching with 'where' command...")
        results = {}
        for cmd in ["where python", "where python3", "where py"]:
            output, _, rc = self.run_command(cmd)
            if rc == 0 and output:
                paths = [p.strip() for p in output.split("\n") if p.strip()]
                for path in paths:
                    results[path] = results.get(path, []) + [cmd]
        return results

    def search_common_paths(self):
        """Search common installation paths."""
        print("[*] Searching common paths...")
        search_patterns = [
            r"C:\Python*",
            r"C:\Program Files\Python*",
            r"C:\Program Files (x86)\Python*",
            r"C:\Program Files\Microsoft\Python*",
            r"C:\Users\*\AppData\Local\Programs\Python*",
            r"C:\Users\*\Anaconda*",
            r"C:\Users\*\Miniconda*",
            r"C:\Users\*\.conda",
            r"C:\Users\*\.local\bin\python*",
            r"C:\Program Files\DeepFaceLab\_internal\python-*",
            r"C:\Users\*\Downloads\DeepFaceLab\*\_internal\python-*",
            r"C:\Users\*\.lmstudio\extensions\backends\vendor\*\python*",
        ]
        found = []
        for pattern in search_patterns:
            output, _, rc = self.run_command(f'dir /s /b "{pattern}" 2>nul')
            if output:
                found.extend(
                    [
                        p.strip()
                        for p in output.split("\n")
                        if p.strip() and "python" in p.lower()
                    ]
                )
        return list(set(found))

    def search_windows_apps(self):
        """Search WindowsApps directory."""
        print("[*] Searching WindowsApps...")
        winapps = os.path.expandvars(
            r"C:\Users\%USERNAME%\AppData\Local\Microsoft\WindowsApps"
        )
        found = []
        if os.path.exists(winapps):
            for item in os.listdir(winapps):
                if "python" in item.lower():
                    found.append(os.path.join(winapps, item))
        return found

    def search_downloads_for_installers(self):
        """Find Python installers in Downloads."""
        print("[*] Searching for Python installers...")
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        installers = []
        if os.path.exists(downloads):
            for f in os.listdir(downloads):
                if re.match(r"python-[\d.]+-amd64\.exe", f, re.I):
                    installers.append(
                        {
                            "file": f,
                            "path": os.path.join(downloads, f),
                            "detected_version": (
                                m.group(1)
                                if (m := re.search(r"python-([\d.]+)", f, re.I))
                                else None
                            ),
                        }
                    )
        return installers

    def check_conda(self):
        """Check for conda installations and environments."""
        print("[*] Checking conda installations...")
        conda_info = []
        output, _, rc = self.run_command("conda info --envs")
        if rc == 0:
            for line in output.split("\n"):
                if line.strip() and "*" in line:
                    parts = line.split()
                    name = parts[0]
                    path = parts[-1].strip() if len(parts) > 1 else None
                    conda_info.append({"name": name, "path": path, "active": True})
        return conda_info

    def analyze_relationships(self):
        """Analyze relationships between Python installations."""
        print("[*] Analyzing relationships...")
        relationships = {
            "standalone_installations": [],
            "embedded_installations": [],
            "virtual_environments": [],
            "conda_envs": [],
            "shared_packages": [],
            "path_conflicts": [],
        }

        for py in self.findings["python_installations"]:
            path = py["path"].lower()
            if "deepfacelab" in path:
                relationships["embedded_installations"].append(py["path"])
            elif "anaconda3" in path:
                relationships["standalone_installations"].append(py["path"])
            elif ".local" in path:
                relationships["standalone_installations"].append(py["path"])
            elif "python31" in path or "python314" in path:
                relationships["standalone_installations"].append(py["path"])

        for venv in self.findings["virtual_environments"]:
            relationships["virtual_environments"].append(venv["path"])

        for env in self.findings["conda_environments"]:
            relationships["conda_envs"].append(env["path"])

        self.findings["relationships"] = relationships

    def generate_summary(self):
        """Generate a summary of findings."""
        print("[*] Generating summary...")
        self.findings["summary"] = {
            "total_installations": len(self.findings["python_installations"]),
            "standalone_count": len(
                [
                    p
                    for p in self.findings["python_installations"]
                    if not any(
                        x in p["path"].lower()
                        for x in ["venv", "envs", "_internal", "lmstudio"]
                    )
                ]
            ),
            "embedded_count": len(self.findings["embedded_pythons"]),
            "conda_env_count": len(self.findings["conda_environments"]),
            "venv_count": len(self.findings["virtual_environments"]),
            "installer_count": len(self.findings["python_installers"]),
            "versions_found": list(
                set(
                    p["version"]
                    for p in self.findings["python_installations"]
                    if p.get("version")
                )
            ),
            "default_recommendation": self.recommend_default(),
        }

    def recommend_default(self):
        """Recommend a default Python installation."""
        candidates = []
        for py in self.findings["python_installations"]:
            path = py["path"].lower()
            if "anaconda3" in path and py.get("version", "").startswith("3.1"):
                candidates.append((py, 100))
            elif "python314" in path:
                candidates.append((py, 90))
            elif "python311" in path and "program files" in path:
                candidates.append((py, 80))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return {
                "path": candidates[0][0]["path"],
                "version": candidates[0][0].get("version"),
                "reason": "Latest or most complete installation",
            }
        return None

    def run_full_scan(self):
        """Run complete system scan."""
        print("=" * 60)
        print("Python System Mapper v1.0")
        print("=" * 60)

        print("\n[1/7] Scanning with where command...")
        where_results = self.search_where()

        print("\n[2/7] Searching common paths...")
        path_results = self.search_common_paths()

        print("\n[3/7] Searching WindowsApps...")
        winapps_results = self.search_windows_apps()

        print("\n[4/7] Checking conda installations...")
        conda_results = self.check_conda()

        print("\n[5/7] Finding installers...")
        installer_results = self.search_downloads_for_installers()

        print("\n[6/7] Processing found installations...")
        all_pythons = set()

        for path in where_results:
            if os.path.isfile(path):
                all_pythons.add(path)

        for path in path_results:
            if "python.exe" in path.lower() and os.path.isfile(path):
                all_pythons.add(path)

        for path in winapps_results:
            if os.path.isfile(path):
                all_pythons.add(path)

        for py_path in all_pythons:
            version = self.get_python_version(py_path)
            self.findings["python_installations"].append(
                {
                    "path": py_path,
                    "version": version,
                    "discovered_via": where_results.get(py_path, ["path_scan"]),
                    "is_in_path": any(
                        p.lower() in py_path.lower()
                        for p in ["python311", "python314", "anaconda3", ".local"]
                    ),
                }
            )

        self.findings["conda_environments"] = conda_results
        self.findings["python_installers"] = installer_results

        self.findings["embedded_pythons"] = [
            {"path": p, "type": "embedded", "source": "DeepFaceLab"}
            for p in self.findings["python_installations"]
            if "deepfacelab" in p["path"].lower()
        ]

        self.findings["virtual_environments"] = [
            {"path": p["path"], "version": p.get("version")}
            for p in self.findings["python_installations"]
            if "venv" in p["path"].lower() or "envs" in p["path"].lower()
        ]

        print("\n[7/7] Analyzing relationships...")
        self.analyze_relationships()
        self.generate_summary()

        return self.findings

    def print_report(self):
        """Print a formatted report."""
        print("\n" + "=" * 60)
        print("PYTHON SYSTEM MAPPER - RESULTS")
        print("=" * 60)

        print("\n--- SUMMARY ---")
        s = self.findings["summary"]
        print(f"Total Python Installations: {s['total_installations']}")
        print(f"Standalone: {s['standalone_count']}")
        print(f"Embedded: {s['embedded_count']}")
        print(f"Conda Environments: {s['conda_env_count']}")
        print(f"Virtual Environments: {s['venv_count']}")
        print(f"Installers Found: {s['installer_count']}")
        print(f"Versions: {', '.join(s['versions_found'])}")

        print("\n--- STANDALONE INSTALLATIONS ---")
        for i, py in enumerate(self.findings["python_installations"], 1):
            if not any(
                x in py["path"].lower()
                for x in ["venv", "envs", "_internal", "lmstudio"]
            ):
                print(f"{i}. {py['path']}")
                print(f"   Version: {py.get('version', 'Unknown')}")

        print("\n--- EMBEDDED PYTHONS ---")
        for py in self.findings["embedded_pythons"]:
            print(f"  - {py['path']} ({py['source']})")

        print("\n--- VIRTUAL ENVIRONMENTS ---")
        for venv in self.findings["virtual_environments"]:
            print(f"  - {venv['path']}")

        print("\n--- CONDA ENVIRONMENTS ---")
        for env in self.findings["conda_environments"]:
            print(f"  - {env['name']} @ {env['path']}")

        print("\n--- PYTHON INSTALLERS (Downloads) ---")
        for inst in self.findings["python_installers"]:
            print(f"  - {inst['file']}")

        print("\n--- DEFAULT RECOMMENDATION ---")
        rec = s.get("default_recommendation")
        if rec:
            print(f"  Path: {rec['path']}")
            print(f"  Version: {rec.get('version', 'Unknown')}")
            print(f"  Reason: {rec['reason']}")

        print("\n" + "=" * 60)

    def save_report(self, filename="python_system_report.json"):
        """Save report to JSON file."""
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.findings, f, indent=2)
        print(f"\n[*] Report saved to: {filepath}")
        return filepath


def main():
    mapper = PythonSystemMapper()
    findings = mapper.run_full_scan()
    mapper.print_report()
    report_path = mapper.save_report()

    print(f"\n[*] Full report available at: {report_path}")
    print(
        "[*] Run 'python -c \"import json; print(json.dumps(json.load(open('python_system_report.json')), indent=2))\"' for raw data"
    )

    return findings


if __name__ == "__main__":
    main()
