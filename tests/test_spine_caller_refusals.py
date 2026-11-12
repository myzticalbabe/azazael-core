#!/usr/bin/env python3
# coding: utf-8
"""Every script that drives the spine write gate must read `skipped`.

Why this file exists. The 2026-09-18..21 outage was not a gate bug: the
proprioception gate refused to write, correctly, and every caller printed a
success-shaped line -- `proposals=0` -- so a ~30 hour stall read as green.
`test_spine_gate_warning.py` pins that the GATE is loud; it cannot pin that the
CALLERS read the flag. `spine_cycle_apply.py`, `night_shift.py` and
`train_today.py` each check `rep.get("skipped")` now. This sweep keeps a NEW
caller from quietly reintroducing the success-shaped line.

It scans scripts/ (the entry-point layer, where the outage lived) for files that
both name `SpineCycleWorm` and call `.run_cycle(`, and requires each to inspect
`skipped`. Observation-only callers use `apply=False` and are never refused --
`test_spine_gate_warning.py` pins that -- so they are not forced to check.
"""
from __future__ import annotations

import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SCRIPTS = os.path.join(_ROOT, "scripts")

#: Callers known to drive the gate with apply=True.
#: `train_today.py` is deliberately absent: it was retired to
#: archive/retired-trainers-2026-10-11/ by afc74bd1 ("Retire dead trainers wave
#: 1a"), and this sweep only scans scripts/. Keeping it listed asserted a file
#: the sweep can no longer see, so the check failed for a retirement rather than
#: for the defect it guards against (a caller printing success-shaped output).
KNOWN_CALLERS = {"night_shift.py", "spine_cycle_apply.py"}


def _gate_callers() -> dict[str, str]:
    callers: dict[str, str] = {}
    for name in sorted(os.listdir(_SCRIPTS)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(_SCRIPTS, name), encoding="utf-8",
                    errors="replace").read()
        if "SpineCycleWorm" in text and ".run_cycle(" in text:
            callers[name] = text
    return callers


class TestSpineCallersReadRefusals(unittest.TestCase):

    def test_the_known_callers_are_still_present(self):
        callers = _gate_callers()
        missing = KNOWN_CALLERS - set(callers)
        self.assertFalse(
            missing,
            "the sweep found no run_cycle driver in: %s" % sorted(missing),
        )

    def test_every_gate_caller_inspects_the_skipped_flag(self):
        callers = _gate_callers()
        blind = [name for name, text in callers.items() if "skipped" not in text]
        self.assertEqual(
            blind, [],
            "a script driving SpineCycleWorm.run_cycle(apply=True) must read "
            "`rep.get('skipped')` -- otherwise a refusal prints as success, the "
            "exact shape that hid the 2026-09-18..21 stall. Offenders: "
            + ", ".join(blind),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
