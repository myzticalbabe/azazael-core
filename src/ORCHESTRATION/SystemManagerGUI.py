#!/usr/bin/env python3
"""
System Manager GUI with Worm Visualization + ADLP Integration
============================================================
GUI with directory structure visualization + full logging to ADLP.
Version: 1.0.0
"""

import os
import json
import datetime
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from pathlib import Path
# from typing import Dict, List, Optional  # removed — all names have native equivalents
from dataclasses import dataclass, field
from enum import Enum
import shutil
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LOG_DIR = Path(os.environ.get("APPDATA", "~/.appdata")).expanduser() / "AssistantLogs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


COLOR_BG = "#0a0a0a"
COLOR_FG = "#00ff00"
COLOR_HIGHLIGHT = "#ff00ff"
COLOR_WARNING = "#ffff00"
COLOR_ERROR = "#ff0000"
COLOR_EMBEDDED = "#00ffff"


class ADLPClient:
    """Simple ADLP client for logging."""

    def __init__(self):
        self.log_file = LOG_DIR / "assistant_log.json"
        self._lock = threading.Lock()

    def log(
        self, category: str, level: str, source: str, message: str, data: dict | None = None
    ):
        entry = {
            "id": str(datetime.datetime.now().timestamp()).replace(".", "")[:12],
            "timestamp": datetime.datetime.now().isoformat(),
            "category": category,
            "level": level,
            "source": source,
            "message": message,
            "data": data or {},
            "tags": ["gui", "system_manager"],
        }
        with self._lock:
            try:
                log_data: dict[str, Any] = {"entries": []}
                if self.log_file.exists():
                    with open(self.log_file, "r", encoding="utf-8") as f:
                        log_data = json.load(f)
                if "entries" not in log_data:
                    log_data["entries"] = []
                log_data["entries"].append(entry)
                log_data["total_entries"] = len(log_data["entries"])
                with open(self.log_file, "w", encoding="utf-8") as f:
                    json.dump(log_data, f, indent=2)
            except Exception as e:
                print(f"ADLP log error: {e}")

    def log_gui_action(self, action: str, details: dict | None = None):
        self.log("assistant", "INFO", "SystemManagerGUI", action, details)

    def log_scan_result(self, scan_type: str, result_count: int, data: dict | None = None):
        self.log(
            "system",
            "INFO",
            f"{scan_type}_crawler",
            f"Scan completed: {result_count} items",
            data,
        )

    def log_python_scan(self, installations: list[dict]):
        self.log(
            "python",
            "INFO",
            "PythonMapper",
            f"Found {len(installations)} Python installations",
            {"installations": installations, "count": len(installations)},
        )


class NodeType(Enum):
    DISK = "disk"
    FOLDER = "folder"
    FILE = "file"
    PYTHON = "python"
    PYTHON_INSTALLER = "installer"
    VENV = "venv"
    CONDA = "conda"


@dataclass
class FileNode:
    name: str
    path: str
    node_type: NodeType
    children: list["FileNode"] = field(default_factory=list)
    size: int = 0
    version: str = ""


