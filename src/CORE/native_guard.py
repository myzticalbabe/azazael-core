# coding: utf-8
"""native_guard — decide whether a native extension can be imported SAFELY.

Why this module exists
----------------------
A native wheel built against the GIL ABI (CPython without ``Py_GIL_DISABLED``)
does not raise ``ImportError`` when loaded by a free-threaded interpreter. It
faults inside the C loader -- on Windows an access violation, which is a
*segfault*: the process dies and no ``try/except`` ever runs. ``onnxruntime``,
``bcrypt`` and ``faiss`` each did exactly this on the 3.14t build (see
``src/eve_unleashed/core.py`` and ``src/SCRIPTS/request_auth.py``, which grew
their own copies of this guard before it was factored out here).

So "is this module importable?" must be answered by a CHILD interpreter, once,
and cached -- never by attempting the import in-process.

Public API::

    from CORE.native_guard import GIL_DISABLED, has_module, import_ok

    if import_ok("onnxruntime"):
        import onnxruntime as ort
    else:
        ...fallback...

``import_ok`` always probes in a child (the fault is a property of THIS runtime,
not only of free-threaded ones), and ``GIL_DISABLED`` is a cheap pre-check for
callers that only care about the free-threading hazard.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import sysconfig
from typing import Dict, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: True when running a free-threaded (no-GIL) build. Cheap, no subprocess.
GIL_DISABLED: bool = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))

_IMPORT_OK: Dict[str, bool] = {}

# The probe imports the module and exits 0 on success. A native fault aborts the
# child (non-zero / raised signal) instead of returning, which is the signal.
_CHILD = (
    "import importlib, sys\n"
    "try:\n"
    "    importlib.import_module(sys.argv[1])\n"
    "except BaseException:\n"
    "    sys.exit(3)\n"
)


def has_module(name: str) -> bool:
    """True if ``name`` is *findable* without executing it.

    ``find_spec`` locates an extension without loading it, so it is safe to call
    in-process even for a GIL-ABI wheel.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except Exception as exc:
        note_failure("native_guard:L63", exc)
        return False


def import_ok(name: str, timeout: float = 60.0) -> bool:
    """Is ``name`` safe to ``import`` in THIS interpreter? (cached)

    Answered in a child process because a GIL-ABI wheel faults at C level here
    rather than raising, and a fault cannot be caught. Returns ``False`` when
    the child dies, times out, or the module is absent.
    """
    cached = _IMPORT_OK.get(name)
    if cached is not None:
        return cached
    if not has_module(name):
        _IMPORT_OK[name] = False
        return False
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD, name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        ok = proc.returncode == 0
    except Exception as exc:
        note_failure("native_guard:L88", exc)
        ok = False
    _IMPORT_OK[name] = ok
    return ok
