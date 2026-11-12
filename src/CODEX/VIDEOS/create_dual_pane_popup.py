"""
Dual-Pane Popup Display for Azazael Cluster
Shows animated dual-pane comparison in a popup window

Usage:
    python create_dual_pane_popup.py
"""

import tkinter as tk
import json
import time
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class _CodePanel(tk.Frame):
    """Panel carrying its code label (declared for the type checker)."""

    code_label: tk.Label


class DualPanePopup:
    """Popup window with dual-pane animated comparison."""

    stat_conflicts_label: tk.Label
    stat_files_label: tk.Label
    stat_time_label: tk.Label
    stat_score_label: tk.Label

    def __init__(self, root=None):
        self.root = root or tk.Tk()
        self.root.title("Azazael Cluster Protocol Comparison")
        self.root.geometry("1200x700")
        self.root.configure(bg="#0a0a1a")

        self.current_scene = 0
        self.progress = 0
        self.running = False
        self.animation_id = None

        self.scenes = self.load_scenes()

        self.create_widgets()
        self.start_animation()

    def load_scenes(self):
        """Load scene data."""
        try:
            with open("frame_sequence.json", "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            note_failure("create_dual_pane_popup:L38", exc)
            data = [
                {
                    "name": "CONFLICTS_DETECTED",
                    "left_code": [
                        ("import", "keyword"),
                        (" subprocess", "variable"),
                        ("", None),
                        ("path = ", "keyword"),
                        ('"/old/path"', "string"),
                        ("", None),
                        ("# ERROR: Not found", "error"),
                    ],
                    "right_code": [
                        ("from hydra_protocols", "keyword"),
                        (" import HydraProtocols", "variable"),
                        ("", None),
                        ("protocols = HydraProtocols()", "success"),
                        ("# Path resolved [OK]", "success"),
                        ("", None),
                        ("# Conflicts fixed", "success"),
                    ],
                    "stats": {
                        "conflicts": 24,
                        "files": 12,
                        "time": "1.2s",
                        "score": "98.5%",
                    },
                },
                {
                    "name": "RESOLUTION",
                    "left_code": [
                        ("conflicts = 252", "error"),
                        ("# CRITICAL: 155", "error"),
                        ("for file in files:", "keyword"),
                        ("    analyze(file)", "variable"),
                        ("# ERROR: Timeout", "error"),
                    ],
                    "right_code": [
                        ("conflicts = 0", "success"),
                        ("# All resolved [OK]", "success"),
                        ("for file in files:", "keyword"),
                        ("    resolve(file)", "variable"),
                        ("# Complete [OK]", "success"),
                    ],
                    "stats": {
                        "conflicts": 252,
                        "files": 45,
                        "time": "2.3s",
                        "score": "100%",
                    },
                },
                {
                    "name": "COMPLETE",
                    "left_code": [
                        ('health = "critical"', "error"),
                        ("score = 45", "error"),
                        ("# Needs fix", "error"),
                    ],
                    "right_code": [
                        ('health = "optimal"', "success"),
                        ("score = 98.5", "success"),
                        ("# Fixed [OK]", "success"),
                    ],
                    "stats": {
                        "conflicts": 0,
                        "files": 0,
                        "time": "0.5s",
                        "score": "100%",
                    },
                },
            ]
        return data

    def create_widgets(self):
        """Create GUI widgets."""

        header_frame = tk.Frame(self.root, bg="#0a0a1a")
        header_frame.pack(fill="x", padx=20, pady=15)

        title_label = tk.Label(
            header_frame,
            text="Azazael Cluster Protocol Comparison",
            font=("Courier New", 20, "bold"),
            fg="#00d5ff",
            bg="#0a0a1a",
        )
        title_label.pack()

        subtitle_label = tk.Label(
            header_frame,
            text="Automated Resolution Engine v2.0",
            font=("Courier New", 10),
            fg="#808080",
            bg="#0a0a1a",
        )
        subtitle_label.pack()

        scene_label = tk.Label(
            header_frame,
            text="SCENE: 1/3",
            font=("Courier New", 12),
            fg="#00d5ff",
            bg="#0a0a1a",
        )
        scene_label.pack(pady=5)

        main_frame = tk.Frame(self.root, bg="#0a0a1a")
        main_frame.pack(expand=True, fill="both", padx=20, pady=10)

        self.left_panel = self.create_panel(
            main_frame, "BEFORE", "#ff4757", "* ISSUES DETECTED"
        )
        self.left_panel.pack(side="left", expand=True, fill="both", padx=(0, 10))

        divider_frame = tk.Frame(main_frame, bg="#0a0a1a", width=20)
        divider_frame.pack(side="left")

        divider_line = tk.Frame(divider_frame, bg="#00d5ff", width=3)
        divider_line.pack(expand=True, fill="y")

        self.right_panel = self.create_panel(
            main_frame, "AFTER", "#00ff88", "* AUTO-FIXED"
        )
        self.right_panel.pack(side="right", expand=True, fill="both", padx=(10, 0))

        self.stats_frame = tk.Frame(self.root, bg="#0a0a1a", height=80)
        self.stats_frame.pack(fill="x", padx=20, pady=10)

        self.create_stats()

        progress_frame = tk.Frame(self.root, bg="#1a1a2e", height=10)
        progress_frame.pack(fill="x", padx=20, pady=(0, 20))

        self.progress_bar = tk.Frame(progress_frame, bg="#00d5ff", width=0, height=10)
        self.progress_bar.pack(side="left", fill="y")

        control_frame = tk.Frame(self.root, bg="#0a0a1a")
        control_frame.pack(fill="x", padx=20, pady=10)

        btn_style = {"font": ("Courier New", 10), "bd": 0, "cursor": "hand2"}

        play_btn = tk.Button(
            control_frame,
            text="[>] Play",
            command=self.toggle_animation,
            **{**btn_style, "bg": "#00d5ff", "fg": "#0a0a1a"},
        )
        play_btn.pack(side="left", padx=5)

        reset_btn = tk.Button(
            control_frame,
            text="[R] Reset",
            command=self.reset_animation,
            **{
                **btn_style,
                "bg": "#0a0a1a",
                "fg": "#00d5ff",
                "bd": 2,
                "relief": "raised",
            },
        )
        reset_btn.pack(side="left", padx=5)

        self.status_label = tk.Label(
            control_frame,
            text="Ready",
            font=("Courier New", 10),
            fg="#00ff88",
            bg="#0a0a1a",
        )
        self.status_label.pack(side="right", padx=20)

    def create_panel(self, parent, title, title_color, status_text) -> "_CodePanel":
        """Create a panel with header, code area, and stats."""
        panel = _CodePanel(parent, bg="#101020", bd=1, relief="solid")
        panel.config(highlightbackground=title_color, highlightthickness=1)

        header = tk.Frame(panel, bg="#151525")
        header.pack(fill="x")

        title_label = tk.Label(
            header,
            text=title,
            font=("Courier New", 12, "bold"),
            fg=title_color,
            bg="#151525",
        )
        title_label.pack(side="left", padx=10, pady=8)

        status_label = tk.Label(
            header,
            text=status_text,
            font=("Courier New", 8),
            fg=title_color,
            bg="#151525",
        )
        status_label.pack(side="right", padx=10, pady=8)

        code_frame = tk.Frame(panel, bg="#101020", padx=10, pady=10)
        code_frame.pack(expand=True, fill="both")

        code_label = tk.Label(
            code_frame,
            text="",
            font=("Courier New", 11),
            fg="#e0e0e0",
            bg="#101020",
            justify="left",
            anchor="nw",
        )
        code_label.pack(expand=True, fill="both")

        panel.code_label = code_label
        return panel

    def create_stats(self):
        """Create statistics display."""
        stats_config = [
            ("conflicts", "Conflicts", "#a500ff"),
            ("files", "Files", "#a500ff"),
            ("time", "Time", "#a500ff"),
            ("score", "Score", "#00ff88"),
        ]

        for key, label_text, color in stats_config:
            frame = tk.Frame(self.stats_frame, bg="#0a0a1a")
            frame.pack(side="left", expand=True, fill="both", padx=5)

            value_label = tk.Label(
                frame,
                text="0",
                font=("Courier New", 24, "bold"),
                fg=color,
                bg="#0a0a1a",
            )
            value_label.pack(pady=(10, 0))

            label = tk.Label(
                frame,
                text=label_text,
                font=("Courier New", 8),
                fg="#808080",
                bg="#0a0a1a",
            )
            label.pack()

            setattr(self, f"stat_{key}_label", value_label)

    def update_scene(self):
        """Update display for current scene."""
        scene = self.scenes[self.current_scene]

        scene_label = self.root.nametowidget("").winfo_children()[0]
        for child in scene_label.winfo_children():
            if isinstance(child, tk.Label) and "SCENE" in child.cget("text"):
                child.config(text=f"SCENE: {self.current_scene + 1}/3")
                break

        left_text = "\n".join([t[0] for t in scene["left_code"]])
        right_text = "\n".join([t[0] for t in scene["right_code"]])

        self.left_panel.code_label.config(text=left_text)
        self.right_panel.code_label.config(text=right_text)

        stats = scene["stats"]
        self.animate_value(
            "conflicts", int(self.stat_conflicts_label.cget("text")), stats["conflicts"]
        )
        self.animate_value(
            "files", int(self.stat_files_label.cget("text")), stats["files"]
        )
        self.stat_time_label.config(text=stats["time"])
        self.stat_score_label.config(text=stats["score"])

    def animate_value(self, stat_name, start, end):
        """Animate numeric value change."""
        value_label = getattr(self, f"stat_{stat_name}_label")
        duration = 500
        start_time = time.time()

        def update():
            elapsed = time.time() - start_time
            progress = min(elapsed / (duration / 1000), 1)
            current = int(start + (end - start) * progress)
            value_label.config(text=str(current))

            if progress < 1:
                self.root.after(16, update)

        update()

    def toggle_animation(self):
        """Toggle animation on/off."""
        self.running = not self.running

        play_btn = None
        for child in self.root.winfo_children():
            if isinstance(child, tk.Frame):
                for btn in child.winfo_children():
                    if isinstance(btn, tk.Button) and "[>]" in btn.cget("text"):
                        play_btn = btn
                        break

        if play_btn:
            if self.running:
                play_btn.config(text="[||] Pause")
                self.status_label.config(text="Playing...", fg="#00ff88")
            else:
                play_btn.config(text="[>] Play")
                self.status_label.config(text="Paused", fg="#ffa500")

    def reset_animation(self):
        """Reset animation to beginning."""
        self.current_scene = 0
        self.progress = 0
        self.running = False
        self.update_scene()

        play_btn = None
        for child in self.root.winfo_children():
            if isinstance(child, tk.Frame):
                for btn in child.winfo_children():
                    if isinstance(btn, tk.Button) and "▶" in btn.cget("text"):
                        play_btn = btn
                        break

        if play_btn:
            play_btn.config(text="▶ Play")
        self.status_label.config(text="Ready", fg="#808080")
        self.progress_bar.config(width=0)

    def start_animation(self):
        """Start the animation loop."""
        self.update_scene()

        def animate():
            if self.running:
                self.progress += 1

                if self.progress >= 100:
                    self.current_scene = (self.current_scene + 1) % len(self.scenes)
                    self.progress = 0
                    self.update_scene()

                self.progress_bar.config(
                    width=int(self.progress / 100 * self.root.winfo_width())
                )

            self.animation_id = self.root.after(50, animate)

        animate()

    def run(self):
        """Run the popup."""
        self.root.mainloop()


def main():
    """Main entry point."""
    print("=" * 60)
    print("  Azazael Dual-Pane Popup Display")
    print("=" * 60)
    print("")
    print("Popup window opening...")
    print("Controls:")
    print("  [>] Play/Pause - Toggle animation")
    print("  [R] Reset - Return to start")
    print("")
    print("Close window to exit")
    print("=" * 60)

    app = DualPanePopup()
    app.run()


if __name__ == "__main__":
    main()
