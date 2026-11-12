#!/usr/bin/env python3
# coding: utf-8
"""The proprioception gate must be LOUD when it refuses, at the source.

Why this file exists. `scripts/spine_cycle_apply.py` already maps a refusal to a
non-zero exit (tests/test_spine_cycle_visibility.py pins that). But it is only
one caller. `night_shift.py`, `train_today.py` and any future caller read the
report dict and would each have to remember to special-case `skipped` -- and two
of them did not, so a refused write read there as "proposals=0": success-shaped.

The fix is at the gate. `SpineCycleWorm.run_cycle` calls `_loud`, which emits a
WARNING through `logging` -- and, crucially, ALSO writes to stderr when the
process has globally disabled logging. That second path is not hypothetical:
`scripts/spine_cycle_apply.py` -- the exact process the hourly scheduler runs --
used to call `logging.disable(logging.CRITICAL)` at import, so a logging-only
warning was swallowed in the one place it had to be seen. That blanket switch
has since been narrowed to the noisy `worm_registry` logger (see
`TestEntryPointDoesNotDisableLogging`), but the stderr fallback stays as
defence-in-depth for any caller that reintroduces one. These tests pin all of
it: the record, the reason text, the stderr fallback under a process-wide
disable, and that the observation-only path is never refused.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "src"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CORE.WORMS.expansion.spine import SpineCycleWorm  # noqa: E402

_LOGGER = logging.getLogger("CORE.WORMS.expansion.spine")
_REASON = "stale artifacts: graphify-out/graph.json"


class TestGateRefusalIsLoud(unittest.TestCase):

    def setUp(self):
        # A sibling entry point (spine_cycle_apply) disables logging globally at
        # import, which would hide the record from assertLogs. Undo it, and
        # restore the previous threshold afterwards so the test does not leak.
        self._prev_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)

    def tearDown(self):
        logging.disable(self._prev_disable)

    def _worm_refusing(self) -> SpineCycleWorm:
        w = SpineCycleWorm(min_interval_seconds=0)
        # Shadow the probe so the refusal is deterministic and offline: the test
        # is about the warning, not about reproducing a stale tree.
        w._self_image_stale = lambda: _REASON  # type: ignore[method-assign]
        return w

    def test_refusal_emits_a_warning_naming_skip_and_reason(self):
        w = self._worm_refusing()
        with self.assertLogs(_LOGGER, level="WARNING") as cm:
            rep = w.run_cycle(apply=True)
        self.assertEqual(rep.get("skipped"), "stale_self_image")
        self.assertFalse(rep.get("success"), "a refusal is not a success")
        blob = "\n".join(cm.output)
        self.assertIn("REFUSED", blob)
        self.assertIn("stale_self_image", blob)
        self.assertIn("graphify-out/graph.json", blob,
                      "the reason must reach the log, not be dropped")

    def test_warning_survives_a_process_wide_logging_disable(self):
        """The scheduler's process disables logging; the refusal must still land.

        This is the regression that a logging-only warning would have missed:
        spine_cycle_apply.py silences the whole module before the gate runs.
        """
        w = self._worm_refusing()
        logging.disable(logging.CRITICAL)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rep = w.run_cycle(apply=True)
        finally:
            logging.disable(self._prev_disable)
        self.assertEqual(rep.get("skipped"), "stale_self_image")
        self.assertIn("REFUSED", buf.getvalue(),
                      "a disabled process must still get the refusal on stderr")
        self.assertIn("graphify-out/graph.json", buf.getvalue())

    def test_observation_only_cycle_does_not_refuse(self):
        """Looking cannot harm: the gate must not hold back an apply=False cycle."""
        w = SpineCycleWorm(min_interval_seconds=0)
        w._self_image_stale = lambda: _REASON  # type: ignore[method-assign]
        logger = logging.getLogger("CORE.WORMS.expansion.spine")
        records: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())  # type: ignore[method-assign]
        logger.addHandler(handler)
        try:
            rep = w.run_cycle(apply=False)
        finally:
            logger.removeHandler(handler)
        self.assertIsNone(rep.get("skipped"),
                          "an observation-only cycle must not be refused")
        self.assertFalse(any("REFUSED" in m for m in records),
                         "no refusal warning on the observation path")


class TestEntryPointDoesNotDisableLogging(unittest.TestCase):
    """The silent-refusal regression lived in the entry point, not the gate.

    `scripts/spine_cycle_apply.py` runs in the scheduler's own process, and it
    used a process-wide `logging.disable(logging.CRITICAL)`. That hid every
    WARNING in the process -- including the gate's -- which is how a ~30h
    refusal read as success. The switch is now narrowed to the one logger that
    was actually noisy (`worm_registry`, one INFO per worm at import). These
    tests fail if the blanket switch ever comes back.
    """

    def test_importing_the_entry_point_leaves_logging_on(self):
        import importlib
        importlib.import_module("spine_cycle_apply")
        self.assertLess(
            logging.root.manager.disable, logging.WARNING,
            "importing the scheduler entry point must not disable logging: "
            "a blanket disable swallows the gate's own refusal warning",
        )

    def test_registry_chatter_is_silenced_by_name(self):
        import importlib
        entry = importlib.import_module("spine_cycle_apply")
        self.assertIsNotNone(entry)
        level = logging.getLogger("worm_registry").level
        self.assertGreaterEqual(
            level, logging.WARNING,
            "the one noisy logger must stay quiet at INFO by name",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
