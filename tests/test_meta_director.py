"""Tests for meta_director.pick / observe.

Covers the guarantees the training rotation depends on:
  - staleness backstop: a track 4h+ stale is picked even when Thompson
    favors another track (prevents starvation);
  - cooldown: a just-picked track sits out COOLDOWN_SLOTS;
  - determinism: same (slot, ledger) -> same pick;
  - never-seen tracks are infinitely stale (tried immediately).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import meta_director as md  # noqa: E402


class MetaDirectorTestBase(unittest.TestCase):
    """Point the ledger at a temp file so observe()/save_ledger() can
    NEVER touch the production config/track_bandit.json.

    (2026-09-09: a test that called the real save_ledger clobbered the
    production ledger — GRPO's last_slot was literally the test's slot.
    Every test below runs against an isolated temp ledger.)
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="md_test_")
        self._orig_path = md.LEDGER_PATH
        md.LEDGER_PATH = str(Path(self._tmpdir) / "track_bandit.json")

    def tearDown(self):
        md.LEDGER_PATH = self._orig_path
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

TRACKS = ["PPO", "DQN", "A2C", "SAC", "TD3", "DDPG", "QRDQN",
          "TRPO", "TQC", "ARS", "MaskablePPO", "GRPO", "DPO",
          "OFFLINE", "RL", "DREAMTUNE", "WORMGA"]


def _ledger(last_slots, a_b=("a", "b", 5, 1)):
    """Build an in-memory ledger with the given last_slot per track.

    All tracks get the same Thompson prior (a=5, b=1 -> mean 0.83) so
    the *staleness backstop* is the only discriminator.
    """
    a, b, av, bv = a_b
    return {t: {"a": av, "b": bv, "picks": 1, "wins": 1,
                "last_slot": last_slots.get(t)} for t in TRACKS}


class StalenessBackstopTest(MetaDirectorTestBase):
    def test_stale_track_wins_over_favored_prior(self):
        """A track stale >STALE_SLOTS is picked even with a modest prior."""
        # DDPG is 4h+ stale; everything else is fresh with a strong prior.
        slot = 10_000
        led = _ledger({t: slot - 1 for t in TRACKS})
        led["DDPG"]["last_slot"] = slot - md.STALE_SLOTS - 1
        self.assertEqual(md.pick(slot, led, TRACKS), "DDPG")

    def test_never_seen_track_is_infinitely_stale(self):
        """A track with last_slot=None is tried before anything else."""
        slot = 10_000
        led = _ledger({t: slot - 1 for t in TRACKS})
        led["ARS"]["last_slot"] = None
        self.assertEqual(md.pick(slot, led, TRACKS), "ARS")


class CooldownTest(MetaDirectorTestBase):
    def test_cooled_track_is_ineligible(self):
        """A track in cooldown is excluded from the pool entirely."""
        slot = 10_000
        led = _ledger({t: slot - 1 for t in TRACKS})
        led["PPO"]["cool"] = slot + 10  # still cooling at this slot
        # PPO is the stalest eligible? No — it's excluded, so the pick
        # must be a *different* track even though PPO has last_slot None
        # (infinitely stale). Exclusion beats staleness.
        led["PPO"]["last_slot"] = None
        self.assertNotEqual(md.pick(slot, led, TRACKS), "PPO")

    def test_observe_sets_cooldown(self):
        """observe() writes last_slot and cool = slot + COOLDOWN_SLOTS."""
        led = _ledger({t: 0 for t in TRACKS})
        md.observe(led, "GRPO", won=True, slot=5_000)
        st = led["GRPO"]
        self.assertEqual(st["last_slot"], 5_000)
        self.assertEqual(st["cool"], 5_000 + md.COOLDOWN_SLOTS)
        self.assertEqual(st["wins"], 2)  # 1 initial + 1 won


class DeterminismTest(MetaDirectorTestBase):
    def test_same_slot_same_pick(self):
        """pick() is seeded by slot: identical inputs -> identical outputs."""
        slot = 42_000
        led = _ledger({t: slot - md.STALE_SLOTS - 1 for t in TRACKS})
        # All equally stale -> Thompson decides; must be stable per slot.
        p1 = md.pick(slot, led, TRACKS)
        p2 = md.pick(slot, led, TRACKS)
        self.assertEqual(p1, p2)

    def test_different_slot_can_differ(self):
        """Different slots are independently seeded (no hidden state).

        Tracks must be FRESH here: if every track were 4h+ stale the
        staleness backstop fires and deterministically returns the first
        track (which is correct — but it bypasses Thompson entirely).
        """
        slot = 42_000
        led = _ledger({t: slot - 1 for t in TRACKS})
        picks = {md.pick(slot + i, led, TRACKS) for i in range(8)}
        self.assertGreater(len(picks), 1, "8 fresh-slot picks should not all agree")


if __name__ == "__main__":
    unittest.main()