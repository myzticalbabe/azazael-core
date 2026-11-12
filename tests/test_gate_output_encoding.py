# coding: utf-8
"""A gate must be able to report its verdict.

`tools/verify_hashes.py` printed U+2705/U+274C/U+26A0/U+1F4E6/U+1F3AF status glyphs. On Windows, stdout defaults to
cp1252, which cannot encode any of them, so the first emoji it reached raised
UnicodeEncodeError. The run died after three lines of banner having produced
no verdict at all -- and it exited 1, which is exactly what "the hashes do not
match" also produces. A reader, or a CI step, cannot tell those apart. So the
defect is not the crash: it is that a crashed gate and a failing gate share an
exit code, and the crash is silent about which one happened.

The test forces a cp1252 stdout and asserts the tool still gets to a verdict.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY_HASHES = ROOT / "tools" / "verify_hashes.py"


class _Cp1252(io.TextIOWrapper):
    """A stdout that cannot encode the glyphs this tool prints."""

    def __init__(self, buf):
        super().__init__(buf, encoding="cp1252", errors="strict", newline="")

    def write(self, s):  # strict, like a real Windows console
        return super().write(s)


def test_verify_hashes_reaches_a_verdict_under_cp1252():
    """The regression: cp1252 stdout must not cost the tool its verdict."""
    proc = subprocess.run(
        [sys.executable, str(VERIFY_HASHES)],
        cwd=ROOT, capture_output=True,
        env={**dict(__import__("os").environ),
             "PYTHONIOENCODING": "cp1252:strict"},
    )
    stderr = proc.stderr.decode("utf-8", errors="replace")
    stdout = proc.stdout.decode("utf-8", errors="replace")

    assert "UnicodeEncodeError" not in stderr, stderr
    assert "Traceback" not in stderr, stderr
    # It must say something about what it found, not just die after a banner.
    assert len(stdout.strip().splitlines()) > 3, stdout
    assert "FILE HASH VERIFICATION" in stdout


def test_verify_hashes_does_not_reuse_the_banner_as_a_verdict():
    """rc=1 must mean 'it ran and reported', never 'it crashed'.

    On this machine the tool is inapplicable (it is a WSL-side tool: its SRC
    and DST paths do not exist here), so the honest verdict is 'source not
    found'. What must not happen is a traceback. This test documents the
    inapplicability rather than pretending the tool passes here.
    """
    proc = subprocess.run(
        [sys.executable, str(VERIFY_HASHES)],
        cwd=ROOT, capture_output=True,
        env={**dict(__import__("os").environ),
             "PYTHONIOENCODING": "cp1252:strict"},
    )
    stdout = proc.stdout.decode("utf-8", errors="replace")
    assert proc.returncode in (0, 1)
    assert "not found" in stdout.lower() or proc.returncode == 0, stdout
