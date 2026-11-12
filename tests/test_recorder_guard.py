# coding: utf-8
"""The recorder guard must cap volume without ever dropping the important rows.

Context: `memory_store.db` reached 6.41 GB because `trainers_v2` wrote one
`episode_complete` row per RL episode (~62 rows/s for 35 minutes) and nothing
on the write path imposed a ceiling. These tests pin the three properties that
make the guard safe rather than merely restrictive:

  1. a per-tick flood is capped a little above the burst (not zero, not all);
  2. errors, criticals and the audited event types ALWAYS pass, even mid-flood;
  3. a process that invents a new event_type every tick is still stopped by the
     session ceiling, which does not depend on knowing the type names.

A fresh RecorderGuard is constructed per test: the module singleton carries
process state, and asserting on it would make tests order-dependent.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from CORE.recorder_guard import ALWAYS_ALLOW, RATE_LIMITS, RecorderGuard  # noqa: E402


def test_per_tick_flood_is_capped_near_burst():
    g = RecorderGuard()
    allowed = sum(1 for _ in range(2000) if g.allow("episode_complete", "info"))
    rate, burst = RATE_LIMITS["episode_complete"]
    # Refill during a tight loop is tiny, so the answer is the burst plus at
    # most a couple of tokens; it must never approach the 2000 attempted.
    assert burst <= allowed <= burst + 5, (allowed, burst, rate)
    snap = g.snapshot()["episode_complete"]
    assert snap["allowed"] == allowed
    assert snap["suppressed"] == 2000 - allowed


def test_errors_and_criticals_always_pass_mid_flood():
    g = RecorderGuard()
    for _ in range(500):
        g.allow("episode_complete", "info")          # exhaust the bucket
    assert all(g.allow("episode_complete", "error") for _ in range(100))
    assert all(g.allow("episode_complete", "critical") for _ in range(100))


def test_audited_types_are_never_throttled():
    g = RecorderGuard()
    for _ in range(500):
        g.allow("episode_complete", "info")
    for event_type in sorted(ALWAYS_ALLOW):
        assert g.allow(event_type, "info"), event_type
    # a hook is counted, not capped
    assert all(g.allow("hook:technique_runtime.verify_boundary", "info")
               for _ in range(200))


def test_unknown_type_falls_under_the_default_cap():
    g = RecorderGuard()
    allowed = sum(1 for _ in range(5000) if g.allow("a_type_nobody_classified", "info"))
    # burst is generous on purpose (legitimate imports), sustained rate is not:
    # 5000 attempted rows must collapse to a few hundred, never thousands.
    assert 0 < allowed <= 210, allowed


def test_session_ceiling_stops_a_new_type_every_tick(monkeypatch):
    import CORE.recorder_guard as rg
    monkeypatch.setattr(rg, "_SESSION_MAX", 40)
    g = RecorderGuard()
    allowed = sum(1 for i in range(2000) if g.allow(f"tick_type_{i % 50}", "info"))
    totals = g.totals()
    assert totals["session_capped"] is True
    assert 40 <= allowed <= 45, (allowed, totals)   # ceiling plus the row that crossed it
    # and the audit trail still flows afterwards
    assert g.allow("evolution_result", "info")
    assert g.allow("anything_at_all", "critical")


def test_kill_switch_disables_every_limit(monkeypatch):
    import CORE.recorder_guard as rg
    importlib.reload(rg)                       # pick up the env at import time
    monkeypatch.setattr(rg, "_DISABLED", True)
    g = rg.RecorderGuard()
    assert g.allow("episode_complete", "info")
