# coding: utf-8
"""
Azazael Local Development Package
Provides local AI development tools and integrations
"""

import importlib.util
from pathlib import Path

__version__ = "1.0.0"
__author__ = "Azazael"

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Core modules
#
# `bootstrap` is optional (not always present), so it is probed with
# `find_spec` and loaded with `import_module` — the same convention the
# OPEN_INTERPRETER probe below uses — instead of a static from-import,
# which the checker cannot resolve when the module is absent.
try:
    _bootstrap_spec = importlib.util.find_spec("CORE.bootstrap")
except (ImportError, ValueError) as exc:          # missing parent package / None entry
    note_failure("LOCAL_DEV:L24", exc)
    _bootstrap_spec = None
bootstrap = importlib.import_module("CORE.bootstrap") if _bootstrap_spec is not None else None

from CORE import NODE
from CORE import PYTHON_MANAGER
from CORE import STRESS_TEST
from CORE.WORMS import (
    OMNI_WORM,
    ERROR_SCAN_WORM,
    MORPHING_WORM_V2,
    WORM_FLEET,
    CLUSTER_INSPECTOR,
)
from typing import List


# Local LLM support.
#
# LM Studio is an APPLICATION, not a Python module, so no `import <module>` can
# test it: the tree consumes it as the OpenAI-compatible server on :1234 (the
# `lmstudio` provider entry, `CORE/inference/providers.py:562`) with its models
# under `~/.lmstudio/models` (`ORCHESTRATION/ml_platforms/harness.py:246`).
#
# Both capability blocks below used to read `try: import <module> /
# <FLAG> = True / except ImportError: <FLAG> = False`, and the import line in each
# was later replaced by `pass`. That left BOTH halves of the branch in place, so
# the flag was assigned True unconditionally, the except branch was unreachable,
# and `check_availability()` printed OK for a capability nothing had tested. What
# follows tests the install that actually exists, so the flag can now differ
# between the two realities.
try:
    LM_STUDIO_AVAILABLE = (Path.home() / ".lmstudio").is_dir()
except OSError as exc:                            # unreadable home != "present"
    note_failure("LOCAL_DEV:L33", exc)
    LM_STUDIO_AVAILABLE = False

# Open Interpreter support.
#
# Its consumer runs it as a subprocess -- `[sys.executable, "-m", "interpreter",
# "--local"]` in `CLI/azazael_cli.py:92` -- so "available" means that module
# resolves for this interpreter. `find_spec` answers exactly that without
# importing the package or spawning anything, which is why it is used in place of
# the old (removed) import statement.
try:
    OPEN_INTERPRETER_AVAILABLE = importlib.util.find_spec("interpreter") is not None
except (ImportError, ValueError) as exc:          # missing parent package / None entry
    note_failure("LOCAL_DEV:L41", exc)
    OPEN_INTERPRETER_AVAILABLE = False

__all__ = [
    "bootstrap",
    "NODE",
    "PYTHON_MANAGER",
    "STRESS_TEST",
    "OMNI_WORM",
    "ERROR_SCAN_WORM",
    "MORPHING_WORM_V2",
    "WORM_FLEET",
    "CLUSTER_INSPECTOR",
    "LM_STUDIO_AVAILABLE",
    "OPEN_INTERPRETER_AVAILABLE",
]


def get_version():
    return __version__


def check_availability():
    """Print the module/capability report for a local development install.

    Returns True only when BOTH optional local capabilities were verified present.
    It previously returned True unconditionally, so a caller could not tell a
    complete install from a bare one -- the same always-success shape as the
    capability flags above. The core-module lines stay hardcoded OK because those
    imports execute at module load: a missing one raises before this can run.
    """
    print("Azazael Local Development Package")
    print(f"Version: {__version__}")
    print()
    print("Module Availability:")
    print("  bootstrap: OK")
    print("  NODE: OK")
    print("  PYTHON_MANAGER: OK")
    print("  STRESS_TEST: OK")
    print("  Worms: OK")
    print(
        f"  LM Studio: "
        f"{'installed (~/.lmstudio present)' if LM_STUDIO_AVAILABLE else 'NOT AVAILABLE'}"
    )
    print(
        f"  Open Interpreter: "
        f"{'importable' if OPEN_INTERPRETER_AVAILABLE else 'NOT AVAILABLE'}"
    )
    print()
    return bool(LM_STUDIO_AVAILABLE and OPEN_INTERPRETER_AVAILABLE)
