"""
Azazael Ecosystem - Clean Informative Popup
Detailed information with smooth, subtle animations
"""

from pathlib import Path
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
        "desc": "Adaptive Sequential ML Cluster. Orchestrates all ecosystem components with 6-stage processing pipeline.",
        "stages": [
            "Discovery",
            "Analysis",
            "Optimization",
            "Validation",
            "Deployment",
            "Feedback",
        ],
        "nodes": {"total": 10, "enabled": 8},
        "commands": [
            "--discover",
            "--analyze",
            "--optimize",
            "--validate",
            "--deploy",
            "--feedback",
        ],
    },
    "HORUS": {
        "icon": "👁️",
        "type": "Presentation Layer",
        "size": "Part of core",
        "desc": "Human-Readable Output System. Formats and displays results with real-time monitoring.",
        "features": [
            "Real-time monitoring",
            "Predictive analytics",
            "Visual formatting",
            "Live updates",
        ],
        "commands": ["--monitor", "--format", "--display"],
    },
    "TARBABY": {
        "icon": "📦",
        "type": "Processing Layer",
        "size": "Part of core",
        "desc": "Binary and Data Processing Module. Handles data transformation, binary operations, and format conversion.",
        "features": [
            "Data transformation",
            "Binary operations",
            "File processing",
            "Format conversion",
        ],
        "commands": ["--transform", "--process", "--convert"],
    },
    "SHORTY": {
        "icon": ">>",
        "type": "Integration Layer",
        "size": "~20 shortcuts",
        "desc": "Executable Shortcuts Manager. Batch file shortcuts for quick ecosystem operations.",
        "shortcuts": [
            "azazael.bat",
            "auto_install.bat",
            "bat_analysis.bat",
            "diagnostics.bat",
            "health_monitor.bat",
        ],
        "location": Path.home() / "Desktop" / "SHORTY",
    },
    "POINDEXTER": {
        "icon": "📊",
        "type": "Analysis Layer",
        "size": "Part of ecosystem",
        "desc": "Mathematical Indexer. Provides statistical analysis, index generation, and data aggregation.",
        "features": [
            "Statistical analysis",
            "Index generation",
            "Data aggregation",
            "Math computations",
        ],
        "commands": ["--stats", "--index", "--aggregate"],
    },
    "PERSEPHONE": {
        "icon": "🔄",
        "type": "Utilities Layer",
        "size": "Part of ecosystem",
        "desc": "Cross-Platform Utilities. Provides OS abstraction and portability functions.",
        "features": [
            "Cross-platform support",
            "OS abstraction",
            "Portability",
            "System utilities",
        ],
        "commands": ["--cross-platform", "--abstract", "--port"],
    },
    "TWITLER": {
        "icon": "🔔",
        "type": "Monitoring & Notifications",
        "size": "9 files",
        "desc": "System Information & Notification System. Monitors health, provides alerts and status checks.",
        "features": [
            "Health monitoring",
            "Quick status",
            "WORMZ_ML execution",
            "Admin operations",
            "Notifications",
        ],
        "files": [
            "TWITLER.bat",
            "TWITLER_Admin.ps1",
            "install_deps.bat",
            "monitor_system.bat",
            "quick_health.bat",
            "quick_status.bat",
            "RUN_AS_ADMIN.bat",
            "run_wormz_ml.bat",
        ],
        "location": Path.home() / "Desktop" / "TWITLER",
    },
    "WORMZ": {
        "icon": "🐛",
        "type": "Execution Layer (Tiers 1-2)",
        "size": "100KB+",
        "desc": "Functional Worms System. Tier 1: 6 individual worms. Tier 2: Coordinated clusters.",
        "worms": {
            "WORMZ": {"size": "25KB", "purpose": "BIBL terminology crawler"},
            "SYSTEMZ": {"size": "7KB", "purpose": "System monitoring"},
            "PROCESSZ": {"size": "9KB", "purpose": "Process management"},
            "DISKZ": {"size": "10KB", "purpose": "Disk management"},
            "CLOCKZ": {"size": "6KB", "purpose": "Time/scheduling"},
            "NETWORXZ": {"size": "7KB", "purpose": "Network management"},
        },
        "clusters": [
            "BIBL",
            "System",
            "Network",
            "Security",
            "Analysis",
            "Validation",
            "Health",
            "Full",
        ],
        "location": Path.home() / "Desktop" / "WORMZ",
    },
    "METAWORMS": {
        "icon": "🎯",
        "type": "Orchestration Layer (Tiers 3-4)",
        "size": "Part of ecosystem",
        "desc": "Metaworms Orchestration. Tier 3: 11 metaworms. Tier 4: Hydratic ML optimization.",
        "metaworms": {
            "Tier 3": [
                "ERRORZ",
                "TRIMZ",
                "METAZ",
                "DEPENDZ",
                "VALIDZ",
                "DEADZ",
                "METRICZ",
                "LOGZ",
                "CHANGEZ",
                "OPTIMIZ",
                "HEALTHZ",
            ],
            "Tier 4": ["HYDRAMETA", "HYDRAMETA_UV"],
        },
        "outputs": [
            "errors.json",
            "trim_results.json",
            "metadata.json",
            "dependencies.json",
            "health.json",
        ],
        "location": Path.home() / "Desktop" / "METAWORMS",
    },
    "TERENCE": {
        "icon": "🔧",
        "type": "Error Resolution",
        "size": "17KB+",
        "desc": "Cross-Tier Error Resolution Protocol. Provides 26 fix patterns with rule-based fixes and RL.",
        "features": [
            "26 Fix Patterns",
            "Rule-based fixes",
            "Reinforcement Learning",
            "Causal analysis",
            "Preview mode",
        ],
        "commands": [
            "--analyze <file>",
            "--fix --preview",
            "--reinforce --history",
            "--scan --all-tiers",
        ],
        "location": Path.home() / "Desktop" / "TERENCE",
    },
    "KLINK": {
        "icon": "🔗",
        "type": "Integration",
        "size": "Part of ecosystem",
        "desc": "Linkage and Connection System. Provides component linking, dependency resolution, and API connections.",
        "features": [
            "Component linking",
            "Dependency resolution",
            "Cross-tier communication",
            "API connections",
        ],
        "commands": ["--link", "--resolve", "--connect"],
    },
}

