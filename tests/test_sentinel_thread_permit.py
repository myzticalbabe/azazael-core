"""Regression for the thread-local sentinel permit (Error #197, 2026-09-22).

`CORE/sentinel_enforcer.py` arms a process-global `sys.addaudithook` at import
that raises `PermissionError` on any write-mode `open` unless
`_GATED_CALL.is_active` is set for THAT thread. `_GATED_CALL` is
`threading.local()`, so a permit granted on the main thread does not cover a
worker thread -- which is how `fs_probe` broke:

    bounded_subprocess_output runs its child on a "bounded-subprocess" thread
    and opens tempfile.TemporaryFile() there
      -> PermissionError: CRITICAL: Unauthorized write to ...\\Temp
      -> result {"ok": false, "timeout": false, "error": "CRITICAL: ..."}
      -> _child_probe returned _UNANSWERED
      -> bounded_is_dir answered None = "unknown"

All 10 `tests/test_fs_probe_backend.py` tests failed on the aggregate run, every
one of them green alone, reproduced in 4 s with:

    pytest tests/test_compliance_step45_init.py tests/test_fs_probe_backend.py

PRODUCTION FIX (this file guards it): `sentinel_enforcer.worker_permit(held)` --
read on the starter thread by `fs_probe._carried_permit`, entered on the worker.
Authority is not widened: `held` comes from the starter, so a probe started with
no permit is still denied.

FALSIFIABILITY. `tests/conftest.py` also swaps `_GATED_CALL` for a process-shared
object, which would make any assertion here pass with or without the fix. So both
tests below rebind `sentinel_enforcer._GATED_CALL` to a REAL `threading.local()`
for their duration (the audit hook reads the module global at call time), and
assert the inherited-permit behaviour and the no-widening behaviour as a pair.
Remove the fix and `test_inherited_permit` fails; widen the fix to a global flag
and `test_no_permit_is_still_denied` fails.
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_ECHO = [sys.executable, "-c", "print('probe-answered')"]


@pytest.fixture()
def real_thread_local_token():
    """Run against the real `threading.local()` token, not conftest's shared one."""
    from CORE import sentinel_enforcer as se

    saved = se._GATED_CALL
    token = threading.local()
    token.is_active = False
    se._GATED_CALL = token
    try:
        yield token
    finally:
        se._GATED_CALL = saved


def test_a_worker_thread_is_denied_when_its_starter_holds_no_permit(
        real_thread_local_token):
    """The premise: with no permit anywhere, the guard really does deny a worker.

    Without this, the fix below could be measuring nothing.
    """
    from CORE import fs_probe

    real_thread_local_token.is_active = False
    res = fs_probe.bounded_subprocess_output(_ECHO, timeout_s=5.0)
    assert res["ok"] is False
    assert res["denied"] is True, res
    assert fs_probe.is_denied(res) is True


def test_a_worker_thread_carries_the_permit_its_starter_held(
        real_thread_local_token):
    """The fix: the starter's permit crosses the thread boundary it created."""
    from CORE import fs_probe

    real_thread_local_token.is_active = True  # granted on THIS thread
    res = fs_probe.bounded_subprocess_output(_ECHO, timeout_s=5.0)
    assert res["denied"] is False, res
    assert res["ok"] is True, res
    assert "probe-answered" in res["out"], res


def test_denials_are_counted_so_they_cannot_hide_as_unknown(real_thread_local_token):
    """"We refused to look" must be countable, not folded into "no answer"."""
    from CORE import fs_probe

    before = fs_probe.probe_backend_stats()["denied_probes"]
    real_thread_local_token.is_active = False
    fs_probe.bounded_subprocess_output(_ECHO, timeout_s=5.0)
    after = fs_probe.probe_backend_stats()["denied_probes"]
    assert after == before + 1, (before, after)


def test_a_delegated_directory_probe_answers_while_the_guard_is_armed(
        real_thread_local_token, tmp_path):
    """The end the user sees: a reachable path is reported reachable."""
    from CORE import fs_probe

    real_thread_local_token.is_active = True
    fs_probe.clear_unreachable_cache()
    before = fs_probe.probe_backend_stats()["denied_probes"]
    assert fs_probe.bounded_is_dir(str(tmp_path)) is True
    assert fs_probe.bounded_first_dir([str(tmp_path)]) == str(tmp_path)
    # The counter is process-wide, and the two denial tests above incremented it
    # on purpose; what matters here is that THIS path caused no denial at all.
    assert fs_probe.probe_backend_stats()["denied_probes"] == before
