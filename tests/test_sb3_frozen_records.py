"""Frozen-record seam for SB3Agent.train (#850 acceptance).

Proves caller-supplied records reach training WITHOUT store
extraction and WITHOUT any real training run (fake algo class).
No live store reads, no GPU.
"""
import unittest

try:
    import stable_baselines3  # noqa: F401
    HAS_SB3 = True
except ImportError:
    HAS_SB3 = False


FROZEN = [
    {"severity": "warning",
     "data": {"success": False, "hook": "probe",
              "subject": "s1", "ok": False},
     "event_type": "hook:probe"},
    {"severity": "info",
     "data": {"success": True, "hook": "probe",
              "subject": "s2", "ok": True},
     "event_type": "hook:probe"},
]


class FakeAlgo:
    """Stands in for the SB3 algorithm class: instant, no training."""
    seen_envs = []

    def __init__(self, policy, env, **kwargs):
        FakeAlgo.seen_envs.append(env)
        self._env = env

    def learn(self, **kwargs):
        FakeAlgo.learned = True

    def set_env(self, env):
        self._env = env


class TestFrozenRecords(unittest.TestCase):
    def setUp(self):
        if not HAS_SB3:
            self.skipTest("SB3 not installed")
        import os
        os.environ["AZAZAEL_GIL_ENFORCED"] = "1"
        FakeAlgo.seen_envs = []
        FakeAlgo.learned = False

    def _agent(self):
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        prop = SB3Agent.__dict__.get("algo_class")
        type(agent).algo_class = FakeAlgo
        self.addCleanup(setattr, type(agent), "algo_class", prop)
        return agent

    def test_frozen_records_skip_extraction(self):
        agent = self._agent()

        def no_pipeline():
            raise AssertionError("extraction must be skipped")

        agent._get_pipeline = no_pipeline
        out = agent.train(records=list(FROZEN), register_model=False,
                          total_timesteps=4)
        self.assertTrue(FakeAlgo.learned)
        self.assertEqual(len(FakeAlgo.seen_envs), 1)
        self.assertTrue(out.get("trained", False))

    def test_empty_records_returns_no_data_error(self):
        agent = self._agent()

        def no_pipeline():
            raise AssertionError("extraction must be skipped")

        agent._get_pipeline = no_pipeline
        out = agent.train(records=[])
        self.assertFalse(out.get("trained", True))
        self.assertIn("error", out)

    def test_none_records_still_extracts(self):
        agent = self._agent()
        calls = {}

        class FakePipeline:
            def extract_from_memory(self, **kwargs):
                calls["kwargs"] = kwargs
                return []

        agent._get_pipeline = lambda: FakePipeline()
        out = agent.train(records=None, source="probe")
        self.assertFalse(out.get("trained", True))
        self.assertEqual(calls["kwargs"].get("source"), "probe")


if __name__ == "__main__":
    unittest.main()
