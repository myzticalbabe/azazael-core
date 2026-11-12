# coding: utf-8
"""
Enhanced Q-Learning Module

Advanced Q-Learning features:
1. Multi-Agent Q-Learning - Coordinate multiple agents
2. Sum-Tree Prioritized Replay - O(log N) priority sampling
3. JIT-Compiled Updates - Numba acceleration
4. Hyperparameter Auto-Tuning - Optuna integration

Author: Azazael Enhancement System
Date: 2026-02-10
Version: 3.0.0
"""

from typing import Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import numpy as np
import json
import random
import time

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from numba import jit

    NUMBA_AVAILABLE = True
except ImportError as exc:
    note_failure("enhanced_qlearning:L28", exc)
    NUMBA_AVAILABLE = False

try:
    import optuna

    OPTUNA_AVAILABLE = True
except ImportError as exc:
    note_failure("enhanced_qlearning:L35", exc)
    OPTUNA_AVAILABLE = False


class TaskType(Enum):
    COMPUTER_VISION = "computer_vision"
    NLP = "nlp"
    TABULAR = "tabular"
    REINFORCEMENT = "reinforcement"
    GENERATIVE = "generative"
    TIME_SERIES = "time_series"
    GRAPH_LEARNING = "graph_learning"


@dataclass
class Transition:
    state: int
    action: int
    reward: float
    next_state: int
    done: bool
    priority: float = 1.0
    timestamp: float = field(default_factory=lambda: time.time())


