# coding: utf-8
"""
Changelog

Module for ml_platforms functionality.

Functions:
    - log_change()
    - log_new_file()
    - log_deleted_file()
    - log_refactor()
    - log_fix()
    - log_info()
    - show_log()
    - clear_log()
    - initialize_log()

Usage:
    >>> import changelog
    >>> changelog.log_change()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
ML Platform Change Logger
Logs all changes to a simple, readable file on the Desktop.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LOG_FILE = Path(os.path.expanduser("~/Desktop")) / "ML_PLATFORM_CHANGES.txt"


def log_change(message: str, category: str = "general") -> None:
    """
    Log a change in natural language.

    Args:
        message: What happened (in plain English)
        category: Type of change - 'added', 'fixed', 'removed', 'updated', 'info'
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    symbols = {
        "added": "+++",
        "fixed": "***",
        "removed": "---",
        "updated": ">>>",
        "info": "---",
    }
    symbol = symbols.get(category, "---")

    log_entry = f"[{timestamp}] {symbol} {message}\n"

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)

    print(f"Logged: {message}")


def log_new_file(file_path: str, description: str = "") -> None:
    """Log that a new file was created."""
    msg = f"Created new file: {file_path}"
    if description:
        msg += f" - {description}"
    log_change(msg, "added")


def log_deleted_file(file_path: str, reason: str = "") -> None:
    """Log that a file was deleted."""
    msg = f"Deleted file: {file_path}"
    if reason:
        msg += f" - Reason: {reason}"
    log_change(msg, "removed")


def log_refactor(old_item: str, new_item: str) -> None:
    """Log a refactoring change."""
    msg = f"Refactored {old_item} into {new_item}"
    log_change(msg, "updated")


def log_fix(issue: str, solution: str) -> None:
    """Log a fix that was applied."""
    msg = f"Fixed: {issue} - Solution: {solution}"
    log_change(msg, "fixed")


def log_info(message: str) -> None:
    """Log general information."""
    log_change(message, "info")


def show_log() -> None:
    """Display the change log."""
    if LOG_FILE.exists():
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        print("\n" + "=" * 60)
        print("ML PLATFORM CHANGE LOG")
        print("=" * 60)
        print(content)
    else:
        print("No log file found.")


def clear_log() -> None:
    """Clear the log file."""
    if LOG_FILE.exists():
        os.remove(LOG_FILE)
        print("Log cleared.")


def initialize_log() -> None:
    """Initialize the log with the current structure."""
    log_info("=" * 60)
    log_info("ML PLATFORM SUITE - CHANGE LOG INITIALIZED")
    log_info("=" * 60)
    log_info("All changes will be logged here for easy tracking.")
    log_info("")
    log_info("Log file location: Desktop/ML_PLATFORM_CHANGES.txt")
    log_info("")
    log_info("Commands:")
    log_info("  from changelog import log_change, show_log")
    log_info("  show_log()  # View all changes")
    log_info("  log_change('message', 'category')  # Log new change")
