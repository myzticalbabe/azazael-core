# coding: utf-8
"""
ML Platform Configuration Module — Deprecated shim layer.
Re-exports from canonical ORCHESTRATION.ml_platforms.
"""

import sys
import warnings
from pathlib import Path

_src_dir = Path(__file__).resolve().parent.parent.parent  # -> azazael/src/
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

warnings.warn(
    "HYDRA.ml_platforms is deprecated. Use ORCHESTRATION.ml_platforms instead.",
    UserWarning,
    stacklevel=2,
)

from ORCHESTRATION.ml_platforms.config import (  # noqa: E402
    load_config,
    list_platforms,
    get_env_path,
)
from ORCHESTRATION.ml_platforms.diagnostics import (  # noqa: E402
    print_platform_status,
    list_workflows,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
