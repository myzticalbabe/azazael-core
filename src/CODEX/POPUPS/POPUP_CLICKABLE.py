"""
Azazael Popup - Clickable with Details
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
        "type": "Orchestration",
        "desc": "Adaptive Sequential ML Cluster with 6-stage processing pipeline",
        "features": [
            "6 Stages",
            "10 Nodes",
            "Priority Scheduling",
            "Adaptive Learning",
        ],
    },
    "HORUS": {
        "icon": "👁️",
        "type": "Presentation",
        "desc": "Human-Readable Output System with real-time monitoring",
        "features": ["24 Animations", "Live Updates", "Predictive Analytics"],
    },
    "TARBABY": {
        "icon": "📦",
        "type": "Processing",
        "desc": "Binary and data processing with format conversion",
        "features": ["Data Transformation", "Binary Operations", "File Processing"],
    },
    "SHORTY": {
        "icon": ">>",
        "type": "Integration",
        "desc": "Batch file shortcuts manager for quick operations",
        "features": ["~20 Shortcuts", "CLI Automation", "Quick Operations"],
    },
    "POINDEXTER": {
        "icon": "📊",
        "type": "Analysis",
        "desc": "Mathematical indexer with statistical analysis",
        "features": ["Statistics", "Index Generation", "Data Aggregation"],
    },
    "PERSEPHONE": {
        "icon": "🔄",
        "type": "Utilities",
        "desc": "Cross-platform utilities with OS abstraction",
        "features": ["Cross-Platform", "OS Abstraction", "Portability"],
    },
    "TWITLER": {
        "icon": "🔔",
        "type": "Monitoring",
        "desc": "System information and notification system",
        "features": ["Health Monitoring", "Quick Status", "Alerts"],
    },
    "WORMZ": {
        "icon": "🐛",
        "type": "Execution",
        "desc": "6 functional worms + 5 clusters",
        "features": ["6 Worms", "5 Clusters", "100KB+", "Parallel"],
    },
    "METAWORMS": {
        "icon": "🎯",
        "type": "Orchestration",
        "desc": "11 metaworms + HYDRAMETA ML optimization",
        "features": ["11 Agents", "HYDRAMETA", "Genetic Algorithms"],
    },
    "TERENCE": {
        "icon": "🔧",
        "type": "Error Resolution",
        "desc": "Cross-tier error resolution with 26 fix patterns",
        "features": ["26 Patterns", "RL Learning", "Preview Mode"],
    },
    "KLINK": {
        "icon": "🔗",
        "type": "Integration",
        "desc": "Linkage and connection system between components",
        "features": ["Component Linking", "Dependencies", "Cross-Tier"],
    },
}

TIERS = {
    "DISTILLATION": {
        "num": "5",
        "color": "#a855f7",
        "desc": "Theoretical knowledge synthesis",
    },
    "HYDRATIC": {"num": "4", "color": "#a855f7", "desc": "ML Optimization (HYDRAMETA)"},
    "METAWORMS": {"num": "3", "color": "#ff4757", "desc": "11 orchestration agents"},
    "CLUSTERWORMS": {
        "num": "2",
        "color": "#ff4757",
        "desc": "Coordinated worm collectives",
    },
    "WORMS": {"num": "1", "color": "#ff4757", "desc": "6 functional worms"},
    "CONCURRENCY": {
        "num": "0",
        "color": "#00f5ff",
        "desc": "Parallel execution engine",
    },
    "OS_CONGRUENCY": {
        "num": "-1",
        "color": "#00ff88",
        "desc": "Windows deep integration",
    },
}

COLORS = {
    "HYDRA": "#ff00aa",
    "HORUS": "#00f5ff",
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


class AzazaelPopup:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Azazael ECOSYSTEM - All 11 Systems")
        self.root.geometry("900x700")
        self.root.configure(bg="#0a0a15")
        self.root.resizable(True, True)

        self.setup_ui()
        self.root.mainloop()

    def setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#0a0a15")
        header.pack(fill="x", pady=15)

        tk.Label(
            header,
            text="Azazael ECOSYSTEM",
            font=("Consolas", 24, "bold"),
            bg="#0a0a15",
            fg="#00f5ff",
        ).pack()
        tk.Label(
            header,
            text="ALL 11 SYSTEMS OPERATIONAL",
            font=("Consolas", 10),
            bg="#0a0a15",
            fg="#00ff88",
        ).pack()

        # Stats
        stats_frame = tk.Frame(self.root, bg="#0a0a15")
        stats_frame.pack(fill="x", padx=20, pady=10)

        for label in ["11 Systems", "7 Tiers", "24 Animations", "26 Patterns"]:
            tk.Label(
                stats_frame,
                text=label,
                font=("Consolas", 11),
                bg="#0a0a15",
                fg="#888",
                relief="solid",
                bd=1,
                padx=15,
                pady=8,
            ).pack(side="left", padx=5)

        # Scrollable canvas for systems
        canvas = tk.Canvas(self.root, bg="#0a0a15", highlightthickness=0)
        scrollbar = tk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        scrollable = tk.Frame(canvas, bg="#0a0a15")

        scrollable.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True, padx=20, pady=10)
        scrollbar.pack(side="right", fill="y")

        # Systems Grid
        systems_frame = tk.Frame(scrollable, bg="#0a0a15")
        systems_frame.pack(fill="both", expand=True)

        row, col = 0, 0
        for name, data in SYSTEMS.items():
            card = self.create_card(systems_frame, name, data, row, col)
            card.pack(side="left", padx=8, pady=8, fill="both", expand=True)
            col += 1
            if col > 3:
                col = 0
                row += 1

        # Tiers Section
        tk.Label(
            scrollable,
            text="7-TIER ARCHITECTURE",
            font=("Consolas", 16, "bold"),
            bg="#0a0a15",
            fg="#00ff88",
        ).pack(pady=(30, 15), anchor="w", padx=10)

        tiers_frame = tk.Frame(scrollable, bg="#0a0a15")
        tiers_frame.pack(fill="x", padx=10, pady=10)

        for name, data in TIERS.items():
            tier_card = self.create_tier_card(tiers_frame, name, data)
            tier_card.pack(fill="x", pady=5)

        # Footer
        tk.Label(
            self.root,
            text="CODEX: {DESKTOP_DIR}\\CODEX".format(DESKTOP_DIR=DESKTOP_DIR),
            bg="#0a0a15",
            fg="#666",
            font=("Consolas", 9),
        ).pack(pady=10)

        # Close button
        btn = tk.Button(
            self.root,
            text="CLOSE",
            command=self.root.destroy,
            bg="#e74c3c",
            fg="white",
            font=("Consolas", 12, "bold"),
            relief="flat",
            padx=30,
            pady=8,
        )
        btn.pack(pady=10)

        # Center window
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 900) // 2
        y = (self.root.winfo_screenheight() - 700) // 2
        self.root.geometry(f"900x700+{x}+{y}")

    def create_card(self, parent, name, data, row, col):
        color = COLORS[name]
        card = tk.Frame(
            parent,
            bg="#0f0f25",
            bd=1,
            relief="solid",
            cursor="hand2",
            width=200,
            height=180,
        )

        # Color strip
        strip = tk.Frame(card, bg=color, height=4)
        strip.pack(fill="x")

        content = tk.Frame(card, bg="#0f0f25")
        content.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(content, text=data["icon"], font=("Arial", 24), bg="#0f0f25").pack()
        tk.Label(
            content, text=name, font=("Consolas", 14, "bold"), bg="#0f0f25", fg=color
        ).pack()
        tk.Label(
            content, text=data["type"], font=("Consolas", 9), bg="#0f0f25", fg="#888"
        ).pack()
        tk.Label(
            content,
            text=data["desc"],
            font=("Consolas", 8),
            bg="#0f0f25",
            fg="#aaa",
            wraplength=180,
        ).pack(pady=8)

        # Click handler
        card.bind("<Button-1>", lambda e, n=name, d=data: self.show_details(n, d))
        content.bind("<Button-1>", lambda e, n=name, d=data: self.show_details(n, d))

        # Hover effects
        card.bind("<Enter>", lambda e: self.on_enter(card, color))
        card.bind("<Leave>", lambda e: self.on_leave(card))

        return card

    def create_tier_card(self, parent, name, data):
        color = data["color"]
        card = tk.Frame(
            parent, bg="#0f0f25", bd=1, relief="solid", cursor="hand2", padx=15, pady=12
        )

        # Number badge
        tk.Label(
            card,
            text=data["num"],
            font=("Consolas", 16, "bold"),
            bg=color,
            fg="white",
            width=3,
            relief="flat",
        ).pack(side="left", padx=(0, 15))

        info = tk.Frame(card, bg="#0f0f25")
        info.pack(side="left", fill="both", expand=True)

        tk.Label(
            info, text=name, font=("Consolas", 14, "bold"), bg="#0f0f25", fg=color
        ).pack(anchor="w")
        tk.Label(
            info, text=data["desc"], font=("Consolas", 10), bg="#0f0f25", fg="#888"
        ).pack(anchor="w")

        # Click handler
        card.bind("<Button-1>", lambda e, n=name, d=data: self.show_tier_details(n, d))
        info.bind("<Button-1>", lambda e, n=name, d=data: self.show_tier_details(n, d))

        return card

    def on_enter(self, card, color):
        card.configure(bg="#1a1a35")
        card.configure(highlightbackground=color, highlightthickness=2)

    def on_leave(self, card):
        card.configure(bg="#0f0f25")
        card.configure(highlightbackground="#0f0f25", highlightthickness=0)

    def show_details(self, name, data):
        features = "\n".join(f"* {f}" for f in data["features"])
        messagebox.showinfo(
            f"{data['icon']} {name}", f"{data['desc']}\n\nFeatures:\n{features}"
        )

    def show_tier_details(self, name, data):
        messagebox.showinfo(f"Tier {data['num']}: {name}", data["desc"])


if __name__ == "__main__":
    AzazaelPopup()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
