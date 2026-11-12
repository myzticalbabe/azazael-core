"""The Win32 shim must be a drop-in for the pywin32 it replaces.

Measured 2026-11-11: pywin32 publishes no free-threaded wheel for ANY version
-- every wheel it has ever shipped carries a GIL ABI tag (cp39..cp315) and
none carries a free-threaded one. The MCP Python SDK imports it at module
level on Windows, so on a free-threaded interpreter ``import mcp`` fails before
any application code runs.

The replacement lives in ``src/win32_shim`` and rebuilds the SDK's exact
surface on ctypes against kernel32. These tests pin the properties the SDK
depends on, because the failure mode is SILENT: a shim that imports but
returns the wrong SHAPE fails at the call site with a TypeError far from the
cause.

What is pinned, and why each one matters:

  * the CONSTANTS the SDK reads, because a wrong access mask means
    OpenProcess silently fails to open the process it was asked to open;
  * the EXCEPTION SHAPE of pywintypes.error, because the SDK catches
    ``pywintypes.error`` around every Win32 call -- a replacement raising a
    different type escapes the SDK's own handler and turns a graceful
    degradation into a crash;
  * the JOB OBJECT round trip, including the nested-dict shape the SDK
    mutates (``info["BasicLimitInformation"]["LimitFlags"] |= ...``) -- a
    ctypes struct would raise TypeError on that line, which is why the shim
    returns a dict;
  * that KILL_ON_JOB_CLOSE actually takes effect, because that flag is the
    WHOLE REASON the SDK uses Job Objects: it is what makes a stdio client's
    process tree die with it;
  * that the shim works with NO pywin32 installed, which is the property the
    whole exercise exists for.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
SHIM = _ROOT / "src" / "win32_shim"

if str(SHIM) not in sys.path:
    sys.path.insert(0, str(SHIM))

import pywintypes  # noqa: E402
import win32api  # noqa: E402
import win32con  # noqa: E402
import win32job  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="the Win32 API is Windows-only")


def test_shim_provides_the_module_names_the_sdk_imports():
    """The SDK imports these four NAMES; a differently-named module is useless."""
    for name in ("pywintypes", "win32api", "win32con", "win32job"):
        assert name in sys.modules, "%s was not importable by name" % name
        assert Path(sys.modules[name].__file__).parent == SHIM, \
            "%s did not resolve to the shim" % name


def test_constants_match_the_documented_win32_values():
    """A wrong access mask means OpenProcess opens nothing, silently."""
    # The standard-handle slots are NEGATIVE sentinels from winnt.h, NOT the
    # fd numbers 0/1/2. Measured against real pywin32: -10, -11, -12. An
    # earlier draft used 0/1/2 and the conformance verifier caught it -- those
    # are not valid standard-handle identifiers, so SetStdHandle would have
    # been pointed at the wrong slots.
    assert win32api.STD_INPUT_HANDLE == -10
    assert win32api.STD_OUTPUT_HANDLE == -11
    assert win32api.STD_ERROR_HANDLE == -12
    assert win32con.PROCESS_SET_QUOTA == 0x0100
    assert win32con.PROCESS_TERMINATE == 0x0001
    assert win32job.JobObjectExtendedLimitInformation == 9
    assert win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x00002000


def test_error_has_pywintypes_shape():
    """The SDK catches pywintypes.error; the shape must match pywin32's.

    Measured against real pywintypes: args == (winerror, funcname, strerror),
    and the three named attributes exist. It must also be an OSError, because
    the SDK's callers catch OSError in places.
    """
    exc = pywintypes.error(5, "OpenProcess", "Access denied")
    assert exc.args == (5, "OpenProcess", "Access denied")
    assert exc.winerror == 5
    assert exc.funcname == "OpenProcess"
    assert exc.strerror == "Access denied"
    assert isinstance(exc, Exception)
    # Measured against real pywintypes: the MRO is error -> Exception, NOT
    # OSError. An earlier draft of the shim subclassed OSError and this test
    # caught it -- a STRICTER type changes which xcept clauses fire, so
    # matching the original exactly is the whole point of a drop-in.
    assert not isinstance(exc, OSError)
    assert type(exc).__mro__[1] is Exception


def test_job_information_is_a_mutable_nested_dict():
    """The SDK MUTATES this structure, so a struct would raise TypeError.

    This is the single most important shape in the whole shim: the SDK does
    ``info["BasicLimitInformation"]["LimitFlags"] |= ...`` and then hands the
    same dict back to SetInformationJobObject.
    """
    job = win32job.CreateJobObject(None, "")
    try:
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        assert isinstance(info, dict)
        assert isinstance(info["BasicLimitInformation"], dict)
        assert isinstance(info["BasicLimitInformation"]["LimitFlags"], int)
        # The mutation the SDK performs must not raise.
        info["BasicLimitInformation"]["LimitFlags"] |= \
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    finally:
        win32api.CloseHandle(job)


def test_job_limit_flags_round_trip():
    """What SetInformationJobObject writes must come back from the query."""
    job = win32job.CreateJobObject(None, "")
    try:
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= \
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info)
        again = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        assert again["BasicLimitInformation"]["LimitFlags"] & \
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    finally:
        win32api.CloseHandle(job)


def test_job_close_terminates_an_assigned_process():
    """KILL_ON_JOB_CLOSE is the WHOLE REASON the SDK uses Job Objects.

    The SDK spawns a stdio client and must be able to take down the child's
    whole tree. If the flag did not take effect, that tree would outlive the
    client -- so this asserts the BEHAVIOUR (the child died), not the flag
    value.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        time.sleep(0.5)
        job = win32job.CreateJobObject(None, "")
        try:
            info = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= \
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation, info)
            handle = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                False, child.pid)
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                win32api.CloseHandle(handle)
        finally:
            # Closing the job with KILL_ON_JOB_CLOSE set kills its members.
            win32api.CloseHandle(job)
        deadline = time.time() + 5
        while time.time() < deadline and child.poll() is None:
            time.sleep(0.1)
        assert child.poll() is not None, \
            "the child outlived the job: KILL_ON_JOB_CLOSE did not take effect"
    finally:
        if child.poll() is None:
            child.kill()


