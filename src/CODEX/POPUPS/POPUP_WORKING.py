"""
Azazael Ecosystem - Working Clickable Popup
Smooth animations with all technical details
"""

import tkinter as tk
from tkinter import messagebox
from typing import List
from CORE.paths import get_desktop_dir

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DESKTOP_DIR = get_desktop_dir()

SYSTEMS = {
    "HYDRA": {
        "icon": "🚀",
        "type": "Orchestration Layer",
        "size": "25KB",
        "lines": 608,
        "desc": "Adaptive Sequential ML Cluster with 6-stage processing pipeline",
        "details": [
            "File: HYDRA.py",
            "Lines: 608",
            "Stages: Discovery, Analysis, Optimization, Validation, Deployment, Feedback",
            "Nodes: 10 total, 8 enabled",
            "Features: Priority scheduling, Adaptive learning, Progress tracking",
            "Commands: --discover, --analyze, --optimize, --validate, --deploy, --feedback",
        ],
    },
    "HORUS": {
        "icon": "👁️",
        "type": "Presentation Layer",
        "size": "Part of core",
        "lines": 450,
        "desc": "Human-Readable Output System with real-time monitoring",
        "details": [
            "File: HORUS.py",
            "Lines: 450+",
            "Features: Real-time monitoring, Predictive analytics",
            "24 Animation protocols integrated",
            "Visual formatting, Live updates",
            "Commands: --monitor, --format, --display",
        ],
    },
    "TARBABY": {
        "icon": "📦",
        "type": "Processing Layer",
        "size": "Part of core",
        "lines": 320,
        "desc": "Binary and Data Processing Module with format conversion",
        "details": [
            "File: TARBABY.py",
            "Lines: 320+",
            "Features: Data transformation, Binary operations",
            "File processing, Format conversion",
            "Commands: --transform, --process, --convert",
        ],
    },
    "SHORTY": {
        "icon": ">>",
        "type": "Integration Layer",
        "size": "~20 shortcuts",
        "lines": "Batch files",
        "desc": "Executable Shortcuts Manager with .bat files",
"details": [
                "Location: {DESKTOP_DIR}\\SHORTY\\".format(DESKTOP_DIR=DESKTOP_DIR),
                "~20 .bat batch files",
            "Shortcuts: azazael.bat, auto_install.bat, bat_analysis.bat",
            "diagnostics.bat, health_monitor.bat",
            "CLI automation, Quick operations",
        ],
    },
    "POINDEXTER": {
        "icon": "📊",
        "type": "Analysis Layer",
        "size": "Part of ecosystem",
        "lines": "Module",
        "desc": "Mathematical Indexer with statistical analysis",
"details": [
                "Location: {DESKTOP_DIR}\\POINDEXTER\\".format(DESKTOP_DIR=DESKTOP_DIR),
                "Features: Statistical analysis, Index generation",
            "Data aggregation, Math computations",
            "Commands: --stats, --index, --aggregate",
        ],
    },
    "PERSEPHONE": {
        "icon": "🔄",
        "type": "Utilities Layer",
        "size": "Part of ecosystem",
        "lines": "Module",
        "desc": "Cross-Platform Utilities with OS abstraction",
"details": [
                "Location: {DESKTOP_DIR}\\PERSEPHONE\\".format(DESKTOP_DIR=DESKTOP_DIR),
                "Features: Cross-platform support, OS abstraction",
            "Portability functions, System utilities",
            "Commands: --cross-platform, --abstract, --port",
        ],
    },
    "TWITLER": {
        "icon": "🔔",
        "type": "Monitoring & Notifications",
        "size": "9 files",
        "lines": "Multiple scripts",
        "desc": "System Information & Notification System",
"details": [
                "Location: {DESKTOP_DIR}\\TWITLER\\".format(DESKTOP_DIR=DESKTOP_DIR),
                "Files: TWITLER.bat, TWITLER_Admin.ps1, install_deps.bat",
            "monitor_system.bat, quick_health.bat, quick_status.bat",
            "RUN_AS_ADMIN.bat, run_wormz_ml.bat",
            "Features: Health monitoring, Quick status, Alerts, Notifications",
        ],
    },
    "WORMZ": {
        "icon": "🐛",
        "type": "Execution Layer (Tiers 1-2)",
        "size": "100KB+",
        "lines": "Multiple files",
        "desc": "Functional Worms System - 6 individual worms + 5 clusters",
"details": [
                "Location: {DESKTOP_DIR}\\WORMZ\\".format(DESKTOP_DIR=DESKTOP_DIR),
                "WORMZ (25KB) - BIBL terminology crawler",
            "SYSTEMZ (7KB) - System monitoring",
            "PROCESSZ (9KB) - Process management",
            "DISKZ (10KB) - Disk management",
            "CLOCKZ (6KB) - Time/scheduling",
            "NETWORXZ (7KB) - Network management",
            "Clusters: BIBL, System, Network, Security, Analysis, Validation, Health, Full",
        ],
    },
    "METAWORMS": {
        "icon": "🎯",
        "type": "Orchestration Layer (Tiers 3-4)",
        "size": "Part of ecosystem",
        "lines": "Multiple files",
        "desc": "Metaworms Orchestration - 11 metaworms + HYDRAMETA ML",
"details": [
                "Location: {DESKTOP_DIR}\\METAWORMS\\".format(DESKTOP_DIR=DESKTOP_DIR),
                "Tier 3: ERRORZ, TRIMZ, METAZ, DEPENDZ, VALIDZ, DEADZ",
            "METRICZ, LOGZ, CHANGEZ, OPTIMIZ, HEALTHZ",
            "Tier 4: HYDRAMETA, HYDRAMETA_UV (ML Optimization)",
            "Outputs: errors.json, trim_results.json, metadata.json",
            "dependencies.json, health.json",
        ],
    },
    "TERENCE": {
        "icon": "🔧",
        "type": "Error Resolution",
        "size": "17KB+",
        "lines": 450,
        "desc": "Cross-Tier Error Resolution Protocol with 26 fix patterns",
        "details": [
            "File: TERENCE.py",
            "Lines: 450+",
            "26 Fix Patterns",
            "Features: Rule-based fixes, Reinforcement learning",
            "Causal analysis, Preview mode",
            "Commands: --analyze <file>, --fix --preview",
            "--reinforce --history, --scan --all-tiers",
        ],
    },
    "KLINK": {
        "icon": "🔗",
        "type": "Integration",
        "size": "Part of ecosystem",
        "lines": "Module",
        "desc": "Linkage and Connection System between components",
        "details": [
            "Location: Part of Azazael core",
            "Features: Component linking, Dependency resolution",
            "Cross-tier communication, API connections",
            "Commands: --link, --resolve, --connect",
        ],
    },
}

