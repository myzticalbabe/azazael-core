# coding: utf-8
"""Tests for `tools/disk_floor_gate.py` -- the pre-commit disk floor.

Why this file exists
--------------------
`tools/preflight_all.py --self-test` refuses a blocking gate whose declared
control artifact is absent, and it is right to: an assertion that a gate can
fail is not a demonstration. This is the demonstration for the disk floor.

What it has to distinguish, and why one direction is not enough
---------------------------------------------------------------
A gate that simply always exits 1 would pass a bare "must refuse" test. So the
same gate is driven into all three states it claims:

* **healthy** -- a floor the volume clears must exit 0, or every commit on a
  healthy disk is blocked and the gate gets removed rather than fixed;
* **below floor** -- a floor above the measured free space must exit 1 and must
  PRINT the measured number, because "disk low" without the reading cannot be
  compared with yesterday's;
* **unmeasurable** -- a volume that cannot be read must exit 2. Folding this
  into 0 is the defect the gate exists to prevent, and folding it into 1 claims
  a measurement nobody made.

The thresholds are derived from the real free space on the volume, so these
pass on a full disk and on an empty one.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import disk_floor_gate as floor  # noqa: E402

GATE = ROOT / "tools" / "disk_floor_gate.py"
GB = 1024.0 ** 3


def _run(args, env_override=None):
    env = dict(os.environ)
    env.pop("AZ_DISK_FLOOR_GB", None)
    if env_override:
        env.update(env_override)
    return subprocess.run([sys.executable, "-X", "utf8", str(GATE), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(ROOT), env=env, timeout=120)


def _free_gb() -> float:
    return shutil.disk_usage(str(ROOT)).free / GB


def test_healthy_volume_passes_and_prints_the_reading():
    done = _run(["--threshold-gb", "0.001"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "GB free" in done.stdout, "the pass path must still print the number"
    assert ">=" in done.stdout


def test_floor_above_free_space_fails_with_the_measured_number():
    floor_gb = _free_gb() * 4 + 1024
    done = _run([], env_override={"AZ_DISK_FLOOR_GB": "%.3f" % floor_gb})
    assert done.returncode == 1, done.stdout + done.stderr
    assert "below the" in done.stdout
    # The number that made it fail has to be in the message, or the operator
    # cannot tell a full disk from a misconfigured floor.
    assert "GB free" in done.stdout


def test_unmeasurable_volume_is_second_class_not_pass_not_fail():
    done = _run(["--path", _absent_volume(), "--threshold-gb", "0.001"])
    assert done.returncode == 2, done.stdout + done.stderr
    assert "UNMEASURED" in done.stdout


def _absent_volume() -> str:
    if sys.platform == "win32":
        for letter in "QRSTUVWXYZ":
            if not os.path.exists(letter + ":/"):
                return letter + ":/no-such-volume"
        pytest.skip("every candidate drive letter exists on this host")
    return "/nonexistent-mount-%d/x" % os.getpid()


def test_threshold_comes_from_config_defaults():
    """HED-5: the knob lives in config/defaults.json, not in this file."""
    config = json.loads((ROOT / "config" / "defaults.json").read_text(encoding="utf-8"))
    assert "disk_floor_gb" in config["preflight"]
    value = config["preflight"]["disk_floor_gb"]
    assert isinstance(value, (int, float)) and value > 0
    # read_default must return exactly that value, or the gate and the config
    # key that documents it have drifted apart.
    from defaults_profile import read_default
    assert float(read_default("preflight.disk_floor_gb", None)) == float(value)


def test_explicit_threshold_beats_the_environment():
    """A --threshold-gb the caller typed must win over an inherited env var."""
    done = _run(["--threshold-gb", "0.001"],
                env_override={"AZ_DISK_FLOOR_GB": "999999"})
    assert done.returncode == 0, done.stdout + done.stderr
