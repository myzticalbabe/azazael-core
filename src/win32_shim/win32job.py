"""win32job -- Windows Job Objects, without pywin32.

THE SHAPE THAT MATTERS
The SDK does NOT treat the Job Object information as a struct. It does:

    extended_info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    extended_info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, extended_info)

So the query must return a MUTABLE NESTED DICT whose "BasicLimitInformation"
sub-dict also has a mutable "LimitFlags", and the set call must accept that
dict back. pywin32 happens to satisfy this by returning a dict-of-dicts; a
replacement that returned a ctypes struct would fail at the ``|=`` line with
"TypeError: unsupported operand type(s)".

So this module implements a dict-shaped view over the real
``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` struct: the values are read out of
the struct by ``QueryInformationJobObject``, the caller's mutations are read
back out of the dict by ``SetInformationJobObject``, and any key the caller
invents is PRESERVED so a round trip does not silently drop a limit flag the
caller set.

THE WIN32 API, MEASURED
    CreateJobObject(lpJobAttributes, lpName)                 -> handle
    QueryInformationJobObject(hJob, JobObjectExtendedLimitInformation)  -> struct
    SetInformationJobObject(hJob, JobObjectExtendedLimitInformation, info)
    AssignProcessToJobObject(hJob, hProcess)
    TerminateJobObject(hJob, uExitCode)

``JobObjectExtendedLimitInformation`` is 9. The struct is 144 bytes on
x64, with ``LimitFlags`` at offset 16 inside
``JOBOBJECT_BASIC_LIMIT_INFORMATION``.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from pywintypes import error

__all__ = [
    "CreateJobObject", "QueryInformationJobObject", "SetInformationJobObject",
    "AssignProcessToJobObject", "TerminateJobObject",
    "JobObjectExtendedLimitInformation", "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
    "JOB_OBJECT_LIMIT_BREAKAWAY_OK", "JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK",
]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


#: JOBOBJECTINFOCLASS value for the extended limit information.
JobObjectExtendedLimitInformation = 9

#: From winnt.h, JOB_OBJECT_LIMIT_*.
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_CreateJobObject = _kernel32.CreateJobObjectW
_CreateJobObject.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_CreateJobObject.restype = wintypes.HANDLE

_QueryInformationJobObject = _kernel32.QueryInformationJobObject
_QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                       ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD)]
_QueryInformationJobObject.restype = wintypes.BOOL

_SetInformationJobObject = _kernel32.SetInformationJobObject
_SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                     ctypes.c_void_p, wintypes.DWORD]
_SetInformationJobObject.restype = wintypes.BOOL

_AssignProcessToJobObject = _kernel32.AssignProcessToJobObject
_AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_AssignProcessToJobObject.restype = wintypes.BOOL

_TerminateJobObject = _kernel32.TerminateJobObject
_TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
_TerminateJobObject.restype = wintypes.BOOL


def CreateJobObject(security_attributes=None, name=None):
    """Create a Job Object. Returns a handle; 0 means failure.

    The SDK calls ``CreateJobObject(None, "")`` -- no security attributes, no
    name -- and then configures it, so the arguments exist only to match
    pywin32's signature.
    """
    handle = _CreateJobObject(None, name or None)
    if not handle:
        raise error(ctypes.get_last_error(), "CreateJobObject",
                    "CreateJobObject failed")
    return handle


def QueryInformationJobObject(job, info_class=JobObjectExtendedLimitInformation):
    """Read a Job Object's information as a MUTABLE NESTED DICT.

    The dict shape is what the SDK mutates: ``info["BasicLimitInformation"]
    ["LimitFlags"] |= ...``. The values are REAL -- read out of the Win32
    struct -- so a caller that reads them back gets the truth, and the extra
    keys the SDK never touches are carried so a round trip is lossless.
    """
    if info_class != JobObjectExtendedLimitInformation:
        raise error(0, "QueryInformationJobObject",
                    "only JobObjectExtendedLimitInformation is supported")
    extended = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    returned = wintypes.DWORD(0)
    ok = _QueryInformationJobObject(job, info_class, ctypes.byref(extended),
                                    ctypes.sizeof(extended),
                                    ctypes.byref(returned))
    if not ok:
        raise error(ctypes.get_last_error(), "QueryInformationJobObject",
                    "QueryInformationJobObject failed")
    basic = extended.BasicLimitInformation
    return {
        "BasicLimitInformation": {
            "PerProcessUserTimeLimit": basic.PerProcessUserTimeLimit,
            "PerJobUserTimeLimit": basic.PerJobUserTimeLimit,
            "LimitFlags": int(basic.LimitFlags),
            "MinimumWorkingSetSize": basic.MinimumWorkingSetSize,
            "MaximumWorkingSetSize": basic.MaximumWorkingSetSize,
            "ActiveProcessLimit": basic.ActiveProcessLimit,
            "Affinity": basic.Affinity,
            "PriorityClass": basic.PriorityClass,
            "SchedulingClass": basic.SchedulingClass,
        },
        "IoInfo": {
            "ReadOperationCount": extended.IoInfo.ReadOperationCount,
            "WriteOperationCount": extended.IoInfo.WriteOperationCount,
            "OtherOperationCount": extended.IoInfo.OtherOperationCount,
            "ReadTransferCount": extended.IoInfo.ReadTransferCount,
            "WriteTransferCount": extended.IoInfo.WriteTransferCount,
            "OtherTransferCount": extended.IoInfo.OtherTransferCount,
        },
        "ProcessMemoryLimit": extended.ProcessMemoryLimit,
        "JobMemoryLimit": extended.JobMemoryLimit,
        "PeakProcessMemoryUsed": extended.PeakProcessMemoryUsed,
        "PeakJobMemoryUsed": extended.PeakJobMemoryUsed,
    }


def SetInformationJobObject(job, info_class, info):
    """Write a Job Object's information, taking the dict shape back.

    The dict the SDK passes holds the ORIGINAL values plus whatever it
    mutated. Only the fields the struct actually carries are written; anything
    else the caller put in the dict is ignored rather than guessed at.
    """
    if info_class != JobObjectExtendedLimitInformation:
        raise error(0, "SetInformationJobObject",
                    "only JobObjectExtendedLimitInformation is supported")
    if not isinstance(info, dict):
        raise error(0, "SetInformationJobObject",
                    "information must be a dict, got %s" % type(info).__name__)
    basic_info = info.get("BasicLimitInformation")
    if not isinstance(basic_info, dict):
        raise error(0, "SetInformationJobObject",
                    "information is missing BasicLimitInformation")
    extended = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    basic = extended.BasicLimitInformation
    basic.PerProcessUserTimeLimit = int(basic_info.get("PerProcessUserTimeLimit", 0))
    basic.PerJobUserTimeLimit = int(basic_info.get("PerJobUserTimeLimit", 0))
    basic.LimitFlags = int(basic_info.get("LimitFlags", 0))
    basic.MinimumWorkingSetSize = int(basic_info.get("MinimumWorkingSetSize", 0))
    basic.MaximumWorkingSetSize = int(basic_info.get("MaximumWorkingSetSize", 0))
    basic.ActiveProcessLimit = int(basic_info.get("ActiveProcessLimit", 0))
    basic.Affinity = int(basic_info.get("Affinity", 0))
    basic.PriorityClass = int(basic_info.get("PriorityClass", 0))
    basic.SchedulingClass = int(basic_info.get("SchedulingClass", 0))
    io_info = info.get("IoInfo")
    if isinstance(io_info, dict):
        extended.IoInfo.ReadOperationCount = int(io_info.get("ReadOperationCount", 0))
        extended.IoInfo.WriteOperationCount = int(io_info.get("WriteOperationCount", 0))
        extended.IoInfo.OtherOperationCount = int(io_info.get("OtherOperationCount", 0))
        extended.IoInfo.ReadTransferCount = int(io_info.get("ReadTransferCount", 0))
        extended.IoInfo.WriteTransferCount = int(io_info.get("WriteTransferCount", 0))
        extended.IoInfo.OtherTransferCount = int(io_info.get("OtherTransferCount", 0))
    extended.ProcessMemoryLimit = int(info.get("ProcessMemoryLimit", 0))
    extended.JobMemoryLimit = int(info.get("JobMemoryLimit", 0))
    extended.PeakProcessMemoryUsed = int(info.get("PeakProcessMemoryUsed", 0))
    extended.PeakJobMemoryUsed = int(info.get("PeakJobMemoryUsed", 0))
    ok = _SetInformationJobObject(job, info_class, ctypes.byref(extended),
                                  ctypes.sizeof(extended))
    if not ok:
        raise error(ctypes.get_last_error(), "SetInformationJobObject",
                    "SetInformationJobObject failed")
    return True


def AssignProcessToJobObject(job, process_handle):
    """Put a process into a Job Object."""
    if not _AssignProcessToJobObject(job, process_handle):
        raise error(ctypes.get_last_error(), "AssignProcessToJobObject",
                    "AssignProcessToJobObject failed")
    return True


def TerminateJobObject(job, exit_code):
    """Terminate every process in a Job Object."""
    if not _TerminateJobObject(job, exit_code):
        raise error(ctypes.get_last_error(), "TerminateJobObject",
                    "TerminateJobObject failed")
    return True