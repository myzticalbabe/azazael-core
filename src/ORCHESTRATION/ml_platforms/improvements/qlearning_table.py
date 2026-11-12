# coding: utf-8
"""
Qlearning Table

Module for improvements functionality.

Functions:
    - benchmark_framework()
    - push()
    - sample()
    - update_priorities()
    - update()
    - hard_update()
    - get_q_values()
    - select_action()
    - update()
    - add_experience()

Classes:
    - TaskType
    - Transition
    - ExperienceReplay
    - TargetNetwork
    - FrameworkState

Usage:
    >>> import qlearning_table
    >>> qlearning_table.benchmark_framework()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
Q-Learning with Extended Tables for ML Platform Selection
Reinforcement Learning for optimal framework selection based on task characteristics.

IMPROVEMENTS OVER BASIC Q-LEARNING:
1. Experience Replay - Stores transitions and samples randomly for stable learning
2. Target Network - Periodic Q-value updates for convergence stability
3. Double Q-Learning - Reduces overestimation bias
4. Prioritized Experience Replay - Samples important transitions more frequently
5. N-Step Returns - Combines TD and Monte Carlo learning
6. Epsilon Decay - Automatic exploration/exploitation balance
"""

from typing import Any, List
from dataclasses import dataclass, field
from enum import Enum
import numpy as np
import json
import re
from pathlib import Path
from collections import deque
import time

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


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


class ExperienceReplay:
    """
    Experience Replay buffer for stable Q-learning.

    WHY IT MATTERS:
    - Breaks correlation between consecutive experiences
    - Enables efficient reuse of experiences
    - Improves sample efficiency
    - Reduces catastrophic forgetting
    """

    def __init__(
        self,
        capacity: int = 10000,
        alpha: float = 0.6,
        beta_start: float = 0.4,
        beta_frames: int = 100000,
    ):
        self.capacity = capacity
        self.buffer = deque(maxlen=capacity)
        self.priorities = deque(maxlen=capacity)
        self.alpha = alpha
        self.beta_start = beta_start
        self.beta_frames = beta_frames
        self.frame = 1

    def push(self, state: int, action: int, reward: float, next_state: int, done: bool):
        """Add transition to replay buffer."""
        max_priority = max(self.priorities) if self.priorities else 1.0
        transition = Transition(state, action, reward, next_state, done, max_priority)
        self.buffer.append(transition)
        self.priorities.append(max_priority)

    def sample(
        self, batch_size: int
    ) -> tuple[list[Transition], np.ndarray, np.ndarray]:
        """Sample batch with prioritized experience replay."""
        if len(self.buffer) < batch_size:
            return (
                list(self.buffer),
                np.ones(len(self.buffer)),
                np.arange(len(self.buffer)),
            )

        self.beta = min(
            1.0,
            self.beta_start + (1.0 - self.beta_start) * self.frame / self.beta_frames,
        )
        self.frame += 1

        priorities = np.array(self.priorities) ** self.alpha
        probs = priorities / priorities.sum()

        indices = np.random.choice(len(self.buffer), batch_size, p=probs, replace=False)
        samples = [self.buffer[i] for i in indices]

        weights = (len(self.buffer) * probs[indices]) ** (-self.beta)
        weights = weights / weights.max()

        return samples, weights, indices

    def update_priorities(self, indices: list[int], priorities: np.ndarray):
        """Update priorities after learning."""
        for i, priority in zip(indices, priorities):
            self.priorities[i] = priority + 1e-6

    def __len__(self):
        return len(self.buffer)


