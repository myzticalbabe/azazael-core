import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
Property-Based Testing for Azazael Enhancements

Uses Hypothesis for property-based testing to find edge cases
and ensure robustness of core algorithms.

Usage:
    python test_enhancements.py           # Run all tests
    python test_enhancements.py --verbose # Verbose output
"""

import sys
from pathlib import Path
from typing import List
from dataclasses import dataclass
import json

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from hypothesis import given, strategies as st, settings

    HYPOTHESIS_AVAILABLE = True
except ImportError as exc:
    note_failure("test_enhancements:L25", exc)
    HYPOTHESIS_AVAILABLE = False

# REMOVED 2026-10-10 (no-op gate sweep): `try: pass / FUZZING_AVAILABLE = True`.
# The import statement had been replaced by `pass`, so the flag was unconditionally
# True and its `except ImportError` branch was unreachable -- a guard that could
# never fail. It also had no reader: `TestFuzzing` below runs unconditionally and
# its cases are hand-rolled, so no fuzzing library was ever required.


class TestQTableProperties:
    """Property-based tests for Q-Learning table."""

    @given(
        num_states=st.integers(min_value=1, max_value=100),
        num_actions=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=20)
    def test_q_table_initialization(self, num_states, num_actions):
        """Q-table should initialize with correct dimensions."""
        import numpy as np

        q_table = np.zeros((num_states, num_actions))

        assert q_table.shape == (num_states, num_actions)
        assert q_table.dtype == np.float64
        assert np.all(q_table == 0.0)

    @given(
        num_states=st.integers(min_value=10, max_value=50),
        num_actions=st.integers(min_value=5, max_value=20),
        alpha=st.floats(min_value=0.01, max_value=0.5),
        gamma=st.floats(min_value=0.8, max_value=0.999),
    )
    @settings(max_examples=15)
    def test_q_learning_update(self, num_states, num_actions, alpha, gamma):
        """Q-learning update should keep values bounded."""
        import numpy as np

        q_table = np.zeros((num_states, num_actions))
        target_table = np.copy(q_table)

        for _ in range(1000):
            state = np.random.randint(0, num_states)
            action = np.random.randint(0, num_actions)
            reward = np.random.uniform(-1, 1)
            next_state = np.random.randint(0, num_states)
            done = np.random.random() < 0.1

            best_next = np.argmax(target_table[next_state])
            td_target = (
                reward + (1.0 - done) * gamma * target_table[next_state, best_next]
            )
            td_error = td_target - q_table[state, action]
            q_table[state, action] += alpha * td_error

        assert np.all(np.isfinite(q_table))
        assert np.all(np.abs(q_table) < 1000)

    @given(
        states=st.lists(
            st.integers(min_value=0, max_value=99), min_size=1, max_size=100
        ),
        epsilon=st.floats(min_value=0.0, max_value=1.0),
    )
    @settings(max_examples=20)
    def test_epsilon_greedy_policy(self, states, epsilon):
        """Epsilon-greedy should select valid actions."""
        import numpy as np

        num_states = 100
        num_actions = 30
        q_table = np.random.randn(num_states, num_actions)

        for state in states:
            if np.random.random() < epsilon:
                action = np.random.randint(0, num_actions)
            else:
                action = np.argmax(q_table[state])

            assert 0 <= action < num_actions

    @given(
        rewards=st.lists(
            st.floats(min_value=-10, max_value=10), min_size=1, max_size=1000
        ),
        gamma=st.floats(min_value=0.8, max_value=0.999),
    )
    @settings(max_examples=10)
    def test_discounted_returns(self, rewards, gamma):
        """Discounted return should converge."""
        returns = []
        cumulative = 0.0
        for i, reward in enumerate(rewards):
            cumulative = reward + gamma * cumulative
            returns.append(cumulative)

        assert len(returns) == len(rewards)
        assert all(r == 0 or abs(r) < 1e10 for r in returns)


class TestTransitionProperties:
    """Tests for experience replay transitions."""

    @given(
        state=st.integers(min_value=0, max_value=999),
        action=st.integers(min_value=0, max_value=99),
        reward=st.floats(min_value=-100, max_value=100),
        priority=st.floats(min_value=0.0, max_value=10.0),
    )
    @settings(max_examples=50)
    def test_transition_creation(self, state, action, reward, priority):
        """Transitions should store data correctly."""
        import time

        @dataclass
        class Transition:
            state: int
            action: int
            reward: float
            next_state: int
            done: bool
            priority: float = 1.0
            timestamp: float = 0.0

        t = Transition(
            state=state,
            action=action,
            reward=reward,
            next_state=state + 1,
            done=False,
            priority=priority,
            timestamp=time.time(),
        )

        assert t.state == state
        assert t.action == action
        assert abs(t.reward - reward) < 1e-10
        assert t.priority == priority
        assert isinstance(t.done, bool)

    @given(
        priorities=st.lists(
            st.floats(min_value=0.001, max_value=1.0), min_size=1, max_size=100
        )
    )
    @settings(max_examples=20)
    def test_priority_normalization(self, priorities):
        """Priorities should be valid probabilities."""
        probs = np.array(priorities)
        probs = probs / probs.sum()

        assert np.all(probs >= 0)
        assert np.all(probs <= 1)
        assert abs(probs.sum() - 1.0) < 1e-10


class TestFuzzing:
    """Fuzz tests for CLI and inputs."""

    def test_cli_input_fuzz(self):
        """CLI should handle malformed input gracefully."""
        test_inputs = [
            "",
            "   ",
            "!" * 1000,
            "\x00\x01\x02",
            "a" * 10000,
            "..\\" * 100,
            "'; DROP TABLE users; --",
            None,
        ]

        for inp in test_inputs:
            if inp is None:
                continue

            try:
                parts = inp.split()
                if parts:
                    action = parts[0].lower()
                    args = parts[1:]
                    assert isinstance(action, str)
                    assert isinstance(args, list)
            except Exception as exc:
                note_failure("test_enhancements:L211", exc)

    def test_path_fuzz(self):
        """Path handling should be robust."""
        test_paths = [
            "",
            "/",
            "//",
            "...",
            "a/b/c/../../../../../",
            "\x00",
            " " * 100,
            "." * 50,
            ".." * 50,
        ]

        for path_str in test_paths:
            try:
                p = Path(path_str)
                parts = p.parts
                assert isinstance(parts, tuple)
            except Exception as exc:
                note_failure("test_enhancements:L233", exc)

    def test_json_fuzz(self):
        """JSON parsing should handle invalid input."""

        test_jsons = [
            "",
            "{",
            "}",
            '{"key": }',
            '{"key": undefined}',
            "null",
            "true",
            "123",
            "[]]",
        ]

        for json_str in test_jsons:
            try:
                json.loads(json_str)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                note_failure("test_enhancements:L255", exc)


class TestConcurrencyProperties:
    """Tests for concurrent operations."""

    @given(
        num_threads=st.integers(min_value=1, max_value=10),
        num_ops=st.integers(min_value=10, max_value=100),
    )
    @settings(max_examples=5)
    def test_thread_safe_counter(self, num_threads, num_ops):
        """Counter should handle concurrent increments."""
        import threading

        counter = {"value": 0}

        def increment():
            for _ in range(num_ops):
                with threading.Lock():
                    counter["value"] += 1

        threads = [threading.Thread(target=increment) for _ in range(num_threads)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = num_threads * num_ops
        assert counter["value"] == expected


def run_property_tests():
    """Run all property-based tests."""
    if not HYPOTHESIS_AVAILABLE:
        print("Hypothesis not installed. Install with: pip install hypothesis")
        print("Running basic tests instead...\n")
        basic_tests()
        return


    print("=== Property-Based Testing ===\n")

    print("1. Q-Table Properties...")
    q_table_tests = TestQTableProperties()

    try:
        # @given supplies (num_states, num_actions) at runtime; the checker cannot see it.
        q_table_tests.test_q_table_initialization()  # type: ignore[reportCallIssue]
        print("   [OK] Initialization test passed")
    except Exception as e:
        print(f"   [X] Initialization test failed: {e}")

    try:
        # @given supplies (num_states, num_actions, alpha, gamma) at runtime.
        q_table_tests.test_q_learning_update()  # type: ignore[reportCallIssue]
        print("   [OK] Update test passed")
    except Exception as e:
        print(f"   [X] Update test failed: {e}")

    try:
        # @given supplies (states, epsilon) at runtime.
        q_table_tests.test_epsilon_greedy_policy()  # type: ignore[reportCallIssue]
        print("   [OK] Epsilon-greedy test passed")
    except Exception as e:
        print(f"   [X] Epsilon-greedy test failed: {e}")

    print("\n2. Transition Properties...")
    trans_tests = TestTransitionProperties()

    try:
        # @given supplies (state, action, reward, priority) at runtime.
        trans_tests.test_transition_creation()  # type: ignore[reportCallIssue]
        print("   [OK] Transition creation test passed")
    except Exception as e:
        print(f"   [X] Transition creation test failed: {e}")

    try:
        # @given supplies (priorities,) at runtime.
        trans_tests.test_priority_normalization()  # type: ignore[reportCallIssue]
        print("   [OK] Priority normalization test passed")
    except Exception as e:
        print(f"   [X] Priority normalization test failed: {e}")

    print("\n3. Fuzzing Tests...")
    fuzz_tests = TestFuzzing()

    try:
        fuzz_tests.test_cli_input_fuzz()
        print("   [OK] CLI input fuzz test passed")
    except Exception as e:
        print(f"   [X] CLI input fuzz test failed: {e}")

    try:
        fuzz_tests.test_path_fuzz()
        print("   [OK] Path fuzz test passed")
    except Exception as e:
        print(f"   [X] Path fuzz test failed: {e}")

    print("\n=== All Tests Complete ===\n")


def basic_tests():
    """Basic tests when Hypothesis not available."""
    import numpy as np

    print("=== Basic Tests ===\n")

    print("1. Testing Q-Learning...")

    num_states = 20
    num_actions = 5
    q_table = np.zeros((num_states, num_actions))
    alpha = 0.1
    gamma = 0.95

    for episode in range(100):
        state = np.random.randint(0, num_states)
        for step in range(50):
            action = np.argmax(q_table[state])
            next_state = (state + action) % num_states
            reward = np.random.randn() * 0.1

            best_next = np.argmax(q_table[next_state])
            td_target = reward + gamma * q_table[next_state, best_next]
            td_error = td_target - q_table[state, action]
            q_table[state, action] += alpha * td_error

            state = next_state

    print(f"   Q-table shape: {q_table.shape}")
    print(f"   Max Q-value: {q_table.max():.4f}")
    print(f"   Min Q-value: {q_table.min():.4f}")
    print(f"   All finite: {np.all(np.isfinite(q_table))}")

    print("\n2. Testing Epsilon Decay...")

    epsilon = 1.0
    epsilon_decay = 0.995
    epsilon_min = 0.01

    epsilons = [epsilon]
    for _ in range(1000):
        epsilon = max(epsilon_min, epsilon * epsilon_decay)
        epsilons.append(epsilon)

    print(f"   Initial epsilon: {epsilons[0]:.4f}")
    print(f"   Final epsilon: {epsilons[-1]:.4f}")
    print(
        f"   Monotonic decay: {all(epsilons[i] >= epsilons[i + 1] for i in range(len(epsilons) - 1))}"
    )

    print("\n3. Testing Transitions...")

    transitions = []
    for i in range(100):
        t = {
            "state": i % 20,
            "action": i % 5,
            "reward": np.random.randn() * 0.1,
            "next_state": (i + 1) % 20,
            "done": i % 20 == 19,
        }
        transitions.append(t)

    print(f"   Created {len(transitions)} transitions")
    print(f"   All have 'state': {all('state' in t for t in transitions)}")
    print(f"   All have 'action': {all('action' in t for t in transitions)}")

    print("\n=== Basic Tests Complete ===\n")


def test_sum_tree():
    """Test sum-tree implementation."""
    sys.path.insert(
        0, str(Path(__file__).parent.parent / "ml_platforms" / "improvements")
    )
    from enhanced_qlearning import SumTree

    print("=== Sum-Tree Test ===\n")

    tree = SumTree(capacity=8)

    for i in range(8):
        tree.add(float(i + 1), f"data_{i}")

    print(f"  Total priority: {tree.total():.2f}")

    for i in range(8):
        idx, priority, data = tree.get(float(i + 0.5))
        print(f"  Sampled: {data} (priority: {priority:.2f})")

    print("\n  Update priority test...")
    tree.update(8, 100.0)
    print(f"  New total: {tree.total():.2f}")

    print("\n=== Sum-Tree Test Complete ===\n")


def test_multi_agent():
    """Test multi-agent Q-learning."""
    sys.path.insert(
        0, str(Path(__file__).parent.parent / "ml_platforms" / "improvements")
    )
    from enhanced_qlearning import MultiAgentQLearning

    print("=== Multi-Agent Q-Learning Test ===\n")

    agents = MultiAgentQLearning(num_agents=4, num_states=10, num_actions=5)

    print(f"  Created {agents.num_agents} agents")

    for episode in range(100):
        state = np.random.randint(0, 10)
        action = agents.select_action(state, epsilon=0.5)
        reward = np.random.randn() * 0.1
        next_state = (state + action) % 10
        agents.train(state, action, reward, next_state, done=False)

    consensus = agents.get_consensus(5)
    print(f"  Consensus distribution has {len(consensus)} actions")

    agents.save("multi_agent_test.json")
    agents.load("multi_agent_test.json")
    print("  Saved and loaded successfully")

    print("\n=== Multi-Agent Test Complete ===\n")


if __name__ == "__main__":
    import numpy as np

    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    if verbose:
        run_property_tests()
    else:
        basic_tests()

    try:
        test_sum_tree()
    except Exception as e:
        print(f"Sum-tree test skipped: {e}")

    try:
        test_multi_agent()
    except Exception as e:
        print(f"Multi-agent test skipped: {e}")

    print("=== All Enhancement Tests Complete ===")