class SumTree:
    """
    Sum-Tree for O(log N) priority sampling.

    More efficient than simple array for prioritized replay.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.tree = np.zeros(2 * capacity - 1)
        self.data = [None] * capacity
        self.size = 0
        self.write = 0

    def _propagate(self, idx: int, change: float):
        """Propagate change up the tree (iterative: root/underflow safe)."""
        while idx > 0:
            idx = (idx - 1) // 2
            self.tree[idx] += change

    def _retrieve(self, idx: int, s: float) -> int:
        """Find the index of the leaf with priority s."""
        left = 2 * idx + 1
        right = left + 1
        if left >= len(self.tree):
            return idx

        if s <= self.tree[left]:
            return self._retrieve(left, s)
        else:
            return self._retrieve(right, s - self.tree[left])

    def total(self) -> float:
        """Get total priority."""
        return self.tree[0]

    def add(self, priority: float, data: Any):
        """Add a new sample."""
        idx = self.write + self.capacity - 1
        self.data[self.write] = data
        self.update(idx, priority)

        self.write += 1
        if self.write >= self.capacity:
            self.write = 0
            self.size = self.capacity
        elif self.size < self.capacity:
            self.size += 1

        return idx

    def update(self, idx: int, priority: float):
        """Update priority of a sample."""
        change = priority - self.tree[idx]
        self.tree[idx] = priority
        self._propagate(idx, change)

    def get(self, s: float) -> tuple[int, float, Any]:
        """Get sample by priority value."""
        idx = self._retrieve(0, s)
        data_idx = idx - self.capacity + 1
        return idx, self.tree[idx], self.data[data_idx]


class PrioritizedReplay:
    """
    Prioritized Experience Replay with Sum-Tree.

    O(log N) sampling instead of O(N).
    """

    def __init__(
        self,
        capacity: int = 10000,
        alpha: float = 0.6,
        beta_start: float = 0.4,
        beta_frames: int = 100000,
    ):
        self.capacity = capacity
        self.alpha = alpha
        self.beta_start = beta_start
        self.beta_frames = beta_frames
        self.frame = 1
        self.sum_tree = SumTree(capacity)
        self.size = 0

    def push(self, state: int, action: int, reward: float, next_state: int, done: bool):
        """Add transition with max priority."""
        priority = (
            self.sum_tree.total() / self.capacity if self.sum_tree.total() > 0 else 1.0
        )
        priority = max(priority, 1e-6)
        transition = Transition(state, action, reward, next_state, done, priority)
        leaf_idx = self.sum_tree.add(priority, transition)

        if self.size < self.capacity:
            self.size += 1

        return leaf_idx

    def sample(
        self, batch_size: int
    ) -> tuple[list[Transition], np.ndarray, np.ndarray]:
        """Sample batch with prioritized probabilities."""
        if self.size < batch_size:
            batch_size = self.size

        self.beta = min(
            1.0,
            self.beta_start + (1.0 - self.beta_start) * self.frame / self.beta_frames,
        )
        self.frame += 1

        batch = []
        indices = []
        priorities = []

        segment = self.sum_tree.total() / batch_size
        for i in range(batch_size):
            a = segment * i
            b = segment * (i + 1)
            s = random.uniform(a, b)
            idx, priority, data = self.sum_tree.get(s)
            batch.append(data)
            indices.append(idx)
            priorities.append(priority)

        probs = np.array(priorities) ** self.alpha
        probs = probs / (probs.sum() + 1e-10)

        weights = (self.size * probs) ** (-self.beta)
        weights = weights / (weights.max() + 1e-10)

        return batch, weights, np.array(indices)

    def update_priorities(self, indices: list[int], priorities: np.ndarray):
        """Update priorities for sampled transitions."""
        for idx, priority in zip(indices, priorities):
            self.sum_tree.update(idx, priority + 1e-6)

    def __len__(self):
        return self.size

    def __iter__(self):
        # Yield stored transitions (non-None) so callers can iterate the
        # replay buffer without reaching into the SumTree.
        for item in self.sum_tree.data:
            if item is not None:
                yield item


if NUMBA_AVAILABLE:

    @jit(nopython=True, cache=True)
    def fast_q_update(
        q_table: np.ndarray,
        target_table: np.ndarray,
        states: np.ndarray,
        actions: np.ndarray,
        rewards: np.ndarray,
        next_states: np.ndarray,
        dones: np.ndarray,
        gamma: float,
        alpha: float,
    ) -> tuple[np.ndarray, float]:
        """
        JIT-compiled Q-value update.

        Much faster than pure Python for large batches.
        """
        batch_size = states.shape[0]
        losses = np.zeros(batch_size)

        for i in range(batch_size):
            s = states[i]
            a = actions[i]
            r = rewards[i]
            ns = next_states[i]
            d = dones[i]

            best_next = np.argmax(target_table[ns])
            td_target = r + (1.0 - d) * gamma * target_table[ns, best_next]
            td_error = td_target - q_table[s, a]
            q_table[s, a] += alpha * td_error
            losses[i] = td_error**2

        return q_table, np.mean(losses)
else:

    def fast_q_update(
        q_table: np.ndarray,
        target_table: np.ndarray,
        states: np.ndarray,
        actions: np.ndarray,
        rewards: np.ndarray,
        next_states: np.ndarray,
        dones: np.ndarray,
        gamma: float,
        alpha: float,
    ) -> tuple[np.ndarray, float]:
        """Fallback pure Python implementation."""
        batch_size = states.shape[0]
        losses = np.zeros(batch_size)

        for i in range(batch_size):
            s = int(states[i])
            a = int(actions[i])
            r = rewards[i]
            ns = int(next_states[i])
            d = dones[i]

            best_next = np.argmax(target_table[ns])
            td_target = r + (1.0 - d) * gamma * target_table[ns, best_next]
            td_error = td_target - q_table[s, a]
            q_table[s, a] += alpha * td_error
            losses[i] = td_error**2

        return q_table, np.mean(losses)


class MultiAgentQLearning:
    """
    Multi-Agent Q-Learning for collaborative decision making.

    Multiple agents vote on decisions, improving robustness.
    """

    def __init__(
        self,
        num_agents: int = 4,
        num_states: int = 100,
        num_actions: int = 30,
        alpha: float = 0.1,
        gamma: float = 0.95,
    ):
        self.num_agents = num_agents
        self.agents = []
        self.coordination_matrix = np.zeros((num_agents, num_agents))

        for _ in range(num_agents):
            agent = {
                "q_table": np.zeros((num_states, num_actions)),
                "alpha": alpha,
                "gamma": gamma,
                "epsilon": 1.0,
                "epsilon_decay": 0.995,
                "epsilon_min": 0.01,
                "reward_history": [],
            }
            self.agents.append(agent)

    def select_action(self, state: int, epsilon: Optional[float] = None) -> int:
        """Vote among agents for best action."""
        votes = []

        for agent in self.agents:
            eps = epsilon if epsilon is not None else agent["epsilon"]
            if np.random.random() < eps:
                votes.append(np.random.randint(0, agent["q_table"].shape[1]))
            else:
                votes.append(int(np.argmax(agent["q_table"][state])))

        vote_counts = {}
        for vote in votes:
            vote_counts[vote] = vote_counts.get(vote, 0) + 1

        best_vote = max(vote_counts.items(), key=lambda x: x[1])[0]

        return best_vote

    def train(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool,
        agent_id: Optional[int] = None,
    ):
        """Train a specific agent or all agents."""
        if agent_id is not None:
            self._train_agent(agent_id, state, action, reward, next_state, done)
        else:
            for i in range(self.num_agents):
                self._train_agent(i, state, action, reward, next_state, done)

    def _train_agent(
        self,
        agent_id: int,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool,
    ):
        """Train single agent."""
        agent = self.agents[agent_id]
        q_table = agent["q_table"]
        alpha = agent["alpha"]
        gamma = agent["gamma"]

        best_next = np.argmax(q_table[next_state])
        td_target = reward + (1.0 - done) * gamma * q_table[next_state, best_next]
        td_error = td_target - q_table[state, action]
        q_table[state, action] += alpha * td_error

        agent["reward_history"].append(reward)

        if agent["epsilon"] > agent["epsilon_min"]:
            agent["epsilon"] *= agent["epsilon_decay"]

        self._update_coordination(agent_id, td_error)

    def _update_coordination(self, agent_id: int, td_error: float):
        """Update coordination matrix based on TD error."""
        for j in range(self.num_agents):
            if j != agent_id:
                self.coordination_matrix[agent_id, j] = 0.9 * self.coordination_matrix[
                    agent_id, j
                ] + 0.1 * abs(td_error)

    def get_consensus(self, state: int) -> dict[int, float]:
        """Get action probabilities from agent consensus."""
        votes = {}
        total = 0.0

        for agent in self.agents:
            q_values = agent["q_table"][state]
            normalized = q_values / (q_values.sum() + 1e-10)
            for action, prob in enumerate(normalized):
                votes[action] = votes.get(action, 0.0) + prob
            total += 1

        return {k: v / total for k, v in votes.items()}

    def save(self, path: str):
        """Save all agent states."""
        data = {
            "num_agents": self.num_agents,
            "agents": [],
            "coordination_matrix": self.coordination_matrix.tolist(),
        }
        for agent in self.agents:
            data["agents"].append(
                {
                    "q_table": agent["q_table"].tolist(),
                    "epsilon": agent["epsilon"],
                    "reward_history": agent["reward_history"],
                }
            )
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str):
        """Load agent states."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.num_agents = data["num_agents"]
        self.coordination_matrix = np.array(data["coordination_matrix"])

        for i, agent_data in enumerate(data["agents"]):
            self.agents[i]["q_table"] = np.array(agent_data["q_table"])
            self.agents[i]["epsilon"] = agent_data["epsilon"]
            self.agents[i]["reward_history"] = agent_data["reward_history"]


