# coding: utf-8
"""AOA panel + data-refresh honesty guards.

Three defects of one shape, all confirmed live on 2026-09-14:

* **The SESS line printed a literal ladder**
  (``INIT>DISCOVERY>INITIALIZE>READY``), so it claimed READY even when arm
  init had failed — a constant dressed as state.
* **The milestone count carried no age.** ``AOA_MILESTONE.json`` had no
  producer at all and was 75.7 days old while the panel read as "now".
  Fixing that exposed a second bug: once the file *does* have a producer, its
  mtime is the refresh time, not the age of the history inside it, so the age
  has to come from the milestone rows' own timestamps.
* **A missing ``~/.aoa/sessions`` dir rendered as "0 sessions"** — an
  unreadable number presented as a zero. Nothing had ever written there.

``refresh_aoa_data.py`` then had to draw a line the hard way: descriptive
fields are repopulated from the live system, but the *baselines* those fields
are compared against are never rewritten, because overwriting a baseline with
the value it measures manufactures agreement instead of reporting it.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AOA = os.path.join(_project_root, "src", "AOA")
for _p in (_project_root, _AOA, os.path.join(_project_root, "src"),
           os.path.join(_project_root, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aoa_monitor as am            # noqa: E402
import refresh_aoa_data as rd       # noqa: E402


class TestAgeFormatting(unittest.TestCase):
    def test_none_is_dashes(self):
        self.assertEqual(am._age_txt(None), "--")

    def test_units_scale(self):
        now = time.time()
        self.assertTrue(am._age_txt(now - 30).endswith("s"))
        self.assertTrue(am._age_txt(now - 300).endswith("m"))
        self.assertTrue(am._age_txt(now - 7200).endswith("h"))

    def test_long_ages_switch_to_days(self):
        """75.7 days must not render as '1817.0h'."""
        age = am._age_txt(time.time() - 75.7 * 86400)
        self.assertTrue(age.endswith("d"), age)
        self.assertAlmostEqual(float(age[:-1]), 75.7, delta=0.2)


class TestIsoToEpochIsATimestamp(unittest.TestCase):
    """`_age_txt` subtracts `time.time()` itself, so passing it a *duration*
    double-subtracts and reports a date in 1970. Pin the contract."""

    def test_returns_epoch_not_duration(self):
        iso = "2026-06-30T20:14:03.405204"
        ep = am._iso_to_epoch(iso)
        self.assertAlmostEqual(ep, datetime.fromisoformat(iso).timestamp(), delta=1)

    def test_age_of_a_timestamp_is_computed_once(self):
        iso = (datetime.now() - timedelta(days=10)).isoformat()
        age = am._age_txt(am._iso_to_epoch(iso))
        self.assertTrue(age.endswith("d"), age)
        self.assertAlmostEqual(float(age[:-1]), 10.0, delta=0.1)

    def test_garbage_is_none_not_zero(self):
        self.assertIsNone(am._iso_to_epoch("not-a-date"))
        self.assertIsNone(am._iso_to_epoch(None))


class TestSessionLineStopsOverclaiming(unittest.TestCase):
    """Source-level guards: every one of these was a live defect."""

    def _src(self):
        with open(os.path.join(_AOA, "aoa_monitor.py"), encoding="utf-8") as f:
            return f.read()

    def test_no_literal_lifecycle_ladder(self):
        src = self._src()
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            self.assertNotIn("INIT>DISCOVERY>INITIALIZE>READY", code,
                             "hardcoded phase ladder returned: %s" % line.strip())

    def test_phase_is_derived_from_init_results(self):
        src = self._src()
        self.assertIn("_phase =", src)
        self.assertIn("DEGRADED", src,
                      "init failure must be able to render as a non-READY phase")

    def test_missing_sessions_dir_is_not_reported_as_zero(self):
        src = self._src()
        self.assertIn("sessions DIR MISSING", src)
        self.assertIn("sessions_dir", src)

    def test_milestone_age_comes_from_content_not_mtime(self):
        """A refresh rewrites the file; the history inside it stays old."""
        src = self._src()
        self.assertIn("ms_newest_ts", src)
        render = src.split("def render(")[-1]
        self.assertNotIn("_age_txt(f.get(\"ms_age\"))", render,
                         "history age must not be the file mtime")

    def test_files_exposes_content_age(self):
        st = am.AOAState.__new__(am.AOAState)   # no bootstrap, no registry
        f = am.AOAState.files(st)
        if f.get("ms_newest"):
            self.assertIsNotNone(f.get("ms_newest_ts"))
            self.assertGreater(f["ms_newest_ts"], 0)
        self.assertIn("sessions_dir", f)


def _milestone_doc():
    return {
        "project": "AOA", "version": "1.1.0", "date": "2026-06-30",
        "architecture": {
            "core_modules": 2,
            "adapter_arms": 1,
            "modules": [{"name": "alpha.py", "lines": 1, "purpose": "x",
                         "path": "core/alpha.py"}],
            "arms": [{"id": "azazael", "name": "old name", "category": "quartet",
                      "priority": 1, "capabilities": 0}],
        },
        "cross_arm_routes": {"total_routes": 56},
        "session": {"last_session_id": "aoa-sess-002", "milestone_count": 2,
                    "milestones": [{"milestone_id": "ms-002", "arm_id": "aoa",
                                    "action": "generate_html", "status": "completed",
                                    "timestamp": "2026-06-30T20:14:03.405204"},
                                   {"milestone_id": "ms-003", "arm_id": "codex",
                                    "action": "add_css", "status": "completed",
                                    "timestamp": "2026-06-30T18:00:00"}]},
    }


def _benchmark_doc():
    return {
        "perfmon": {"cpu_percent": 22.1, "memory_percent": 60.6,
                    "memory_available_gb": 6.1, "disk_percent": 95.5,
                    "disk_free_gb": 33.8, "cpu_count": 16},
        "azazael": {"total_worms": 108, "initialized": True},
        "system": {"python_version": "3.14.6", "free_threaded": True,
                   "os": "Windows", "node": "OLD-NODE", "cpu_count": 16},
        "eve": {"benchmark_date": "2026-08-27", "min_gguf_tps": 25.0,
                "best_gguf_model": "gemma-4-E2B-it (Q4_K_M)",
                "best_gguf_tps": 28.16, "discovery_gguf": 6},
    }


class _TmpTree(unittest.TestCase):
    """Redirect the refresh tool at a temp tree with a fake live system."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        os.makedirs(os.path.join(self.base, "core"))
        with open(os.path.join(self.base, "core", "alpha.py"), "w", encoding="utf-8") as fh:
            fh.write("a = 1\nb = 2\nc = 3\n")          # 3 lines, was 1

        self.milestone = os.path.join(self.base, "AOA_MILESTONE.json")
        self.benchmark = os.path.join(self.base, "benchmark_data.json")
        with open(self.milestone, "w", encoding="utf-8") as fh:
            json.dump(_milestone_doc(), fh)
        with open(self.benchmark, "w", encoding="utf-8") as fh:
            json.dump(_benchmark_doc(), fh)

        from pathlib import Path
        self._saved = (rd.BASE, rd.MILESTONE, rd.BENCHMARK)
        rd.BASE = Path(self.base)
        rd.MILESTONE = Path(self.milestone)
        rd.BENCHMARK = Path(self.benchmark)
        self._saved_live = (rd.live_arms, rd.live_worms, rd.live_perfmon)
        rd.live_arms = lambda: [{"arm_id": "azazael", "name": "Azazael Neural Mesh",
                                 "category": "quartet", "priority": 10,
                                 "capabilities": 4},
                                {"arm_id": "kali", "name": "Kali Toolchain",
                                 "category": "support", "priority": 6,
                                 "capabilities": 40}]
        rd.live_worms = lambda: 226
        rd.live_perfmon = lambda: {"cpu_percent": 31.5, "memory_percent": 88.0,
                                   "memory_available_gb": 1.2, "disk_percent": 87.7,
                                   "disk_free_gb": 93.2, "cpu_count": 16}

    def tearDown(self):
        rd.BASE, rd.MILESTONE, rd.BENCHMARK = self._saved
        rd.live_arms, rd.live_worms, rd.live_perfmon = self._saved_live
        self.tmp.cleanup()

    def _ms(self):
        with open(self.milestone, encoding="utf-8") as fh:
            return json.load(fh)

    def _bm(self):
        with open(self.benchmark, encoding="utf-8") as fh:
            return json.load(fh)


