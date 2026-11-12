"""win32_shim -- pywin32's Windows surface, rebuilt on ctypes.

WHAT THIS IS
A pure-stdlib implementation of the exact slice of pywin32 that the MCP
Python SDK imports, so that the SDK can be INSTALLED and IMPORTED on a
free-threaded interpreter where no pywin32 wheel can ever exist.

THE BLOCKER, MEASURED
pywin32 publishes no free-threaded wheels. Across its ENTIRE published
version history every wheel carries a GIL ABI tag (cp39, cp310 ... cp315) and
not one artifact carries a free-threaded (``t``) tag. The MCP SDK declares
``pywin32>=311; sys_platform == "win32"`` as a hard dependency, so on
3.14t/3.15t/3.16t:

  * ``pip install mcp`` cannot RESOLVE the dependency, and
  * ``import mcp`` fails before any application code runs, because
    ``mcp/__init__.py`` eagerly imports ``mcp.client.stdio``, which reaches
    ``mcp/os/win32/utilities.py``, which imports pywin32 at module level.

The SDK's use of pywin32 is narrow: Job Objects (so a stdio client's process
tree dies with it) and one standard-handle fixup. Those are Win32 API calls,
so they can be made with ``ctypes`` -- no compiled extension, no wheel, any
interpreter.

THE SURFACE IS MEASURED, NOT GUESSED
Exactly one file in the SDK touches pywin32:
``mcp/os/win32/utilities.py``. It uses four functions, two constants, one
exception type, and one dict-shaped information structure. Every one of them
is implemented here, with the semantics the SDK depends on -- including the
NESTED-DICT shape of the Job Object information, because the SDK mutates
``info["BasicLimitInformation"]["LimitFlags"] |= ...`` and a ctypes struct
would raise TypeError there.

HOW TO USE IT
Put this directory on ``sys.path`` (it IS the import path -- the modules are
named ``pywintypes``, ``win32api``, ``win32con`` and ``win32job`` so the
SDK's own imports resolve unchanged) and it acts as the pywin32 provider.

    import sys; sys.path.insert(0, ".../src/win32_shim")
    import mcp            # works, with no pywin32 installed

VERIFY IT
``tools/verify_win32_shim.py`` compares this shim against the REAL pywin32
wherever real pywin32 exists, so the claim "drop-in" is measured rather than
asserted.
"""

from __future__ import annotations

__all__ = ["PROVIDES", "VERSION", "available", "why"]

VERSION = "1.0.0"

#: The pywin32 module names this shim provides.
PROVIDES = ("pywintypes", "win32api", "win32con", "win32job")

#: Failures this package absorbed, newest last. A probe that cannot answer
#: must still say why.
FAILURES: list[str] = []


def available() -> bool:
    """Can this shim actually provide the surface? Probed, not assumed.

    Importing the modules is the only honest test: a ctypes binding that
    cannot find kernel32 would import fine and fail at the first call.

    The failure is RECORDED, not swallowed. A probe that answers False
    without saying why is indistinguishable from a probe that was never run,
    and the whole point of this package is to be able to say precisely where
    the Win32 surface stands.
    """
    try:
        import importlib

        for name in PROVIDES:
            importlib.import_module(name)
        return True
    except Exception as exc:
        FAILURES.append("available(): %s: %s" % (type(exc).__name__, exc))
        return False


def why() -> str:
    """One line an operator can quote: what this provides and for whom."""
    return ("win32_shim %s provides %s on ctypes -- lets the MCP SDK import "
            "on free-threaded interpreters where pywin32 has no wheel"
            % (VERSION, ", ".join(PROVIDES)))