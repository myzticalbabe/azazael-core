# coding: utf-8
"""
HORUS LIVE MONITOR - Embedded Real-Time Ecosystem Output

Embedded in HORUS for live ecosystem monitoring.

Usage:
    >>> from HORUS import launch_monitor
    >>> launch_monitor()
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
# from typing import Dict, Optional  # removed — all names have native equivalents
from dataclasses import dataclass
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Dynamic path resolution
try:
    from CORE.paths import get_src_dir
except ImportError as exc:
    note_failure("HORUS_LIVE_MONITOR:L23", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from CORE.paths import get_src_dir

_ORCH_DIR = get_src_dir() / "ORCHESTRATION"


@dataclass
class CompInfo:
    name: str
    status: str = "idle"
    last_output: str = ""
    last_update: (str | None) = None
    color: str = "#00ff88"


class HORUSLiveMonitor:
    """Live monitor embedded in HORUS."""

    COMPONENTS = {
        "SYSTEMZ": {"cmd": ["WORMZ/SYSTEMZ.py", "--status"], "color": "#00d4ff"},
        "PROCESSZ": {"cmd": ["WORMZ/PROCESSZ.py", "--status"], "color": "#00d4ff"},
        "DISKZ": {"cmd": ["WORMZ/DISKZ.py", "--status"], "color": "#00d4ff"},
        "NETWORXZ": {"cmd": ["WORMZ/NETWORXZ.py", "--status"], "color": "#00d4ff"},
        "CLOCKZ": {"cmd": ["WORMZ/CLOCKZ.py", "--status"], "color": "#00d4ff"},
        "CONCURRENCY": {"cmd": ["WORMZ/CONCURRENCY.py", "--scan"], "color": "#7c3aed"},
        "HYDRA": {"cmd": ["HYDRA/HYDRA.py", "--all"], "color": "#10b981"},
        "TERENCE": {"cmd": ["TERENCE/TERENCE.py", "--scan"], "color": "#f59e0b"},
        "ERRORZ": {"cmd": ["METAWORMS/ERRORZ.py", "--all"], "color": "#ef4444"},
        "HYDRAMETA": {"cmd": ["METAWORMS/HYDRAMETA.py", "--all"], "color": "#10b981"},
        "OPTIMIZER": {"cmd": ["ECOSYSTEM_OPTIMIZER.py", "-o"], "color": "#10b981"},
        "CRAWLER": {"cmd": ["ECOSYSTEM_CRAWLER_LAUNCHER.py"], "color": "#00d4ff"},
    }

    def __init__(self):
        self.base_path = _ORCH_DIR
        self.components: dict[str, CompInfo] = {}
        self._init_components()

    def _init_components(self):
        for name, info in self.COMPONENTS.items():
            self.components[name] = CompInfo(name=name, color=info["color"])

    def run_component(self, name: str) -> str:
        if name not in self.components:
            return f"Unknown component: {name}"

        info = self.components[name]
        comp_spec = self.COMPONENTS[name]
        info.status = "running"

        try:
            cmd = [sys.executable, str(self.base_path / comp_spec["cmd"][0])] + comp_spec["cmd"][
                1:
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            output = result.stdout + result.stderr
            info.last_output = output[:500]
            info.status = "error" if "error" in output.lower() else "active"
        except Exception as e:
            note_failure("HORUS_LIVE_MONITOR:L81", e)
            info.last_output = str(e)
            info.status = "error"

        info.last_update = datetime.now().strftime("%H:%M:%S")
        return output[:500] if output else "No output"

    def run_all(self) -> dict[str, dict]:
        results = {}
        for name in self.components:
            output = self.run_component(name)
            results[name] = {
                "status": self.components[name].status,
                "output": output,
                "last_update": self.components[name].last_update,
            }
        return results

    def get_status(self) -> dict:
        return {
            "timestamp": datetime.now().isoformat(),
            "components": {
                name: {"status": comp.status, "last_update": comp.last_update}
                for name, comp in self.components.items()
            },
        }

    def export_status(self, filepath: str | None = None) -> str:
        if filepath is None:
            filepath = str(self.base_path / "HORUS" / "output" / "monitor_status.json")
        Path(filepath).write_text(json.dumps(self.get_status(), indent=2), encoding="utf-8")
        return filepath


def launch_monitor():
    """Launch the live monitor (requires tkinter)."""
    try:
        from tkinter import Tk
        from tkinter import scrolledtext
        import tkinter as tk

        root = Tk()
        root.title("HORUS LIVE MONITOR")
        root.geometry("1200x800")
        root.configure(bg="#0d0d1a")

        # Header
        tk.Label(
            root,
            text="HORUS LIVE MONITOR",
            font=("Consolas", 16, "bold"),
            bg="#0d0d1a",
            fg="#00d4ff",
        ).pack(pady=15)

        # Console
        console = scrolledtext.ScrolledText(
            root, font=("Consolas", 10), bg="#000000", fg="#00ff88", wrap=tk.WORD
        )
        console.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        def log(msg, comp="SYSTEM"):
            ts = datetime.now().strftime("%H:%M:%S")
            console.insert(tk.END, f"[{ts}] [{comp}] {msg}\n")
            console.see(tk.END)

        def run_all():
            log("=" * 50)
            log("RUNNING FULL ECOSYSTEM")
            log("=" * 50)
            for name in HORUSLiveMonitor.COMPONENTS:
                log(f"Starting {name}...", name)
                try:
                    cmd = [
                        sys.executable,
                        str(_ORCH_DIR / HORUSLiveMonitor.COMPONENTS[name]["cmd"][0]),
                    ]
                    cmd.extend(HORUSLiveMonitor.COMPONENTS[name]["cmd"][1:])
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
                    )
                    log(result.stdout[:300], name)
                except Exception as e:
                    log(str(e), name)
                log(f"{name} complete", name)
            log("=" * 50)
            log("ECOSYSTEM RUN COMPLETE")
            log("=" * 50)

        # Buttons
        btn_frame = tk.Frame(root, bg="#16213e", pady=10)
        btn_frame.pack(fill=tk.X, padx=10)

        tk.Button(
            btn_frame,
            text="Run All",
            font=("Consolas", 10, "bold"),
            bg="#1a3a1a",
            fg="#00ff88",
            command=run_all,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            btn_frame,
            text="Clear",
            font=("Consolas", 10),
            bg="#3a1a1a",
            fg="#ff6b6b",
            command=lambda: console.delete(1.0, tk.END),
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            btn_frame,
            text="Quit",
            font=("Consolas", 10),
            bg="#3a1a1a",
            fg="#ff6b6b",
            command=root.quit,
        ).pack(side=tk.RIGHT, padx=5)

        log("HORUS LIVE MONITOR READY")
        log(f"Monitoring {len(HORUSLiveMonitor.COMPONENTS)} components")

        root.mainloop()

    except ImportError:
        print("tkinter not available. Install tkinter to use live monitor.")
        print("Using console mode instead:")
        monitor = HORUSLiveMonitor()
        results = monitor.run_all()
        print(json.dumps(results, indent=2))
        return monitor

    return None


if __name__ == "__main__":
    launch_monitor()