TIER_LABELS = {
    "5": {
        "name": "DISTILLATION",
        "desc": "Theoretical knowledge synthesis",
        "color": "#a855f7",
    },
    "4": {
        "name": "HYDRATIC METAWORMS",
        "desc": "ML Optimization (HYDRAMETA)",
        "color": "#a855f7",
    },
    "3": {"name": "METAWORMS", "desc": "11 orchestration agents", "color": "#ff4757"},
    "2": {
        "name": "CLUSTERWORMS",
        "desc": "Coordinated worm collectives",
        "color": "#ff4757",
    },
    "1": {"name": "WORMS", "desc": "6 functional agents", "color": "#ff4757"},
    "0": {
        "name": "CONCURRENCY",
        "desc": "Parallel execution engine (8-16 workers)",
        "color": "#00f5ff",
    },
    "-1": {
        "name": "OS_CONGRUENCY",
        "desc": "Deep Windows integration",
        "color": "#00ff88",
    },
}


class AzazaelPopup:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Azazael Ecosystem - All Systems")
        self.root.geometry("1000x750")
        self.root.configure(bg="#0d0d1a")
        self.root.resizable(True, True)

        self.setup_ui()
        self.root.mainloop()

    def setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#0d0d1a")
        header.pack(fill="x", pady=15)

        tk.Label(
            header,
            text="Azazael Ecosystem",
            font=("Consolas", 22, "bold"),
            bg="#0d0d1a",
            fg="#ffffff",
        ).pack()
        tk.Label(
            header,
            text="Complete System Documentation",
            font=("Consolas", 10),
            bg="#0d0d1a",
            fg="#888",
        ).pack()

        # Stats bar
        stats_frame = tk.Frame(self.root, bg="#15152a", pady=12)
        stats_frame.pack(fill="x", padx=20)

        stats = [
            ("11", "Systems"),
            ("7", "Tiers"),
            ("100KB+", "Code"),
            ("26", "Patterns"),
            ("6", "Worms"),
            ("11", "Metaworms"),
        ]

        for value, label in stats:
            frame = tk.Frame(stats_frame, bg="#15152a")
            frame.pack(side="left", padx=20)
            tk.Label(
                frame,
                text=value,
                font=("Consolas", 18, "bold"),
                bg="#15152a",
                fg="#00d4ff",
            ).pack()
            tk.Label(
                frame, text=label, font=("Consolas", 9), bg="#15152a", fg="#888"
            ).pack()

        # Main content - scrollable
        canvas = tk.Canvas(self.root, bg="#0d0d1a", highlightthickness=0)
        scrollbar = tk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        scrollable = tk.Frame(canvas, bg="#0d0d1a")

        scrollable.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable, anchor="nw", width=980)
        canvas.configure(yscrollcommand=scrollbar.set, width=1000)

        canvas.pack(side="left", fill="both", expand=True, padx=(20, 0), pady=10)
        scrollbar.pack(side="right", fill="y", pady=10)

        # Systems section title
        tk.Label(
            scrollable,
            text="=== CORE SYSTEMS ===",
            font=("Consolas", 12, "bold"),
            bg="#0d0d1a",
            fg="#666",
        ).pack(pady=(10, 15), anchor="w", padx=10)

        # Systems grid
        for i, (name, data) in enumerate(SYSTEMS.items()):
            card = self.create_system_card(scrollable, name, data)
            card.pack(fill="x", padx=10, pady=5)

        # Tiers section title
        tk.Label(
            scrollable,
            text="=== 7-TIER ARCHITECTURE ===",
            font=("Consolas", 12, "bold"),
            bg="#0d0d1a",
            fg="#666",
        ).pack(pady=(25, 15), anchor="w", padx=10)

        # Tiers
        for num in ["-1", "0", "1", "2", "3", "4", "5"]:
            tier_data = TIER_LABELS[num]
            tier_card = self.create_tier_card(scrollable, num, tier_data)
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
            bg="#333",
            fg="white",
            font=("Consolas", 11, "bold"),
            relief="flat",
            padx=40,
            pady=8,
        )
        btn.pack(pady=10)

        # Center window
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 1000) // 2
        y = (self.root.winfo_screenheight() - 750) // 2
        self.root.geometry(f"1000x750+{x}+{y}")

    def create_system_card(self, parent, name, data):
        color = self.get_color(name)

        card = tk.Frame(
            parent, bg="#12121f", bd=1, relief="solid", cursor="hand2", padx=15, pady=12
        )

        # Left: Icon and name
        left = tk.Frame(card, bg="#12121f")
        left.pack(side="left", padx=(0, 20))

        tk.Label(left, text=data["icon"], font=("Arial", 28), bg="#12121f").pack()
        tk.Label(
            left, text=name, font=("Consolas", 14, "bold"), bg="#12121f", fg=color
        ).pack()
        tk.Label(
            left, text=data["type"], font=("Consolas", 8), bg="#12121f", fg="#666"
        ).pack()

        # Middle: Description
        middle = tk.Frame(card, bg="#12121f")
        middle.pack(side="left", fill="x", expand=True, padx=10)

        tk.Label(
            middle,
            text=data["desc"],
            font=("Consolas", 10),
            bg="#12121f",
            fg="#aaa",
            wraplength=500,
            justify="left",
        ).pack(anchor="w")

        # Right: Details
        right = tk.Frame(card, bg="#12121f")
        right.pack(side="right", padx=(20, 0))

        if "size" in data:
            tk.Label(
                right,
                text=f"Size: {data['size']}",
                font=("Consolas", 9),
                bg="#12121f",
                fg="#666",
            ).pack(anchor="e")

        # Hover and click effects
        def on_enter(e):
            card.configure(bg="#1a1a30")

        def on_leave(e):
            card.configure(bg="#12121f")

        def on_click(e):
            self.show_system_details(name, data)

        card.bind("<Enter>", on_enter)
        card.bind("<Leave>", on_leave)
        card.bind("<Button-1>", on_click)

        for child in card.winfo_children():
            child.bind("<Button-1>", on_click)
            child.bind("<Enter>", on_enter)
            child.bind("<Leave>", on_leave)

        return card

    def create_tier_card(self, parent, num, data):
        color = data["color"]

        card = tk.Frame(
            parent, bg="#12121f", bd=1, relief="solid", cursor="hand2", padx=12, pady=10
        )

        # Tier number
        tk.Label(
            card,
            text=num,
            font=("Consolas", 14, "bold"),
            bg=color,
            fg="white",
            width=3,
            relief="flat",
        ).pack(side="left", padx=(0, 15))

        # Info
        info = tk.Frame(card, bg="#12121f")
        info.pack(side="left", fill="x", expand=True)

        tk.Label(
            info,
            text=data["name"],
            font=("Consolas", 12, "bold"),
            bg="#12121f",
            fg=color,
        ).pack(anchor="w")
        tk.Label(
            info, text=data["desc"], font=("Consolas", 10), bg="#12121f", fg="#888"
        ).pack(anchor="w")

        # Hover effects
        def on_enter(e):
            card.configure(bg="#1a1a30")

        def on_leave(e):
            card.configure(bg="#12121f")

        def on_click(e):
            self.show_tier_details(num, data)

        card.bind("<Enter>", on_enter)
        card.bind("<Leave>", on_leave)
        card.bind("<Button-1>", on_click)

        return card

    def get_color(self, name):
        colors = {
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
        return colors.get(name, "#888")

    def show_system_details(self, name, data):
        lines = []
        lines.append(f"{data['icon']} {name}")
        lines.append("=" * 50)
        lines.append(f"Type: {data['type']}")
        lines.append(f"Size: {data['size']}")
        lines.append("")
        lines.append(data["desc"])
        lines.append("")

        if "stages" in data:
            lines.append(f"Stages: {', '.join(data['stages'])}")
            lines.append(
                f"Nodes: {data['nodes']['total']} total, {data['nodes']['enabled']} enabled"
            )

        if "features" in data:
            lines.append("")
            lines.append("Features:")
            for f in data["features"]:
                lines.append(f"  * {f}")

        if "worms" in data:
            lines.append("")
            lines.append("Worms:")
            for w, info in data["worms"].items():
                lines.append(f"  * {w}: {info['size']} - {info['purpose']}")

        if "metaworms" in data:
            lines.append("")
            lines.append("Metaworms:")
            for tier, worms in data["metaworms"].items():
                lines.append(f"  {tier}: {', '.join(worms)}")

        if "shortcuts" in data:
            lines.append("")
            lines.append("Shortcuts:")
            for s in data["shortcuts"][:5]:
                lines.append(f"  * {s}")

        if "files" in data:
            lines.append("")
            lines.append("Files:")
            for f in data["files"][:5]:
                lines.append(f"  * {f}")

        if "location" in data:
            lines.append("")
            lines.append(f"Location: {data['location']}")

        if "commands" in data:
            lines.append("")
            lines.append("Commands:")
            for c in data["commands"]:
                lines.append(f"  {c}")

        messagebox.showinfo(f"{data['icon']} {name}", "\n".join(lines))

    def show_tier_details(self, num, data):
        tier_info = {
            "-1": {
                "name": "OS_CONGRUENCY",
                "full": "Tier -1: OS Congruency",
                "desc": "Deep Windows OS integration providing services, registry access, event logs, Windows updates, driver enumeration, user sessions, disk partitions, and network adapters.",
                "components": [
                    "Services Management",
                    "Startup Programs",
                    "Registry Access",
                    "Event Logs",
                    "Windows Updates",
                    "Driver Enumeration",
                    "User Sessions",
                    "Disk Partitions",
                    "Network Adapters",
                ],
            },
            "0": {
                "name": "CONCURRENCY",
                "full": "Tier 0: Concurrency Manager",
                "desc": "Cotemporaneous execution engine providing parallel worm and cluster execution with configurable thread pool (8-16 workers).",
                "components": [
                    "Thread Pool (8-16 workers)",
                    "Parallel Worm Execution",
                    "Thread-Safe Operations",
                    "Task Manager",
                    "Worm Executor",
                ],
            },
            "1": {
                "name": "WORMS",
                "full": "Tier 1: Worms (Functional Foundation)",
                "desc": "6 functional worms providing individual system capabilities.",
                "components": [
                    "WORMZ (25KB) - BIBL terminology crawler",
                    "SYSTEMZ (7KB) - System monitoring",
                    "PROCESSZ (9KB) - Process management",
                    "DISKZ (10KB) - Disk management",
                    "CLOCKZ (6KB) - Time/scheduling",
                    "NETWORXZ (7KB) - Network management",
                ],
            },
            "2": {
                "name": "CLUSTERWORMS",
                "full": "Tier 2: ClusterWorms (Coordinated Collectives)",
                "desc": "Pre-configured worm clusters for coordinated multi-worm operations.",
                "components": [
                    "BIBL Cluster: WORMZ + TRIMZ",
                    "System Cluster: SYSTEMZ + PROCESSZ + DISKZ",
                    "Network Cluster: NETWORXZ + CLOCKZ + DISKZ",
                    "Security Cluster: PROCESSZ + NETWORXZ + SYSTEMZ",
                    "Analysis Cluster: METAZ + METRICZ + DEPENDZ",
                    "Validation Cluster: VALIDZ + DEADZ + ERRORZ",
                    "Health Cluster: HEALTHZ + OPTIMIZ + SYSTEMZ",
                    "Full Cluster: All worms + WORMZ",
                ],
            },
            "3": {
                "name": "METAWORMS",
                "full": "Tier 3: Metaworms (Orchestration)",
                "desc": "11 metaworms providing higher-level orchestration and analysis capabilities.",
                "components": [
                    "ERRORZ - Error crawling",
                    "TRIMZ - Content trimming",
                    "METAZ - Metadata extraction",
                    "DEPENDZ - Dependency mapping",
                    "VALIDZ - Syntax validation",
                    "DEADZ - Dead code detection",
                    "METRICZ - Code metrics",
                    "LOGZ - Log analysis",
                    "CHANGEZ - Change tracking",
                    "OPTIMIZ - Performance optimization",
                    "HEALTHZ - Health checks",
                ],
            },
            "4": {
                "name": "HYDRATIC METAWORMS",
                "full": "Tier 4: Hydratic Metaworms (ML Optimization)",
                "desc": "ML-powered optimization using population-based parameter tuning and genetic algorithms.",
                "components": [
                    "HYDRAMETA - Population-based tuning",
                    "HYDRAMETA_UV - uv-powered optimization",
                    "Genetic Algorithms",
                    "Trial-Based Learning",
                    "Fitness Evaluation",
                    "Persistence",
                    "Alternating Strategies",
                ],
            },
            "5": {
                "name": "DISTILLATION",
                "full": "Tier 5: Theoretical Distillation",
                "desc": "Theoretical apex where accumulated knowledge from all tiers is synthesized into unified intelligence patterns.",
                "components": [
                    "Knowledge synthesis",
                    "Pattern consolidation",
                    "Intelligence patterns",
                    "Unified understanding",
                ],
            },
        }

        info = tier_info.get(
            num, {"full": data["name"], "desc": data["desc"], "components": []}
        )

        lines = []
        lines.append(info["full"])
        lines.append("=" * 50)
        lines.append("")
        lines.append(info["desc"])
        lines.append("")
        lines.append("Components:")
        for c in info["components"]:
            lines.append(f"  * {c}")

        messagebox.showinfo(f"Tier {num}: {info['name']}", "\n".join(lines))


if __name__ == "__main__":
    AzazaelPopup()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