class WormCanvas(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=COLOR_BG, highlightthickness=0, **kwargs)
        self.nodes = {}
        self.scroll_y = tk.Scrollbar(parent, orient="vertical", command=self.yview)
        self.configure(yscrollcommand=self.scroll_y.set)
        self.scroll_y.pack(side="right", fill="y")
        self._on_resize(None)

    def _on_resize(self, event):
        self.configure(scrollregion=self.bbox("all"))

    def draw_worm_tree(self, root_node: FileNode, x: int = 50, y: int = 30):
        self.delete("all")
        self.nodes = {}
        self.node_positions = {}
        self._draw_node(root_node, x, y, 0)
        self._on_resize(None)

    def _draw_node(self, node: FileNode, x: int, y: int, level: int):
        node_id = f"node_{id(node)}"
        self.nodes[node_id] = node
        color = self._get_node_color(node.node_type)
        radius = self._get_node_radius(node.node_type)

        self.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            fill=color,
            outline=color,
            tags=("node", node_id),
        )

        text_color = (
            COLOR_BG
            if node.node_type in [NodeType.PYTHON, NodeType.VENV, NodeType.CONDA]
            else COLOR_FG
        )
        self.create_text(
            x + radius + 10,
            y,
            text=node.name,
            fill=text_color,
            anchor="w",
            font=(
                "Consolas",
                10 if node.node_type in [NodeType.PYTHON, NodeType.VENV] else 8,
            ),
        )

        self.node_positions[node_id] = (x, y)

        if node.children:
            child_x = x + 200
            child_y_start = y - (len(node.children) * 40) // 2
            for i, child in enumerate(node.children):
                child_y = child_y_start + i * 40
                self._draw_node(child, child_x, child_y, level + 1)
                self._draw_worm_segment(x, y, child_x, child_y)

    def _draw_worm_segment(
        self, x1: int, y1: int, x2: int, y2: int, color: str = COLOR_FG
    ):
        mid_x = (x1 + x2) // 2
        points = [x1, y1, mid_x, y1, mid_x, y2, x2, y2]
        self.create_line(points, fill=color, width=2, smooth=True, splinesteps=10)

    def _get_node_color(self, node_type: NodeType) -> str:
        colors = {
            NodeType.DISK: "#444444",
            NodeType.FOLDER: "#666666",
            NodeType.FILE: "#888888",
            NodeType.PYTHON: COLOR_HIGHLIGHT,
            NodeType.PYTHON_INSTALLER: COLOR_WARNING,
            NodeType.VENV: COLOR_EMBEDDED,
            NodeType.CONDA: COLOR_WARNING,
        }
        return colors.get(node_type, COLOR_FG)

    def _get_node_radius(self, node_type: NodeType) -> int:
        radii = {
            NodeType.DISK: 25,
            NodeType.FOLDER: 18,
            NodeType.FILE: 8,
            NodeType.PYTHON: 22,
            NodeType.PYTHON_INSTALLER: 12,
            NodeType.VENV: 15,
            NodeType.CONDA: 20,
        }
        return radii.get(node_type, 10)