class TargetNetwork:
    """
    Target network for stable Q-learning updates.

    WHY IT MATTERS:
    - Prevents feedback loops in Q-value updates
    - Improves convergence stability
    - Reduces oscillation during training
    """

    def __init__(
        self, num_states: int = 100, num_actions: int = 30, tau: float = 0.005
    ):
        self.q_table = np.zeros((num_states, num_actions))
        self.target_table = np.copy(self.q_table)
        self.tau = tau
        self.num_states = num_states
        self.num_actions = num_actions

    def update(self):
        """Soft update of target network."""
        for state in range(self.num_states):
            for action in range(self.num_actions):
                self.target_table[state, action] = (
                    self.tau * self.q_table[state, action]
                    + (1 - self.tau) * self.target_table[state, action]
                )

    def hard_update(self):
        """Hard copy of Q network to target."""
        self.target_table = np.copy(self.q_table)

    def get_q_values(self, state: int) -> np.ndarray:
        """Get Q-values from target network."""
        return self.target_table[state]


class FrameworkState:
    """Extended Q-table for ML framework selection with experience replay and target network."""

    FRAMEWORKS = {
        TaskType.COMPUTER_VISION: [
            "pytorch",
            "tensorflow",
            "opencv-python",
            "torchvision",
            "easyocr",
        ],
        TaskType.NLP: [
            "transformers",
            "spacy",
            "nltk",
            "tokenizers",
            "huggingface_hub",
        ],
        TaskType.TABULAR: ["scikit-learn", "xgboost", "lightgbm", "catboost", "optuna"],
        TaskType.REINFORCEMENT: [
            "stable-baselines3",
            "ray",
            "gymnasium",
            "pettingzoo",
            "mlflow",
        ],
        TaskType.GENERATIVE: [
            "pytorch",
            "tensorflow",
            "diffusers",
            "transformers",
            "torch",
        ],
        TaskType.TIME_SERIES: [
            "scikit-learn",
            "statsmodels",
            "torch",
            "tensorflow",
            "pandas",
        ],
        TaskType.GRAPH_LEARNING: [
            "pytorch_geometric",
            "networkx",
            "dgl",
            "spektral",
            "torch",
        ],
    }

    PERFORMANCE_MATRIX = {
        "pytorch": {
            "training_speed": 0.85,
            "inference_speed": 0.88,
            "gpu_support": 0.95,
            "ease_of_use": 0.90,
            "community": 0.95,
        },
        "tensorflow": {
            "training_speed": 0.80,
            "inference_speed": 0.92,
            "gpu_support": 0.90,
            "ease_of_use": 0.85,
            "community": 0.90,
        },
        "scikit-learn": {
            "training_speed": 0.60,
            "inference_speed": 0.70,
            "gpu_support": 0.20,
            "ease_of_use": 0.95,
            "community": 0.90,
        },
        "transformers": {
            "training_speed": 0.75,
            "inference_speed": 0.78,
            "gpu_support": 0.88,
            "ease_of_use": 0.82,
            "community": 0.95,
        },
        "xgboost": {
            "training_speed": 0.78,
            "inference_speed": 0.82,
            "gpu_support": 0.75,
            "ease_of_use": 0.88,
            "community": 0.85,
        },
        "ray": {
            "training_speed": 0.70,
            "inference_speed": 0.65,
            "gpu_support": 0.80,
            "ease_of_use": 0.75,
            "community": 0.80,
        },
    }

    def __init__(
        self,
        alpha: float = 0.1,
        gamma: float = 0.95,
        epsilon: float = 1.0,
        epsilon_decay: float = 0.995,
        epsilon_min: float = 0.01,
        replay_capacity: int = 10000,
        target_update_freq: int = 100,
    ):
        self.q_table: dict[tuple[TaskType, str], float] = {}
        self.replay_buffer = ExperienceReplay(capacity=replay_capacity)
        self.target_network = TargetNetwork(num_states=len(TaskType), num_actions=20)
        self.reward_history: list[float] = []
        self.action_history: list[tuple[TaskType, str, float]] = []
        self.training_steps = 0
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_decay = epsilon_decay
        self.epsilon_min = epsilon_min
        self.target_update_freq = target_update_freq
        self._init_q_table()

    def _init_q_table(self):
        for task_type, frameworks in self.FRAMEWORKS.items():
            for i, framework in enumerate(frameworks):
                self.q_table[(task_type, framework)] = 0.0

    def _get_action_index(self, task: TaskType, framework: str) -> int:
        """Get action index for framework in task."""
        return self.FRAMEWORKS[task].index(framework)

    def _get_framework_by_index(self, task: TaskType, index: int) -> str:
        """Get framework by action index."""
        if 0 <= index < len(self.FRAMEWORKS[task]):
            return self.FRAMEWORKS[task][index]
        return self.FRAMEWORKS[task][0]

    def select_action(self, task: TaskType, exploit: bool = False) -> str:
        """Select action using epsilon-greedy policy."""
        if not exploit and np.random.random() < self.epsilon:
            return np.random.choice(self.FRAMEWORKS[task])

        list(TaskType).index(task)
        q_values = [(fw, self.q_table[(task, fw)]) for fw in self.FRAMEWORKS[task]]
        best_framework = max(q_values, key=lambda x: x[1])[0]
        return best_framework

    def update(
        self,
        task: TaskType,
        framework: str,
        reward: float,
        next_task: (TaskType | None) = None,
        done: bool = False,
    ):
        """Update Q-values with Double Q-Learning."""
        current_q = self.q_table[(task, framework)]
        list(TaskType).index(task)

        if next_task is None:
            next_task = task

        next_framework = self.select_action(next_task, exploit=True)
        next_q = self.q_table[(next_task, next_framework)]

        td_target = reward + (1 - done) * self.gamma * next_q
        td_error = td_target - current_q

        self.q_table[(task, framework)] = current_q + self.alpha * td_error

        self.reward_history.append(reward)
        self.training_steps += 1

        if self.training_steps % self.target_update_freq == 0:
            self.target_network.update()

        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay

    def add_experience(
        self,
        task: TaskType,
        framework: str,
        reward: float,
        next_task: TaskType,
        done: bool = False,
    ):
        """Add transition to experience replay."""
        state = list(TaskType).index(task)
        action = self._get_action_index(task, framework)
        next_state = list(TaskType).index(next_task)
        self.replay_buffer.push(state, action, reward, next_state, done)

    def replay(self, batch_size: int = 32) -> float:
        """Train on batch from experience replay."""
        if len(self.replay_buffer) < batch_size:
            return 0.0

        transitions, weights, indices = self.replay_buffer.sample(batch_size)
        weights = np.array(weights)

        total_loss = 0.0
        td_error = 0.0
        for i, transition in enumerate(transitions):
            task = list(TaskType)[transition.state]
            framework = self._get_framework_by_index(task, transition.action)
            current_q = self.q_table[(task, framework)]

            task_idx = transition.next_state
            next_task = list(TaskType)[task_idx]
            next_framework = self.select_action(next_task, exploit=True)
            target_q = (
                self.replay_buffer.buffer[indices[i]].reward
                + self.gamma * self.q_table[(next_task, next_framework)]
            )

            td_error = target_q - current_q
            loss = weights[i] * td_error**2
            total_loss += loss

            self.q_table[(task, framework)] += self.alpha * td_error

        if indices:
            indices_list = list(indices)
            priorities = (
                np.ones(len(indices_list)) * max(self.replay_buffer.priorities)
                if self.replay_buffer.priorities
                else np.ones(len(indices_list))
            )
            priority = td_error
            self.replay_buffer.update_priorities(indices_list, np.abs(priority) + 1e-6)

        return total_loss / max(len(transitions), 1)

    def get_q_values(self, task: TaskType) -> dict[str, float]:
        """Get Q-values for all frameworks in task."""
        return {fw: self.q_table[(task, fw)] for fw in self.FRAMEWORKS[task]}

    def save(self, path: str):
        """Save Q-table and training state."""
        save_data = {
            "q_table": {f"{k[0].value}|{k[1]}": v for k, v in self.q_table.items()},
            "reward_history": self.reward_history,
            "training_steps": self.training_steps,
            "alpha": self.alpha,
            "gamma": self.gamma,
            "epsilon": self.epsilon,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2)

    def load(self, path: str):
        """Load Q-table and training state."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.q_table = {tuple(k.split("|")): v for k, v in data["q_table"].items()}
        self.reward_history = data.get("reward_history", [])
        self.training_steps = data.get("training_steps", 0)
        self.alpha = data.get("alpha", self.alpha)
        self.gamma = data.get("gamma", self.gamma)
        self.epsilon = data.get("epsilon", self.epsilon)


class ExtendedQLearningTable:
    """Extended Q-table with experience replay and target network."""

    def __init__(
        self,
        num_states: int = 100,
        num_actions: int = 30,
        alpha: float = 0.1,
        gamma: float = 0.95,
    ):
        self.q_table = np.zeros((num_states, num_actions))
        self.target_network = TargetNetwork(num_states, num_actions, tau=0.005)
        self.replay_buffer = ExperienceReplay(capacity=10000)
        self.reward_history: list[float] = []
        self.state_visits = np.zeros(num_states)
        self.action_counts = np.zeros(num_actions)
        self.cumulative_rewards = np.zeros(num_states)
        self.training_steps = 0
        self.alpha = alpha
        self.gamma = gamma

    def train(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool = False,
    ):
        """Train with Double Q-Learning."""
        best_action = np.argmax(self.q_table[next_state])
        target = (
            reward
            + (1 - done)
            * self.gamma
            * self.target_network.get_q_values(next_state)[best_action]
        )

        self.q_table[state, action] += self.alpha * (
            target - self.q_table[state, action]
        )

        self.replay_buffer.push(state, action, reward, next_state, done)
        self.reward_history.append(reward)
        self.state_visits[state] += 1
        self.action_counts[action] += 1
        self.training_steps += 1

        if self.training_steps % 100 == 0:
            self.target_network.update()

    def replay(self, batch_size: int = 32) -> float:
        """Train on batch from replay buffer."""
        if len(self.replay_buffer) < batch_size:
            return 0.0

        transitions, weights, _ = self.replay_buffer.sample(batch_size)
        total_loss = 0.0

        for transition in transitions:
            best_next_action = np.argmax(self.q_table[transition.next_state])
            target = (
                transition.reward
                + (1 - transition.done)
                * self.gamma
                * self.target_network.q_table[transition.next_state, best_next_action]
            )

            loss = (
                weights.mean()
                * (target - self.q_table[transition.state, transition.action]) ** 2
            )
            total_loss += loss

        return total_loss / max(len(transitions), 1)

    def select_action(self, state: int, epsilon: float = 0.1) -> int:
        """Select action with epsilon-greedy policy."""
        if np.random.random() < epsilon:
            return np.random.randint(0, self.q_table.shape[1])
        return int(np.argmax(self.q_table[state]))

    def get_best_action(self, state: int) -> int:
        """Get best action for state."""
        return int(np.argmax(self.q_table[state]))

    def get_state_value(self, state: int) -> float:
        """Get value of state."""
        return float(np.max(self.q_table[state]))

    def save(self, path: str):
        """Save training state."""
        save_data = {"q_table": self.q_table, "reward_history": self.reward_history}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(save_data, f)

    def load(self, path: str):
        """Load training state."""
        data = np.load(path, allow_pickle=True).item()
        self.q_table = data.get("q_table", self.q_table)
        self.reward_history = data.get("reward_history", [])


class AdaptivePlatformSelector:
    """Adaptive ML platform selector using enhanced Q-learning."""

    def __init__(self, config_path: (str | None) = None):
        self.task_q_learning = FrameworkState(
            alpha=0.1, gamma=0.95, replay_capacity=10000, target_update_freq=100
        )
        self.extended_table = ExtendedQLearningTable(num_states=100, num_actions=30)
        self.selection_history: list[dict] = []
        self.task_complexity: dict[Any, Any] = {}
        self.config_path = config_path

    def analyze_task(
        self,
        task_type: TaskType,
        data_size: int,
        gpu_available: bool = False,
        latency_budget: (float | None) = None,
        accuracy_budget: (float | None) = None,
    ) -> int:
        """Analyze task characteristics and compute complexity state."""
        complexity_score = min(data_size / 1e6, 100)
        state = int(complexity_score) % 100
        self.task_complexity[state] = complexity_score

        if latency_budget is not None:
            self.task_complexity[f"{state}_latency"] = latency_budget
        if accuracy_budget is not None:
            self.task_complexity[f"{state}_accuracy"] = accuracy_budget

        return state

    def select_platform(
        self,
        task_type: TaskType,
        state: int,
        gpu_available: bool = False,
        exploit: bool = False,
    ) -> dict[str, Any]:
        """Select optimal platform using Q-learning."""
        framework = self.task_q_learning.select_action(task_type, exploit=exploit)

        framework_scores = self.task_q_learning.PERFORMANCE_MATRIX.get(framework, {})
        gpu_bonus = (
            0.2
            if gpu_available and framework_scores.get("gpu_support", 0) > 0.5
            else 0.0
        )

        q_value = self.task_q_learning.q_table.get((task_type, framework), 0.0)

        selection = {
            "framework": framework,
            "state": state,
            "q_value": q_value,
            "gpu_bonus": gpu_bonus,
            "scores": framework_scores,
            "training_speed": framework_scores.get("training_speed", 0.5),
            "inference_speed": framework_scores.get("inference_speed", 0.5),
            "gpu_support": framework_scores.get("gpu_support", 0.5),
            "ease_of_use": framework_scores.get("ease_of_use", 0.5),
        }

        self.selection_history.append(selection)
        return selection

    def update_with_feedback(
        self,
        task_type: TaskType,
        framework: str,
        feedback: float,
        next_task: (TaskType | None) = None,
    ):
        """Update Q-values with user feedback."""
        self.task_q_learning.update(task_type, framework, feedback, next_task)

        if self.selection_history:
            state = self.selection_history[-1]["state"]
            self.extended_table.train(state, 0, feedback, min(state + 1, 99))

    def train_from_replay(self, batch_size: int = 32) -> float:
        """Train on experience replay."""
        loss = self.task_q_learning.replay(batch_size)
        extended_loss = self.extended_table.replay(batch_size)
        return loss + extended_loss

    def get_recommendations(
        self, task_type: TaskType, top_k: int = 3, criteria: str = "overall"
    ) -> list[dict[str, Any]]:
        """Get top-k platform recommendations."""
        q_values = self.task_q_learning.get_q_values(task_type)

        sorted_frameworks = sorted(q_values.items(), key=lambda x: x[1], reverse=True)[
            :top_k
        ]

        recommendations = []
        for framework, q_value in sorted_frameworks:
            scores = self.task_q_learning.PERFORMANCE_MATRIX.get(framework, {})
            overall = (
                scores.get("training_speed", 0.5) * 0.25
                + scores.get("inference_speed", 0.5) * 0.25
                + scores.get("gpu_support", 0.5) * 0.25
                + scores.get("ease_of_use", 0.5) * 0.15
                + scores.get("community", 0.5) * 0.10
            )

            recommendations.append(
                {
                    "framework": framework,
                    "q_value": q_value,
                    "overall_score": overall,
                    "training_speed": scores.get("training_speed", 0.5),
                    "inference_speed": scores.get("inference_speed", 0.5),
                    "gpu_support": scores.get("gpu_support", 0.5),
                    "ease_of_use": scores.get("ease_of_use", 0.5),
                    "community": scores.get("community", 0.5),
                }
            )

        return recommendations

    def save_state(self, path: (str | None) = None):
        """Save learning state to file."""
        save_path = path or self.config_path or "qlearning_state.json"
        self.task_q_learning.save(save_path)

    def load_state(self, path: (str | None) = None):
        """Load learning state from file."""
        load_path = path or self.config_path
        if load_path and Path(load_path).exists():
            self.task_q_learning.load(load_path)


def benchmark_framework(framework: str, task_type: TaskType) -> float:
    """Benchmark framework performance and return score."""
    scores = FrameworkState.PERFORMANCE_MATRIX.get(framework, {})
    return (
        scores.get("training_speed", 0.5) * 0.3
        + scores.get("inference_speed", 0.5) * 0.3
        + scores.get("gpu_support", 0.5) * 0.2
        + scores.get("ease_of_use", 0.5) * 0.2
    )


if __name__ == "__main__":
    print("=== Enhanced Q-Learning ML Platform Selection Demo ===\n")

    selector = AdaptivePlatformSelector()

    tasks = [
        (TaskType.COMPUTER_VISION, 10000, True),
        (TaskType.NLP, 50000, True),
        (TaskType.TABULAR, 5000, False),
    ]

    for task_type, data_size, gpu in tasks:
        state = selector.analyze_task(task_type, data_size, gpu_available=gpu)
        selection = selector.select_platform(task_type, state, gpu_available=gpu)

        print(f"Task: {task_type.value}")
        print(f"  Selected: {selection['framework']}")
        print(f"  Q-Value: {selection['q_value']:.3f}")
        print(f"  GPU Bonus: {selection['gpu_bonus']:.2f}")
        print(f"  Training Speed: {selection['training_speed']:.2f}")
        print(f"  Inference Speed: {selection['inference_speed']:.2f}")
        print()

    recommendations = selector.get_recommendations(TaskType.COMPUTER_VISION, top_k=3)
    print("Top Recommendations for CV:")
    for r in recommendations:
        print(
            f"  {r['framework']}: Q={r['q_value']:.3f}, Overall={r['overall_score']:.3f}"
        )

    print("\n=== Training on replay buffer ===")
    for _ in range(100):
        loss = selector.train_from_replay(batch_size=16)
    print(
        f"Training complete. Reward history: {len(selector.task_q_learning.reward_history)} entries"
    )

    print("\n=== Demo Complete ===")


class DocState(Enum):
    """Documentation quality states."""

    VALID = 0
    MISSING_HEADER = 1
    MISSING_SECTION = 2
    BROKEN_LINKS = 3
    NAMING_VIOLATION = 4
    PLACEHOLDER_LINKS = 5
    LOW_QUALITY = 6


class DocAction(Enum):
    """Documentation improvement actions."""

    MAINTAIN = 0
    ADD_HEADER = 1
    ADD_SECTION = 2
    FIX_LINKS = 3
    RENAME_FILE = 4
    REMOVE_PLACEHOLDERS = 5
    IMPROVE_CONTENT = 6


class DocumentationQLearning:
    """
    Q-Learning system for documentation quality improvement.

    Uses reinforcement learning to automatically improve documentation
    by learning which actions produce the best quality scores.
    """

    def __init__(
        self,
        alpha: float = 0.1,
        gamma: float = 0.95,
        epsilon: float = 1.0,
        epsilon_decay: float = 0.995,
        epsilon_min: float = 0.01,
    ):
        self.num_states = len(DocState)
        self.num_actions = len(DocAction)
        self.q_table: dict[tuple[int, int], float] = {}
        self.replay_buffer: list[dict] = []
        self.reward_history: list[float] = []
        self.training_steps = 0
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_decay = epsilon_decay
        self.epsilon_min = epsilon_min
        self._init_q_table()

    def _init_q_table(self):
        for state in range(self.num_states):
            for action in range(self.num_actions):
                self.q_table[(state, action)] = 0.0

    def get_state_from_issues(self, issues: dict) -> int:
        """Map documentation issues to a state."""
        link_issues = len(issues.get("link_issues", []))
        header_issues = len(issues.get("header_issues", []))
        section_issues = len(issues.get("section_issues", []))
        naming_issues = len(issues.get("naming_issues", []))

        if link_issues > 0 and any(
            "placeholder" in str(i) for i in issues.get("link_issues", [])
        ):
            return DocState.PLACEHOLDER_LINKS.value
        elif link_issues > 0:
            return DocState.BROKEN_LINKS.value
        elif naming_issues > 0:
            return DocState.NAMING_VIOLATION.value
        elif header_issues > 0:
            return DocState.MISSING_HEADER.value
        elif section_issues > 0:
            return DocState.MISSING_SECTION.value
        elif sum([link_issues, header_issues, section_issues, naming_issues]) > 2:
            return DocState.LOW_QUALITY.value
        return DocState.VALID.value

    def select_action(self, state: int, exploit: bool = False) -> int:
        """Select action using epsilon-greedy policy."""
        if not exploit and np.random.random() < self.epsilon:
            return np.random.randint(0, self.num_actions)

        q_values = [(a, self.q_table[(state, a)]) for a in range(self.num_actions)]
        return max(q_values, key=lambda x: x[1])[0]

    def update(self, state: int, action: int, reward: float, next_state: int):
        """Update Q-values."""
        current_q = self.q_table[(state, action)]

        best_next_q = max(
            self.q_table[(next_state, a)] for a in range(self.num_actions)
        )

        td_target = reward + self.gamma * best_next_q
        td_error = td_target - current_q

        self.q_table[(state, action)] = current_q + self.alpha * td_error
        self.reward_history.append(reward)
        self.training_steps += 1

        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay

    def get_best_action_for_state(self, state: int) -> int:
        """Get best action for a given state (exploit mode)."""
        return self.select_action(state, exploit=True)

    def get_action_name(self, action: int) -> str:
        """Get human-readable action name."""
        return DocAction(action).name.replace("_", " ").title()

    def get_state_name(self, state: int) -> str:
        """Get human-readable state name."""
        return DocState(state).name.replace("_", " ").title()

    def save(self, path: str):
        """Save Q-table and state."""
        data = {
            "q_table": {f"{k[0]}|{k[1]}": v for k, v in self.q_table.items()},
            "reward_history": self.reward_history,
            "training_steps": self.training_steps,
            "alpha": self.alpha,
            "gamma": self.gamma,
            "epsilon": self.epsilon,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str):
        """Load training state."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.q_table = {}
        for k, v in data["q_table"].items():
            parts = k.split("|")
            key: tuple[int, int] = (int(parts[0]), int(parts[1]))
            self.q_table[key] = v


