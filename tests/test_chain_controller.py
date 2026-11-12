"""Contracts for the clean replacement chain controller."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import chain_controller as controller  # noqa: E402


class TestChainController(unittest.TestCase):
    def test_job_table_contains_current_meta_and_training_jobs(self):
        self.assertIn("meta", controller.JOBS)
        self.assertIn("train", controller.JOBS)
        self.assertEqual(controller.JOBS["meta"][3], "meta_evolution_cycle.py")
        # Operator decision 2026-09-25: meta is AUTHORIZED to mutate. With
        # apply pinned off (the old assertion here) the self-modification loop
        # sat on verdict=DRY every cycle (FORENSIC 2026-09-24 PART 8, Layer 2).
        self.assertEqual(controller.JOBS["meta"][4], ("--apply",))
        # The spine cycle restores write authority (self-image gate self-heal)
        # and mesh is the fleet perception loop; both were dropped from the
        # replacement chain and are pinned back in (PART 8, Layer 3).
        self.assertIn("spine", controller.JOBS)
        self.assertIn("mesh", controller.JOBS)
        self.assertEqual(controller.JOBS["spine"][3], "spine_cycle_apply.py")
        self.assertEqual(controller.JOBS["mesh"][3], "run_mesh_cycle.py")
        self.assertIn("meta", controller.MUTATING_JOBS)
        self.assertIn("spine", controller.MUTATING_JOBS)
        self.assertNotIn("mesh", controller.MUTATING_JOBS)

    def test_render_cadence_covers_slowest_input(self):
        """T-4 (2026-10-19): a render fires after its slowest input refreshes.

        The `render` job draws wallboards from state written on the
        `evolution` (600 s) and `terence` (1800 s) cadences. At 300 s it
        produced five pages per terence refresh, four drawing identical
        input. The cadence is pinned as a DERIVATION over this table, not a
        constant, so slowing an input cadence later without following it here
        fails this test instead of silently re-introducing the online loop
        (from the TODO_315T_BUILD T-4 disposition, EXECUTABLE #6).
        """
        inputs = {"evolution", "terence"}
        for name in inputs:
            self.assertIn(name, controller.JOBS)
        slowest_input = max(controller.JOBS[name][0] for name in inputs)
        self.assertEqual(controller.JOBS["render"][0], slowest_input)
        # A cadence change must not silently absorb the per-run timeout: the
        # timeout is a per-run budget and stays 240 s.
        self.assertEqual(controller.JOBS["render"][1], 240)

    def test_status_is_read_only(self):
        state, result, detail = controller._own_task_state()
        self.assertIn(state, {"ok", "disabled", "unknown"})
        self.assertTrue(detail)
        report = controller.status()
        self.assertFalse(report["scheduler_mutated"])
        self.assertEqual(report["task"], controller.TASK_NAME)

    def test_dry_run_does_not_execute(self):
        result = controller.run_one("meta", dry_run=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["command"][1].endswith("meta_evolution_cycle.py"), True)

    def test_unknown_job_is_rejected(self):
        with self.assertRaises(ValueError):
            controller.run_one("not-a-job")

    def test_plan_is_deterministic_for_fixed_time(self):
        first = controller.plan(now=1000.0, job="meta")
        second = controller.plan(now=1000.0, job="meta")
        self.assertEqual(first, second)
        self.assertEqual(first["selected"], "meta")

    def test_observation_only_excludes_mutation_jobs(self):
        report = controller.plan(now=time.time(), observation_only=True)
        self.assertNotIn("wiring", report["due"])
        self.assertNotIn("hyperopt", report["due"])

    def test_heartbeat_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chain_controller.log"
            old = controller.HEARTBEAT_PATH
            controller.HEARTBEAT_PATH = path
            try:
                controller._heartbeat("test", selected="meta")
                row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
            finally:
                controller.HEARTBEAT_PATH = old
        self.assertEqual(row["event"], "test")
        self.assertEqual(row["selected"], "meta")

    def test_lock_refuses_live_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chain_controller.lock"
            old = controller.LOCK_PATH
            controller.LOCK_PATH = path
            path.write_text(str(controller.os.getpid()), encoding="utf-8")
            try:
                with self.assertRaises(RuntimeError):
                    with controller.controller_lock():
                        pass
            finally:
                controller.LOCK_PATH = old

    def test_lock_owner_reports_current_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chain_controller.lock"
            old = controller.LOCK_PATH
            controller.LOCK_PATH = path
            path.write_text(str(controller.os.getpid()), encoding="utf-8")
            try:
                owner = controller._lock_owner()
            finally:
                controller.LOCK_PATH = old
        self.assertEqual(owner["pid"], controller.os.getpid())
        self.assertTrue(owner["alive"])
        self.assertTrue(owner["current"])

    def test_duplicate_live_controller_exits_successfully(self):
        args = mock.Mock(
            dry_run=False, catch_up_on_start=False, interval=1.0,
            max_cycles=0, allow_mutations=False,
        )
        conflict = RuntimeError("chain controller already running (PID 123)")
        with tempfile.TemporaryDirectory() as directory:
            old = controller.HEARTBEAT_PATH
            controller.HEARTBEAT_PATH = Path(directory) / "chain_controller.log"
            try:
                with mock.patch.object(controller, "fire", side_effect=conflict):
                    result = controller._run_live(args)
                rows = [
                    json.loads(line)
                    for line in controller.HEARTBEAT_PATH.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            finally:
                controller.HEARTBEAT_PATH = old
        self.assertEqual(result, 0)
        events = [row["event"] for row in rows]
        self.assertIn("live_poll_skipped", events)
        self.assertEqual(events[-1], "live_exit")

    def test_idle_poll_preserves_last_actual_fire(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            names = ("HEALTH_PATH", "LAST_FIRE_PATH", "DUE_PATH", "PLAN_PATH")
            old = {name: getattr(controller, name) for name in names}
            try:
                controller.HEALTH_PATH = base / "health.json"
                controller.LAST_FIRE_PATH = base / "last_fire.json"
                controller.DUE_PATH = base / "due.json"
                controller.PLAN_PATH = base / "plan.json"
                result = {
                    "job": "train", "ok": True, "returncode": 0,
                    "finished_at": "2026-09-24T15:00:00",
                }
                controller._remember_fire("train", result, observation_only=False)
                controller._write_json(controller.HEALTH_PATH, {
                    "schema": "azazael.chain_controller.fire.v1",
                    "ran": False, "reason": "no due job", "selected": None,
                })
                with mock.patch.object(
                    controller, "_own_task_state", return_value=("ok", 267009, "Running")
                ):
                    report = controller.status()
            finally:
                for name, path in old.items():
                    setattr(controller, name, path)
        self.assertEqual(report["task_last_result"], 267009)
        self.assertEqual(report["last_fire"]["job"], "train")
        self.assertTrue(report["last_result"]["ok"])


if __name__ == "__main__":
    unittest.main()
