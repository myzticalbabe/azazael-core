import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))

#!/usr/bin/env python3
"""
Unit Test Configuration and Conftest
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

# Add ecosystem paths (dynamic — uses user's actual Desktop location)
_src_test_dir = Path(__file__).resolve().parent.parent.parent  # CODEX/TESTING/ -> CODEX/ -> src/
if str(_src_test_dir) not in sys.path:
    sys.path.insert(0, str(_src_test_dir))

ECOSYSTEM_ROOT = Path.home() / "Desktop"
sys.path.insert(0, str(ECOSYSTEM_ROOT / "Azazael"))
sys.path.insert(0, str(ECOSYSTEM_ROOT / "HYDRA"))
sys.path.insert(0, str(ECOSYSTEM_ROOT / "WORMZ"))
sys.path.insert(0, str(ECOSYSTEM_ROOT / "METAWORMS"))
sys.path.insert(0, str(ECOSYSTEM_ROOT / "HORUS"))
sys.path.insert(0, str(ECOSYSTEM_ROOT / "TERENCE"))
sys.path.insert(0, str(ECOSYSTEM_ROOT / "KLINK"))


def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line("markers", "unit: mark as unit test")
    config.addinivalue_line("markers", "integration: mark as integration test")
    config.addinivalue_line("markers", "performance: mark as performance test")
