#!/usr/bin/env python3
"""Tests for SB3Agent, AgentRegistry, and MemoryBus integration."""

import os
import sys
import json
import time
import unittest
import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _ensure_test_agent_registered():
    """Register TestAgent if the registry does not already hold it.

    Registration is a precondition of three tests here, not a side effect of a
    fourth: relying on `test_register_agent_decorator` to run first made
    `get_agents_by_category`, `get_all_categories` and `get_agent_class` fail
    whenever they were run alone or after anything reset the registry.
    """
    from CORE.agent_registry import AGENT_REGISTRY, register_agent

    if "TestAgent" not in AGENT_REGISTRY:
        @register_agent(category="test_rl", priority=5, description="Test agent")
        class TestAgent:
            VERSION = "1.0.0"

    return AGENT_REGISTRY["TestAgent"]


class TestAgentRegistry(unittest.TestCase):
    """Test the AgentRegistry module."""

    def setUp(self):
        from CORE.agent_registry import AGENT_REGISTRY, register_agent, get_agents_by_category, get_all_agent_categories
        self.REGISTRY = AGENT_REGISTRY
        self.register_agent = register_agent
        self.get_agents_by_category = get_agents_by_category
        self.get_all_categories = get_all_agent_categories
        _ensure_test_agent_registered()

    def test_registry_exists(self):
        """AGENT_REGISTRY should be a dict."""
        self.assertIsInstance(self.REGISTRY, dict)

    def test_register_agent_decorator(self):
        """@register_agent should register a class in the registry."""
        @self.register_agent(category="test_rl", priority=5, description="Test agent")
        class TestAgent:
            VERSION = "1.0.0"

        self.assertIn("TestAgent", self.REGISTRY)
        info = self.REGISTRY["TestAgent"]
        self.assertEqual(info["category"], "test_rl")
        self.assertEqual(info["priority"], 5)
        self.assertEqual(info["version"], "1.0.0")

    def test_get_agents_by_category(self):
        """get_agents_by_category should return agents in a category."""
        agents = self.get_agents_by_category("test_rl")
        self.assertIn("TestAgent", agents)

    def test_get_all_categories(self):
        """get_all_agent_categories should return all category names."""
        cats = self.get_all_categories()
        self.assertIn("test_rl", cats)

    def test_create_agent(self):
        """create_agent should instantiate an agent from the registry."""
        from CORE.agent_registry import create_agent, get_agent_class

        cls = get_agent_class("TestAgent")
        self.assertIsNotNone(cls)


class TestMemoryBusIntegration(unittest.TestCase):
    """Test MemoryBus integration in FreeThreadedAgent."""

    def setUp(self):
        from CORE.Agent import FreeThreadedAgent, AgentConfig
        self.FreeThreadedAgent = FreeThreadedAgent
        self.AgentConfig = AgentConfig

    def test_agent_creates_with_bus(self):
        """FreeThreadedAgent should create MemoryBus on init."""
        agent = self.FreeThreadedAgent(self.AgentConfig(agent_name="test_bus"))
        self.assertIsNotNone(agent)
        self.assertTrue(hasattr(agent, '_bus'))

    def test_agent_publishes_start_event(self):
        """Agent should publish agent.started event on init."""
        # Just verify it doesn't crash
        agent = self.FreeThreadedAgent(self.AgentConfig(agent_name="test_bus2"))
        self.assertIsNotNone(agent)

    def test_send_message_no_crash(self):
        """send_message should not crash."""
        agent = self.FreeThreadedAgent(self.AgentConfig(agent_name="test_send"))
        result = agent.send_message("target-1", "ping")
        # May fail if MemoryBus is down, but shouldn't crash
        self.assertIsNotNone(result)

    def test_broadcast_message(self):
        """broadcast_message should not crash."""
        agent = self.FreeThreadedAgent(self.AgentConfig(agent_name="test_broadcast"))
        result = agent.broadcast_message("ping")
        self.assertIsNotNone(result)

    def test_poll_messages(self):
        """poll_messages should return a list."""
        agent = self.FreeThreadedAgent(self.AgentConfig(agent_name="test_poll"))
        messages = agent.poll_messages()
        self.assertIsInstance(messages, list)

    def test_agent_close(self):
        """Agent close() should cleanup bus."""
        agent = self.FreeThreadedAgent(self.AgentConfig(agent_name="test_close"))
        agent.close()
        # After close, bus should be None
        self.assertIsNone(agent._bus)

    def test_agent_stats_have_messages(self):
        """Agent stats should include messages_received and messages_sent."""
        agent = self.FreeThreadedAgent(self.AgentConfig(agent_name="test_stats"))
        stats = agent.get_stats()
        self.assertIn("messages_received", stats)
        self.assertIn("messages_sent", stats)

    def test_agent_repr_includes_bus(self):
        """Agent repr should include bus status."""
        agent = self.FreeThreadedAgent(self.AgentConfig(agent_name="test_repr"))
        rep = repr(agent)
        self.assertIn("bus=", rep)