class HyperparameterTuner:
    """
    Auto-tune Q-learning hyperparameters using Optuna.
    """

    def __init__(self, num_trials: int = 50):
        self.num_trials = num_trials
        self.study = None

    def objective(self, trial: optuna.Trial) -> float:
        """Optuna objective function."""
        alpha = trial.suggest_float("alpha", 0.01, 0.5, log=True)
        gamma = trial.suggest_float("gamma", 0.8, 0.999)
        tau = trial.suggest_float("tau", 0.001, 0.1, log=True)

        num_states = 20
        num_actions = 5
        q_table = np.zeros((num_states, num_actions))
        target_table = np.copy(q_table)

        total_reward = 0.0
        for episode in range(100):
            state = np.random.randint(0, num_states)
            for step in range(50):
                action = np.argmax(q_table[state])
                next_state = (state + action) % num_states
                reward = np.random.randn() * 0.1
                done = step >= 49

                best_next = np.argmax(target_table[next_state])
                td_target = (
                    reward + (1.0 - done) * gamma * target_table[next_state, best_next]
                )
                td_error = td_target - q_table[state, action]
                q_table[state, action] += alpha * td_error
                target_table = tau * q_table + (1 - tau) * target_table

                total_reward += reward
                state = next_state
                if done:
                    break

        return total_reward

    def tune(self) -> dict[str, float]:
        """Run hyperparameter tuning."""
        if not OPTUNA_AVAILABLE:
            return {"alpha": 0.1, "gamma": 0.95, "tau": 0.005}

        self.study = optuna.create_study(direction="maximize")
        self.study.optimize(self.objective, n_trials=self.num_trials)

        best = self.study.best_params
        return {
            "alpha": best.get("alpha", 0.1),
            "gamma": best.get("gamma", 0.95),
            "tau": best.get("tau", 0.005),
        }

    def get_best_params(self) -> dict[str, float]:
        """Get best parameters from study."""
        if self.study is None:
            return {"alpha": 0.1, "gamma": 0.95, "tau": 0.005}
        return self.study.best_params


