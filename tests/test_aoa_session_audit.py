# coding: utf-8
"""The AOA session audit trail, and the timestamp contract that feeds it.

`SessionManager` shipped with a working `save_session()` that nothing ever
called, so `~/.aoa/sessions/` did not exist and the AOA panel correctly
reported `sessions DIR MISSING` — for a subsystem that had never recorded
anything. The benchmark workflow now records a real session: arm-init
outcomes, every workflow step with its measured duration, and the verdict.

Recording a duration first required a small fix in the router: it stamped
`started_at` / `completed_at` on each step but did not return them, so every
step would have been logged as `0.0 ms` — which reads as *instant* rather than
*unmeasured*. These tests pin both halves.
"""
import json
import os
import sys
import tempfile
import unittest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AOA = os.path.join(_project_root, "src", "AOA")
for _p in (_project_root, _AOA, os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class _FakeArm:
    is_initialized = True

    def __init__(self, body=None):
        self._body = body or {"status": "ok", "data": {"ok": True}}

    def execute(self, capability, payload=None):
        return dict(self._body)


class _FakeRegistry:
    def __init__(self, arms):
        self._arms = arms

    def get_arm(self, arm_id):
        return self._arms.get(arm_id)


class _FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)
        return event


class TestRouterReturnsStepTimestamps(unittest.TestCase):
    """Without these, a recorded duration can only be a lie or a zero."""

    def _run(self):
        from core.router import CrossArmRouter, WorkflowStep
        registry = _FakeRegistry({"probe": _FakeArm()})
        bus = _FakeBus()
        router = CrossArmRouter(registry, bus)
        wf = router.create_workflow(name="t")
        wf.add_step(WorkflowStep(step_id="s1", arm_id="probe",
                                 capability="get_status", payload={}))
        return router.execute_workflow(wf.workflow_id), bus

    def test_each_result_carries_start_and_complete(self):
        results, _ = self._run()
        self.assertEqual(len(results), 1)
        r = results[0]
        for key in ("started_at", "completed_at"):
            self.assertIn(key, r, "router must return %s for duration to exist" % key)
            self.assertTrue(r[key], "%s must not be empty" % key)

    def test_the_timestamps_are_ordered_and_parseable(self):
        from datetime import datetime
        results, _ = self._run()
        r = results[0]
        start = datetime.fromisoformat(r["started_at"])
        done = datetime.fromisoformat(r["completed_at"])
        self.assertGreaterEqual(done, start)

    def test_status_and_identity_are_still_there(self):
        results, _ = self._run()
        r = results[0]
        self.assertEqual(r["step_id"], "s1")
        self.assertEqual(r["arm_id"], "probe")
        self.assertEqual(r["capability"], "get_status")
        self.assertEqual(r["status"], "completed")


class TestSessionManagerWritesRealSessions(unittest.TestCase):
    def _mgr(self, tmp):
        from core.session_manager import SessionManager
        return SessionManager(storage_dir=tmp)

    def test_save_creates_the_directory_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "sessions")
            self.assertFalse(os.path.exists(store))
            mgr = self._mgr(store)
            sess = mgr.create_session(name="audit", tags=["t"])
            mgr.record_milestone("arm_a", "initialize", status=None)
            mgr.complete_session()
            path = mgr.save_session(sess.session_id)
            self.assertTrue(os.path.exists(path),
                            "save_session must create ~/.aoa/sessions if absent")

            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["session_id"], sess.session_id)
            self.assertEqual(doc["status"], "completed")
            self.assertTrue(doc["completed_at"])
            self.assertEqual(len(doc["milestones"]), 1)

            back = self._mgr(store).load_session(sess.session_id)
            self.assertEqual(len(back.milestones), 1)

    def test_a_failed_milestone_is_recorded_as_failed(self):
        from core.session_manager import MilestoneStatus
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._mgr(tmp)
            mgr.create_session()
            mgr.record_milestone("arm_b", "initialize",
                                 status=MilestoneStatus.FAILED)
            doc = mgr.get_active_session().to_dict()
            self.assertEqual(doc["milestones"][0]["status"], "failed")

    def test_durations_survive_the_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._mgr(tmp)
            sess = mgr.create_session()
            mgr.record_milestone("arm_c", "get_status", duration_ms=4748.9)
            mgr.complete_session()
            path = mgr.save_session(sess.session_id)
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertAlmostEqual(doc["milestones"][0]["duration_ms"], 4748.9, places=1)


