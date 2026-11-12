"""win32con -- the Windows constants the MCP SDK uses, without pywin32.

MEASURED SURFACE
``mcp/os/win32/utilities.py`` reads exactly two constants from this module:

    win32con.PROCESS_SET_QUOTA    0x0100
    win32con.PROCESS_TERMINATE    0x0001

Both are the documented Win32 ``PROCESS_ACCESS_RIGHTS`` values from
``winnt.h``, and both are stable ABI constants -- they are part of the
Windows API, not of any binding.

WHY LITERALS AND NOT A LOOKUP
The values are fixed by the operating system and are not expected to change,
so writing them out is both correct and cheaper than a lookup. The SDK uses
them only to build the access mask passed to ``OpenProcess``.
"""

from __future__ import annotations

__all__ = [
    "PROCESS_TERMINATE",
    "PROCESS_SET_QUOTA",
    "PROCESS_QUERY_INFORMATION",
    "PROCESS_VM_READ",
    "PROCESS_ALL_ACCESS",
    "STILL_ACTIVE",
]

#: From winnt.h, PROCESS_ACCESS_RIGHTS.
PROCESS_TERMINATE = 0x0001
PROCESS_CREATE_THREAD = 0x0002
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_ALL_ACCESS = 0x1F0FFF

#: Exit code reported for a process that is still running.
STILL_ACTIVE = 259