class EnhancedQLearningTable:
    """
    Enhanced Q-Learning table with all improvements.
    """

    def __init__(
        self,
        num_states: int = 100,
        num_actions: int = 30,
        alpha: float = 0.1,
        gamma: float = 0.95,
        use_prioritized: bool = True,
        use_jit: bool = True,
    ):
        self.num_states = num_states
        self.num_actions = num_actions
        self.alpha = alpha
        self.gamma = gamma

        self.q_table = np.zeros((num_states, num_actions))
        self.target_table = np.copy(self.q_table)
        self.tau = 0.005

        self.use_prioritized = use_prioritized
        self.use_jit = use_jit

        if use_prioritized:
            self.replay_buffer = PrioritizedReplay(capacity=10000)
        else:
            self.replay_buffer = None

        self.reward_history: list[float] = []
        self.state_visits = np.zeros(num_states)
        self.action_counts = np.zeros(num_actions)
        self.training_steps = 0

    def train(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool = False,
    ):
        """Train with single step."""
        best_next = np.argmax(self.target_table[next_state])
        td_target = (
            reward
            + (1.0 - done) * self.gamma * self.target_table[next_state, best_next]
        )
        td_error = td_target - self.q_table[state, action]

        self.q_table[state, action] += self.alpha * td_error

        if self.replay_buffer is not None:
            priority = abs(td_error) + 1e-6
            leaf_idx = self.replay_buffer.push(state, action, reward, next_state, done)
            self.replay_buffer.update_priorities(
                [leaf_idx], np.array([priority])
            )

        self.reward_history.append(reward)
        self.state_visits[state] += 1
        self.action_counts[action] += 1
        self.training_steps += 1

        if self.training_steps % 100 == 0:
            self._soft_update()

        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay

    def replay(self, batch_size: int = 32) -> float:
        """Train on batch from replay buffer."""
        if self.replay_buffer is None or len(self.replay_buffer) < batch_size:
            return 0.0

        states = np.array([t.state for t in self.replay_buffer])
        actions = np.array([t.action for t in self.replay_buffer])
        rewards = np.array([t.reward for t in self.replay_buffer])
        next_states = np.array([t.next_state for t in self.replay_buffer])
        dones = np.array([t.done for t in self.replay_buffer])

        if self.use_jit and NUMBA_AVAILABLE:
            self.q_table, loss = fast_q_update(
                self.q_table,
                self.target_table,
                states,
                actions,
                rewards,
                next_states,
                dones,
                self.gamma,
                self.alpha,
            )
        else:
            for i in range(len(states)):
                best_next = np.argmax(self.target_table[next_states[i]])
                td_target = (
                    rewards[i]
                    + (1.0 - dones[i])
                    * self.gamma
                    * self.target_table[next_states[i], best_next]
                )
                td_error = td_target - self.q_table[states[i], actions[i]]
                self.q_table[states[i], actions[i]] += self.alpha * td_error
                loss = td_error**2

        return loss if "loss" in dir() else 0.0

    def _soft_update(self):
        """Soft update of target network."""
        for state in range(self.num_states):
            for action in range(self.num_actions):
                self.target_table[state, action] = (
                    self.tau * self.q_table[state, action]
                    + (1 - self.tau) * self.target_table[state, action]
                )

    def select_action(self, state: int, epsilon: float = 0.1) -> int:
        """Select action with epsilon-greedy policy."""
        if np.random.random() < epsilon:
            return np.random.randint(0, self.num_actions)
        return int(np.argmax(self.q_table[state]))

    def get_best_action(self, state: int) -> int:
        """Get best action for state."""
        return int(np.argmax(self.q_table[state]))

    def get_state_value(self, state: int) -> float:
        """Get value of state."""
        return float(np.max(self.q_table[state]))

    @property
    def epsilon(self) -> float:
        return getattr(self, "_epsilon", 1.0)

    @epsilon.setter
    def epsilon(self, value: float):
        self._epsilon = value

    @property
    def epsilon_min(self) -> float:
        return getattr(self, "_epsilon_min", 0.01)

    @epsilon_min.setter
    def epsilon_min(self, value: float):
        self._epsilon_min = value

    @property
    def epsilon_decay(self) -> float:
        return getattr(self, "_epsilon_decay", 0.995)

    @epsilon_decay.setter
    def epsilon_decay(self, value: float):
        self._epsilon_decay = value

    def save(self, path: str):
        """Save training state."""
        data = {
            "q_table": self.q_table.tolist(),
            "target_table": self.target_table.tolist(),
            "reward_history": self.reward_history,
            "training_steps": self.training_steps,
            "alpha": self.alpha,
            "gamma": self.gamma,
            "tau": self.tau,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str):
        """Load training state."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.q_table = np.array(data["q_table"])
        self.target_table = np.array(data["target_table"])
        self.reward_history = data.get("reward_history", [])
        self.training_steps = data.get("training_steps", 0)
        self.alpha = data.get("alpha", self.alpha)
        self.gamma = data.get("gamma", self.gamma)
        self.tau = data.get("tau", self.tau)


if __name__ == "__main__":
    print("=== Enhanced Q-Learning Demo ===\n")

    print("1. Testing Sum-Tree Prioritized Replay")
    tree = SumTree(8)
    for i in range(8):
        tree.add(float(i + 1), f"data_{i}")
    print(f"  Total priority: {tree.total():.2f}")
    idx, pri, data = tree.get(5.0)
    print(f"  Sampled: {data} (priority: {pri:.2f})")

    print("\n2. Testing Multi-Agent Q-Learning")
    multi_agent = MultiAgentQLearning(num_agents=3, num_states=10, num_actions=5)
    for _ in range(100):
        state = np.random.randint(0, 10)
        action = multi_agent.select_action(state, epsilon=0.5)
        reward = np.random.randn() * 0.1
        next_state = (state + action) % 10
        multi_agent.train(state, action, reward, next_state, done=False)

    consensus = multi_agent.get_consensus(5)
    print(f"  Consensus at state 5: {len(consensus)} actions")

    print("\n3. Testing JIT-Compiled Updates")
    q = np.zeros((10, 5))
    t = np.copy(q)
    s = np.array([0, 1, 2, 3, 4])
    a = np.array([0, 1, 2, 3, 4])
    r = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    ns = np.array([1, 2, 3, 4, 0])
    d = np.array([False, False, False, False, True])

    q, loss = fast_q_update(q, t, s, a, r, ns, d, 0.95, 0.1)
    print(f"  JIT update loss: {loss:.4f}")

    print("\n4. Testing Hyperparameter Tuning")
    tuner = HyperparameterTuner(num_trials=10)
    best = tuner.tune()
    print(
        f"  Best params: alpha={best['alpha']:.4f}, gamma={best['gamma']:.4f}, tau={best['tau']:.4f}"
    )

    print("\n=== Demo Complete ===")
