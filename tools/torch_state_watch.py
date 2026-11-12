"""Name the test that leaves ``torch`` partially initialized in ``sys.modules``.

Why this exists
---------------
In the aggregate suite two files fail with::

    AttributeError: partially initialized module 'torch' has no attribute 'nn'
    (most likely due to a circular import)

Both files pass alone *and* paired, and every prefix of the collected order
containing them is green, so bisection by file cannot find the source: the
failure needs the long run, not a particular neighbour.  The message is
misleading as well -- nothing is circular.  A partial module stays in
``sys.modules`` when an ``import`` is *interrupted* after the parent module
object was created but before its body finished, which is what a timeout or a
killed worker thread does.  The next ``import torch`` then returns the corpse
instead of re-importing.

So the instrument is a state probe, not a tracer: after every test phase, check
whether ``torch`` is present but missing the attributes a completed import
always has.  The first test after which that is true is the one that broke it
-- the polluter names itself, and the victim is whatever imports torch next.

Usage
-----
    PYTHONPATH=tools TORCH_WATCH_LOG=logs/torch_state_watch.log \
        python -m pytest tests/ -p torch_state_watch

``-p no:torch_state_watch`` disarms it.  The probe is read-only and never
raises: an instrument that can fail the run it is measuring is worse than no
instrument.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

# Attributes every completed `import torch` has.  A module missing any of these
# is either mid-import (fine) or a corpse left by an interrupted import (not).
_REQUIRED = ("nn", "Tensor", "optim", "autograd")

_LOG_ENV = "TORCH_WATCH_LOG"
_DEFAULT_LOG = os.path.join("logs", "torch_state_watch.log")


def _log_path() -> str:
    return os.environ.get(_LOG_ENV) or _DEFAULT_LOG


def _missing_attrs(mod: Any) -> list[str]:
    import builtins  # noqa: F401

    def has(attr: str) -> bool:
        return getattr(mod, attr, None) is not None

    return [a for a in _REQUIRED if not has(a)]


def _probe() -> tuple[bool, list[str], list[str]]:
    """Return (broken, missing attrs, torch submodules still registered)."""
    try:
        mod = sys.modules.get("torch")
        if mod is None:
            return False, [], []
        missing = _missing_attrs(mod)
        subs = sorted(k for k in sys.modules if k.startswith("torch."))
        return bool(missing), missing, subs
    except Exception:  # pragma: no cover - the probe must never raise
        return False, [], []


class _Watch:
    """Holds the first-observation record so the report is not a flood."""

    def __init__(self) -> None:
        self.first_broken_at: str | None = None
        self.last_phase: str = ""
        self.observations: int = 0
        self.broken_phases: int = 0

    def write(self, line: str) -> None:
        try:
            path = _log_path()
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            print("torch_state_watch: write failed", file=sys.stderr)


_STATE = _Watch()


def _check(when: str, nodeid: str) -> None:
    broken, missing, subs = _probe()
    _STATE.observations += 1
    if not broken:
        return
    _STATE.broken_phases += 1
    if _STATE.first_broken_at is None:
        _STATE.first_broken_at = nodeid
        _STATE.write(
            "%s FIRST PARTIAL torch after %s %s: missing=%s submodules=%d"
            % (time.strftime("%H:%M:%S"), when, nodeid, ",".join(missing), len(subs))
        )
    else:
        _STATE.write(
            "%s partial torch still present after %s %s: missing=%s"
            % (time.strftime("%H:%M:%S"), when, nodeid, ",".join(missing))
        )


def pytest_collectreport(report: Any) -> None:
    """A module-level `import torch` that dies mid-way is a collection report."""
    try:
        if getattr(report, "failed", False):
            _check("collect", str(getattr(report, "nodeid", "?")))
    except Exception:
        pass


def pytest_runtest_logreport(report: Any) -> None:
    """Per-phase, so setup damage is attributed to the test that caused it."""
    try:
        _check(str(getattr(report, "when", "?")), str(getattr(report, "nodeid", "?")))
    except Exception:
        pass


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    try:
        if _STATE.first_broken_at is None:
            _STATE.write(
                "%s NO partial torch observed in %d phases (exitstatus=%s)"
                % (time.strftime("%H:%M:%S"), _STATE.observations, exitstatus)
            )
        else:
            _STATE.write(
                "%s SUMMARY first partial torch after: %s (broken phases: %d of %d)"
                % (
                    time.strftime("%H:%M:%S"),
                    _STATE.first_broken_at,
                    _STATE.broken_phases,
                    _STATE.observations,
                )
            )
    except Exception:
        print("torch_state_watch: summary write failed", file=sys.stderr)
