# coding: utf-8
"""The mandated memory-protocol step 1 must not report success while empty.

`tools/session_start.py` exited 0 while delivering NOTHING from either store.
The per-store `except Exception` was catching a UnicodeEncodeError raised by
`print()`, not a store failure: stdout is cp1252 on this host, PMB's payload
decoded a raw byte to U+FFFD and engram's carried U+2192, so the payload could
not be written and the handler relabelled it `unreachable`. Recorded as
engram #706.

These tests pin the properties that make a silent empty return impossible, in
the shape tests/test_gate_output_encoding.py established for the same class:

  1. a payload the console cannot encode is DELIVERED (degraded), not replaced
     by an `unreachable` line -- this is the guard, and it fails the moment the
     delivery path is reverted;
  2. the tool states on one line what each store delivered;
  3. `--strict` is the only way to get a non-zero exit, so the documented
     never-blocking contract for the default invocation is preserved.

Tests 1 and 3 are hermetic (the stores are stubs, so they test the tool, not
the daemon). Test 2 runs the real command the protocol runs.
"""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "session_start.py"

#: The two characters that actually broke it -- as escapes, so this new file
#: stays ASCII (the pre-commit checklist bans non-ASCII in new code).
ARROW = "\u2192"
REPLACEMENT = "\ufffd"
PAYLOAD = "engram context: 3 session(s); newest note used %s and %s" % (
    ARROW, REPLACEMENT)


class _Cp1252(io.TextIOWrapper):
    """A stdout that cannot encode the payload, like a real Windows console."""

    def __init__(self, buf):
        super().__init__(buf, encoding="cp1252", errors="strict", newline="")


def _load_tool():
    """Import tools/session_start.py as a module (its main() stays uncalled)."""
    spec = importlib.util.spec_from_file_location("session_start_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub(payload):
    """A `subprocess` stand-in whose every call returns `payload` on stdout."""
    def run(*args, **kwargs):
        return types.SimpleNamespace(stdout=payload, stderr="", returncode=0)
    return types.SimpleNamespace(run=run)


def _cp1252_stdout(monkeypatch):
    """Point sys.stdout at a strict cp1252 stream; return the byte buffer."""
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", _Cp1252(buf))
    return buf


def test_an_unencodable_payload_is_delivered_not_reported_unreachable(monkeypatch):
    """The regression: a print failure must never become a store failure.

    Before the fix, `engram_context()` raised UnicodeEncodeError inside its own
    `except Exception`, printed `engram unreachable (...)`, and delivered
    nothing -- while engram had answered perfectly.
    """
    module = _load_tool()
    buf = _cp1252_stdout(monkeypatch)
    monkeypatch.setattr(module, "subprocess", _stub(PAYLOAD))
    monkeypatch.setattr(module, "ENGRAM_BIN", sys.executable)  # .exists() passes

    delivered, status = module.engram_context()
    sys.stdout.flush()
    written = buf.getvalue().decode("cp1252", errors="replace")

    assert delivered > 0, "an unencodable character cost the store its payload"
    assert "unreachable" not in status.lower(), status
    assert "3 session(s)" in written, written


def test_the_summary_line_states_what_each_store_delivered():
    """End-to-end, the way the protocol invokes it, under a cp1252 console."""
    env = dict(os.environ, PYTHONIOENCODING="cp1252:strict")
    env.pop("PYTHONUTF8", None)
    # The start-side capture is real in production, but this e2e test must
    # never write the production store; the disable flag is the documented
    # opt-out and MUST show up in the summary line (a silent disable would
    # look like a green capture).
    env.pop("AZAZAEL_MODEL_ID", None)
    env["AZAZAEL_PAMS_CAPTURE"] = "0"
    proc = subprocess.run(
        [sys.executable, str(TOOL), "test"],
        cwd=ROOT, capture_output=True, env=env, timeout=300)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")

    assert "Traceback" not in stderr, stderr
    assert "UnicodeEncodeError" not in stderr, stderr
    assert "session_start: delivered PMB=" in stdout, stdout
    assert "pams_capture=disabled" in stdout, stdout


def test_strict_is_the_only_way_to_a_non_zero_exit(monkeypatch):
    """Default stays never-blocking; --strict is the opt-in gate.

    Both stores are stubbed empty, so this is the "delivered nothing" case --
    the exact state that used to be invisible.
    """
    module = _load_tool()
    monkeypatch.setattr(module, "subprocess", _stub(""))
    monkeypatch.setattr(module, "ENGRAM_BIN", sys.executable)
    monkeypatch.setattr(module, "PMB_PY", Path(sys.executable))
    monkeypatch.setattr(module, "CLIENT", Path(sys.executable))

    _cp1252_stdout(monkeypatch)
    assert module.main(["task"]) == 0, "the default invocation must not block"

    buf = _cp1252_stdout(monkeypatch)
    assert module.main(["task", "--strict"]) == 1
    sys.stdout.flush()
    text = buf.getvalue().decode("cp1252", errors="replace")
    assert "NO CONTEXT" in text, text
