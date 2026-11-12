"""Regression tests for the sh_runner path-flavor fix.

The runner assumed WSL bash (``/mnt/c/...``) while this box resolves
``bash`` to Git-Bash (``/c/...``), so every bash run failed with
"No such file or directory" and the polyglot golden agreement went red.
These tests lock the flavored mapping: MSYS by default, WSL when the
binary or the distro marker says so, both drive-prefix forms handled.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from INTERPRETERS.sh_runner import _bash_flavor, _to_bash_path


def _win(p):
    return "C:" + chr(92) + p.replace("/", chr(92))


def test_msys_is_default_flavor():
    assert _bash_flavor(r"C:\Program Files\Git\usr\bin\bash.EXE") == "msys"
    assert _bash_flavor("") == "msys"
    assert _bash_flavor("/usr/bin/bash") == "msys"


def test_wsl_detected_by_binary_or_marker(monkeypatch):
    assert _bash_flavor(r"C:\Windows\System32\wsl.exe") == "wsl"
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert _bash_flavor("/usr/bin/bash") == "wsl"


def test_both_prefix_forms_map_to_msys():
    assert _to_bash_path(_win("Azazael/t.sh"), "bash") == "/c/Azazael/t.sh"
    assert _to_bash_path("C:/Azazael/t.sh", "bash") == "/c/Azazael/t.sh"


def test_wsl_flavor_maps_to_mnt():
    wsl = r"C:\Windows\System32\wsl.exe"
    assert _to_bash_path(_win("Azazael/t.sh"), wsl) == "/mnt/c/Azazael/t.sh"


def test_posix_paths_pass_through():
    assert _to_bash_path("/c/Azazael/t.sh", "bash") == "/c/Azazael/t.sh"
    assert _to_bash_path("relative/t.sh", "bash") == "relative/t.sh"


def test_bash_runs_after_fix():
    from INTERPRETERS.sh_runner import ShRunner
    res = ShRunner().run_code('echo AZAZAEL-GOLDEN-OK')
    assert res.success and "AZAZAEL-GOLDEN-OK" in res.stdout
