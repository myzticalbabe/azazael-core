# coding: utf-8
"""
Azazael Bootstrapper v2.0 - With Dynamic Logging
"""
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

ROOT = Path(__file__).parent
HYDRA_ML = ROOT / "HYDRA" / "ml_platforms"

if str(HYDRA_ML) not in sys.path:
    sys.path.insert(0, str(HYDRA_ML))

sys.path.insert(0, str(ROOT / "UTILITIES"))

try:
    from AzazaelDynamicLogger import info, success, section
    section("AZAZAEL BOOTSTRAP")
    info(f"Root: {ROOT}")
    info(f"ML Platform: {HYDRA_ML}")
    success("Dynamic logging initialized")
    print("Bootstrap complete. Dynamic logging active.")
except Exception as e:
    print(f"Bootstrap warning: {e}")