class SystemManagerGUI:
    VERSION = "1.0.0"

    def __init__(self):
        self.adlp = ADLPClient()
        self.adlp.log_gui_action("GUI_STARTED", {"version": self.VERSION})

        self.root = tk.Tk()
        self.root.title(f"System Manager GUI - Worm Visualization v{self.VERSION}")
        self.root.geometry("1400x900")
        self.root.configure(bg=COLOR_BG)

        self.python_nodes = []
        self._setup_styles()
        self._build_ui()
        self._scan_system_async()

    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("dark")
        except Exception as exc:
            note_failure("SystemManagerGUI:L226", exc)
        style.configure("TFrame", background=COLOR_BG)
        style.configure(
            "TLabel", background=COLOR_BG, foreground=COLOR_FG, font=("Consolas", 10)
        )
        style.configure(
            "Treeview",
            background="#1a1a1a",
            foreground=COLOR_FG,
            fieldbackground="#1a1a1a",
            font=("Consolas", 9),
        )

    def _build_ui(self):
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(self.root, bg="#111111", width=250)
        sidebar.grid(row=0, column=0, sticky="ns", padx=2, pady=2)

        tk.Label(
            sidebar,
            text="SYSTEM CRAWLERS",
            bg="#111111",
            fg=COLOR_FG,
            font=("Consolas", 12, "bold"),
        ).pack(pady=10, padx=10)

        buttons = [
            ("[ FULL SCAN ]", self._scan_system),
            ("[ PYTHON ]", self._scan_python),
            ("[ PROCESSES ]", self._scan_processes),
            ("[ DISK ]", self._scan_disk),
            ("[ NETWORK ]", self._scan_network),
        ]

        for text, cmd in buttons:
            tk.Button(
                sidebar,
                text=text,
                bg="#222222",
                fg=COLOR_FG,
                font=("Consolas", 10),
                command=cmd,
            ).pack(fill="x", padx=10, pady=3)

        tk.Label(
            sidebar,
            text="ACTIONS",
            bg="#111111",
            fg=COLOR_FG,
            font=("Consolas", 12, "bold"),
        ).pack(pady=(30, 10))

        tk.Button(
            sidebar,
            text="[ OPTIMIZE ]",
            bg="#222222",
            fg=COLOR_WARNING,
            font=("Consolas", 10),
            command=self._run_optimize,
        ).pack(fill="x", padx=10, pady=3)
        tk.Button(
            sidebar,
            text="[ REFRESH ]",
            bg="#222222",
            fg=COLOR_FG,
            font=("Consolas", 10),
            command=self._scan_system,
        ).pack(fill="x", padx=10, pady=3)

        main_area = tk.Frame(self.root, bg=COLOR_BG)
        main_area.grid(row=0, column=1, sticky="nsew", padx=2, pady=2)
        main_area.grid_rowconfigure(1, weight=1)
        main_area.grid_columnconfigure(0, weight=1)

        self.status_label = tk.Label(
            main_area,
            text="Ready",
            bg="#111111",
            fg=COLOR_FG,
            font=("Consolas", 10),
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=2, pady=2)

        self.notebook = ttk.Notebook(main_area)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)

        self.worm_frame = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.worm_frame, text=" WORM VISUALIZATION ")
        self.worm_frame.grid_rowconfigure(0, weight=1)
        self.worm_frame.grid_columnconfigure(0, weight=1)

        self.worm_canvas = WormCanvas(self.worm_frame, width=800, height=600)
        self.worm_canvas.grid(row=0, column=0, sticky="nsew")

        legend_frame = tk.Frame(self.worm_frame, bg="#111111")
        legend_frame.grid(row=1, column=0, sticky="ew", pady=5)

        for text, color in [
            ("Python Standalone (green) ", COLOR_HIGHLIGHT),
            ("Embedded/venv (magenta) ", COLOR_EMBEDDED),
            ("Installer (yellow) ", COLOR_WARNING),
            ("Folder (gray) ", "#666666"),
        ]:
            tk.Label(
                legend_frame,
                text=f"o {text}",
                bg="#111111",
                fg=color,
                font=("Consolas", 9),
            ).pack(side="left", padx=10)

        self.tree_frame = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.tree_frame, text=" TREE VIEW ")
        self.tree_frame.grid_rowconfigure(0, weight=1)
        self.tree_frame.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(self.tree_frame, show="tree")
        tree_scroll = tk.Scrollbar(
            self.tree_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")

        self.details_frame = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.details_frame, text=" DETAILS ")
        self.details_text = scrolledtext.ScrolledText(
            self.details_frame,
            bg="#111111",
            fg=COLOR_FG,
            font=("Consolas", 10),
            wrap="word",
        )
        self.details_text.pack(fill="both", expand=True, padx=5, pady=5)

        self.python_frame = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.python_frame, text=" PYTHON INSTALLATIONS ")
        self.python_frame.grid_rowconfigure(0, weight=1)
        self.python_frame.grid_columnconfigure(0, weight=1)

        self.python_tree = ttk.Treeview(
            self.python_frame, columns=("Type", "Path", "Version"), show="headings"
        )
        self.python_tree.heading("Type", text="Type")
        self.python_tree.heading("Path", text="Path")
        self.python_tree.heading("Version", text="Version")
        self.python_tree.column("Type", width=100)
        self.python_tree.column("Path", width=400)
        self.python_tree.column("Version", width=80)
        py_scroll = tk.Scrollbar(
            self.python_frame, orient="vertical", command=self.python_tree.yview
        )
        self.python_tree.configure(yscrollcommand=py_scroll.set)
        self.python_tree.grid(row=0, column=0, sticky="nsew")
        py_scroll.grid(row=0, column=1, sticky="ns")

    def _set_status(self, text: str):
        self.status_label.config(text=f"  {text}")
        self.adlp.log("assistant", "INFO", "SystemManagerGUI", text)

    def _scan_system_async(self):
        threading.Thread(target=self._scan_system, daemon=True).start()

    def _scan_system(self):
        self._set_status("Scanning system...")
        self.root.config(cursor="watch")
        self.root.update()

        root_node = self._build_python_tree()
        self.worm_canvas.draw_worm_tree(root_node)
        self._update_tree_view(root_node)
        self._update_python_list()

        self._set_status("Scan complete")
        self.root.config(cursor="")

    def _scan_python(self):
        self._set_status("Scanning Python installations...")
        self._update_python_list()
        self.adlp.log_scan_result("python", len(self.python_nodes))
        self._set_status(
            f"Python scan complete: {len(self.python_nodes)} installations"
        )

    def _scan_processes(self):
        self._set_status("Scanning processes...")
        processes = self._get_processes()
        self._show_details(json.dumps(processes[:50], indent=2))
        self.adlp.log_scan_result("processes", len(processes))
        self._set_status(f"Found {len(processes)} processes")

    def _scan_disk(self):
        self._set_status("Scanning disk...")
        disk_info = self._get_disk_info()
        self._show_details(json.dumps(disk_info, indent=2))
        self.adlp.log_scan_result("disk", 1)
        self._set_status("Disk scan complete")

    def _scan_network(self):
        self._set_status("Scanning network...")
        net_info = self._get_network_info()
        self._show_details(json.dumps(net_info, indent=2))
        self.adlp.log_scan_result("network", 1)
        self._set_status("Network scan complete")

    def _run_optimize(self):
        self._set_status("Running optimization...")
        result = self._optimize_system()
        self._show_details(json.dumps(result, indent=2))
        self.adlp.log(
            "system", "INFO", "optimization", "Optimization completed", result
        )
        self._set_status("Optimization complete")

    def _build_python_tree(self) -> FileNode:
        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
        root = FileNode("System", system_drive, NodeType.DISK)

        python_root = FileNode("Python", system_drive + "Python", NodeType.FOLDER)
        root.children.append(python_root)

        installations = self._find_python_installations()
        self.python_nodes = installations

        for inst in installations:
            if inst["type"] == "standalone":
                python_root.children.append(
                    FileNode(
                        f"Python {inst.get('version', '?')}",
                        inst["path"],
                        NodeType.PYTHON,
                        version=inst.get("version", ""),
                    )
                )
            elif inst["type"] in ["embedded", "venv"]:
                python_root.children.append(
                    FileNode(
                        f"{inst.get('name', 'Embedded')} {inst.get('version', '?')}",
                        inst["path"],
                        NodeType.VENV,
                        version=inst.get("version", ""),
                    )
                )
            elif inst["type"] == "installer":
                python_root.children.append(
                    FileNode(
                        f"Installer {inst.get('version', '?')}.exe",
                        inst.get("path", ""),
                        NodeType.PYTHON_INSTALLER,
                        size=inst.get("size_mb", 0),
                    )
                )
            elif inst["type"] == "conda":
                conda_root = FileNode(
                    "Conda", str(Path.home() / "anaconda3"), NodeType.CONDA
                )
                root.children.append(conda_root)

        return root

    def _find_python_installations(self) -> list[dict]:
        installations = []

        # Production 3.15t leads; 3.14t was retired 2026-10-19, so the only
        # 3.14 entry is the sanctioned 3.14m venv.
        known_paths = [
            (r"C:\Azazael\.venvs\py315t\Scripts\python.exe", "venv", "3.15.0rc3"),
            (r"C:\Azazael\.venvs\py314m\Scripts\python.exe", "venv", "3.14.5"),
            (r"C:\Program Files\Python311\python.exe", "standalone", "3.11.9"),
            (r"C:\Users\clint\anaconda3\python.exe", "conda", "3.13.9"),
            (r"C:\Users\clint\.local\bin\python3.11.exe", "standalone", "3.11.14"),
        ]

        for path, ptype, version in known_paths:
            if os.path.exists(path):
                installations.append({"path": path, "type": ptype, "version": version})

        embedded_paths = [
            (
                r"C:\Program Files\DeepFaceLab\_internal\python-3.6.8\python.exe",
                "embedded",
                "3.6.8",
                "DeepFaceLab",
            ),
            (
                r"C:\Users\clint\.lmstudio\extensions\backends\vendor\_amphibian\cpython3.11-win-x86@6\python.exe",
                "embedded",
                "3.11.9",
                "LMStudio",
            ),
            (
                r"C:\Users\clint\Desktop\Azazael_UNIFIED\.venv\Scripts\python.exe",
                "venv",
                "3.11.14",
                "Azazael",
            ),
        ]

        for path, ptype, version, name in embedded_paths:
            if os.path.exists(path):
                installations.append(
                    {"path": path, "type": ptype, "version": version, "name": name}
                )

        downloads = os.path.expanduser("~/Downloads")
        if os.path.exists(downloads):
            for f in os.listdir(downloads):
                if "python-" in f.lower() and ".exe" in f.lower():
                    installations.append(
                        {
                            "path": os.path.join(downloads, f),
                            "type": "installer",
                            "version": f.split("-")[1] if "-" in f else "?",
                            "name": f,
                        }
                    )

        self.adlp.log_python_scan(installations)
        return installations

    def _get_processes(self) -> list[dict]:
        result = subprocess.run("tasklist /FO CSV /NH", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        processes = []
        for line in result.stdout.split("\n"):
            if line.strip():
                parts = line.split(",")
                if len(parts) >= 2:
                    processes.append(
                        {"name": parts[0].strip('"'), "pid": parts[1].strip('"')}
                    )
        return processes[:800]

    def _get_disk_info(self) -> dict:
        total, used, free = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        return {
            "total_gb": total // (1024**3),
            "used_gb": used // (1024**3),
            "free_gb": free // (1024**3),
        }

    def _get_network_info(self) -> dict:
        result = subprocess.run("ipconfig /all", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        return {"ipconfig": result.stdout[:3000]}

    def _optimize_system(self) -> dict:
        result = {
            "timestamp": datetime.datetime.now().isoformat(),
            "temp_cleaned": self._cleanup_temp(),
            "installers_removed": self._cleanup_installers(),
        }
        return result

    def _cleanup_temp(self) -> dict:
        temp = os.path.expandvars("%TEMP%")
        count = 0
        if os.path.exists(temp):
            for f in os.listdir(temp):
                try:
                    path = os.path.join(temp, f)
                    if os.path.isfile(path):
                        os.remove(path)
                        count += 1
                except Exception as exc:
                    note_failure("SystemManagerGUI:L589", exc)
        return {"files_removed": count}

    def _cleanup_installers(self) -> dict:
        downloads = os.path.expanduser("~/Downloads")
        removed = 0
        if os.path.exists(downloads):
            for f in os.listdir(downloads):
                if "python-" in f.lower() and ".exe" in f.lower():
                    try:
                        os.remove(os.path.join(downloads, f))
                        removed += 1
                    except Exception as exc:
                        note_failure("SystemManagerGUI:L602", exc)
        return {"count": removed}

    def _update_tree_view(self, root_node: FileNode):
        for item in self.tree.get_children():
            self.tree.delete(item)
        root_id = self.tree.insert("", "end", text=root_node.name)
        self._add_tree_children(root_id, root_node)

    def _add_tree_children(self, parent_id: str, node: FileNode):
        for child in node.children:
            child_id = self.tree.insert(parent_id, "end", text=child.name)
            self._add_tree_children(child_id, child)

    def _update_python_list(self):
        for item in self.python_tree.get_children():
            self.python_tree.delete(item)
        for inst in self.python_nodes:
            self.python_tree.insert(
                "",
                "end",
                values=(
                    inst["type"].upper(),
                    inst["path"][:50] + "..."
                    if len(inst["path"]) > 50
                    else inst["path"],
                    inst.get("version", "?"),
                ),
            )

    def _show_details(self, text: str):
        self.details_text.delete(1.0, tk.END)
        self.details_text.insert(tk.END, text)
        self.notebook.select(self.details_frame)

    def run(self):
        self.root.mainloop()


def main():
    app = SystemManagerGUI()
    app.run()


if __name__ == "__main__":
    main()
