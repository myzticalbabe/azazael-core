"""pywintypes -- the exception type, without pywin32.

WHAT THIS IS
A drop-in replacement for the one piece of ``pywintypes`` that the MCP SDK
uses. The SDK raises and catches ``pywintypes.error`` around every Win32 call
it makes, so a replacement that raises a DIFFERENT type would not be caught
by those handlers -- the failure would escape as an unexpected exception and
the SDK's graceful-degradation path would never run.

Measured 2026-11-11 against the real pywintypes:

    >>> e = pywintypes.error(5, "OpenProcess", "Access denied")
    >>> e.args
    (5, "OpenProcess", "Access denied")
    >>> e.winerror, e.funcname, e.strerror
    (5, "OpenProcess", "Access denied")

This module reproduces that shape exactly: the three-argument form, the
``.args`` tuple, and the three named attributes.

WHY A REPLACEMENT EXISTS AT ALL
pywin32 publishes NO free-threaded wheels -- not for 3.13t, 3.14t, 3.15t, and
certainly not 3.16t. Measured against the full published version history:
every wheel carries a GIL ABI tag (cp39..cp315) and there is not one
free-threaded artifact. The MCP SDK depends on pywin32 unconditionally on
Windows, so ``pip install mcp`` cannot even RESOLVE on a free-threaded
interpreter, and ``import mcp`` fails before any application code runs.

Reported upstream as modelcontextprotocol/python-sdk#2233 ("pywin32
installation fails on Windows - hard dep only needed for client stdio, but
pulled in for all users via eager import in __init__.py"), with the
free-threading angle confirmed independently in that thread.

The API surface this package implements is measured, not guessed: see
``win32api`` for the four functions the SDK calls, and ``tools/verify_win32_shim.py``
for the conformance check that runs against the REAL pywin32 when it is
present.
"""

from __future__ import annotations

__all__ = ["error", "WindowsError"]


class error(Exception):  # noqa: N801 - the name is pywin32's, lowercase by contract
    """A Win32 error, in pywin32's exact shape.

    Measured against the real pywintypes: the MRO is
    ``error -> Exception -> BaseException -> object`` -- it is NOT an OSError
    subclass -- and ``args`` is ``(winerror, funcname, strerror)``.

    An earlier draft of this shim subclassed ``OSError``, on the belief that
    the SDK's callers catch it that way. The real package does not, and a
    replacement that is a STRICTER type changes which handlers fire: an
    ``except OSError`` that would have caught the original would let this one
    through. Matching the original exactly is the whole point of a drop-in, so
    this subclasses ``Exception`` -- exactly as pywin32 does.
    """

    def __init__(self, winerror=0, funcname="", strerror=""):
        self.winerror = winerror
        self.funcname = funcname
        self.strerror = strerror
        super().__init__(winerror, funcname, strerror)

    def __str__(self):
        parts = []
        if self.funcname:
            parts.append(self.funcname)
        if self.strerror:
            parts.append(self.strerror)
        if self.winerror:
            parts.append("(%d)" % self.winerror)
        return ": ".join(parts) if parts else "Windows error"


#: pywin32 also exports this alias; some callers use it.
WindowsError = error