def test_open_process_and_close_handle():
    """The SDK opens the child's process handle to assign it to a job."""
    handle = win32api.OpenProcess(
        win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_TERMINATE,
        False, os.getpid())
    assert handle
    assert win32api.CloseHandle(handle) is True


def test_open_process_raises_pywintypes_error_on_failure():
    """A failure must raise pywintypes.error, so the SDK can catch it."""
    with pytest.raises(pywintypes.error):
        win32api.OpenProcess(
            win32con.PROCESS_ALL_ACCESS, False, 0xFFFFFFFC)


def test_set_std_handle_round_trip():
    """SetStdHandle must return the PREVIOUS handle, and restore it."""
    import msvcrt

    # Real pywin32 returns None here (measured), even though the Win32 API
    # itself returns the previous handle. The SDK discards the value, but a
    # drop-in that returns a handle where the original returns None is still a
    # difference someone trips over.
    result = win32api.SetStdHandle(
        win32api.STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(1))
    assert result is None


def test_shim_works_without_pywin32_installed():
    """The property the whole exercise exists for.

    Asserted in a SUBPROCESS with pywin32's import BLOCKED, so the test cannot
    pass by accidentally importing the real thing.
    """
    code = (
        "import sys, importlib.abc, importlib.machinery\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'pywin32' or name.startswith('pywin32.'):\n"
        "            raise ImportError('pywin32 is blocked for this test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "sys.path.insert(0, %r)\n"
        "import win32api, win32job, win32con, pywintypes\n"
        "job = win32job.CreateJobObject(None, '')\n"
        "info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)\n"
        "info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE\n"
        "win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)\n"
        "win32api.CloseHandle(job)\n"
        "print('OK')\n" % str(SHIM)
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "OK" in proc.stdout


def test_conformance_against_real_pywin32_when_present():
    """The drop-in claim is measured against the real thing, not asserted.

    Where pywin32 is installed, the shim and the original must agree on every
    probed surface. Where it is not installed, the comparison is skipped --
    there is nothing to compare against.
    """
    verifier = _ROOT / "tools" / "verify_win32_shim.py"
    if not verifier.exists():
        pytest.skip("the conformance verifier is not present")
    proc = subprocess.run([sys.executable, str(verifier)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=300)
    output = proc.stdout + proc.stderr
    # The skip condition is the MODULES, not the pywin32 package name:
    # pywin32 installs its modules at the TOP level (win32api.pyd,
    # win32job.pyd), so ind_spec("pywin32") is None on an interpreter
    # that has pywin32 perfectly installed. Checking the package name made
    # this test skip where it should have compared.
    if "real pywin32 not importable here" in output:
        pytest.skip("real pywin32 is not installed in this interpreter")
    assert proc.returncode == 0, output[-1200:]
    assert "CONFORMANT" in output