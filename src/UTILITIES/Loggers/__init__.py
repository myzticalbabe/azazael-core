# Import from canonical root AzazaelDynamicLogger
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
_root_src = str(Path(__file__).resolve().parent.parent.parent)  # src/
if _root_src not in sys.path:
    sys.path.insert(0, _root_src)

# Production import path for the canonical dynamic logger (see PMB orphan
# goals: Loggers wired into the production logging path; the UTILITIES-level
# compressed duplicate is gone, this re-export is the single package face).
from AzazaelDynamicLogger import DynamicLogger

__all__ = ["DynamicLogger"]

