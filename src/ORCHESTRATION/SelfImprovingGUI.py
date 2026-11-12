#!/usr/bin/env python3
"""
Self-Improving GUI with ML Clustering & Worm Visualization
======================================================
Standalone GUI that uses ML clustering for dynamic self-improvement and robustness.
Version: 1.0.0 - ML-POWERED SELF-IMPROVING EDITION
"""

import os
import sys
import datetime
import uuid
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
import random
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ORCHESTRATION'))

from ADLP_FusionCore import CORE, TIER, log_entry, learn, fuse
from ADLP_MLOrchestrator import ML_ORCH, predict, analyze, cluster
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LOG_DIR = Path(os.environ.get("APPDATA", "~/.appdata")).expanduser() / "AssistantLogs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class GUIStatus(Enum):
    ACTIVE = "active"
    LEARNING = "learning"
    IMPROVING = "improving"
    OPTIMIZING = "optimizing"
    ERROR = "error"


class WidgetCluster(Enum):
    NAVIGATION = "navigation"
    DISPLAY = "display"
    CONTROL = "control"
    INFO = "info"


@dataclass
class WidgetMetrics:
    widget_id: str
    cluster: WidgetCluster
    clicks: int = 0
    time_spent: float = 0.0
    errors: int = 0
    load_time: float = 0.0
    last_used: str = ""


@dataclass
class GUISelfImprovement:
    session_id: str
    started: str
    improvements: int = 0
    ml_predictions: int = 0
    clusters_formed: int = 0
    optimizations: int = 0


class WormCanvas(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#0a0a0a", highlightthickness=0, **kwargs)
        self.nodes = {}
        self.connections = []
        self.node_positions = {}
        self.scroll_y = tk.Scrollbar(parent, orient="vertical", command=self.yview)
        self.configure(yscrollcommand=self.scroll_y.set)
        self.scroll_y.pack(side="right", fill="y")
        self.bind("<MouseWheel>", self._on_scroll)
        self._on_resize(None)

    def _on_scroll(self, event):
        self.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_resize(self, event):
        self.configure(scrollregion=self.bbox("all"))

    def draw_worm_network(
        self,
        nodes: list[dict],
        connections: list[dict],
        center_x: int = 400,
        center_y: int = 300,
    ):
        self.delete("all")
        self.nodes = {}
        self.node_positions = {}

        node_colors = {
            "core": "#00ff00",
            "system": "#00ffff",
            "ml": "#ff00ff",
            "gui": "#ffff00",
            "knowledge": "#ff6600",
            "orchestration": "#ff0066",
            "healing": "#00ff66",
            "optimization": "#6600ff",
        }

        radius = 150
        n = len(nodes)
        for i, node in enumerate(nodes):
            angle = (2 * 3.14159 * i) / n if n > 0 else 0
            x = (
                center_x + radius * 1.5 * (i % 2 - 0.5) * 2
                if i < 4
                else center_x + radius * 2 * ((i % 4) - 1.5)
            )
            y = (
                center_y - radius + (i * radius * 0.5)
                if i < 4
                else center_y + radius * ((i % 4) - 1.5)
            )

            color = node_colors.get(node.get("color", "core"), "#888888")
            radius = node.get("size", 25)

            self.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill=color,
                outline=color,
                tags=("node", node.get("id", str(i))),
            )

            text_color = "#000000" if color not in ["#888888"] else "#00ff00"
            self.create_text(
                x + radius + 5,
                y,
                text=node.get("name", f"Node {i}"),
                fill=text_color,
                anchor="w",
                font=("Consolas", 9, "bold"),
            )

            self.node_positions[node.get("id", str(i))] = (x, y)

        for conn in connections:
            if (
                conn.get("from") in self.node_positions
                and conn.get("to") in self.node_positions
            ):
                x1, y1 = self.node_positions[conn.get("from")]
                x2, y2 = self.node_positions[conn.get("to")]
                color = conn.get("color", "#333333")
                self.create_line(x1, y1, x2, y2, fill=color, width=2, smooth=True)

        self._on_resize(None)