class TestDescriptiveFieldsAreRepopulated(_TmpTree):
    def test_module_line_counts_refresh(self):
        rd.refresh_milestone()
        mods = self._ms()["architecture"]["modules"]
        self.assertEqual(mods[0]["lines"], 3)

    def test_arm_roster_comes_from_the_registry(self):
        rd.refresh_milestone()
        arch = self._ms()["architecture"]
        self.assertEqual(arch["adapter_arms"], 2)
        self.assertEqual([a["id"] for a in arch["arms"]], ["azazael", "kali"])
        self.assertEqual(arch["arms"][0]["name"], "Azazael Neural Mesh")
        self.assertEqual(arch["arms"][0]["capabilities"], 4)

    def test_worm_count_recorded(self):
        rd.refresh_milestone()
        self.assertEqual(self._ms()["session"]["worm_count"], 226)

    def test_live_machine_readings_refresh(self):
        rd.refresh_benchmark()
        perf = self._bm()["perfmon"]
        self.assertEqual(perf["cpu_percent"], 31.5)
        self.assertEqual(perf["disk_percent"], 87.7)


class TestBaselinesAreNotRewritten(_TmpTree):
    """The whole point of a baseline is that the live value can disagree."""

    def test_worm_baseline_survives_the_refresh(self):
        rd.refresh_benchmark()
        az = self._bm()["azazael"]
        self.assertEqual(az["total_worms"], 108,
                         "the drift reference must not absorb the live value")
        self.assertEqual(az["live_worm_count"], 226,
                         "the live value belongs beside the baseline, not over it")

    def test_eve_thresholds_survive(self):
        before = _benchmark_doc()["eve"]
        rd.refresh_benchmark()
        self.assertEqual(self._bm()["eve"], before)

    def test_the_stamp_names_the_baseline_date_and_freezes(self):
        rd.refresh_benchmark()
        meta = self._bm()["_meta"]
        self.assertEqual(meta["baseline_date"], "2026-08-27")
        self.assertIn("azazael.total_worms", meta["baseline_keys_not_rewritten"])
        self.assertIn("eve.min_gguf_tps", meta["baseline_keys_not_rewritten"])

    def test_recorded_history_is_untouched(self):
        before = _milestone_doc()["session"]["milestones"]
        rd.refresh_milestone()
        after = self._ms()["session"]["milestones"]
        self.assertEqual(after, before,
                         "real work logs must survive a descriptive refresh")

    def test_provenance_says_what_was_left_alone(self):
        rd.refresh_milestone()
        prov = self._ms()["provenance"]
        self.assertIn("session.milestones", prov["recorded_history_untouched"])