TIERS = {
    "-1": {
        "name": "OS_CONGRUENCY",
        "desc": "Deep Windows integration",
        "full": "Tier -1: OS_CONGRUENCY\n\nDeep Windows OS integration providing:\n* Services Management\n* Startup Programs\n* Registry Access\n* Event Logs\n* Windows Updates\n* Driver Enumeration\n* User Sessions\n* Disk Partitions\n* Network Adapters",
    },
    "0": {
        "name": "CONCURRENCY",
        "desc": "Parallel execution engine",
        "full": "Tier 0: CONCURRENCY\n\nCotemporaneous execution engine:\n* Thread Pool (8-16 workers)\n* Parallel Worm Execution\n* Thread-Safe Operations\n* Task Manager\n* Worm Executor\n* Cluster Executor",
    },
    "1": {
        "name": "WORMS",
        "desc": "6 functional agents",
        "full": "Tier 1: WORMS\n\n6 Functional Worms:\n* WORMZ (25KB) - BIBL terminology crawler\n* SYSTEMZ (7KB) - System monitoring\n* PROCESSZ (9KB) - Process management\n* DISKZ (10KB) - Disk management\n* CLOCKZ (6KB) - Time/scheduling\n* NETWORXZ (7KB) - Network management",
    },
    "2": {
        "name": "CLUSTERWORMS",
        "desc": "Coordinated worm collectives",
        "full": "Tier 2: CLUSTERWORMS\n\nPre-configured Coordinated Collectives:\n* BIBL: WORMZ + TRIMZ\n* System: SYSTEMZ + PROCESSZ + DISKZ\n* Network: NETWORXZ + CLOCKZ + DISKZ\n* Security: PROCESSZ + NETWORXZ + SYSTEMZ\n* Analysis: METAZ + METRICZ + DEPENDZ\n* Validation: VALIDZ + DEADZ + ERRORZ\n* Health: HEALTHZ + OPTIMIZ + SYSTEMZ\n* Full: SYSTEMZ + PROCESSZ + DISKZ + NETWORXZ + WORMZ",
    },
    "3": {
        "name": "METAWORMS",
        "desc": "11 orchestration agents",
        "full": "Tier 3: METAWORMS\n\n11 Orchestration Metaworms:\n* ERRORZ - Error crawling\n* TRIMZ - Content trimming\n* METAZ - Metadata extraction\n* DEPENDZ - Dependency mapping\n* VALIDZ - Syntax validation\n* DEADZ - Dead code detection\n* METRICZ - Code metrics\n* LOGZ - Log analysis\n* CHANGEZ - Change tracking\n* OPTIMIZ - Performance optimization\n* HEALTHZ - Health checks",
    },
    "4": {
        "name": "HYDRATIC",
        "desc": "ML Optimization (HYDRAMETA)",
        "full": "Tier 4: HYDRATIC METAWORMS\n\nML Optimization:\n* HYDRAMETA - Population-based parameter tuning\n* HYDRAMETA_UV - uv-powered optimization\n* Genetic Algorithms\n* Trial-Based Learning\n* Fitness Evaluation\n* Persistence\n* Alternating Strategies",
    },
    "5": {
        "name": "DISTILLATION",
        "desc": "Knowledge synthesis",
        "full": "Tier 5: DISTILLATION\n\nTheoretical Knowledge Synthesis:\n* Knowledge accumulation from all executions\n* Pattern consolidation\n* Intelligence pattern synthesis\n* Unified understanding",
    },
}