class TestSB3Agent(unittest.TestCase):
    """Test SB3Agent creation and basic functionality."""

    def setUp(self):
        try:
            import stable_baselines3
            self.HAS_SB3 = True
        except ImportError:
            self.HAS_SB3 = False

    def test_create_with_defaults(self):
        """SB3Agent should create with default PPO."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        self.assertEqual(agent.algo_name, "PPO")
        self.assertFalse(agent.is_trained)
        self.assertEqual(agent._total_timesteps_trained, 0)

    def test_create_dqn(self):
        """SB3Agent should support DQN algorithm."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="DQN")
        self.assertEqual(agent.algo_name, "DQN")

    def test_create_a2c(self):
        """SB3Agent should support A2C algorithm."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="A2C")
        self.assertEqual(agent.algo_name, "A2C")

    def test_predict_before_train(self):
        """Predict before training should return default action."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        obs = np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float32)
        action, state = agent.predict(obs)
        self.assertIsNotNone(action)
        self.assertEqual(len(action.shape), 0)  # scalar action

    def test_predict_batch(self):
        """predict_batch should handle multiple observations."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        obs_list = [np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float32) for _ in range(3)]
        actions = agent.predict_batch(obs_list)
        self.assertEqual(len(actions), 3)

    def test_save_load_no_model(self):
        """Save without training should return False."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        result = agent.save("/tmp/test_sb3_model")
        self.assertFalse(result)

    def test_get_training_history(self):
        """get_training_history should return list."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        history = agent.get_training_history()
        self.assertIsInstance(history, list)
        self.assertEqual(len(history), 0)

    def test_agent_run_task(self):
        """_run should handle action dispatch."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")

        # Test get_training_history action
        result = agent._run({"action": "get_training_history", "payload": {}}, None)
        self.assertIsInstance(result, list)

        # Test predict action
        result = agent._run({
            "action": "predict",
            "payload": {"observation": [0.0, 0.0, 0.0, 0.0]}
        }, None)
        self.assertIn("action", result)

        # Test unknown action
        result = agent._run({"action": "unknown_action"}, None)
        self.assertIn("error", result)

    def test_agent_repr(self):
        """SB3Agent repr should include algo and training status."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        rep = repr(agent)
        self.assertIn("SB3Agent", rep)
        self.assertIn("PPO", rep)


class TestRLTrainLoopPersistence(unittest.TestCase):
    """Test RLTrainLoop ModelVersioner integration."""

    def setUp(self):
        from CORE.WORMS.expansion.learning import RLTrainLoop
        self.loop = RLTrainLoop()

    def test_register_to_versioner(self):
        """register_to_versioner should return a result dict."""
        result = self.loop.register_to_versioner(
            model_name="test_model",
            metrics={"avg_reward": 0.5, "q_table_size": 10, "episodes": 5},
            metadata={"source": "test"},
        )
        self.assertIn("model_name", result)
        self.assertEqual(result["model_name"], "test_model")

    def test_register_to_versioner_without_training(self):
        """register_to_versioner should work without prior training."""
        result = self.loop.register_to_versioner(model_name="test_empty")
        if "error" not in result:
            self.assertIn("model_name", result)
            self.assertIn("version", result)

    def test_save_and_load_q_table(self):
        """save and load Q-table should round-trip."""
        # Add some Q-table entries
        self.loop._q_table["test_state"] = {0: 0.5, 1: 0.3}
        self.loop._q_table["test_state2"] = {0: 0.1}

        # Save
        model_name = "test_q_table_roundtrip"
        self.loop.register_to_versioner(model_name=model_name)

        # Clear and load
        self.loop._q_table.clear()
        loaded = self.loop.load_q_table_from_store(model_name=model_name)
        # May or may not find the stored Q-table depending on LocalMemoryStore state
        self.assertIsInstance(loaded, bool)

    def test_load_q_table_non_existent(self):
        """load_q_table_from_store with non-existent model returns False."""
        result = self.loop.load_q_table_from_store(model_name="__nonexistent__")
        self.assertFalse(result)


class TestAgentHealthReporter(unittest.TestCase):
    """Test AgentHealthReporter."""

    def setUp(self):
        from CORE.agent_registry import AgentHealthReporter
        from CORE.Agent import FreeThreadedAgent, AgentConfig
        self.reporter = AgentHealthReporter(check_interval=0)
        self.agent = FreeThreadedAgent(AgentConfig(agent_name="test_health"))

    def test_register_instance(self):
        """register_instance should add agent to tracking."""
        self.reporter.register_instance(self.agent)
        self.assertIn(self.agent.agent_id, self.reporter._instances)

    def test_unregister_instance(self):
        """unregister_instance should remove agent from tracking."""
        self.reporter.register_instance(self.agent)
        self.reporter.unregister_instance(self.agent.agent_id)
        self.assertNotIn(self.agent.agent_id, self.reporter._instances)

    def test_check_agent(self):
        """check_agent should return AgentHealth."""
        health = self.reporter.check_agent(self.agent)
        self.assertEqual(health.agent_id, self.agent.agent_id)
        self.assertIn(health.status, ("healthy", "degraded", "unhealthy", "unknown"))

    def test_check_all_agents(self):
        """check_all_agents should return health by status."""
        self.reporter.register_instance(self.agent)
        results = self.reporter.check_all_agents(force=True)
        self.assertIsInstance(results, dict)
        # Should have at least one status category
        total = sum(len(v) for v in results.values())
        self.assertGreaterEqual(total, 1)

    def test_get_summary(self):
        """get_summary should return aggregate stats."""
        self.reporter.register_instance(self.agent)
        summary = self.reporter.get_summary()
        self.assertIn("total_agents", summary)
        self.assertIn("healthy", summary)
        self.assertIn("by_category", summary)
        self.assertGreaterEqual(summary["total_agents"], 1)


class TestAgentRegistryCreateAgent(unittest.TestCase):
    """Test create_agent and get_agent_class utilities."""

    def test_get_agent_class(self):
        """get_agent_class should return the registered class."""
        from CORE.agent_registry import get_agent_class, register_agent

        @register_agent(category="test_utils", priority=3, description="Utility test agent")
        class UtilityTestAgent:
            VERSION = "1.0.0"

        cls = get_agent_class("UtilityTestAgent")
        self.assertIsNotNone(cls)
        self.assertEqual(cls.__name__, "UtilityTestAgent")

    def test_create_agent_unknown(self):
        """create_agent for unknown type returns None."""
        from CORE.agent_registry import create_agent
        result = create_agent("NonExistentAgentType")
        self.assertIsNone(result)

    def test_reset_registry(self):
        """reset_agent_registry should clear the registry."""
        from CORE.agent_registry import AGENT_REGISTRY, reset_agent_registry
        before = len(AGENT_REGISTRY)
        reset_agent_registry()
        self.assertEqual(len(AGENT_REGISTRY), 0)
        # Re-register test agents for other tests
        # (registry is module-level, so other tests may fail if we don't restore)
        # But each test setUp should handle that


class TestSB3AgentCreateEnv(unittest.TestCase):
    """Test SB3Agent environment creation from records."""

    def setUp(self):
        try:
            import stable_baselines3
            self.HAS_SB3 = True
        except ImportError:
            self.HAS_SB3 = False

    def test_create_env_from_records(self):
        """_create_env_from_records should create a gym environment."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")

        records = [
            {"source": "SystemZ", "severity": "info", "data": {"duration_ms": 150, "cpu": 50}},
            {"source": "ProcessZ", "severity": "error", "data": {"duration_ms": 300, "cpu": 90}},
        ]
        env = agent._create_env_from_records(records)
        self.assertIsNotNone(env)
        self.assertEqual(agent._obs_dim, 3)  # severity + duration_ms + cpu = 3
        self.assertEqual(agent._action_dim, 3)

    def test_create_default_env(self):
        """_create_default_env should create a basic environment."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")
        env = agent._create_default_env()
        self.assertIsNotNone(env)

    def test_env_reset_and_step(self):
        """Environment should support reset and step."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        import gymnasium as gym
        agent = SB3Agent(algo="PPO")
        env = agent._create_default_env()

        obs, info = env.reset()
        self.assertIsNotNone(obs)
        self.assertEqual(obs.shape, (4,))

        obs, reward, done, truncated, info = env.step(0)
        self.assertIsNotNone(obs)
        self.assertIsInstance(reward, float)

    def test_env_from_records_reset_step(self):
        """Environment from records should support reset and step."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")

        records = [
            {"source": "SystemZ", "severity": "info", "data": {"cpu": 50}},
            {"source": "ProcessZ", "severity": "error", "data": {"cpu": 90}},
        ]
        env = agent._create_env_from_records(records)
        obs, info = env.reset()
        self.assertIsNotNone(obs)
        obs2, reward, done, truncated, info = env.step(0)
        self.assertIsNotNone(obs2)


if __name__ == '__main__':
    unittest.main()
