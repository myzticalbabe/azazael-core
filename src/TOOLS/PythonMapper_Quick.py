#!/usr/bin/env python3
"""Python System Quick Mapper - Fast system scan for Python installations."""

import os
import sys
import json
import shlex
import subprocess
import re
from pathlib import Path
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
# This tool uses Windows-specific commands (where, dir /s /b).
if sys.platform != "win32":
    print("[!] PythonSystem QuickMapper is Windows-only (uses 'where' & 'dir' commands).")
    sys.exit(0)
# ────────────────────────────────────────────────────────────────


class QuickMapper:
    def __init__(self):
        self.data = {
            "timestamp": datetime.now().isoformat(),
            "executables": [],
            "installations": [],
            "embedded": [],
            "downloads": [],
            "conda_envs": [],
            "venvs": [],
            "summary": {},
        }

    def run(self, cmd):
        try:
            r = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace"
            )
            return r.stdout.strip(), r.returncode
        except Exception as exc:
            note_failure("PythonMapper_Quick:L40", exc)
            return "", 1

    def get_version(self, path):
        if not os.path.exists(path):
            return None
        out, _ = self.run(f'"{path}" --version 2>&1')
        m = re.search(r"Python\s+([\d.]+)", out)
        return m.group(1) if m else "unknown"

    def scan(self):
        print("=" * 50)
        print("PYTHON SYSTEM MAPPER - QUICK SCAN")
        print("=" * 50)

        # where command
        print("[1/6] Scanning PATH...")
        for cmd in ["where python", "where python3", "where py"]:
            out, rc = self.run(cmd)
            if rc == 0:
                for line in out.split("\n"):
                    line = line.strip()
                    if line and os.path.exists(line):
                        self.data["executables"].append(
                            {"path": line, "version": self.get_version(line)}
                        )

        # Known paths
        print("[2/6] Checking known paths...")
        known_paths = [
            # Production first, then the sanctioned 3.14 fallback venv. The
            # Python314 install below is INVENTORY only: the interpreter exists
            # as a base install, but 3.14t was retired and is never a launch
            # target (see CORE/python_env.py).
            r"C:\Azazael\.venvs\py315t\Scripts\python.exe",
            r"C:\Azazael\.venvs\py314m\Scripts\python.exe",
            r"C:\Python314\python.exe",
            r"C:\Program Files\Python311\python.exe",
            r"C:\Users\clint\anaconda3\python.exe",
            r"C:\Users\clint\.local\bin\python3.11.exe",
            r"C:\Program Files\DeepFaceLab\_internal\python-3.6.8\python.exe",
            r"C:\Users\clint\.lmstudio\extensions\backends\vendor\_amphibian\cpython3.11-win-x86@6\python.exe",
            r"C:\Users\clint\Desktop\Azazael_UNIFIED\.venv\Scripts\python.exe",
        ]
        for p in known_paths:
            if os.path.exists(p):
                if not any(e["path"] == p for e in self.data["executables"]):
                    self.data["executables"].append(
                        {"path": p, "version": self.get_version(p)}
                    )

        # Downloads
        print("[3/6] Checking Downloads...")
        dl = os.path.expanduser("~/Downloads")
        if os.path.exists(dl):
            for f in os.listdir(dl):
                if re.match(r"python-[\d.]+-amd64\.exe", f, re.I):
                    self.data["downloads"].append(f)

        # Conda
        print("[4/6] Checking conda...")
        out, rc = self.run("conda info --envs 2>nul")
        if rc == 0:
            for line in out.split("\n"):
                if line.strip():
                    parts = line.split()
                    self.data["conda_envs"].append({"name": parts[0] if parts else "?"})

        # Categorize
        print("[5/6] Categorizing...")
        for e in self.data["executables"]:
            p = e["path"].lower()
            if "deepfacelab" in p:
                e["category"] = "embedded"
                self.data["embedded"].append(e["path"])
            elif "lmstudio" in p:
                e["category"] = "app_bundled"
            elif "azazael" in p:
                e["category"] = "virtualenv"
                self.data["venvs"].append(e["path"])
            elif "anaconda3" in p:
                e["category"] = "conda"
            elif "program files" in p and "venv" not in p:
                e["category"] = "standalone"
            elif ".local" in p:
                e["category"] = "user_install"
            else:
                e["category"] = "other"

        # Summary
        self.data["summary"] = {
            "total_executables": len(self.data["executables"]),
            "standalone": len(
                [
                    e
                    for e in self.data["executables"]
                    if e.get("category") == "standalone"
                ]
            ),
            "embedded": len(self.data["embedded"]),
            "conda": len(
                [e for e in self.data["executables"] if e.get("category") == "conda"]
            ),
            "venvs": len(self.data["venvs"]),
            "downloads": len(self.data["downloads"]),
            "versions": list(
                set(e["version"] for e in self.data["executables"] if e.get("version"))
            ),
        }

        # Report
        print("\n" + "=" * 50)
        s = self.data["summary"]
        print(f"Total Executables: {s['total_executables']}")
        print(f"Standalone: {s['standalone']}")
        print(f"Embedded: {s['embedded']}")
        print(f"Conda: {s['conda']}")
        print(f"Virtual Envs: {s['venvs']}")
        print(f"Downloads (installers): {s['downloads']}")
        print(f"Versions: {', '.join(s['versions'])}")

        print("\n--- ALL INSTALLATIONS ---")
        for i, e in enumerate(self.data["executables"], 1):
            print(f"{i}. {e['path']}")
            print(f"   Version: {e.get('version')} | Category: {e.get('category')}")

        print("\n--- DOWNLOADS ---")
        for d in self.data["downloads"]:
            print(f"  - {d}")

        # Save
        output_path = Path(__file__).parent / "python_system_quickmap.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        print(f"\n[+] Saved to: {output_path}")
        print("=" * 50)


if __name__ == "__main__":
    QuickMapper().scan()