COLORS = {
    "HYDRA": "#ff00aa",
    "HORUS": "#00d4ff",
    "TARBABY": "#00ff88",
    "SHORTY": "#ffdd00",
    "POINDEXTER": "#bf00ff",
    "PERSEPHONE": "#ff6b35",
    "TWITLER": "#22c55e",
    "WORMZ": "#ff4757",
    "METAWORMS": "#a855f7",
    "TERENCE": "#06b6d4",
    "KLINK": "#ec4899",
}


class AnimatedCard:
    def __init__(self, parent, name, data, callback):
        self.parent = parent
        self.name = name
        self.data = data
        self.callback = callback
        self.color = COLORS[name]
        self.hovered = False
        self.animating = False
        self.create_widget()

    def create_widget(self):
        self.frame = tk.Frame(
            self.parent, bg="#12121f", bd=1, relief="solid", cursor="hand2"
        )

        # Left side - Icon and name
        left = tk.Frame(self.frame, bg="#12121f")
        left.pack(side="left", padx=15, pady=12)

        self.icon_label = tk.Label(
            left, text=self.data["icon"], font=("Arial", 24), bg="#12121f"
        )
        self.icon_label.pack()

        self.name_label = tk.Label(
            left,
            text=self.name,
            font=("Consolas", 12, "bold"),
            bg="#12121f",
            fg=self.color,
        )
        self.name_label.pack()

        self.type_label = tk.Label(
            left, text=self.data["type"], font=("Consolas", 8), bg="#12121f", fg="#666"
        )
        self.type_label.pack()

        # Middle - Description
        mid = tk.Frame(self.frame, bg="#12121f")
        mid.pack(side="left", fill="both", expand=True, padx=15, pady=12)

        self.desc_label = tk.Label(
            mid,
            text=self.data["desc"],
            font=("Consolas", 9),
            bg="#12121f",
            fg="#aaa",
            wraplength=400,
            justify="left",
        )
        self.desc_label.pack(anchor="w")

        # Right - Size info
        right = tk.Frame(self.frame, bg="#12121f")
        right.pack(side="right", padx=15, pady=12)

        self.size_label = tk.Label(
            right,
            text=f"Size: {self.data['size']}",
            font=("Consolas", 9),
            bg="#12121f",
            fg="#555",
        )
        self.size_label.pack()

        # Animated indicator
        self.indicator = tk.Label(
            right, text="▶", font=("Consolas", 10), bg="#12121f", fg=self.color
        )
        self.indicator.pack(pady=(5, 0))

        # Bind events
        self.frame.bind("<Enter>", self.on_enter)
        self.frame.bind("<Leave>", self.on_leave)
        self.frame.bind("<Button-1>", self.on_click)

        for child in self.frame.winfo_children():
            child.bind("<Enter>", self.on_enter)
            child.bind("<Leave>", self.on_leave)
            child.bind("<Button-1>", self.on_click)

    def on_enter(self, e):
        self.hovered = True
        self.frame.configure(bg="#1a1a30")
        self.animate_color("#1a1a30", self.color)
        self.indicator.config(fg="#fff")
        self.start_pulse()

    def on_leave(self, e):
        self.hovered = False
        self.frame.configure(bg="#12121f")
        self.animate_color("#12121f", "#555")
        self.indicator.config(fg=self.color)
        self.stop_pulse()

    def on_click(self, e):
        self.callback(self.name, self.data)

    def animate_color(self, target_bg, target_fg):
        self.frame.configure(bg=target_bg)

    def start_pulse(self):
        if self.hovered and not self.animating:
            self.animating = True
            self.pulse()

    def stop_pulse(self):
        self.animating = False

    def pulse(self):
        if not self.animating:
            return
        # Subtle pulse animation on indicator
        current = self.indicator.cget("text")
        self.indicator.config(text="▶" if current == "▶" else "▶")
        self.frame.after(500, self.pulse)


