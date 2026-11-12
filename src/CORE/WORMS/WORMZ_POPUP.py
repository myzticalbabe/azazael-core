# coding: utf-8
"""
WORMZ POPUP LAUNCHER
Simple tkinter popup for WORMZ BIBL crawler
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
import subprocess
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def run_wormz_popup():
    """Run WORMZ with full popup GUI."""

    root = tk.Tk()
    root.title("WORMZ - BIBL Crawler")
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
        text="WORMZ",
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
    log("WORMZ - BIBL Crawler Popup")
    log("=" * 60)
    log("")

    def execute_crawl():
        status_label.config(text="Running...")
        progress["value"] = 0
        root.update_idletasks()

        # Run WORMZ using dynamic path resolution
        worms_dir = Path(__file__).resolve().parent  # CORE/WORMS/
        wormz_script = worms_dir / "WORMZ.py"
        cmd = [sys.executable, str(wormz_script), "--all"]

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1
        )

        stdout = process.stdout
        if stdout is None:
            raise RuntimeError("WORMZ subprocess stdout pipe was not created")

        for line in iter(stdout.readline, ""):
            line = line.strip()
            if line:
                log(line)
                if "Crawl complete" in line or "COMPLETE" in line:
                    progress["value"] = 100

        process.wait(timeout=1800)

        log("")
        log("=" * 60)
        log("CRAWL FINISHED - Popup will close in 5 seconds")
        log("=" * 60)

        status_label.config(text="Complete!")
        root.after(5000, root.destroy)

    log("Starting WORMZ crawl...")
    root.after(1000, execute_crawl)
    root.mainloop()


if __name__ == "__main__":
    run_wormz_popup()


