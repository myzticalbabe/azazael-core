#!/usr/bin/env python3
# coding: utf-8
"""Smoke-run each entry point: the logging fix must resolve at RUNTIME.

Why this file exists. The sweep guard in `test_logging_hygiene_sweep.py` proves
no entry point *calls* `logging.disable`, and `test_spine_gate_warning.py`
proves importing `spine_cycle_apply` leaves logging on -- but neither runs the
other nine entry points. The module-level `quiet_noisy_loggers()` call and the
`from CORE.log_hygiene import ...` that resolves it are only exercised when the
module actually executes, and a broken import path or a later `logging.disable`
would sail past an import-only test.

`runpy.run_path(..., run_name="<not __main__>")` executes a script's module-level
statements (the sys.path insert, the helper import, the helper call) WITHOUT its
`if __name__ == "__main__":` block, so no network, no training, no G: writes.

Two properties are pinned per entry point:
  * module-level execution does not raise;
  * logging is left ON (root.manager.disable stays below WARNING) and
    `worm_registry` is quieted BY NAME (level >= WARNING).
"""
from __future__ import annotations

import logging
import os
import runpy
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SCRIPTS = os.path.join(_ROOT, "scripts")

#: Every entry point that carries the shared hygiene call.
#: Retired 2026-10-11 (trainer/loop retirement wave 1): evolve_loop.py,
#: train_fleet.py, train_today.py moved to archive/retired-trainers-2026-10-11/.
ENTRY_POINTS = (
    "berks_monitor.py",
    "gossip_export.py",
    "gossip_round.py",
    "night_shift.py",
    "regen_config_trace.py",
    "spine_cycle_apply.py",
    "threat_monitor.py",
)


class TestEntryPointSmoke(unittest.TestCase):

    def test_every_entry_point_quiets_by_name_without_disabling_logging(self):
        for name in ENTRY_POINTS:
            with self.subTest(entry=name):
                path = os.path.join(_SCRIPTS, name)
                self.assertTrue(os.path.isfile(path), f"{name} is missing")
                prev_disable = logging.root.manager.disable
                prev_argv = sys.argv
                logging.disable(logging.NOTSET)
                # Some entry points read sys.argv at import (train_today picks
                # its cycle count from it); under pytest that is pytest's argv.
                sys.argv = [path]
                try:
                    runpy.run_path(path, run_name="_smoke_" + name)
                    self.assertLess(
                        logging.root.manager.disable, logging.WARNING,
                        f"{name} disabled logging process-wide at import",
                    )
                    self.assertGreaterEqual(
                        logging.getLogger("worm_registry").level, logging.WARNING,
                        f"{name} did not quiet worm_registry by name",
                    )
                finally:
                    sys.argv = prev_argv
                    logging.disable(prev_disable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