class AzazaelPopup:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Azazael Ecosystem - All Systems")
        self.root.geometry("1050x750")
        self.root.configure(bg="#0d0d1a")
        self.root.resizable(True, True)

        self.setup_ui()
        self.animate_header()
        self.root.mainloop()

    def setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#0d0d1a")
        header.pack(fill="x", pady=15)

        self.title_label = tk.Label(
            header,
            text="Azazael Ecosystem",
            font=("Consolas", 20, "bold"),
            bg="#0d0d1a",
            fg="#fff",
        )
        self.title_label.pack()

        self.subtitle = tk.Label(
            header,
            text="Complete Technical Documentation",
            font=("Consolas", 10),
            bg="#0d0d1a",
            fg="#666",
        )
        self.subtitle.pack()

        # Animated subtitle dots
        self.subtitle_dots = tk.Label(
            header, text="", font=("Consolas", 10), bg="#0d0d1a", fg="#00d4ff"
        )
        self.subtitle_dots.pack()

        # Stats bar
        self.create_stats_bar()

        # Scrollable content
        canvas = tk.Canvas(self.root, bg="#0d0d1a", highlightthickness=0)
        scrollbar = tk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        scrollable = tk.Frame(canvas, bg="#0d0d1a")

        scrollable.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable, anchor="nw", width=1030)
        canvas.configure(yscrollcommand=scrollbar.set, width=1050)

        canvas.pack(side="left", fill="both", expand=True, padx=(20, 0), pady=10)
        scrollbar.pack(side="right", fill="y", pady=10)

        # Systems section
        tk.Label(
            scrollable,
            text="=== SYSTEMS ===",
            font=("Consolas", 11, "bold"),
            bg="#0d0d1a",
            fg="#555",
        ).pack(pady=(10, 15), anchor="w", padx=10)

        self.system_cards = []
        for name in SYSTEMS:
            card = AnimatedCard(scrollable, name, SYSTEMS[name], self.show_details)
            card.frame.pack(fill="x", padx=10, pady=5)
            self.system_cards.append(card)

        # Tiers section
        tk.Label(
            scrollable,
            text="=== 7-TIER ARCHITECTURE ===",
            font=("Consolas", 11, "bold"),
            bg="#0d0d1a",
            fg="#555",
        ).pack(pady=(25, 15), anchor="w", padx=10)

        for num in ["-1", "0", "1", "2", "3", "4", "5"]:
            tier = TIER_LABELS[num]
            tier_card = self.create_tier_card(scrollable, num, tier)
            tier_card.pack(fill="x", padx=10, pady=3)

        # Footer
        tk.Label(
            scrollable,
            text="CODEX: {DESKTOP_DIR}\\CODEX".format(DESKTOP_DIR=DESKTOP_DIR),
            font=("Consolas", 9),
            bg="#0d0d1a",
            fg="#444",
        ).pack(pady=20)

        # Close button
        btn = tk.Button(
            self.root,
            text="Close",
            command=self.root.destroy,
            bg="#222",
            fg="white",
            font=("Consolas", 11, "bold"),
            relief="flat",
            padx=40,
            pady=8,
        )
        btn.pack(pady=10)

        # Center window
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 1050) // 2
        y = (self.root.winfo_screenheight() - 750) // 2
        self.root.geometry(f"1050x750+{x}+{y}")

    def create_stats_bar(self):
        stats_frame = tk.Frame(self.root, bg="#15152a", pady=10)
        stats_frame.pack(fill="x", padx=20)

        stats = [
            ("11", "Systems", "#00d4ff"),
            ("7", "Tiers", "#00ff88"),
            ("608+", "Lines", "#ff00aa"),
            ("26", "Patterns", "#ffdd00"),
            ("100KB+", "Code", "#a855f7"),
        ]

        self.stat_labels = []
        for value, label, color in stats:
            frame = tk.Frame(stats_frame, bg="#15152a")
            frame.pack(side="left", padx=25)

            val_lbl = tk.Label(
                frame, text=value, font=("Consolas", 16, "bold"), bg="#15152a", fg=color
            )
            val_lbl.pack()

            lbl = tk.Label(
                frame, text=label, font=("Consolas", 9), bg="#15152a", fg="#666"
            )
            lbl.pack()

            self.stat_labels.append((val_lbl, color))

    def create_tier_card(self, parent, num, data):
        color = data["color"]

        frame = tk.Frame(
            parent, bg="#12121f", bd=1, relief="solid", cursor="hand2", padx=12, pady=8
        )

        # Number badge
        badge = tk.Label(
            frame,
            text=num,
            font=("Consolas", 12, "bold"),
            bg=color,
            fg="white",
            width=3,
            relief="flat",
        )
        badge.pack(side="left", padx=(0, 15))

        # Info
        info = tk.Frame(frame, bg="#12121f")
        info.pack(side="left", fill="x", expand=True)

        tk.Label(
            info,
            text=data["name"],
            font=("Consolas", 11, "bold"),
            bg="#12121f",
            fg=color,
        ).pack(anchor="w")
        tk.Label(
            info, text=data["desc"], font=("Consolas", 9), bg="#12121f", fg="#777"
        ).pack(anchor="w")

        # Arrow
        arrow = tk.Label(frame, text="▶", font=("Consolas", 10), bg="#12121f", fg=color)
        arrow.pack(side="right", padx=10)

        # Hover
        def on_enter(e):
            frame.configure(bg="#1a1a30")
            arrow.config(fg="#fff")

        def on_leave(e):
            frame.configure(bg="#12121f")
            arrow.config(fg=color)

        def on_click(e):
            self.show_tier_details(num, TIER[num])

        frame.bind("<Enter>", on_enter)
        frame.bind("<Leave>", on_leave)
        frame.bind("<Button-1>", on_click)

        return frame

    def animate_header(self):
        # Animate stats colors
        colors = ["#00d4ff", "#00ff88", "#ff00aa", "#ffdd00", "#a855f7"]
        idx = [0]

        def cycle_stats():
            for i, (lbl, _) in enumerate(self.stat_labels):
                lbl.config(fg=colors[(idx[0] + i) % len(colors)])
            idx[0] = (idx[0] + 1) % len(colors)
            self.root.after(800, cycle_stats)

        cycle_stats()

    def show_details(self, name, data):
        lines = []
        lines.append(f"{data['icon']} {name}")
        lines.append("=" * 50)
        lines.append(f"Type: {data['type']}")
        lines.append(f"Size: {data['size']}")
        lines.append("")
        lines.append(data["desc"])
        lines.append("")
        lines.append("=== Technical Details ===")
        for detail in data["details"]:
            lines.append(f"* {detail}")

        messagebox.showinfo(f"{data['icon']} {name}", "\n".join(lines))

    def show_tier_details(self, num, data):
        full = data["desc"]
        messagebox.showinfo(f"Tier {num}: {data['name']}", full)


