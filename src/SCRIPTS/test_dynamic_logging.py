# coding: utf-8
"""
Test Dynamic Logging System
"""
import sys
sys.path.insert(0, str(__file__.rsplit('UTILITIES', 1)[0] + 'UTILITIES'))

from AzazaelDynamicLogger import info, warn, error, success, section, logger
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

section("DYNAMIC LOGGING TEST")

info("Testing info message")
warn("Testing warning message")
error("Testing error message")
success("Testing success message")

info("Checking log history persistence...")
history = logger.get_history()
print(f"\nHistory count: {len(history)} messages")

print("\nTest complete. Check logs/ directory for persistent output.")
