"""Tests for CORE/FreeThreadedAgent.py."""
import sys, os, unittest, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from CORE.FreeThreadedAgent import FreeThreadedAgent
from CORE.MemoryBus import MemoryBus

class ConcreteAgent(FreeThreadedAgent):
    def __init__(self, name="test-agent", **kwargs):
        super().__init__(name=name, **kwargs)
        self.events = []
    def run(self):
        self.events.append("run_started")
        while self.is_running:
            time.sleep(0.05)
        self.events.append("run_ended")
    def on_start(self):
        self.events.append("on_start_called")
    def on_stop(self):
        self.events.append("on_stop_called")
    def on_error(self, exc):
        self.events.append("on_error_called:" + type(exc).__name__)

class TestAbstractEnforcement(unittest.TestCase):
    def test_cannot_instantiate_abstract(self):
        with self.assertRaises(TypeError):
            FreeThreadedAgent(name="bad")
    def test_can_instantiate_concrete(self):
        agent = ConcreteAgent(name="test")
        self.assertEqual(agent.name, "test")
        self.assertFalse(agent.is_running)

class TestLifecycle(unittest.TestCase):
    def test_start_calls_on_start(self):
        agent = ConcreteAgent()
        agent.start()
        time.sleep(0.3)
        self.assertIn("on_start_called", agent.events)
        self.assertIn("run_started", agent.events)
        agent.stop()
        agent.wait(timeout=2)
    def test_stop_calls_on_stop(self):
        agent = ConcreteAgent()
        agent.start()
        time.sleep(0.1)
        agent.stop()
        agent.wait(timeout=2)
        self.assertIn("on_stop_called", agent.events)
        self.assertIn("run_ended", agent.events)
    def test_is_running_toggle(self):
        agent = ConcreteAgent()
        self.assertFalse(agent.is_running)
        agent.start()
        time.sleep(0.1)
        self.assertTrue(agent.is_running)
        agent.stop()
        agent.wait(timeout=2)
        self.assertFalse(agent.is_running)
    def test_double_start(self):
        agent = ConcreteAgent()
        agent.start()
        agent.start()
        time.sleep(0.1)
        self.assertTrue(agent.is_running)
        agent.stop()
        agent.wait(timeout=2)
    def test_double_stop(self):
        agent = ConcreteAgent()
        agent.start()
        time.sleep(0.1)
        agent.stop()
        agent.stop()
        agent.wait(timeout=2)
        self.assertFalse(agent.is_running)

class TestMemoryBusIntegration(unittest.TestCase):
    def test_agent_has_memory_bus(self):
        bus = MemoryBus()
        agent = ConcreteAgent(memory_bus=bus)
        self.assertIs(agent.memory_bus, bus)

class TestErrorHandling(unittest.TestCase):
    def test_on_error_called(self):
        class BrokenAgent(ConcreteAgent):
            def run(self):
                self.events.append("run_started")
                raise RuntimeError("boom")
        agent = BrokenAgent()
        agent.start()
        time.sleep(0.3)
        agent.stop()
        agent.wait(timeout=2)
        self.assertIn("on_error_called:RuntimeError", agent.events)

if __name__ == "__main__":
    unittest.main()