TIER_LABELS = {
    "-1": {
        "name": "OS_CONGRUENCY",
        "desc": "Windows deep integration",
        "color": "#00ff88",
    },
    "0": {
        "name": "CONCURRENCY",
        "desc": "Parallel execution engine",
        "color": "#00d4ff",
    },
    "1": {"name": "WORMS", "desc": "6 functional agents", "color": "#ff4757"},
    "2": {
        "name": "CLUSTERWORMS",
        "desc": "Coordinated collectives",
        "color": "#ff4757",
    },
    "3": {"name": "METAWORMS", "desc": "11 orchestration agents", "color": "#ff4757"},
    "4": {"name": "HYDRATIC", "desc": "ML Optimization", "color": "#a855f7"},
    "5": {"name": "DISTILLATION", "desc": "Knowledge synthesis", "color": "#a855f7"},
}

TIER = {
    "-1": {
        "name": "OS_CONGRUENCY",
        "desc": "Tier -1: OS Congruency\n\nDeep Windows OS Integration\n\nProvides comprehensive system access:\n* Services Management - Start/stop/query Windows services\n* Startup Programs - Registry + folder startup items\n* Registry Access - WinReg read/write\n* Event Logs - Windows Event Log analysis\n* Windows Updates - Update status checking\n* Driver Enumeration - list installed drivers\n* User Sessions - Active user detection\n* Disk Partitions - All partitions with detail\n* Network Adapters - Adapter status & stats\n* CPU/Memory/Disk - Deep system monitoring",
    },
    "0": {
        "name": "CONCURRENCY",
        "desc": "Tier 0: Concurrency Manager\n\nCotemporaneous Execution Engine\n\nProvides parallel execution:\n* Thread Pool - Configurable workers (default: 8)\n* Parallel Worm Execution - SYSTEMZ, PROCESSZ, DISKZ, NETWORXZ concurrently\n* Parallel Cluster Execution - Multiple clusters at once\n* Thread-Safe Operations - Lock-protected shared state\n* Task Manager - Submit/track tasks\n* Worm Executor - Load and execute worms concurrently\n* Cluster Executor - Execute clusters in parallel",
    },
    "1": {
        "name": "WORMS",
        "desc": "Tier 1: Worms (Functional Foundation)\n\n6 Individual Worms:\n* WORMZ (25KB) - BIBL terminology crawler\n  Commands: --all, --delta, --folder, --popup\n* SYSTEMZ (7KB) - System monitoring\n  Commands: --status, --cpu, --memory, --disk, --json\n* PROCESSZ (9KB) - Process management\n  Commands: --list, --find, --kill, --summary\n* DISKZ (10KB) - Disk management\n  Commands: --usage, --partitions, --io, --analyze\n* CLOCKZ (6KB) - Time/scheduling\n  Commands: --time, --uptime, --schedule, --tasks\n* NETWORXZ (7KB) - Network management\n  Commands: --interfaces, --connections, --stats, --ping",
    },
    "2": {
        "name": "CLUSTERWORMS",
        "desc": "Tier 2: ClusterWorms\n\nCoordinated Worm Collectives:\n* BIBL: WORMZ + TRIMZ - Content crawling & trimming\n* System: SYSTEMZ + PROCESSZ + DISKZ - Full system monitoring\n* Network: NETWORXZ + CLOCKZ + DISKZ - Infrastructure health\n* Security: PROCESSZ + NETWORXZ + SYSTEMZ - Security auditing\n* Analysis: METAZ + METRICZ + DEPENDZ - Code analysis\n* Validation: VALIDZ + DEADZ + ERRORZ - Code validation\n* Health: HEALTHZ + OPTIMIZ + SYSTEMZ - System health\n* Full: SYSTEMZ + PROCESSZ + DISKZ + NETWORXZ + WORMZ - Complete ecosystem",
    },
    "3": {
        "name": "METAWORMS",
        "desc": "Tier 3: Metaworms\n\n11 Orchestration Agents:\n* ERRORZ - Error crawling (outputs: errors.json)\n* TRIMZ - Content trimming (outputs: trim_results.json)\n* METAZ - Metadata extraction (outputs: metadata.json)\n* DEPENDZ - Dependency mapping (outputs: dependencies.json)\n* VALIDZ - Syntax validation (outputs: validation.json)\n* DEADZ - Dead code detection (outputs: dead_code.json)\n* METRICZ - Code metrics (outputs: metrics.json)\n* LOGZ - Log analysis (outputs: log_analysis.json)\n* CHANGEZ - Change tracking (outputs: changes.json)\n* OPTIMIZ - Performance optimization (outputs: optimization.json)\n* HEALTHZ - Health checks (outputs: health.json)",
    },
    "4": {
        "name": "HYDRATIC",
        "desc": "Tier 4: Hydratic Metaworms\n\nML Optimization:\n* HYDRAMETA - Population-based parameter tuning\n  Features: Genetic algorithms, Trial-based learning\n  Fitness evaluation, Persistence\n* HYDRAMETA_UV - uv-powered optimization\nAlternating Strategies:\n  - round_robin\n  - performance_based\n  - ml_optimized",
    },
    "5": {
        "name": "DISTILLATION",
        "desc": "Tier 5: Theoretical Distillation\n\nTheoretical Apex:\nKnowledge accumulated from:\n* All worm executions\n* All cluster coordinations\n* All metaworm optimizations\n* All hydratic improvements\n\nResult: Unified intelligence patterns",
    },
}


if __name__ == "__main__":
    AzazaelPopup()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