class TestFailedReadingsAreNotRecorded(_TmpTree):
    def test_zero_perfmon_reading_does_not_overwrite(self):
        """An uninitialised arm answers with zeros; zeros are not evidence."""
        rd.live_perfmon = lambda: {"cpu_percent": 0, "memory_percent": 0,
                                   "memory_available_gb": 0.0}
        rd.refresh_benchmark()
        perf = self._bm()["perfmon"]
        self.assertEqual(perf["cpu_percent"], 22.1)
        self.assertEqual(perf["memory_percent"], 60.6)
        self.assertEqual(perf["memory_available_gb"], 6.1)

    def test_version_drift_is_recorded_when_real(self):
        rd.refresh_benchmark()
        self.assertEqual(self._bm()["system"]["python_version"],
                         "%d.%d.%d" % sys.version_info[:3])


class TestCheckModeWritesNothing(_TmpTree):
    def test_check_reports_but_leaves_files_alone(self):
        before_ms = self._ms()
        before_bm = self._bm()
        ch = rd.refresh_milestone(check_only=True)
        ch += rd.refresh_benchmark(check_only=True)
        self.assertTrue(ch, "drift exists in the fixture, so check must report it")
        self.assertEqual(self._ms(), before_ms)
        self.assertEqual(self._bm(), before_bm)


class TestExMachinaAbsentArtifacts(unittest.TestCase):
    """The reference behaviour: a subsystem that was never wired must not be
    reported as one that stopped."""

    def test_absent_file_is_omitted_not_infinite(self):
        import exmachina_live_monitor as em
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "fitness_oracle.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            saved = em._EX_ARTIFACTS, em._EX_MORPH, em._EX_STATE
            em._EX_ARTIFACTS = (("oracle", "fitness_oracle.json"),
                                ("gate", "fitness_oracle.last.json"))
            em._EX_MORPH = os.path.join(tmp, "nope.jsonl")
            em._EX_STATE = tmp
            try:
                ages = em.ex_ages()
            finally:
                em._EX_ARTIFACTS, em._EX_MORPH, em._EX_STATE = saved
        self.assertIn("oracle", ages)
        self.assertNotIn("gate", ages,
                         "a missing artifact must be omitted, not reported as ancient")


if __name__ == "__main__":
    unittest.main()
