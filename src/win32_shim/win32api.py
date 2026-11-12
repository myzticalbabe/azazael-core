"""win32api -- the four functions the MCP SDK calls, without pywin32.

MEASURED SURFACE, NOT A GUESS
Exactly four functions are called by ``mcp/os/win32/utilities.py`` (the only
file in the SDK that touches pywin32), plus three constants:

    win32api.STD_INPUT_HANDLE / STD_OUTPUT_HANDLE / STD_ERROR_HANDLE
    win32api.SetStdHandle(std_id, os_handle)
    win32api.OpenProcess(access, inherit, pid)
    win32api.CloseHandle(handle)

Everything here goes through ``ctypes`` against ``kernel32``. That is not a
workaround dressed up as a library -- these are the same Win32 entry points
pywin32 itself calls; pywin32 is a binding, and ``ctypes`` is another binding
to the same DLL. The difference is that ctypes needs no compiled extension, so
it works on an interpreter for which no pywin32 wheel will ever be built.

WHY THIS MATTERS HERE
pywin32 publishes no free-threaded wheels at all (measured across the full
published history: GIL ABIs cp39-cp315 only, zero t-suffixed artifacts).
The MCP SDK hard-depends on it on Windows, so a free-threaded interpreter
cannot resolve the dependency and cannot import the SDK. This module exists so
that the dependency can be satisfied by pure stdlib on ANY interpreter.

WHY THE ERRORS ARE pywintypes.error
The SDK catches ``pywintypes.error`` around these calls. Raising anything else
would escape the SDK's own handler and break its graceful degradation, so the
failure type is part of the contract, not an implementation detail.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from pywintypes import error

__all__ = [
    "STD_INPUT_HANDLE", "STD_OUTPUT_HANDLE", "STD_ERROR_HANDLE",
    "SetStdHandle", "OpenProcess", "CloseHandle", "GetLastError",
]

#: Standard-handle slots for fd 0/1/2.
#:
#: These are the Win32 values from ``winnt.h``, where each is a NEGATIVE
#: sentinel: ``(DWORD)-10``, ``(DWORD)-11`` and ``(DWORD)-12``. They are NOT
#: the file descriptor numbers 0/1/2 -- an earlier draft of this shim used
#: those, which would have pointed SetStdHandle at the wrong slots (0/1/2 are
#: not valid standard-handle identifiers at all).
#:
#: Measured against real pywin32: the values are -10, -11 and -12, and they
#: are SIGNED Python ints (not the unsigned 0xFFFFFFF6 form). pywin32 hands
#: back the signed value, so the shim does too -- a comparison against the
#: real package is then exact rather than merely equivalent-after-masking.
#: SetStdHandle declares its first argument as DWORD, so ctypes converts the
#: negative int to the same 0xFFFFFFF6 the header specifies.
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12

_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_SetStdHandle = _kernel32.SetStdHandle
_SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
_SetStdHandle.restype = wintypes.HANDLE

_OpenProcess = _kernel32.OpenProcess
_OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_OpenProcess.restype = wintypes.HANDLE

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL


def GetLastError():
    """The last Win32 error code for this thread."""
    return ctypes.get_last_error()


def SetStdHandle(std_id, os_handle):
    """Point a Win32 standard-handle slot at an OS handle.

    ``os_handle`` is a raw Win32 HANDLE (what ``msvcrt.get_osfhandle``
    returns), NOT a CRT file descriptor.

    Returns None, which is what real pywin32 returns -- measured, because the
    obvious implementation (returning the PREVIOUS handle, as the Win32 API
    itself does) is observably different. The MCP SDK discards the return
    value, so this only matters to anyone comparing the two implementations
    directly; a drop-in that returns a handle where the original returns None
    is a difference someone will trip over.
    """
    previous = _SetStdHandle(std_id, os_handle)
    if previous == _INVALID_HANDLE_VALUE:
        raise error(ctypes.get_last_error(), "SetStdHandle",
                    "SetStdHandle failed")
    return None


def OpenProcess(desired_access, inherit_handle, process_id):
    """Open a process by pid. Returns a handle; 0 means failure.

    pywin32 raises on failure here, and the SDK relies on that: it calls
    ``OpenProcess`` inside a ``try`` and turns a failure into "this process
    cannot be put in a job", not into a crash.
    """
    handle = _OpenProcess(desired_access, inherit_handle, process_id)
    if not handle:
        raise error(ctypes.get_last_error(), "OpenProcess",
                    "OpenProcess failed")
    return handle


def CloseHandle(handle):
    """Close a Win32 handle. Returns True on success.

    pywin32 raises here too. The SDK calls it in a ``finally:`` after
    ``AssignProcessToJobObject``, so raising on a failed close would mask the
    original exception -- but pywin32's behaviour IS to raise, and matching it
    is what makes this a drop-in.
    """
    ok = _CloseHandle(handle)
    if not ok:
        raise error(ctypes.get_last_error(), "CloseHandle",
                    "CloseHandle failed")
    return True