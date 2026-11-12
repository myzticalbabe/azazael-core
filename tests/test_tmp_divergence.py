# coding: utf-8
r"""Pin the measured bash/Python `/tmp` divergence and the translation that fixes it.

Every literal below was MEASURED by the coordinator on this host on 2026-10-15 and
is reproduced here rather than derived:

  bash    echo "written-by-bash" > /tmp/az732probe.txt  -> the file EXISTS (16 bytes)
          ls -la /tmp/az732probe.txt
  python  os.path.exists('/tmp/az732probe.txt')          -> False
          os.path.abspath('/tmp/az732probe.txt')         -> C:\tmp\az732probe.txt
          tempfile.gettempdir()                          -> C:\Users\clint\AppData\Local\Temp
          os.environ['LOCALAPPDATA']                     -> C:\Users\clint\AppData\Local
  python  open('/tmp/az732py.txt', 'w')                  -> C:\tmp\az732py.txt
  bash    ls /tmp/az732py.txt                            -> No such file or directory

The Windows-only literal expectations are gated on os.name == 'nt' so this file
cannot claim a platform it is not running on (a bare `return` is the stdlib-only
guard -- importing pytest just to call skip would add a dependency the sibling
tools in this directory do not take). The invariant that must hold EVERYWHERE is
pinned without a gate: the translation targets tempfile.gettempdir(), the single
directory bash's /tmp and Python agree on.
"""

import os
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from tmp_paths import bash_tmp_dir, resolve_tmp_path  # noqa: E402 - after sys.path

IS_WINDOWS = os.name == "nt"
C_DRIVE_TMP = os.path.join("C:" + os.sep, "tmp")
MEASURED_NAME = "az732probe.txt"


def _python_abspath_of_the_bash_tmp_path():
    """What Python thinks /tmp/az732probe.txt is -- the half that disagrees."""
    return os.path.abspath("/tmp/" + MEASURED_NAME)


def test_bash_tmp_dir_is_the_python_temp_dir():
    assert bash_tmp_dir() == tempfile.gettempdir()


def test_the_measured_divergence_both_sides_report_success():
    """The defect in one assertion: Python's abspath is NOT where bash wrote."""
    if not IS_WINDOWS:
        return  # a Windows/git-bash property; asserting it elsewhere would lie
    assert _python_abspath_of_the_bash_tmp_path() != os.path.join(
        tempfile.gettempdir(), MEASURED_NAME)


def test_abspath_lands_in_c_drive_tmp_on_windows():
    if not IS_WINDOWS:
        return
    assert _python_abspath_of_the_bash_tmp_path() == os.path.join(
        C_DRIVE_TMP, MEASURED_NAME)


def test_gettempdir_is_localappdata_temp_on_windows():
    if not IS_WINDOWS:
        return
    local = os.environ.get("LOCALAPPDATA")
    assert local, "LOCALAPPDATA missing on a Windows host"
    assert os.path.normcase(tempfile.gettempdir()) == os.path.normcase(
        os.path.join(local, "Temp"))


def test_resolve_tmp_path_targets_the_temp_dir_everywhere():
    """THE invariant, platform-independent: the mapping cannot lie about its target."""
    assert resolve_tmp_path("/tmp/x") == os.path.join(tempfile.gettempdir(), "x")


def test_nested_remainder_survives():
    assert resolve_tmp_path("/tmp/a/b.txt") == os.path.join(
        tempfile.gettempdir(), "a", "b.txt")


def test_bare_and_var_tmp_map_to_the_directory():
    assert resolve_tmp_path("/tmp") == tempfile.gettempdir()
    assert resolve_tmp_path("/var/tmp") == tempfile.gettempdir()
    assert resolve_tmp_path("/var/tmp/x") == os.path.join(
        tempfile.gettempdir(), "x")


def test_a_partial_prefix_is_not_rewritten():
    """`/tmpfoo` is not `/tmp` -- the trap a startswith-only matcher falls into."""
    assert resolve_tmp_path("/tmpfoo/x") == "/tmpfoo/x"


def test_non_posix_paths_are_returned_unchanged():
    for unchanged in ("change.diff", "C:/tmp/x",
                      os.path.join(C_DRIVE_TMP, "x"),
                      os.path.join("%TEMP%", "x")):
        assert resolve_tmp_path(unchanged) == unchanged


def test_parent_traversal_is_refused():
    assert resolve_tmp_path("/tmp/../etc/passwd") == "/tmp/../etc/passwd"


def test_non_str_is_returned_as_given():
    given = Path("/tmp/x")
    assert resolve_tmp_path(given) is given


def test_a_write_through_the_resolver_is_readable_at_that_path():
    """The fix, exercised end to end rather than asserted about."""
    target = resolve_tmp_path("/tmp/az732_roundtrip_probe.txt")
    assert target == os.path.join(tempfile.gettempdir(),
                                  "az732_roundtrip_probe.txt")
    try:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("roundtrip")
        assert os.path.exists(target), target
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "roundtrip"
    finally:
        if os.path.exists(target):
            os.remove(target)