class TestTheWorkflowActuallyRecordsItsRun(unittest.TestCase):
    """The interesting half is that it is *wired*, since the heavy run cannot
    be a unit test. A missing call is exactly how the dir stayed empty."""

    def _src(self):
        with open(os.path.join(_AOA, "test_benchmark_workflow.py"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_it_imports_and_uses_the_session_manager(self):
        src = self._src()
        self.assertIn("get_session_manager", src)
        self.assertIn("save_session", src,
                      "recording without saving leaves the dir empty again")
        self.assertIn("complete_session", src)

    def test_it_records_init_outcomes_and_every_step(self):
        src = self._src()
        self.assertIn("MilestoneStatus.FAILED", src,
                      "a failed init/step must be recordable as a failure")
        self.assertIn("benchmark_verdict", src,
                      "the run must close with its verdict")

    def test_a_save_failure_is_reported_not_swallowed(self):
        src = self._src()
        self.assertIn("session save FAILED", src)

    def test_it_uses_the_router_timestamps_for_duration(self):
        src = self._src()
        self.assertIn("completed_at", src)
        self.assertIn("started_at", src)


class TestBusDoesNotSwallowSubscriberErrors(unittest.TestCase):
    """`publish()` used `except: pass`, so a subscriber that raised was
    silently never delivered to — a broken consumer was indistinguishable from
    one that legitimately did nothing, with 9 arms subscribed."""

    def _bus(self):
        from core.event_bus import EventBus
        return EventBus()

    def test_a_raising_subscriber_is_counted(self):
        from core.event_bus import AOEvent
        bus = self._bus()
        bus.subscribe("workflow.*", lambda e: (_ for _ in ()).throw(
            ValueError("boom")))
        bus.publish(AOEvent(event_type="workflow.started", source_arm="t"))
        self.assertEqual(bus.callback_error_count, 1)

    def test_the_error_names_the_callback_and_the_pattern(self):
        from core.event_bus import AOEvent
        bus = self._bus()

        def broken(e):
            raise KeyError("nope")

        bus.subscribe("workflow.*", broken)
        bus.publish(AOEvent(event_type="workflow.started", source_arm="t"))
        rec = bus.get_callback_errors()[0]
        self.assertEqual(rec["event_type"], "workflow.started")
        self.assertEqual(rec["pattern"], "workflow.*")
        self.assertIn("broken", rec["callback"])
        self.assertIn("KeyError", rec["error"])

    def test_one_bad_subscriber_does_not_block_the_others(self):
        """Isolation must be preserved: counting the failure cannot turn a
        previously-surviving publisher into a raising one."""
        from core.event_bus import AOEvent
        bus = self._bus()
        seen = []
        bus.subscribe("*", seen.append)
        bus.subscribe("*", lambda e: 1 / 0)
        bus.publish(AOEvent(event_type="x", source_arm="t"))
        self.assertEqual(len(seen), 1, "the good subscriber must still be called")
        self.assertEqual(bus.callback_error_count, 1)

    def test_a_clean_bus_reports_zero(self):
        from core.event_bus import AOEvent
        bus = self._bus()
        bus.subscribe("*", lambda e: None)
        bus.publish(AOEvent(event_type="x", source_arm="t"))
        self.assertEqual(bus.callback_error_count, 0)


if __name__ == "__main__":
    unittest.main()
