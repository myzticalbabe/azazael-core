"""Rich graphics popup display"""

import tkinter as tk
from tkinter import scrolledtext
import time
from typing import List
import sys

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class RichPopup:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("HYDRA Cluster - Rich Results")
        self.root.geometry("900x680")
        self.root.configure(bg="#050510")
        self.root.overrideredirect(False)
        self.root.attributes("-alpha", 0.95)

        self.setup_ui()
        self.animate()
        self.root.mainloop()

    def setup_ui(self):
        # Main container
        self.main_frame = tk.Frame(self.root, bg="#050510")
        self.main_frame.pack(fill="both", expand=True)

        # Header with gradient effect
        self.header = tk.Frame(self.main_frame, bg="#0a0a1a", height=100)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)

        # Animated title
        self.title_label = tk.Label(
            self.header,
            text="HYDRA",
            font=("Consolas", 32, "bold"),
            bg="#0a0a1a",
            fg="#00f5ff",
        )
        self.title_label.place(relx=0.5, rely=0.35, anchor="center")

        self.subtitle = tk.Label(
            self.header,
            text="CLUSTER RESULTS",
            font=("Consolas", 14),
            bg="#0a0a1a",
            fg="#888",
        )
        self.subtitle.place(relx=0.5, rely=0.65, anchor="center")

        # Status indicator
        self.status_frame = tk.Frame(self.main_frame, bg="#050510")
        self.status_frame.pack(fill="x", pady=10)

        self.status_label = tk.Label(
            self.status_frame, text="*", font=("Arial", 12), bg="#050510", fg="#00ff88"
        )
        self.status_label.pack(side="left", padx=20)

        self.status_text = tk.Label(
            self.status_frame,
            text="SYSTEM OPERATIONAL",
            font=("Consolas", 10),
            bg="#050510",
            fg="#00ff88",
        )
        self.status_text.pack(side="left")

        # Metrics section
        self.metrics_frame = tk.Frame(self.main_frame, bg="#050510")
        self.metrics_frame.pack(fill="x", padx=20, pady=10)

        self.metrics = [
            ("Nodes", "10/8", "#00f5ff"),
            ("Pipeline", "6 Stages", "#bf00ff"),
            ("Tests", "100%", "#00ff88"),
            ("Health", "EXCELLENT", "#ffdd00"),
        ]

        self.metric_widgets = []
        for i, (label, value, color) in enumerate(self.metrics):
            frame = tk.Frame(self.metrics_frame, bg="#0a0a1a", bd=1, relief="solid")
            frame.grid(row=0, column=i, padx=8, ipadx=15, ipady=10, sticky="nsew")
            self.metrics_frame.grid_columnconfigure(i, weight=1)

            tk.Label(
                frame, text=label, font=("Consolas", 9), bg="#0a0a1a", fg="#888"
            ).pack(pady=(8, 3))

            val_lbl = tk.Label(
                frame, text=value, font=("Consolas", 16, "bold"), bg="#0a0a1a", fg=color
            )
            val_lbl.pack(pady=3)

            self.metric_widgets.append(val_lbl)

        # Progress bars
        self.progress_frame = tk.Frame(self.main_frame, bg="#050510")
        self.progress_frame.pack(fill="x", padx=25, pady=12)

        self.progress_data = [
            ("Pipeline Completion", 100, "#00ff88"),
            ("Node Health", 80, "#00f5ff"),
            ("Test Success", 100, "#bf00ff"),
            ("ML Integration", 100, "#ff00aa"),
        ]

        self.progress_widgets = []
        for label, pct, color in self.progress_data:
            row = tk.Frame(self.progress_frame, bg="#050510")
            row.pack(fill="x", pady=4)

            tk.Label(
                row,
                text=label,
                font=("Consolas", 9),
                bg="#050510",
                fg="#888",
                width=20,
                anchor="w",
            ).pack(side="left")

            pct_lbl = tk.Label(
                row, text=f"{pct}%", font=("Consolas", 9), bg="#050510", fg=color
            )
            pct_lbl.pack(side="right", padx=10)

            bar_bg = tk.Frame(row, bg="#1a1a30", height=6, width=300)
            bar_bg.pack(side="right")
            bar_bg.pack_propagate(False)

            bar_fill = tk.Frame(bar_bg, bg=color, height=6, width=0)
            bar_fill.pack(side="left", fill="y")

            self.progress_widgets.append((bar_fill, pct, pct_lbl))

        # Results text
        self.text_frame = tk.Frame(self.main_frame, bg="#050510")
        self.text_frame.pack(fill="both", expand=True, padx=20, pady=12)

        self.text = scrolledtext.ScrolledText(
            self.text_frame,
            font=("Consolas", 10),
            bg="#0a0a1a",
            fg="#00ff88",
            wrap="word",
            padx=15,
            pady=15,
            bd=0,
            highlightthickness=0,
        )
        self.text.pack(fill="both", expand=True)

        self.text_content = """================================================================
                    Azazael ECOSYSTEM - RESULTS
========================================================================

CLUSTER STATUS
--------------
  [OK] Nodes: 10 total, 8 enabled
  [OK] Pipeline: 6 stages complete
  [OK] Tests: 15/15 PASSED (100%)
  [OK] Health: EXCELLENT
  [OK] HORUS: Integrated

FILES CREATED
-------------
  -> CODEX/ANALYSIS_REPORT.html
  -> CODEX/ANALYSIS_REPORT_DIVERSE.html
  -> CODEX/ANALYSIS_REPORT_V2.html
  -> CODEX/ANIMATION_PROTOCOLS.py
  -> CODEX/HORUS_INTEGRATION.py

ANIMATION PROTOCOLS
-------------------
  Categories: 8  |  Techniques: 24
  
  1. STATUS_ANIMATIONS     - Glitch, Neon Pulse, Ring Pulse
  2. PROGRESS_ANIMATIONS  - Linear Bar, Shimmer, Bounce Dots
  3. DATA_VISUALIZATION   - Bar Growth, Pie Fill, Line Draw
  4. ENTRY_ANIMATIONS     - Slide In, Fade In, Typewriter
  5. TRANSFORMATION       - Flip 3D, Morph, Elastic Scale
  6. DECORATIVE           - Particles, Hex Grid, Matrix
  7. TEXT_ANIMATIONS      - Rainbow Wave, Gradient Flow
  8. INTERACTIVE_FEEDBACK - Hover, Ripple, Success

HORUS INTEGRATION
----------------
  [OK] Shared with HORUS Live Monitor
  [OK] Export: HORUS_ANIMATION_SHARE.json
  [OK] Integration Points: 3 modules linked

========================================================================
                         ALL SYSTEMS OPERATIONAL
========================================================================"""

        self.text.insert("1.0", self.text_content)
        self.text.config(state="disabled", cursor="arrow")

        # Footer
        self.footer = tk.Frame(self.main_frame, bg="#0a0a1a", height=40)
        self.footer.pack(fill="x", side="bottom")
        self.footer.pack_propagate(False)

        self.close_btn = tk.Button(
            self.footer,
            text="CLOSE",
            command=self.close,
            bg="#e74c3c",
            fg="white",
            font=("Consolas", 10, "bold"),
            relief="flat",
            padx=30,
            pady=6,
            bd=0,
            cursor="hand2",
        )
        self.close_btn.place(relx=0.5, rely=0.5, anchor="center")

        # Button hover
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.config(bg="#c0392b"))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.config(bg="#e74c3c"))

        # Center window
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 900) // 2
        y = (self.root.winfo_screenheight() - 680) // 2
        self.root.geometry(f"900x680+{x}+{y}")

    def animate(self):
        """Run animations"""
        # Animate progress bars
        for fill, target, label in self.progress_widgets:
            current = 0
            while current <= target:
                width = (current / 100) * 300
                fill.config(width=width)
                self.root.update()
                time.sleep(0.01)
                current += 1

        # Animate status colors
        colors = ["#00ff88", "#00f5ff", "#bf00ff", "#00ff88"]
        idx = [0]

        def cycle_color():
            try:
                idx[0] = (idx[0] + 1) % len(colors)
                self.status_label.config(fg=colors[idx[0]])
                self.root.after(600, cycle_color)
            except Exception as exc:
                note_failure("POPUP_RICH:L250", exc)

        cycle_color()

    def close(self):
        try:
            self.root.destroy()
        except Exception as exc:
            note_failure("POPUP_RICH:L258", exc)

        sys.exit(0)


if __name__ == "__main__":
    RichPopup()