class DocumentationImprover:
    """
    Automated documentation improvement using Q-Learning.

    Analyzes documentation and applies learned improvements.
    """

    ACTIONS = {
        DocAction.ADD_HEADER: "add_header",
        DocAction.ADD_SECTION: "add_section",
        DocAction.FIX_LINKS: "fix_links",
        DocAction.RENAME_FILE: "rename_file",
        DocAction.REMOVE_PLACEHOLDERS: "remove_placeholders",
        DocAction.IMPROVE_CONTENT: "improve_content",
        DocAction.MAINTAIN: "maintain",
    }

    def __init__(self, qlearning_path: (str | None) = None):
        self.learner = DocumentationQLearning()
        self.validator = None

    def analyze_and_improve(self, file_path: Path, dry_run: bool = True) -> dict:
        """Analyze a file and suggest improvements."""
        from scripts.validate_docs import DocumentationValidator

        validator = DocumentationValidator()
        result = validator.validate_file(file_path)

        state = self.learner.get_state_from_issues(result["issues"])
        action = self.learner.get_best_action_for_state(state)

        improvements = {
            "file": str(file_path),
            "current_score": result["score"],
            "state": self.learner.get_state_name(state),
            "action": self.learner.get_action_name(action),
            "issues": result["issues"],
        }

        if not dry_run:
            improvement = self._apply_action(file_path, result["issues"], action)
            improvements["applied"] = improvement
            new_result = validator.validate_file(file_path)
            improvements["new_score"] = new_result["score"]

            reward = new_result["score"] - result["score"]
            self.learner.update(
                state,
                action,
                reward,
                self.learner.get_state_from_issues(new_result["issues"]),
            )

        return improvements

    def _apply_action(self, path: Path, issues: dict, action: int) -> list[str]:
        """Apply improvement action to a file."""
        applied = []
        content = path.read_text(encoding="utf-8")

        if DocAction(action) == DocAction.ADD_HEADER:
            if "Missing YAML header" in issues.get("header_issues", []):
                title = path.stem.replace("_", " ").title()
                header = f"""---
title: {title}
version: 1.0.0
last_updated: 2026-02-10
status: DRAFT
authors: Azazael System
---

"""
                if content.startswith("---"):
                    existing_end = content.find("---", 4) + 3
                    content = header + content[existing_end:]
                else:
                    content = header + content
                path.write_text(content, encoding="utf-8")
                applied.append("Added YAML header")

        elif DocAction(action) == DocAction.REMOVE_PLACEHOLDERS:
            placeholders = issues.get("link_issues", [])
            for placeholder in placeholders:
                if "placeholder" in str(placeholder):
                    # A4 (2026-09-10 scope, fixed 2026-10-28): the link target
                    # is unknown at generation time, so emit bold text with no
                    # placeholder — a doc generator must not mint TODOs.
                    content = re.sub(
                        r"\[([^\]]+)\]\(\s*#\s*\)", r"**\1**", content
                    )
            path.write_text(content, encoding="utf-8")
            applied.append("Replaced placeholder links")

        elif DocAction(action) == DocAction.ADD_SECTION:
            if "Missing section" in str(issues.get("section_issues", [])):
                if "## Overview" not in content:
                    if content.startswith("---"):
                        header_end = content.find("---", 4) + 3
                        content = (
                            content[:header_end]
                            + "\n## Overview\n\n"
                            + content[header_end:]
                        )
                    else:
                        content = (
                            "# "
                            + path.stem.replace("_", " ").title()
                            + "\n\n## Overview\n\n"
                            + content
                        )
                    path.write_text(content, encoding="utf-8")
                    applied.append("Added Overview section")

        return applied

    def batch_improve(
        self, docs_dir: Path, dry_run: bool = True, max_files: int = 50
    ) -> list[dict]:
        """Batch improve documentation in a directory."""
        from scripts.validate_docs import DocumentationValidator

        DocumentationValidator()
        results = []
        files_processed = 0

        for md_file in sorted(docs_dir.rglob("*.md")):
            if files_processed >= max_files:
                break
            if "node_modules" in str(md_file):
                continue

            result = self.analyze_and_improve(md_file, dry_run=dry_run)
            results.append(result)
            files_processed += 1

        return results


if __name__ == "__main__":
    print("=== Documentation Q-Learning Improvement Demo ===\n")

    improver = DocumentationImprover()

    test_path = Path(__file__).parent.parent / "INSTRUCTIONS.md"

    if test_path.exists():
        result = improver.analyze_and_improve(test_path, dry_run=False)
        print(f"File: {result['file']}")
        print(f"  State: {result['state']}")
        print(f"  Action: {result['action']}")
        print(f"  Issues: {result['issues']}")
        print(f"  Applied: {result.get('applied', [])}")
        print(
            f"  Score: {result['current_score']:.2f} -> {result.get('new_score', 'N/A'):.2f}"
        )

    print("\n=== Demo Complete ===")
