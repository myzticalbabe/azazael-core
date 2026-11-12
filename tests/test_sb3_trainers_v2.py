#!/usr/bin/env python3
"""Tests for the trainers_v2 worm-identity contract and the SB3Agent migration.

Covers (F2 closure slice):
- TrainerContext tags/payload carry worm_id
- create_trainer routing: rl/intake/offline -> RLTrainLoopV2,
  grpo -> GRPOTrainerV2, algo names -> SB3TrainerV2, unknown raises
- SB3TrainerV2 rejects unknown algos; invalid env reports cleanly;
  tiny gym env trains end-to-end with worm identity in the result
- SB3Agent default + explicit worm_id, repr, get_trainer() delegation,
  and worm_id stamped on every train() return path
- NeuralPipeline._train_sb3 and OrchestratorConfig thread worm_id
"""

import os
import sys
import inspect
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestTrainerContext(unittest.TestCase):
    def test_tags_carry_worm(self):
        from CORE.WORMS.expansion.trainers_v2 import TrainerContext
        ctx = TrainerContext(worm_id="w1", task_id="t1", task_type="rl")
        self.assertIn("worm:w1", ctx.to_tags())
        self.assertEqual(ctx.enrich_data({})["_worm_id"], "w1")
        self.assertIn("worm:w1", ctx.to_source("RL"))

    def test_session_tag_optional(self):
        from CORE.WORMS.expansion.trainers_v2 import TrainerContext
        bare = TrainerContext(worm_id="w1", task_id="t1", task_type="rl")
        self.assertFalse([t for t in bare.to_tags() if t.startswith("session:")])
        sess = TrainerContext(worm_id="w1", task_id="t1", task_type="rl",
                              session_id="s1")
        self.assertIn("session:s1", sess.to_tags())


class TestCreateTrainer(unittest.TestCase):
    def test_rl_routing(self):
        from CORE.WORMS.expansion.trainers_v2 import create_trainer, RLTrainLoopV2
        t = create_trainer("rl", worm_id="w1")
        self.assertIsInstance(t, RLTrainLoopV2)
        self.assertEqual(t.context.worm_id, "w1")

    def test_grpo_routing(self):
        from CORE.WORMS.expansion.trainers_v2 import create_trainer, GRPOTrainerV2
        t = create_trainer("grpo", worm_id="w2")
        self.assertIsInstance(t, GRPOTrainerV2)

    def test_sb3_routing_lowercase_algo(self):
        from CORE.WORMS.expansion.trainers_v2 import create_trainer, SB3TrainerV2
        for algo in ("ppo", "dqn", "maskableppo"):
            t = create_trainer(algo, worm_id="w3")
            self.assertIsInstance(t, SB3TrainerV2)
            self.assertEqual(t.context.worm_id, "w3")

    def test_unknown_raises(self):
        from CORE.WORMS.expansion.trainers_v2 import create_trainer
        with self.assertRaises(ValueError):
            create_trainer("nope", worm_id="w4")


def _tiny_env():
    """Minimal gymnasium env for the success-path test."""
    import importlib.util
    if importlib.util.find_spec("gymnasium") is None:
        return None
    import numpy as np
    import gymnasium as gym

    class TinyEnv(gym.Env):
        def __init__(self):
            super().__init__()
            self.observation_space = gym.spaces.Box(
                low=-1.0, high=1.0, shape=(2,), dtype=np.float32)
            self.action_space = gym.spaces.Discrete(2)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return np.zeros(2, dtype=np.float32), {}

        def step(self, action):
            obs = np.zeros(2, dtype=np.float32)
            return obs, 1.0, True, False, {}

    return TinyEnv()


class TestSB3TrainerV2(unittest.TestCase):
    def test_bad_algo_raises(self):
        from CORE.WORMS.expansion.trainers_v2 import TrainerContext, SB3TrainerV2
        ctx = TrainerContext(worm_id="w1", task_id="t1", task_type="ppo")
        with self.assertRaises(ValueError):
            SB3TrainerV2(ctx, "NOT_AN_ALGO")

    def test_invalid_env_reports_cleanly(self):
        from CORE.WORMS.expansion.trainers_v2 import TrainerContext, SB3TrainerV2
        ctx = TrainerContext(worm_id="w1", task_id="t1", task_type="ppo")
        t = SB3TrainerV2(ctx, "PPO")
        res = t.train(env=object(), total_timesteps=10)
        self.assertFalse(res.get("success"))
        self.assertIn("error", res)
        self.assertEqual(res.get("worm_id"), "w1")

    def test_tiny_env_trains_with_worm_identity(self):
        env = _tiny_env()
        if env is None:
            self.skipTest("gymnasium unavailable")
        from CORE.WORMS.expansion.trainers_v2 import TrainerContext, SB3TrainerV2
        ctx = TrainerContext(worm_id="w_tiny", task_id="t1", task_type="ppo")
        t = SB3TrainerV2(ctx, "PPO")
        res = t.train(env=env, total_timesteps=10)
        self.assertTrue(res.get("success"), msg=res.get("error"))
        self.assertEqual(res.get("worm_id"), "w_tiny")


class TestSB3AgentWormIdentity(unittest.TestCase):
    def test_default_worm_id(self):
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        self.assertEqual(agent.worm_id, "sb3_ppo")

    def test_explicit_worm_id(self):
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="DQN", worm_id="my_worm")
        self.assertEqual(agent.worm_id, "my_worm")

    def test_repr_includes_worm(self):
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO", worm_id="w9")
        self.assertIn("w9", repr(agent))

    def test_get_trainer_delegates(self):
        from CORE.sb3_agent import SB3Agent, HAS_TRAINERS_V2
        if not HAS_TRAINERS_V2:
            self.skipTest("trainers_v2 unavailable")
        from CORE.WORMS.expansion.trainers_v2 import SB3TrainerV2
        agent = SB3Agent(algo="PPO", worm_id="w7")
        trainer = agent.get_trainer()
        self.assertIsInstance(trainer, SB3TrainerV2)
        self.assertEqual(trainer.context.worm_id, "w7")

    def test_train_error_path_carries_worm(self):
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO", worm_id="w_err")
        res = agent.train(source="NoSuchSource", total_timesteps=10,
                          records=[], register_model=False)
        # records=[] is falsy -> "No training data" error; either way a dict.
        self.assertIsInstance(res, dict)
        if not res.get("trained"):
            self.assertEqual(res.get("worm_id"), "w_err")


class TestOrchestratorWormThreading(unittest.TestCase):
    def test_pipeline_sb3_threads_worm_id(self):
        from CORE.NeuralPipeline import NeuralPipeline
        src = inspect.getsource(NeuralPipeline._train_sb3)
        self.assertIn("worm_id", src)
        self.assertIn("SB3Agent(algo=\"PPO\", worm_id=worm_id)", src)

    def test_orchestrator_config_has_worm_id(self):
        from CORE.WORMS.expansion.scheduling import OrchestratorConfig
        self.assertIsNone(OrchestratorConfig().worm_id)
        self.assertEqual(OrchestratorConfig(worm_id="w_cfg").worm_id, "w_cfg")


if __name__ == "__main__":
    unittest.main()
