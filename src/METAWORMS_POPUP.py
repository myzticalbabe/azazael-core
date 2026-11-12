# coding: utf-8
"""
METAWORMS POPUP LAUNCHER
Simple tkinter popup for METAWORMS management
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
from pathlib import Path
import subprocess
import sys
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
    from CORE.paths import get_desktop_dir
except ImportError as exc:
    note_failure("METAWORMS_POPUP:L17", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from CORE.paths import get_desktop_dir


def run_metaworms_popup():
    """Run METAWORMS with full popup GUI."""

    root = tk.Tk()
    root.title("METAWORMS - Meta Worm Management")
    root.geometry("900x700")
    root.configure(bg="#1a1a2e")

    def log(msg):
        text_area.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    # Header
    header = tk.Frame(root, bg="#16213e", pady=10)
    header.pack(fill="x")

    tk.Label(
        header,
        text="METAWORMS",
        font=("Consolas", 28, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    status_label = tk.Label(
        header,
        text="Initializing...",
        font=("Consolas", 12),
        bg="#16213e",
        fg="#7c3aed",
    )
    status_label.pack()

    # Progress bar
    progress = ttk.Progressbar(root, mode="determinate", length=850)
    progress.pack(pady=10, padx=25)

    # Text area
    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=35
    )
    text_area.pack(fill="both", expand=True, padx=10, pady=10)

    log("=" * 60)
    log("METAWORMS - Meta Worm Management Popup")
    log("=" * 60)
    log("")

    def execute_worms():
        status_label.config(text="Running all meta worms...")
        progress["value"] = 0
        root.update_idletasks()

        # Run METAWORMS
        cmd = [sys.executable, str(get_desktop_dir() / "METAWORMS" / "METAWORMS.py"), "--all"]

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1
        )

        worms = ["METAZ", "DEPENDZ", "VALIDZ", "DEADZ", "METRICZ", "LOGZ", "CHANGEZ"]
        current_worm = 0

        # Created with stdout=subprocess.PIPE, so the pipe always exists; this
        # explicit guard is the checker-facing narrowing (WORMZ_POPUP pattern).
        stdout = process.stdout
        if stdout is None:
            raise RuntimeError("METAWORMS subprocess stdout pipe was not created")

        for line in iter(stdout.readline, ""):
            line = line.strip()
            if line:
                log(line)
                if any(w in line for w in worms):
                    if "] Running..." in line:
                        current_worm = worms.index(
                            line.split("]")[0].split("[")[1].strip()
                        )
                    progress["value"] = ((current_worm + 1) / len(worms)) * 100

        process.wait(timeout=1800)

        log("")
        log("=" * 60)
        log("ALL WORMS COMPLETE - Popup will close in 5 seconds")
        log("=" * 60)

        status_label.config(text="Complete!")
        progress["value"] = 100

        root.after(5000, root.destroy)

    log("Starting METAWORMS...")
    root.after(1000, execute_worms)
    root.mainloop()


if __name__ == "__main__":
    run_metaworms_popup()
