# coding: utf-8
"""The AOA panel's routing + provenance telemetry, and the durability trap.

Added 2026-09-14 alongside elastic routing. The panel gained two lines:
`ROUTE` (what the router can address, and what it just chose) and `PROV`
(contract violations, and whether learned state is durable).

This needs its own test because the obvious implementation returns a
*plausible* wrong answer rather than an obvious one. `CrossArmRouter` writes
each outcome back into the spine's Q table, and
`spine_registry.get_q_weights()` returns the live in-process dict -- which,
seconds after a write-back, contains that write. So answering "how much has
the router learned?" from it reports 1 and reads like success, while the
value evaporates at process exit because nothing called
`save_spine_state()`.

Measured on this machine 2026-09-14: the persisted Q table held 5 `route:`
keys and **zero** `aoa_route:` keys, and no file under `~/.azazael/`
mentioned `aoa_route` at all -- while a routed call in-process reported two
successful write-backs. Both numbers were true; only one was durable.

So durability is read from the saved snapshot on disk, the in-process figure
is reported beside it and labelled, and a write-back that has not been saved
is reported as a GAP rather than as "0 learned" (which reads as "nothing
has run"). These tests pin all of that, plus the wallboard's standing
promise that telemetry can never break the frame.
"""
import os
import re
import sys
import time
import unittest
from datetime import datetime

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AOA = os.path.join(_project_root, "src", "AOA")
for _p in (_project_root, _AOA, os.path.join(_project_root, "src"),
           os.path.join(_project_root, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aoa_monitor as am  # noqa: E402

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip(text):
    return _ANSI.sub("", text)


class _EmptyRegistry:
    @property
    def arms(self):
        return {}

    def list_arms(self):
        return []

    def get_arm(self, arm_id):
        return None


class _BrokenRegistry:
    """Every access fails -- the probe must still return, not raise."""

    @property
    def arms(self):
        raise RuntimeError("registry unavailable")

    def list_arms(self):
        raise RuntimeError("registry unavailable")

    def get_arm(self, arm_id):
        raise RuntimeError("registry unavailable")


def _state(**overrides):
    """A real AOAState with the 10 s arm bootstrap skipped."""
    st = am.AOAState.__new__(am.AOAState)
    st.registry = _EmptyRegistry()
    st.instances = {}
    st.init_results = {}
    st.arm_meta = {}
    st.cap_ids = {}
    st.startup_elapsed = 0.0
    st.last_probe = time.time()
    st.arm_status = {}
    st.workflow = {}
    st.wf_history = []
    st.routing = {}
    st.__dict__.update(overrides)
    return st


def _frame(routing, width=220):
    """Render one frame and return it as plain text (ANSI stripped).

    `render` returns a single already-joined string, not a list of lines.
    Joining it again walks it character by character, which turns every escape
    sequence into one character per line and makes every `assertIn` fail for a
    reason that has nothing to do with the assertion.

    Width is wide by default so `_fit` cannot truncate the segment under test:
    a truncated tail would also make `assertIn` fail for the wrong reason.
    """
    st = _state(routing=routing)
    return _strip(am.render(st, datetime.now(), width))


class TestPersistGapIsVisible(unittest.TestCase):
    """A write-back that has not been saved must not read as 'learning'."""

    def test_unsaved_write_backs_are_reported_as_a_gap(self):
        txt = _frame({"caps": 63, "pairs": 71, "pick_cap": "get_status",
                      "pick_arm": "azazael", "pick_reason": "cold_start",
                      "pick_measured": 0, "route_arm": "perfmon",
                      "route_status": "ok", "route_ms": 102.0,
                      "write_backs": 3, "inproc": 3, "learned": 0,
                      "contract": {"violations": 1, "checked": 1,
                                   "clean": False}})
        self.assertIn("PERSIST GAP", txt)
        self.assertIn("3 write(s) not saved", txt)
        self.assertIn("no save_spine_state", txt)

    def test_durable_learning_is_reported_without_a_gap(self):
        txt = _frame({"caps": 63, "write_backs": 5, "inproc": 5, "learned": 5,
                      "contract": {"violations": 0, "checked": 5,
                                   "clean": True}})
        self.assertIn("learned 5 durable / 5 in-process", txt)
        self.assertNotIn("PERSIST GAP", txt)

    def test_in_process_and_durable_are_never_conflated(self):
        """The two figures stay distinct even when they differ."""
        txt = _frame({"caps": 63, "write_backs": 4, "inproc": 4, "learned": 1,
                      "contract": {}})
        self.assertIn("learned 1 durable / 4 in-process", txt)

    def test_idle_is_named_rather_than_shown_as_zero(self):
        txt = _frame({"caps": 63, "write_backs": 0, "inproc": 0, "learned": 0,
                      "contract": {}})
        self.assertIn("idle: no routing run yet", txt)
        self.assertNotIn("PERSIST GAP", txt)

    def test_unreadable_durable_state_is_not_reported_as_zero_learned(self):
        """'Could not read' is not the same claim as 'learned nothing'."""
        txt = _frame({"caps": 63, "write_backs": 2, "inproc": 2,
                      "learned": None, "contract": {}})
        self.assertIn("state unreadable", txt)
        self.assertNotIn("learned 0 durable", txt)


class TestFrameSurvivesBadTelemetry(unittest.TestCase):

    def test_probe_error_renders_as_a_line_not_a_traceback(self):
        txt = _frame({"error": "ledger exploded"})
        self.assertIn("probe error: ledger exploded", txt)
        # The frame is still complete: the arm table and session line survive.
        self.assertIn("ARMS", txt)
        self.assertIn("SESS", txt)

    def test_missing_telemetry_renders_no_route_lines(self):
        txt = _frame({})
        self.assertNotIn("PERSIST GAP", txt)
        self.assertNotIn("probe error", txt)

    def test_routing_probe_degrades_gracefully_instead_of_raising(self):
        """Wallboard rule: telemetry must never cost the panel its frame.

        A registry that raises on every access is handled *inside* the router
        (`capability_index` and `_registry_key` both guard), so the probe
        reports zero addressable capabilities and a failed route rather than
        an error key. Graceful degradation is the better outcome; this test
        pins it so a future change cannot quietly convert it into an
        exception that takes the frame down.
        """
        st = _state(registry=_BrokenRegistry())
        out = am.AOAState._routing_probe(st)
        self.assertIsInstance(out, dict)
        self.assertNotIn("error", out)
        self.assertEqual(out["caps"], 0)
        self.assertEqual(out["pick_reason"], "no_candidate")
        self.assertEqual(out["route_status"], "error")

    def test_routing_probe_still_returns_when_the_spine_is_unavailable(self):
        """Losing the ledger must cost learning telemetry, not the probe."""
        st = _state(registry=_EmptyRegistry())
        out = am.AOAState._routing_probe(st)
        self.assertIsInstance(out, dict)
        self.assertIn("caps", out)


class TestLabelsDoNotCollide(unittest.TestCase):
    """Two true numbers must not share one noun.

    `ROUTES` reports the sum of per-arm capability counts -- 71 slots across
    9 arms. The new line reports distinct capability names -- 63. Labelling
    both "caps" made two correct figures read as one wrong one.
    """

    def _src(self):
        with open(os.path.join(_AOA, "aoa_monitor.py"), encoding="utf-8") as fh:
            return fh.read()

    def test_slot_count_is_labelled_slots(self):
        src = self._src()
        self.assertIn("slots {_live_caps} live", src)
        self.assertNotIn('f"caps {_live_caps} live', src)

    def test_distinct_count_is_labelled_distinct(self):
        self.assertIn("distinct caps", self._src())


class TestDurabilityIsReadFromDisk(unittest.TestCase):
    """Source guard for the honesty invariant.

    Reading `get_q_weights()` here would return the in-process dict, which
    contains the write-back performed moments earlier in the same probe.
    """

    def _probe_src(self):
        with open(os.path.join(_AOA, "aoa_monitor.py"), encoding="utf-8") as fh:
            src = fh.read()
        start = src.index("def _routing_probe")
        return src[start:src.index("def _one_status", start)]

    def test_durable_count_comes_from_the_saved_snapshot(self):
        body = self._probe_src()
        self.assertIn("spine_state.json", body)
        self.assertIn('_raw.count("aoa_route:")', body)

    def test_durable_count_is_not_taken_from_the_live_q_dict(self):
        body = self._probe_src()
        # The live dict may be read ONLY to populate the labelled in-process
        # figure -- never to populate `learned`.
        self.assertNotIn('out["learned"] = sum', body)
        self.assertIn('out["inproc"] = sum', body)

    def test_the_probe_persists_the_write_back_it_measures(self):
        """The PROV line's own gap must be closed in code, not painted over.

        `save_spine_state` was called from NO AOA consumer: the spine cycle
        persists in a different process and never sees an Arm outcome, so the
        write-back the probe performs had no route to disk and the line
        reported a permanent PERSIST GAP. The probe now saves only when a
        write actually landed, so an idle refresh still costs nothing.
        """
        body = self._probe_src()
        self.assertIn("save_spine_state", body)
        self.assertIn('out["persisted"]', body)

    def test_the_machine_now_has_durable_aoa_route_keys(self):
        """Pins the finding, not just the code path.

        UPDATED DELIBERATELY, 2026-09-15: the tripwire fired. This test used to
        assert ``raw.count("aoa_route:") == 0`` — that routing write-backs were
        NOT durable — and it now finds 2 such keys in
        ``~/.azazael/spine_state.json``. That is the fix landing, not a
        regression: AOA routing outcomes are being saved, so the PROV line's
        expectation has to move with it (the probe itself computes `learned`
        from the snapshot, so the display was already correct — it was only
        this pinned finding that had gone stale).

        It is inverted rather than deleted so the opposite drift is caught too:
        if routing learning ever stops being durable, this fails again.
        """
        state = os.path.join(os.path.expanduser("~"), ".azazael",
                             "spine_state.json")
        if not os.path.exists(state):
            self.skipTest("spine_state.json not present")
        with open(state, encoding="utf-8", errors="ignore") as fh:
            raw = fh.read()
        self.assertIn("route:", raw,
                      "the persisted table should carry the pre-existing route keys")
        self.assertGreater(
            raw.count("aoa_route:"), 0,
            "aoa_route learning is no longer durable -- the PERSIST GAP "
            "finding is live again, so the PROV line and this test both need "
            "to move back")


if __name__ == "__main__":
    unittest.main()
