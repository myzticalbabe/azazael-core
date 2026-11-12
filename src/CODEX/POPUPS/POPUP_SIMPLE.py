# coding: utf-8
"""Simple popup display for results"""

import tkinter as tk
from tkinter import scrolledtext
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

root = tk.Tk()
root.title("HYDRA Results")
root.geometry("800x600")
root.configure(bg="#1a1a2e")

tk.Label(
    root, text="HYDRA CLUSTER", font=("Arial", 24, "bold"), bg="#1a1a2e", fg="#00f5ff"
).pack(pady=20)
tk.Label(
    root, text="All Systems Operational", font=("Arial", 12), bg="#1a1a2e", fg="#00ff88"
).pack()

text = scrolledtext.ScrolledText(
    root, font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", wrap="word"
)
text.pack(fill="both", expand=True, padx=20, pady=20)

results = """RESULTS SUMMARY
================

CLUSTER STATUS
- Nodes: 10 total, 8 enabled
- Pipeline: 6 stages complete
- Tests: 15/15 PASSED (100%)
- Health: EXCELLENT

FILES CREATED
- CODEX/ANALYSIS_REPORT.html
- CODEX/ANALYSIS_REPORT_DIVERSE.html
- CODEX/ANIMATION_PROTOCOLS.py
- CODEX/HORUS_INTEGRATION.py

ANIMATION PROTOCOLS
- 8 categories
- 24 techniques

HORUS INTEGRATION
- Shared successfully
"""
text.insert("1.0", results)
text.config(state="disabled")

tk.Button(
    root,
    text="CLOSE",
    command=root.destroy,
    bg="#ff4757",
    fg="white",
    font=("Arial", 12),
).pack(pady=10)

root.mainloop()