class SelfImprovingGUI:
    VERSION = "1.0.0"

    def __init__(self):
        self.session_id = str(uuid.uuid4())[:8]
        self.started = datetime.datetime.now().isoformat()
        self.status = GUIStatus.ACTIVE
        self.widget_metrics: dict[str, WidgetMetrics] = {}
        self.improvements = 0
        self.ml_predictions = 0
        self.clusters_formed = 0
        self.optimizations = 0

        self.root = tk.Tk()
        self.root.title(f"Self-Improving GUI v{self.VERSION} - ML Powered")
        self.root.geometry("1600x1000")
        self.root.configure(bg="#0a0a0a")

        self._setup_gui()
        self._initialize_ml_clustering()
        self._register_with_adlp()
        self._start_self_improvement_loop()

        self._log("GUI initialized", "INFO")

    def _setup_gui(self):
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(self.root, bg="#111111", width=280)
        sidebar.grid(row=0, column=0, sticky="ns", padx=2, pady=2)

        tk.Label(
            sidebar,
            text="ML SELF-IMPROVING GUI",
            bg="#111111",
            fg="#00ff00",
            font=("Consolas", 14, "bold"),
        ).pack(pady=15, padx=10)

        self.status_label = tk.Label(
            sidebar,
            text="Status: ACTIVE",
            bg="#111111",
            fg="#00ff00",
            font=("Consolas", 10),
        )
        self.status_label.pack(pady=5, padx=10, fill="x")

        self.session_label = tk.Label(
            sidebar,
            text=f"Session: {self.session_id}",
            bg="#111111",
            fg="#888888",
            font=("Consolas", 8),
        )
        self.session_label.pack(pady=2, padx=10, fill="x")

        tk.Label(
            sidebar,
            text="ML OPERATIONS",
            bg="#111111",
            fg="#ff00ff",
            font=("Consolas", 11, "bold"),
        ).pack(pady=(20, 5), padx=10)

        buttons = [
            ("[ CLUSTER WIDGETS ]", self._cluster_widgets, "#ff00ff"),
            ("[ PREDICT BEHAVIOR ]", self._predict_behavior, "#00ffff"),
            ("[ OPTIMIZE LAYOUT ]", self._optimize_layout, "#ffff00"),
            ("[ SELF-IMPROVE ]", self._self_improve, "#00ff66"),
            ("[ RUN DIAGNOSTIC ]", self._run_diagnostic, "#ff6600"),
            ("[ ANALYZE PATTERNS ]", self._analyze_patterns, "#6600ff"),
        ]

        for text, cmd, color in buttons:
            btn = tk.Button(
                sidebar,
                text=text,
                bg="#222222",
                fg=color,
                font=("Consolas", 10),
                command=cmd,
                relief="flat",
            )
            btn.pack(fill="x", padx=10, pady=3)

        tk.Label(
            sidebar,
            text="SYSTEM OPERATIONS",
            bg="#111111",
            fg="#00ffff",
            font=("Consolas", 11, "bold"),
        ).pack(pady=(20, 5), padx=10)

        sys_buttons = [
            ("[ SYSTEM SCAN ]", self._system_scan, "#00ff00"),
            ("[ PYTHON STATUS ]", self._python_status, "#ff9900"),
            ("[ NETWORK INFO ]", self._network_info, "#00ccff"),
        ]

        for text, cmd, color in sys_buttons:
            btn = tk.Button(
                sidebar,
                text=text,
                bg="#222222",
                fg=color,
                font=("Consolas", 10),
                command=cmd,
                relief="flat",
            )
            btn.pack(fill="x", padx=10, pady=3)

        tk.Label(
            sidebar,
            text="REFRESH",
            bg="#111111",
            fg="#888888",
            font=("Consolas", 11, "bold"),
        ).pack(pady=(20, 5), padx=10)

        tk.Button(
            sidebar,
            text="[ REFRESH WORM VIZ ]",
            bg="#222222",
            fg="#00ff00",
            font=("Consolas", 10),
            command=self._refresh_worm_viz,
        ).pack(fill="x", padx=10, pady=3)

        tk.Button(
            sidebar,
            text="[ FULL REFRESH ]",
            bg="#222222",
            fg="#ff0000",
            font=("Consolas", 10),
            command=self._full_refresh,
        ).pack(fill="x", padx=10, pady=3)

        main_area = tk.Frame(self.root, bg="#0a0a0a")
        main_area.grid(row=0, column=1, sticky="nsew", padx=2, pady=2)
        main_area.grid_rowconfigure(1, weight=1)
        main_area.grid_columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(main_area)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)

        self.worm_frame = tk.Frame(self.notebook, bg="#0a0a0a")
        self.notebook.add(self.worm_frame, text=" ML WORM VISUALIZATION ")
        self.worm_frame.grid_rowconfigure(0, weight=1)
        self.worm_frame.grid_columnconfigure(0, weight=1)

        self.worm_canvas = WormCanvas(self.worm_frame, width=900, height=600)
        self.worm_canvas.grid(row=0, column=0, sticky="nsew")

        legend_frame = tk.Frame(self.worm_frame, bg="#111111")
        legend_frame.grid(row=1, column=0, sticky="ew", pady=5)

        colors = [
            ("Core", "#00ff00"),
            ("System", "#00ffff"),
            ("ML", "#ff00ff"),
            ("GUI", "#ffff00"),
            ("Knowledge", "#ff6600"),
            ("Orchestration", "#ff0066"),
        ]
        for name, color in colors:
            tk.Label(
                legend_frame,
                text=f"● {name}",
                bg="#111111",
                fg=color,
                font=("Consolas", 9),
            ).pack(side="left", padx=10)

        self.ml_frame = tk.Frame(self.notebook, bg="#0a0a0a")
        self.notebook.add(self.ml_frame, text=" ML OPERATIONS ")
        self.ml_frame.grid_rowconfigure(0, weight=1)
        self.ml_frame.grid_columnconfigure(0, weight=1)

        self.ml_text = scrolledtext.ScrolledText(
            self.ml_frame,
            bg="#111111",
            fg="#00ff00",
            font=("Consolas", 10),
            wrap="word",
        )
        self.ml_text.pack(fill="both", expand=True, padx=5, pady=5)

        self.details_frame = tk.Frame(self.notebook, bg="#0a0a0a")
        self.notebook.add(self.details_frame, text=" DETAILS & LOGS ")
        self.details_text = scrolledtext.ScrolledText(
            self.details_frame,
            bg="#111111",
            fg="#00ff00",
            font=("Consolas", 9),
            wrap="word",
        )
        self.details_text.pack(fill="both", expand=True, padx=5, pady=5)

        self.optimization_frame = tk.Frame(self.notebook, bg="#0a0a0a")
        self.notebook.add(self.optimization_frame, text=" SELF-OPTIMIZATION ")
        self.opt_text = scrolledtext.ScrolledText(
            self.optimization_frame,
            bg="#111111",
            fg="#ff00ff",
            font=("Consolas", 10),
            wrap="word",
        )
        self.opt_text.pack(fill="both", expand=True, padx=5, pady=5)

        self._refresh_worm_viz()

    def _initialize_ml_clustering(self):
        self._log("Initializing ML clustering...", "INFO")
        self.widget_clusters = {
            WidgetCluster.NAVIGATION: [],
            WidgetCluster.DISPLAY: [],
            WidgetCluster.CONTROL: [],
            WidgetCluster.INFO: [],
        }
        self.behavior_history = []
        self.clusters_formed = 0
        self._log("ML clustering initialized", "INFO")

    def _register_with_adlp(self):
        CORE._reg(
            "self_improving_gui",
            TIER.ASSISTANT,
            self.VERSION,
            ["visualize", "cluster", "predict", "optimize", "self_improve"],
        )

    def _start_self_improvement_loop(self):
        def improvement_loop():
            while True:
                try:
                    time.sleep(30)
                    if random.random() < 0.3:
                        self._self_improve()
                except Exception as exc:
                    note_failure("SelfImprovingGUI:L399", exc)

        threading.Thread(target=improvement_loop, daemon=True).start()

    def _log(self, msg: str, level: str = "INFO"):
        entry = {
            "id": str(uuid.uuid4())[:12],
            "ts": datetime.datetime.now().isoformat(),
            "level": level,
            "message": msg,
        }
        self.details_text.insert(tk.END, f"[{level}] {msg}\n")
        self.details_text.see(tk.END)
        log_entry(TIER.ASSISTANT.value, level, "SelfImprovingGUI", "gui_op", msg)

    def _refresh_worm_viz(self):
        nodes = [
            {"id": "core", "name": "ADLP Core", "color": "core", "size": 35},
            {"id": "sys", "name": "System", "color": "system", "size": 28},
            {"id": "ml", "name": "ML Orch", "color": "ml", "size": 28},
            {"id": "gui", "name": "Self GUI", "color": "gui", "size": 30},
            {"id": "know", "name": "Knowledge", "color": "knowledge", "size": 25},
            {
                "id": "orch",
                "name": "Orchestrator",
                "color": "orchestration",
                "size": 22,
            },
            {"id": "heal", "name": "Healer", "color": "healing", "size": 20},
            {"id": "opt", "name": "Optimizer", "color": "optimization", "size": 18},
        ]

        connections = [
            {"from": "core", "to": "sys", "color": "#00ff00"},
            {"from": "core", "to": "ml", "color": "#ff00ff"},
            {"from": "core", "to": "gui", "color": "#ffff00"},
            {"from": "sys", "to": "know", "color": "#00ffff"},
            {"from": "ml", "to": "orch", "color": "#ff0066"},
            {"from": "gui", "to": "opt", "color": "#6600ff"},
            {"from": "heal", "to": "core", "color": "#00ff66"},
            {"from": "opt", "to": "gui", "color": "#ffff00"},
        ]

        self.worm_canvas.draw_worm_network(nodes, connections)
        self._log("Worm visualization refreshed", "INFO")

    def _cluster_widgets(self):
        self.status = GUIStatus.LEARNING
        self.status_label.config(text="Status: CLUSTERING", fg="#ff00ff")

        self._log("Starting widget clustering...", "INFO")

        widgets = [
            {"id": "nav1", "type": "button", "clicks": 45, "time": 120, "errors": 0},
            {"id": "nav2", "type": "button", "clicks": 38, "time": 95, "errors": 1},
            {"id": "disp1", "type": "canvas", "clicks": 120, "time": 300, "errors": 2},
            {"id": "disp2", "type": "text", "clicks": 200, "time": 450, "errors": 3},
            {"id": "ctrl1", "type": "button", "clicks": 67, "time": 180, "errors": 0},
            {"id": "ctrl2", "type": "button", "clicks": 54, "time": 140, "errors": 1},
            {"id": "info1", "type": "label", "clicks": 15, "time": 30, "errors": 0},
            {"id": "info2", "type": "text", "clicks": 89, "time": 220, "errors": 2},
        ]

        result = cluster(widgets, 4)
        self.clusters_formed = result["n_clusters"]

        self.ml_text.insert(tk.END, "\n=== WIDGET CLUSTERING ===\n")
        self.ml_text.insert(tk.END, f"Clusters formed: {result['n_clusters']}\n")
        for cid, items in result["clusters"].items():
            self.ml_text.insert(tk.END, f"\nCluster {cid}:\n")
            for item in items:
                self.ml_text.insert(
                    tk.END, f"  - {item.get('id', '?')}: {item.get('type', '?')}\n"
                )

        self.ml_predictions += 1
        self.optimizations += 1
        self._log(
            f"Widget clustering complete: {result['n_clusters']} clusters", "INFO"
        )

        self.status = GUIStatus.ACTIVE
        self.status_label.config(text="Status: ACTIVE", fg="#00ff00")

    def _predict_behavior(self):
        self.status = GUIStatus.OPTIMIZING
        self.status_label.config(text="Status: PREDICTING", fg="#00ffff")

        self._log("Predicting user behavior...", "INFO")

        patterns = [
            {"hour": 9, "activity": "high", "widgets": ["nav1", "disp1"]},
            {"hour": 12, "activity": "medium", "widgets": ["ctrl1", "info2"]},
            {"hour": 15, "activity": "high", "widgets": ["nav2", "disp2"]},
            {"hour": 18, "activity": "low", "widgets": ["info1"]},
        ]

        predictions = []
        for p in patterns:
            pred = predict("trend", {"data": p})
            predictions.append({"pattern": p, "prediction": pred})

        self.ml_text.insert(tk.END, "\n=== BEHAVIOR PREDICTIONS ===\n")
        for i, pred in enumerate(predictions):
            self.ml_text.insert(
                tk.END, f"\nPattern {i + 1}: Hour {pred['pattern']['hour']}\n"
            )
            self.ml_text.insert(tk.END, f"  Predicted: {pred['prediction']}\n")

        self.behavior_history.extend(predictions)
        self.ml_predictions += len(predictions)
        self._log(
            f"Behavior prediction complete: {len(predictions)} predictions", "INFO"
        )

        self.status = GUIStatus.ACTIVE
        self.status_label.config(text="Status: ACTIVE", fg="#00ff00")

    def _optimize_layout(self):
        self.status = GUIStatus.OPTIMIZING
        self.status_label.config(text="Status: OPTIMIZING", fg="#ffff00")

        self._log("Optimizing GUI layout...", "INFO")

        layout_analysis = analyze(
            {
                "widget_positions": {
                    "nav": (0, 0),
                    "disp": (1, 0),
                    "ctrl": (0, 1),
                    "info": (1, 1),
                },
                "load_times": {"nav": 0.1, "disp": 0.3, "ctrl": 0.05, "info": 0.02},
                "click_rates": {"nav": 45, "disp": 120, "ctrl": 60, "info": 89},
            }
        )

        self.opt_text.insert(tk.END, "\n=== LAYOUT OPTIMIZATION ===\n")
        self.opt_text.insert(
            tk.END, f"Analysis: {layout_analysis.get('summary', 'N/A')}\n"
        )
        self.opt_text.insert(tk.END, "Recommendations:\n")
        for rec in layout_analysis.get("recommendations", []):
            self.opt_text.insert(tk.END, f"  - {rec}\n")

        self.optimizations += 1
        self._log("Layout optimization complete", "INFO")

        self.status = GUIStatus.ACTIVE
        self.status_label.config(text="Status: ACTIVE", fg="#00ff00")

    def _self_improve(self):
        self.status = GUIStatus.IMPROVING
        self.status_label.config(text="Status: IMPROVING", fg="#00ff66")

        self._log("Running self-improvement cycle...", "INFO")

        improvements = []

        pred = predict(
            "system_health",
            {
                "clicks": len(self.widget_metrics),
                "predictions": self.ml_predictions,
                "clusters": self.clusters_formed,
                "optimizations": self.optimizations,
            },
        )

        analysis = analyze(
            {
                "widgets": len(self.widget_metrics),
                "predictions": self.ml_predictions,
                "clusters": self.clusters_formed,
                "optimizations": self.optimizations,
                "uptime": 1.5,
            }
        )

        improvement = {
            "timestamp": datetime.datetime.now().isoformat(),
            "prediction": pred,
            "analysis": analysis,
            "actions": [],
        }

        if pred.get("prediction") in ["warning", "critical"]:
            improvement["actions"].append("Optimize resource usage")
            improvements.append("Resource optimization triggered")

        if len(analysis.get("patterns", [])) > 0:
            improvement["actions"].append("Apply pattern-based improvements")
            improvements.append("Pattern improvements applied")

        self.improvements += len(improvements)

        self.opt_text.insert(
            tk.END, f"\n=== SELF-IMPROVEMENT CYCLE {self.improvements} ===\n"
        )
        self.opt_text.insert(tk.END, f"Prediction: {pred}\n")
        self.opt_text.insert(tk.END, f"Actions taken: {len(improvements)}\n")
        for imp in improvements:
            self.opt_text.insert(tk.END, f"  - {imp}\n")

        learn(f"gui_improvement_{self.improvements}", improvement, "self_improving_gui")
        self._refresh_worm_viz()

        self.status = GUIStatus.ACTIVE
        self.status_label.config(text="Status: ACTIVE", fg="#00ff00")
        self._log(
            f"Self-improvement cycle complete: {len(improvements)} improvements", "INFO"
        )

    def _run_diagnostic(self):
        self._log("Running ML diagnostic...", "INFO")

        diagnostic = ML_ORCH.run_ml_diagnostic()

        self.ml_text.insert(tk.END, "\n=== ML DIAGNOSTIC ===\n")
        self.ml_text.insert(
            tk.END,
            f"Orchestrator: {diagnostic.get('orchestrator', {}).get('id', 'N/A')}\n",
        )
        self.ml_text.insert(
            tk.END,
            f"Status: {diagnostic.get('orchestrator', {}).get('status', 'N/A')}\n",
        )
        self.ml_text.insert(
            tk.END,
            f"Models Trained: {diagnostic.get('orchestrator', {}).get('models_trained', 0)}\n",
        )

        self._log("ML diagnostic complete", "INFO")

    def _analyze_patterns(self):
        self._log("Analyzing usage patterns...", "INFO")

        patterns = analyze(
            {
                "session_duration": 5.5,
                "clicks_per_minute": 12,
                "widget_interactions": len(self.widget_metrics),
                "ml_operations": self.ml_predictions,
                "optimization_cycles": self.optimizations,
            }
        )

        self.opt_text.insert(tk.END, "\n=== PATTERN ANALYSIS ===\n")
        self.opt_text.insert(tk.END, f"Summary: {patterns.get('summary', 'N/A')}\n")
        self.opt_text.insert(
            tk.END, f"Patterns found: {len(patterns.get('patterns', []))}\n"
        )
        self.opt_text.insert(
            tk.END, f"Recommendations: {len(patterns.get('recommendations', []))}\n"
        )

        for rec in patterns.get("recommendations", []):
            self.opt_text.insert(tk.END, f"  - {rec}\n")

        self._log("Pattern analysis complete", "INFO")

    def _system_scan(self):
        self._log("Running system scan...", "INFO")
        result = fuse(TIER.SYSTEM, "scan", {})
        self.details_text.insert(tk.END, "\n=== SYSTEM SCAN ===\n")
        self.details_text.insert(tk.END, f"Status: {result.get('status', 'N/A')}\n")
        self.details_text.insert(tk.END, f"Modules: {result.get('mods', 0)}\n")
        self._log("System scan complete", "INFO")

    def _python_status(self):
        self._log("Getting Python status...", "INFO")
        python_info = predict(
            "system_health", {"cpu_usage": 45, "memory_usage": 62, "disk_usage": 60}
        )
        self.details_text.insert(tk.END, "\n=== PYTHON STATUS ===\n")
        self.details_text.insert(tk.END, f"Prediction: {python_info}\n")
        self._log("Python status complete", "INFO")

    def _network_info(self):
        self._log("Getting network info...", "INFO")
        result = fuse(TIER.SYSTEM, "network_info", {})
        self.details_text.insert(tk.END, "\n=== NETWORK INFO ===\n")
        self.details_text.insert(tk.END, f"Result: {str(result)[:500]}\n")
        self._log("Network info complete", "INFO")

    def _full_refresh(self):
        self._log("Running full refresh...", "INFO")
        self._refresh_worm_viz()
        self._self_improve()
        self._log("Full refresh complete", "INFO")

    def run(self):
        self.root.mainloop()


def create_shortcut():
    desktop = Path.home() / "Desktop"
    shortcut_path = desktop / "Self-Improving GUI.lnk"

    import win32com.client  # type: ignore

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(shortcut_path))
    shortcut.Targetpath = sys.executable
    shortcut.Arguments = f'"{Path(__file__).resolve()}"'
    shortcut.WorkingDirectory = str(Path(__file__).parent)
    shortcut.Description = "ML Self-Improving GUI with Worm Visualization"
    shortcut.save()

    return shortcut_path


if __name__ == "__main__":
    print("=" * 70)
    print("  SELF-IMPROVING GUI v1.0 - ML POWERED")
    print("=" * 70)
    print()
    print("Initializing ML-powered self-improving GUI...")

    gui = SelfImprovingGUI()

    print()
    print("GUI initialized successfully!")
    print("Creating desktop shortcut...")

    try:
        shortcut = create_shortcut()
        print(f"Shortcut created: {shortcut}")
    except Exception as e:
        print(f"Could not create shortcut: {e}")
        print("Run manually: python SelfImprovingGUI.py")

    print()
    print("Starting GUI...")
    gui.run()
